import base64
import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from store.models import Order, PaymentAttempt, PaymentEvent, PromoCode, Refund
from store.services.inventory import move_reservations, reserve
from store.services.notifications import queue_event
from store.services.orders import record, release_promo, request_refund


class PaymentProvider(Protocol):
    def checkout(self, attempt: PaymentAttempt) -> dict: ...
    def encode(self, payload: dict) -> dict: ...
    def decode(self, data: str, signature: str) -> dict: ...


class MockLiqPay:
    """Local simulation of LiqPay v7 data/signature envelope (SHA3-256)."""

    def signature(self, data):
        key = settings.MOCK_LIQPAY_KEY
        return base64.b64encode(hashlib.sha3_256((key + data + key).encode()).digest()).decode()

    def encode(self, payload):
        data = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        return {"data": data, "signature": self.signature(data)}

    def decode(self, data, signature):
        if (
            not signature.isascii()
            or len(data) > 20000
            or not hmac.compare_digest(self.signature(data), signature)
        ):
            raise ValidationError("Неправильний підпис платежу.")
        try:
            payload = json.loads(base64.b64decode(data, validate=True))
        except (ValueError, UnicodeError) as exc:
            raise ValidationError("Пошкоджена подія платежу.") from exc
        if not isinstance(payload, dict):
            raise ValidationError("Неправильний формат події.")
        return payload

    def checkout(self, attempt):
        return self.encode(
            {
                "version": 7,
                "public_key": "demo_public_key",
                "action": "pay",
                "amount": str(Decimal(attempt.order.total) / 100),
                "currency": "UAH",
                "order_id": str(attempt.pk),
                "description": "Демо " + attempt.order.number,
            }
        )

    def callback(self, attempt, status):
        return {
            "version": 7,
            "order_id": str(attempt.pk),
            "merchant_order_id": str(attempt.order_id),
            "payment_id": "mock-" + str(attempt.pk),
            "event_id": f"mock-{attempt.pk}-{status}",
            "amount": str(Decimal(attempt.order.total) / 100),
            "currency": "UAH",
            "status": status,
        }


provider = MockLiqPay()


def reactivate_promo(order):
    redemption = getattr(order, "redemption", None)
    if not redemption:
        return
    promo = PromoCode.objects.select_for_update().get(pk=redemption.promo_id)
    if redemption.state == "released":
        if (
            not promo.active
            or not promo.starts_at <= timezone.now() < promo.ends_at
            or promo.redemptions.exclude(state="released").count() >= promo.max_uses
        ):
            raise ValidationError("Резерв промокоду минув; оформіть нове замовлення.")
        redemption.state = "reserved"
        redemption.save(update_fields=["state"])


@transaction.atomic
def begin_attempt(order_id, key):
    order = Order.objects.select_for_update().get(pk=order_id)
    existing = PaymentAttempt.objects.filter(key=key).first()
    if existing:
        if existing.order_id != order.pk:
            raise ValidationError("Ключ платежу належить іншому замовленню.")
        return existing
    if (
        order.payment_method != "online"
        or order.status != Order.Status.PENDING
        or order.payment_status in {Order.PaymentStatus.PAID, Order.PaymentStatus.REFUNDED}
    ):
        raise ValidationError("Це замовлення не можна оплатити онлайн.")
    current = order.payment_attempts.filter(
        status__in=["created", "processing", "wait_accept"]
    ).first()
    if current and order.reservations.filter(state="held").exists():
        return current
    reactivate_promo(order)
    if not order.reservations.filter(state="held").exists():
        reserve(order, order.items.all())
    order.reserved_until = timezone.now() + timedelta(minutes=settings.RESERVATION_MINUTES)
    order.payment_status = Order.PaymentStatus.PROCESSING
    order.save(update_fields=["reserved_until", "payment_status"])
    return PaymentAttempt.objects.create(order=order, key=key)


