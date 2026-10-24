import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from store.models import (
    Cart,
    GuestOrderAccess,
    Order,
    OrderItem,
    OrderStatusHistory,
    PromoRedemption,
    ShippingQuote,
)
from store.services.cart import allocate_discount, summarize
from store.services.inventory import move_reservations, reserve
from store.services.notifications import queue_mail
from store.services.shipping import provider


def issue_guest_access(order):
    token = secrets.token_urlsafe(32)
    GuestOrderAccess.objects.create(
        order=order,
        token_digest=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(seconds=settings.GUEST_LINK_SECONDS),
    )
    return token


def has_guest_access(order, token):
    if not token or len(token) > 100:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    return order.access_links.filter(token_digest=digest, expires_at__gt=timezone.now()).exists()


@transaction.atomic
def create_order(cart, quote_id, checkout_key, data, customer=None):
    cart = Cart.objects.select_for_update().get(pk=cart.pk)
    existing = Order.objects.filter(checkout_key=checkout_key).first()
    if existing:
        if existing.cart_id != cart.pk:
            raise ValidationError("Ключ оформлення належить іншому кошику.")
        return existing, None
    quote = ShippingQuote.objects.filter(pk=quote_id, cart=cart).first()
    if not quote or quote.expires_at <= timezone.now():
        raise ValidationError("Розрахунок застарів. Перевірте замовлення ще раз.")
    summary = summarize(cart, lock_promo=True)
    weight = sum(i.variant.weight * i.quantity for i in summary.items)
    shipping, commission = provider.quote(
        quote.service, weight, summary.total, quote.payment_method
    )
    if (
        not summary.items
        or summary.fingerprint != quote.fingerprint
        or (shipping, commission) != (quote.shipping, quote.commission)
    ):
        raise ValidationError("Ціни або кошик змінилися. Перевірте оновлений підсумок.")
    if any(not item.variant.product.active for item in summary.items):
        raise ValidationError("Один із товарів більше не продається.")
    order = Order.objects.create(
        checkout_key=checkout_key,
        cart=cart,
        customer=customer,
        name=data["name"],
        email=data["email"].lower(),
        phone=data["phone"],
        notes=data.get("notes", ""),
        payment_method=quote.payment_method,
        delivery_method=quote.service,
        destination=quote.destination,
        subtotal=summary.subtotal,
        discount=summary.discount,
        shipping=shipping,
        commission=commission,
        total=quote.total,
        reserved_until=timezone.now() + timedelta(minutes=settings.RESERVATION_MINUTES),
    )
    shares = allocate_discount(summary.items, summary.discount, summary.promo)
    for item in summary.items:
        OrderItem.objects.create(
            order=order,
            variant=item.variant,
            name=item.variant.product.name,
            weight=item.variant.weight,
            grind=item.grind.name,
            quantity=item.quantity,
            unit_price=item.variant.price,
            discount=shares.get(item.pk, 0),
        )
    reserve(order, summary.items)
    if summary.promo:
        PromoRedemption.objects.create(order=order, promo=summary.promo)
    if order.payment_method != "online":
        move_reservations(order, "consumed")
        order.status = Order.Status.CONFIRMED
        order.reserved_until = None
        order.save(update_fields=["status", "reserved_until"])
        if summary.promo:
            order.redemption.state = "redeemed"
            order.redemption.save(update_fields=["state"])
    token = issue_guest_access(order) if customer is None else None
    url = settings.SITE_URL + order.get_absolute_url() + ("?access=" + token if token else "")
    OrderStatusHistory.objects.create(order=order, status=order.status, note="Замовлення створено")
    queue_mail(
        f"order-created:{order.pk}",
        order.email,
        f"Плин · замовлення {order.number}",
        f"Дякуємо! Це демонстраційне замовлення.\nПереглянути: {url}\nГостьове посилання діє 7 днів.",
    )
    cart.items.all().delete()
    cart.promo_code = ""
    cart.save(update_fields=["promo_code", "updated_at"])
    return order, token
