import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from store.models import Cart, GrindOption, Order, OrderItem, ProductVariant, StockItem
from store.services.cart import add_item, summarize
from store.services.checkout import create_order
from store.services.shipping import create_quote

pytestmark = pytest.mark.django_db


def test_server_discount_and_item_snapshots(cart, promo, checkout_data):
    cart.promo_code = promo.code
    cart.save()
    quote = create_quote(cart, checkout_data)
    order, token = create_order(cart, quote.pk, uuid.uuid4(), checkout_data)
    assert order.subtotal == 72000
    assert order.discount == 7200
    assert order.total == 72300
    assert sum(item.discount for item in order.items.all()) == 7200
    assert token and len(token) > 30
    variant = order.items.first().variant
    variant.price = 99000
    variant.product.name = "Нова назва"
    variant.product.save()
    variant.save()
    item = OrderItem.objects.get(order=order)
    assert item.name == "Демо кава" and item.unit_price == 36000


def test_confirmation_retry_returns_same_order(cart, checkout_data):
    quote = create_quote(cart, checkout_data)
    key = uuid.uuid4()
    first, _ = create_order(cart, quote.pk, key, checkout_data)
    second, token = create_order(cart, quote.pk, key, checkout_data)
    assert first.pk == second.pk and token is None
    assert Order.objects.count() == 1
    assert first.reservations.count() == 1


@pytest.mark.parametrize("change", ["price", "cart", "quote-expiry", "shipping"])
def test_changed_checkout_requires_review(cart, checkout_data, change):
    quote = create_quote(cart, checkout_data)
    if change == "price":
        ProductVariant.objects.filter(pk=cart.items.first().variant_id).update(price=40000)
    elif change == "cart":
        cart.items.update(quantity=3)
    elif change == "quote-expiry":
        quote.expires_at = timezone.now() - timedelta(seconds=1)
        quote.save()
    else:
        from store.models import ShopSettings

        ShopSettings.objects.update(free_shipping_threshold=1)
    with pytest.raises(ValidationError):
        create_order(cart, quote.pk, uuid.uuid4(), checkout_data)
    assert not Order.objects.exists()


def test_unavailable_or_archived_product_cannot_be_ordered(cart, checkout_data):
    quote = create_quote(cart, checkout_data)
    StockItem.objects.update(on_hand=1)
    with pytest.raises(ValidationError):
        create_order(cart, quote.pk, uuid.uuid4(), checkout_data)
    assert not Order.objects.exists()
    cart.items.first().variant.product.__class__.objects.update(active=False)
    with pytest.raises(ValidationError):
        create_order(cart, quote.pk, uuid.uuid4(), checkout_data)


def test_grinds_share_same_stock(cart, coffee):
    variant, _ = coffee
    StockItem.objects.update(on_hand=3)
    second_grind = GrindOption.objects.create(slug="filter", name="Для фільтра")
    add_item(cart, variant, second_grind, 1)
    with pytest.raises(ValidationError):
        add_item(cart, variant, second_grind, 1)
    assert cart.items.count() == 2


@pytest.mark.parametrize(
    "kind,value,expected",
    [("percent", 15, 10800), ("fixed", 10000, 10000), ("fixed", 999999, 72000)],
)
def test_discount_calculation(cart, promo, kind, value, expected):
    promo.kind, promo.value = kind, value
    promo.save()
    cart.promo_code = promo.code
    assert summarize(cart).discount == expected


def test_sale_items_excluded_until_enabled(cart, promo):
    ProductVariant.objects.update(regular_price=50000)
    cart.promo_code = promo.code
    assert summarize(cart).discount == 0
    promo.allow_sale = True
    promo.save()
    assert summarize(cart).discount == 7200


@pytest.mark.parametrize("condition", ["expired", "not-started", "inactive", "minimum", "limit"])
def test_invalid_promo(cart, promo, condition):
    if condition == "expired":
        promo.ends_at = timezone.now() - timedelta(seconds=1)
    elif condition == "not-started":
        promo.starts_at = timezone.now() + timedelta(hours=1)
    elif condition == "inactive":
        promo.active = False
    elif condition == "minimum":
        promo.minimum = 100000
    else:
        promo.max_uses = 0
    promo.save()
    cart.promo_code = promo.code
    with pytest.raises(ValidationError):
        summarize(cart)


@pytest.mark.parametrize(
    "service,payment",
    [("pickup", "cod"), ("warehouse", "cash"), ("courier", "card"), ("locker", "cash")],
)
def test_invalid_delivery_payment_combinations(cart, checkout_data, service, payment):
    checkout_data.update(delivery_method=service, payment_method=payment)
    with pytest.raises(ValidationError):
        create_quote(cart, checkout_data)


@pytest.mark.parametrize(
    "service,payment,shipping,commission",
    [
        ("pickup", "cash", 0, 0),
        ("pickup", "card", 0, 0),
        ("pickup", "online", 0, 0),
        ("warehouse", "cod", 7500, 3440),
        ("locker", "online", 6500, 0),
        ("courier", "cod", 11000, 3440),
    ],
)
def test_delivery_quotes(cart, checkout_data, service, payment, shipping, commission):
    checkout_data.update(
        delivery_method=service,
        payment_method=payment,
        branch=f"demo-odesa-{service}-1",
        street="Вигадана вулиця, 7",
    )
    quote = create_quote(cart, checkout_data)
    assert quote.shipping == shipping and quote.commission == commission
    assert quote.total == 72000 + shipping + commission


def test_free_shipping_and_extra_weight(cart, checkout_data):
    cart.items.update(quantity=5)
    quote = create_quote(cart, checkout_data)
    assert quote.shipping == 0
    ProductVariant.objects.update(price=10000)
    assert create_quote(cart, checkout_data).shipping == 9000


def test_branch_must_belong_to_city(cart, checkout_data):
    checkout_data["branch"] = "demo-kyiv-warehouse-1"
    with pytest.raises(ValidationError):
        create_quote(cart, checkout_data)


def test_database_prevents_negative_available_stock(coffee):
    with pytest.raises(IntegrityError), transaction.atomic():
        StockItem.objects.update(reserved=21)


def test_cannot_reuse_other_cart_checkout_key(cart, checkout_data):
    quote = create_quote(cart, checkout_data)
    key = uuid.uuid4()
    create_order(cart, quote.pk, key, checkout_data)
    another = Cart.objects.create()
    with pytest.raises(ValidationError):
        create_order(another, quote.pk, key, checkout_data)
