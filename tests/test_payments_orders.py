import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from store.models import (
    Order,
    OutboxEvent,
    PaymentEvent,
    PromoRedemption,
    Refund,
    StockItem,
    StockMovement,
)
from store.services.checkout import create_order
from store.services.notifications import queue_mail
from store.services.orders import create_shipment, expire_order, transition, update_shipment
from store.services.payments import apply_event, begin_attempt, provider, queue_simulated_callback
from store.services.shipping import create_quote
from store.tasks import poll_shipments, process_one

pytestmark = pytest.mark.django_db


@pytest.fixture
def order(cart, checkout_data):
    return create_order(cart, create_quote(cart, checkout_data).pk, uuid.uuid4(), checkout_data)[0]


def pay(order, status="success"):
    attempt = begin_attempt(order.pk, uuid.uuid4())
    apply_event(provider.callback(attempt, status))
    order.refresh_from_db()
    return attempt


def test_duplicate_success_only_consumes_once(order):
    attempt = pay(order)
    for _ in range(3):
        apply_event(provider.callback(attempt, "success"))
    stock = StockItem.objects.get()
    assert stock.on_hand == 18 and stock.reserved == 0
    assert PaymentEvent.objects.count() == 1
    assert StockMovement.objects.filter(delta=-2).count() == 1
    assert order.status == Order.Status.CONFIRMED and order.payment_status == "paid"


def test_failure_and_retry(order):
    first = pay(order, "failure")
    assert StockItem.objects.get().reserved == 0
    second = pay(order)
    assert second.pk != first.pk
    assert StockItem.objects.get().on_hand == 18
    assert order.payment_status == "paid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "0.01"),
        ("currency", "EUR"),
        ("merchant_order_id", str(uuid.uuid4())),
        ("status", "unknown"),
    ],
)
def test_rejects_invalid_payment_data(order, field, value):
    attempt = begin_attempt(order.pk, uuid.uuid4())
    payload = provider.callback(attempt, "success")
    payload[field] = value
    with pytest.raises(ValidationError):
        apply_event(payload)
    assert not PaymentEvent.objects.exists()


def test_signature_and_envelope(order):
    attempt = begin_attempt(order.pk, uuid.uuid4())
    payload = provider.callback(attempt, "success")
    envelope = provider.encode(payload)
    assert provider.decode(**envelope) == payload
    with pytest.raises(ValidationError):
        provider.decode(envelope["data"], "wrong")


def test_expiry_releases_stock_and_promo(cart, promo, checkout_data):
    cart.promo_code = promo.code
    cart.save()
    order = create_order(cart, create_quote(cart, checkout_data).pk, uuid.uuid4(), checkout_data)[0]
    Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now() - timedelta(seconds=1))
    expire_order(order.pk)
    assert StockItem.objects.get().reserved == 0
    assert PromoRedemption.objects.get().state == "released"


@pytest.mark.parametrize("available", [True, False])
def test_late_success_recovers_or_refunds(order, available):
    attempt = begin_attempt(order.pk, uuid.uuid4())
    Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now() - timedelta(seconds=1))
    expire_order(order.pk)
    if not available:
        StockItem.objects.update(on_hand=0)
    apply_event(provider.callback(attempt, "success"))
    order.refresh_from_db()
    if available:
        assert order.status == "confirmed" and StockItem.objects.get().on_hand == 18
    else:
        assert order.status == "review"
        refund = Refund.objects.get()
        assert process_one(OutboxEvent.objects.get(key=f"refund:{refund.pk}").pk)
        order.refresh_from_db()
        assert order.payment_status == "refunded" and StockItem.objects.get().on_hand == 0


def test_old_failure_does_not_release_new_attempt_reserve(order):
    first = begin_attempt(order.pk, uuid.uuid4())
    Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now() - timedelta(seconds=1))
    expire_order(order.pk)
    begin_attempt(order.pk, uuid.uuid4())
    apply_event(provider.callback(first, "failure"))
    assert StockItem.objects.get().reserved == 2


def test_extra_success_is_refunded_without_changing_paid_order(order):
    first = begin_attempt(order.pk, uuid.uuid4())
    Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now() - timedelta(seconds=1))
    expire_order(order.pk)
    second = pay(order)
    apply_event(provider.callback(first, "success"))
    refund = Refund.objects.get(purpose="duplicate")
    assert refund.attempt_id == first.pk
    process_one(OutboxEvent.objects.get(key=f"refund:{refund.pk}").pk)
    order.refresh_from_db()
    assert order.payment_status == "paid" and order.paid_attempt_id == second.pk
    assert StockItem.objects.get().on_hand == 18


