import hashlib
import json
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from store.models import Cart, CartItem, PromoCode, StockItem


def get_cart(request):
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user)
        return cart
    cart_id = request.session.get("cart_id")
    cart = Cart.objects.filter(pk=cart_id, user=None).first() if cart_id else None
    if not cart:
        cart = Cart.objects.create()
        request.session["cart_id"] = str(cart.pk)
    return cart


@transaction.atomic
def add_item(cart, variant, grind, quantity):
    cart = Cart.objects.select_for_update().get(pk=cart.pk)
    quantity = int(quantity)
    if not 1 <= quantity <= 30 or not variant.product.active:
        raise ValidationError("Оберіть доступний товар і кількість від 1 до 30.")
    existing = cart.items.filter(variant=variant).aggregate(total=Sum("quantity"))["total"] or 0
    stock = StockItem.objects.select_for_update().get(variant=variant)
    if existing + quantity > stock.available:
        raise ValidationError("На складі недостатньо цієї фасовки.")
    item, created = CartItem.objects.get_or_create(
        cart=cart, variant=variant, grind=grind, defaults={"quantity": quantity}
    )
    if not created:
        if item.quantity + quantity > 30:
            raise ValidationError("Максимум 30 упаковок в одному рядку.")
        item.quantity += quantity
        item.save(update_fields=["quantity"])
    cart.save(update_fields=["updated_at"])


@transaction.atomic
def merge_carts(guest_id, user):
    target, _ = Cart.objects.get_or_create(user=user)
    if not guest_id or str(target.pk) == str(guest_id):
        return target
    carts = list(
        Cart.objects.select_for_update().filter(pk__in=[target.pk, guest_id]).order_by("pk")
    )
    guest = next((c for c in carts if str(c.pk) == str(guest_id) and c.user_id is None), None)
    if not guest:
        return target
    for item in guest.items.select_related("variant__stock", "variant__product", "grind"):
        current = (
            target.items.filter(variant=item.variant).aggregate(total=Sum("quantity"))["total"] or 0
        )
        quantity = min(item.quantity, max(0, item.variant.stock.available - current))
        if quantity and item.variant.product.active:
            line, created = target.items.get_or_create(
                variant=item.variant, grind=item.grind, defaults={"quantity": quantity}
            )
            if not created:
                line.quantity = min(30, line.quantity + quantity)
                line.save(update_fields=["quantity"])
    if not target.promo_code:
        target.promo_code = guest.promo_code
        target.save(update_fields=["promo_code", "updated_at"])
    guest.items.all().delete()
    return target


@dataclass
class Summary:
    items: list
    subtotal: int
    discount: int
    promo: PromoCode | None
    fingerprint: str

    @property
    def total(self):
        return self.subtotal - self.discount


def summarize(cart, *, strict=True, lock_promo=False):
    items = list(
        cart.items.select_related("variant__product", "variant__stock", "grind").order_by("pk")
    )
    subtotal = sum(item.variant.price * item.quantity for item in items)
    discount = 0
    promo = None
    if cart.promo_code:
        query = PromoCode.objects.select_for_update() if lock_promo else PromoCode.objects
        promo = query.filter(code=cart.promo_code.upper()).first()
        now = timezone.now()
        valid = (
            promo
            and promo.active
            and promo.starts_at <= now < promo.ends_at
            and subtotal >= promo.minimum
        )
        if valid:
            valid = promo.redemptions.exclude(state="released").count() < promo.max_uses
        if not valid:
            if strict:
                raise ValidationError(
                    "Промокод недоступний, вичерпаний або не виконані його умови."
                )
            promo = None
        else:
            eligible = sum(
                i.variant.price * i.quantity
                for i in items
                if promo.allow_sale or not i.variant.regular_price
            )
            discount = min(
                eligible,
                (eligible * promo.value + 50) // 100 if promo.kind == "percent" else promo.value,
            )
    for item in items:
        item.line_total = item.variant.price * item.quantity
    snapshot = [
        [
            i.pk,
            i.variant_id,
            i.quantity,
            i.grind_id,
            i.variant.price,
            i.variant.regular_price,
            i.variant.product.active,
        ]
        for i in items
    ]
    fingerprint = hashlib.sha256(
        json.dumps([snapshot, cart.promo_code, subtotal, discount], sort_keys=True).encode()
    ).hexdigest()
    return Summary(items, subtotal, discount, promo, fingerprint)


def allocate_discount(items, discount, promo):
    eligible = [i for i in items if promo and (promo.allow_sale or not i.variant.regular_price)]
    base = sum(i.line_total for i in eligible)
    shares = {i.pk: (discount * i.line_total // base if base else 0) for i in eligible}
    remainder = discount - sum(shares.values())
    for item in eligible[:remainder]:
        shares[item.pk] += 1
    return shares
