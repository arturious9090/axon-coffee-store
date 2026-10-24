import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.utils import timezone

from store.models import Cart, CartItem, Order, StockItem
from store.services.checkout import create_order
from store.services.orders import expire_order
from store.services.payments import apply_event, begin_attempt, provider
from store.services.shipping import create_quote

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgres]


def run_together(functions):
    barrier = Barrier(len(functions))

    def execute(function):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return function()
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        return list(pool.map(execute, functions))


def two_checkouts(coffee, checkout_data, promo=None):
    variant, grind = coffee
    result = []
    for _ in range(2):
        cart = Cart.objects.create(promo_code=promo.code if promo else "")
        CartItem.objects.create(cart=cart, variant=variant, grind=grind, quantity=1)
        quote = create_quote(cart, checkout_data)

        def checkout(cart_id=cart.pk, quote_id=quote.pk):
            try:
                create_order(Cart.objects.get(pk=cart_id), quote_id, uuid.uuid4(), checkout_data)
                return "created"
            except ValidationError:
                return "rejected"

        result.append(checkout)
    return result


def test_only_one_buyer_gets_last_bag(coffee, checkout_data):
    StockItem.objects.update(on_hand=1)
    assert sorted(run_together(two_checkouts(coffee, checkout_data))) == ["created", "rejected"]
    assert Order.objects.count() == 1
    stock = StockItem.objects.get()
    assert stock.on_hand == 1 and stock.reserved == 1


def test_promo_last_use_is_atomic(coffee, checkout_data, promo):
    promo.max_uses = 1
    promo.minimum = 0
    promo.save()
    assert sorted(run_together(two_checkouts(coffee, checkout_data, promo))) == [
        "created",
        "rejected",
    ]
    assert promo.redemptions.exclude(state="released").count() == 1


def test_payment_and_expiry_share_order_lock(cart, checkout_data):
    order = create_order(cart, create_quote(cart, checkout_data).pk, uuid.uuid4(), checkout_data)[0]
    attempt = begin_attempt(order.pk, uuid.uuid4())
    payload = provider.callback(attempt, "success")
    Order.objects.filter(pk=order.pk).update(reserved_until=timezone.now() - timedelta(seconds=1))
    run_together([lambda: apply_event(payload), lambda: expire_order(order.pk)])
    order.refresh_from_db()
    stock = StockItem.objects.get()
    assert order.status == "confirmed" and order.payment_status == "paid"
    assert stock.on_hand == 18 and stock.reserved == 0


def test_duplicate_callbacks_consumption_is_atomic(cart, checkout_data):
    order = create_order(cart, create_quote(cart, checkout_data).pk, uuid.uuid4(), checkout_data)[0]
    attempt = begin_attempt(order.pk, uuid.uuid4())
    payload = provider.callback(attempt, "success")
    run_together([lambda: apply_event(payload), lambda: apply_event(payload)])
    assert StockItem.objects.get().on_hand == 18
    assert order.payment_attempts.get().events.count() == 1