def test_cancel_and_refund_are_repeatable(order):
    pay(order)
    transition(order.pk, "cancel", note="Демо")
    transition(order.pk, "cancel", note="Повтор")
    refund = Refund.objects.get()
    event = OutboxEvent.objects.get(key=f"refund:{refund.pk}")
    assert process_one(event.pk)
    assert not process_one(event.pk)
    assert StockItem.objects.get().on_hand == 20
    assert StockMovement.objects.filter(delta=2).count() == 1
    assert Refund.objects.count() == 1


def test_late_payment_after_cancellation_is_refunded(order):
    attempt = begin_attempt(order.pk, uuid.uuid4())
    transition(order.pk, "cancel")
    apply_event(provider.callback(attempt, "success"))
    assert Refund.objects.filter(purpose="full").exists()
    assert StockItem.objects.get().on_hand == 20


def test_shipment_is_idempotent_and_return_restocks(order):
    pay(order)
    first = create_shipment(order.pk)
    assert create_shipment(order.pk).pk == first.pk
    update_shipment(order.pk, "in_transit", "event-1")
    update_shipment(order.pk, "returned", "event-2")
    update_shipment(order.pk, "returned", "event-2")
    assert StockItem.objects.get().on_hand == 20
    assert Refund.objects.count() == 1


def test_invalid_order_and_delivery_transitions_rejected(order):
    with pytest.raises(ValidationError):
        transition(order.pk, "complete")
    with pytest.raises(ValidationError):
        create_shipment(order.pk)
    pay(order)
    create_shipment(order.pk)
    with pytest.raises(ValidationError):
        update_shipment(order.pk, "delivered", "invalid")


@pytest.mark.parametrize("method", ["cash", "card"])
def test_pickup_cycle(cart, checkout_data, method):
    checkout_data.update(delivery_method="pickup", payment_method=method)
    order = create_order(cart, create_quote(cart, checkout_data).pk, uuid.uuid4(), checkout_data)[0]
    assert order.status == "confirmed" and order.payment_status == "unpaid"
    transition(order.pk, "ready")
    transition(order.pk, "paid")
    transition(order.pk, "complete")
    order.refresh_from_db()
    assert order.status == "completed" and order.payment_status == "paid"


def test_cod_delivery_does_not_implicitly_mark_paid(cart, checkout_data):
    checkout_data["payment_method"] = "cod"
    order = create_order(cart, create_quote(cart, checkout_data).pk, uuid.uuid4(), checkout_data)[0]
    shipment = create_shipment(order.pk)
    shipment.created_at = timezone.now() - timedelta(minutes=10)
    shipment.save()
    for _ in range(3):
        poll_shipments()
    order.refresh_from_db()
    assert order.status == "completed" and order.payment_status == "unpaid"
    transition(order.pk, "paid")
    order.refresh_from_db()
    assert order.payment_status == "paid"


def test_outbox_keeps_failed_event_and_retries(order):
    attempt = begin_attempt(order.pk, uuid.uuid4())
    event = queue_simulated_callback(attempt, "success")
    with patch("store.tasks.apply_event", side_effect=RuntimeError("worker failure")):
        assert not process_one(event.pk)
    event.refresh_from_db()
    assert event.processed_at is None and event.attempts == 1
    OutboxEvent.objects.filter(pk=event.pk).update(available_at=timezone.now())
    assert process_one(event.pk)
    assert StockItem.objects.get().on_hand == 18


def test_mail_failure_is_retryable_and_processed_event_is_skipped(order, mailoutbox):
    queue_mail("unique-notification", "demo@example.com", "Демо", "Тест")
    event = OutboxEvent.objects.get(key="mail:unique-notification")
    with patch("store.tasks.DemoEmailProvider.send", side_effect=OSError("mail unavailable")):
        assert not process_one(event.pk)
    OutboxEvent.objects.filter(pk=event.pk).update(available_at=timezone.now())
    assert process_one(event.pk)
    assert not process_one(event.pk)
    assert len(mailoutbox) == 1