@transaction.atomic
def apply_event(payload):
    if not all(
        isinstance(payload.get(field), str)
        for field in [
            "order_id",
            "event_id",
            "status",
            "payment_id",
            "merchant_order_id",
            "currency",
        ]
    ):
        raise ValidationError("Неправильні типи полів події платежу.")
    try:
        attempt_id = uuid.UUID(payload["order_id"])
        amount = Decimal(str(payload["amount"])) * 100
        event_id = str(payload["event_id"])
        status = payload["status"]
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise ValidationError("Неповна подія платежу.") from exc
    if (
        not amount.is_finite()
        or amount != amount.to_integral_value()
        or len(event_id) > 120
        or not event_id
    ):
        raise ValidationError("Неправильна сума або ідентифікатор події.")
    attempt = PaymentAttempt.objects.select_related("order").filter(pk=attempt_id).first()
    if not attempt:
        raise ValidationError("Платіж не знайдено.")
    order = Order.objects.select_for_update().get(pk=attempt.order_id)
    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
    if (
        payload.get("currency") != order.currency
        or int(amount) != order.total
        or payload.get("merchant_order_id") != str(order.pk)
    ):
        raise ValidationError("Сума, валюта або замовлення не відповідають платежу.")
    if payload["payment_id"] != "mock-" + str(attempt.pk) or payload.get("version") != 7:
        raise ValidationError("Неправильний ідентифікатор платежу або версія симулятора.")
    if status not in {"success", "failure", "error", "processing", "wait_accept", "reversed"}:
        raise ValidationError("Невідомий статус платежу.")
    existing = PaymentEvent.objects.filter(external_id=event_id).first()
    if existing:
        if existing.attempt_id != attempt.pk or existing.payload != payload:
            raise ValidationError("Конфлікт ідентифікатора події платежу.")
        return order
    PaymentEvent.objects.create(
        external_id=event_id, attempt=attempt, status=status, payload=payload
    )
    if order.payment_status in {Order.PaymentStatus.PAID, Order.PaymentStatus.REFUNDED}:
        if status == "success" and attempt.pk != order.paid_attempt_id:
            refund, _ = Refund.objects.get_or_create(
                attempt=attempt,
                defaults={
                    "order": order,
                    "amount": order.total,
                    "purpose": "duplicate",
                    "reason": "Повторний успішний платіж",
                },
            )
            queue_event(f"refund:{refund.pk}", "refund", {"refund_id": refund.pk})
            attempt.status = "success"
            attempt.save(update_fields=["status"])
        return order
    latest = order.payment_attempts.order_by("-created_at", "-pk").first()
    if status != "success" and latest and latest.pk != attempt.pk:
        return order
    attempt.status = status
    attempt.save(update_fields=["status"])
    if status == "success":
        order.payment_status = Order.PaymentStatus.PAID
        order.paid_attempt = attempt
        try:
            if order.status == Order.Status.CANCELLED:
                raise ValidationError("Успішна оплата скасованого замовлення")
            with transaction.atomic():
                reactivate_promo(order)
                if not order.reservations.filter(state="held").exists():
                    reserve(order, order.items.all())
                move_reservations(order, "consumed")
                if hasattr(order, "redemption"):
                    order.redemption.state = "redeemed"
                    order.redemption.save(update_fields=["state"])
            order.status = Order.Status.CONFIRMED
        except ValidationError:
            order.status = Order.Status.REVIEW
            request_refund(order, "Пізня оплата: товар або промокод недоступний")
        order.reserved_until = None
    elif status in {"failure", "error", "reversed"}:
        release_promo(order)
        move_reservations(order, "released")
        order.payment_status = Order.PaymentStatus.FAILED
        order.reserved_until = None
    else:
        order.payment_status = Order.PaymentStatus.PROCESSING
    order.save()
    record(order, "Платіж: " + status)
    return order


def queue_simulated_callback(attempt, status, delay_seconds=0):
    if status not in {"success", "failure", "processing", "wait_accept", "error", "reversed"}:
        raise ValidationError("Невідомий сценарій.")
    return queue_event(
        f"payment:{attempt.pk}:{status}",
        "payment",
        provider.callback(attempt, status),
        timezone.now() + timedelta(seconds=delay_seconds),
    )
