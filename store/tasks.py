from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from store.models import AuditLog, Notification, Order, OutboxEvent, Refund, Shipment
from store.services.notifications import DemoEmailProvider
from store.services.orders import expire_order, record
from store.services.payments import apply_event
from store.services.shipping import provider as shipping_provider


def process_one(event_id):
    try:
        with transaction.atomic():
            event = (
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(pk=event_id, processed_at=None, available_at__lte=timezone.now())
                .first()
            )
            if not event:
                return False
            if event.topic == "payment":
                apply_event(event.payload)
            elif event.topic == "mail":
                notification = Notification.objects.select_for_update().get(
                    pk=event.payload["notification_id"]
                )
                if not notification.sent_at:
                    DemoEmailProvider().send(notification)
                    notification.sent_at = timezone.now()
                    notification.save(update_fields=["sent_at"])
            elif event.topic == "refund":
                refund_info = Refund.objects.get(pk=event.payload["refund_id"])
                order = Order.objects.select_for_update().get(pk=refund_info.order_id)
                refund = Refund.objects.select_for_update().get(pk=refund_info.pk)
                if refund.status != "completed":
                    refund.status = "completed"
                    refund.save(update_fields=["status"])
                    if refund.purpose == "full":
                        order.payment_status = Order.PaymentStatus.REFUNDED
                        order.save(update_fields=["payment_status"])
                        record(order, "Повне демонстраційне повернення коштів")
                    else:
                        AuditLog.objects.create(
                            action="Демонстраційне повернення повторного платежу",
                            object_id=str(refund.attempt_id),
                        )
            else:
                raise ValueError("Unknown outbox topic")
            event.processed_at = timezone.now()
            event.attempts += 1
            event.last_error = ""
            event.save(update_fields=["processed_at", "attempts", "last_error"])
            return True
    except Exception as exc:
        event = OutboxEvent.objects.get(pk=event_id)
        OutboxEvent.objects.filter(pk=event_id, processed_at=None).update(
            attempts=F("attempts") + 1,
            last_error=type(exc).__name__,
            available_at=timezone.now()
            + timedelta(seconds=min(300, 2 ** min(event.attempts + 1, 8))),
        )
        return False


@shared_task
def process_outbox():
    ids = list(
        OutboxEvent.objects.filter(processed_at=None, available_at__lte=timezone.now())
        .order_by("pk")
        .values_list("pk", flat=True)[:100]
    )
    return sum(process_one(pk) for pk in ids)


@shared_task
def expire_reservations():
    ids = list(
        Order.objects.filter(
            Q(status="pending", reserved_until__lte=timezone.now())
            | Q(status="ready", pickup_until__lte=timezone.now())
        ).values_list("pk", flat=True)
    )
    for order_id in ids:
        expire_order(order_id)
    return len(ids)


@shared_task
def poll_shipments():
    count = 0
    for shipment in Shipment.objects.exclude(status__in=["delivered", "returned", "cancelled"]):
        status = shipping_provider.track(shipment)
        if status != shipment.status:
            from store.services.orders import update_shipment

            update_shipment(shipment.order_id, status, f"poll:{shipment.pk}:{status}")
        count += 1
    return count
