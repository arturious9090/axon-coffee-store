from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from store.models import (
    AuditLog,
    Order,
    OrderStatusHistory,
    PromoCode,
    Refund,
    Shipment,
    ShipmentEvent,
)
from store.services.inventory import move_reservations
from store.services.notifications import queue_event, queue_mail
from store.services.shipping import provider


def record(order, note, actor=None):
    OrderStatusHistory.objects.create(order=order, status=order.status, note=note)
    AuditLog.objects.create(actor=actor, action=note, object_id=str(order.pk))
    queue_mail(
        f"status:{order.pk}:{order.status}:{order.payment_status}",
        order.email,
        f"Плин · {order.number}: {order.get_status_display()}",
        f"Демонстраційне замовлення {order.number}.\n{order.get_status_display()}\nОплата: {order.get_payment_status_display()}",
    )


def release_promo(order):
    redemption = getattr(order, "redemption", None)
    if redemption:
        PromoCode.objects.select_for_update().get(pk=redemption.promo_id)
        redemption.state = "released"
        redemption.save(update_fields=["state"])


def request_refund(order, reason):
    refund, _ = Refund.objects.get_or_create(
        order=order,
        purpose="full",
        defaults={"amount": order.total, "reason": reason, "attempt_id": order.paid_attempt_id},
    )
    queue_event(f"refund:{refund.pk}", "refund", {"refund_id": refund.pk})
    return refund


@transaction.atomic
def transition(order_id, action, actor=None, note=""):
    order = Order.objects.select_for_update().get(pk=order_id)
    if action == "cancel":
        if order.status == Order.Status.CANCELLED:
            return order
        if order.status not in {
            Order.Status.PENDING,
            Order.Status.CONFIRMED,
            Order.Status.PACKING,
            Order.Status.READY,
        }:
            raise ValidationError("Це замовлення вже не можна скасувати; потрібне повернення.")
        release_promo(order)
        move_reservations(order, "released")
        move_reservations(order, "returned")
        order.status = Order.Status.CANCELLED
        order.reserved_until = None
        if order.payment_status == Order.PaymentStatus.PAID:
            request_refund(order, note or "Замовлення скасовано")
        if hasattr(order, "shipment"):
            order.shipment.status = "cancelled"
            order.shipment.save(update_fields=["status"])
    elif action == "packing":
        if order.status == Order.Status.PACKING:
            return order
        if order.status != Order.Status.CONFIRMED:
            raise ValidationError("Готувати можна лише підтверджене замовлення.")
        order.status = Order.Status.PACKING
    elif action == "ready":
        if order.status == Order.Status.READY:
            return order
        if order.delivery_method != "pickup" or order.status not in {
            Order.Status.CONFIRMED,
            Order.Status.PACKING,
        }:
            raise ValidationError("До самовивозу готується лише підтверджене замовлення.")
        order.status = Order.Status.READY
        order.pickup_until = timezone.now() + timedelta(
            days=order.destination.get("holding_days", 3)
        )
    elif action == "paid":
        if order.payment_status == Order.PaymentStatus.PAID:
            return order
        if order.payment_method == "online" or order.status not in {
            Order.Status.READY,
            Order.Status.SHIPPED,
            Order.Status.COMPLETED,
        }:
            raise ValidationError(
                "Оплату на місці/післяплату підтверджують під час видачі або після доставки."
            )
        order.payment_status = Order.PaymentStatus.PAID
    elif action == "complete":
        if order.status == Order.Status.COMPLETED:
            return order
        if order.status != Order.Status.READY or order.payment_status != Order.PaymentStatus.PAID:
            raise ValidationError("Для видачі самовивозу замовлення має бути готове й оплачене.")
        order.status = Order.Status.COMPLETED
    elif action == "refund":
        if order.payment_status == Order.PaymentStatus.REFUNDED:
            return order
        if order.payment_status != Order.PaymentStatus.PAID or not note.strip():
            raise ValidationError("Потрібна оплачена покупка та причина повернення.")
        if order.status not in {
            Order.Status.CANCELLED,
            Order.Status.REVIEW,
            Order.Status.COMPLETED,
        }:
            raise ValidationError(
                "Спершу скасуйте замовлення або підтвердіть отримання повернення."
            )
        request_refund(order, note)
    else:
        raise ValidationError("Невідома дія.")
    order.save()
    record(order, note or action, actor)
    return order


@transaction.atomic
def create_shipment(order_id, actor=None, mock_scenario="normal"):
    order = Order.objects.select_for_update().get(pk=order_id)
    existing = Shipment.objects.filter(order=order).first()
    if existing:
        return existing
    if order.delivery_method == "pickup" or order.status not in {
        Order.Status.CONFIRMED,
        Order.Status.PACKING,
    }:
        raise ValidationError("Для цього замовлення не можна створити ТТН.")
    data = provider.create_shipment(order, scenario=mock_scenario)
    shipment = Shipment.objects.create(order=order, tracking_number=data["IntDocNumber"])
    order.status = Order.Status.SHIPPED
    order.save(update_fields=["status"])
    record(order, "Демонстраційну ТТН створено", actor)
    return shipment


@transaction.atomic
def update_shipment(order_id, status, event_id, actor=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    shipment = Shipment.objects.select_for_update().get(order=order)
    existing = ShipmentEvent.objects.filter(external_id=event_id).first()
    if existing:
        if existing.shipment_id != shipment.pk or existing.status != status:
            raise ValidationError("Конфлікт ідентифікатора події доставки.")
        return shipment
    transitions = {
        "created": {"in_transit", "returned"},
        "in_transit": {"arrived", "returned"},
        "arrived": {"delivered", "returned"},
        "delivered": {"returned"},
    }
    if status not in transitions.get(shipment.status, set()):
        raise ValidationError("Недопустима зміна статусу доставки.")
    ShipmentEvent.objects.create(shipment=shipment, external_id=event_id, status=status)
    shipment.status = status
    shipment.save(update_fields=["status"])
    if status == "delivered":
        order.status = Order.Status.COMPLETED
    elif status == "returned":
        release_promo(order)
        move_reservations(order, "returned")
        order.status = Order.Status.CANCELLED
        if order.payment_status == Order.PaymentStatus.PAID:
            request_refund(order, "Повернення від перевізника")
    order.save(update_fields=["status"])
    record(order, "Доставка: " + status, actor)
    return shipment


@transaction.atomic
def expire_order(order_id):
    order = Order.objects.select_for_update().get(pk=order_id)
    if (
        order.status == Order.Status.PENDING
        and order.reserved_until
        and order.reserved_until <= timezone.now()
        and order.payment_status != Order.PaymentStatus.PAID
    ):
        release_promo(order)
        move_reservations(order, "released")
        order.reserved_until = None
        order.save(update_fields=["reserved_until"])
        record(order, "Термін резерву минув; оплату можна повторити")
    elif (
        order.status == Order.Status.READY
        and order.pickup_until
        and order.pickup_until <= timezone.now()
    ):
        transition(order.pk, "cancel", note="Термін самовивозу минув")
