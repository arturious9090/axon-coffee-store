from collections import defaultdict

from django.core.exceptions import ValidationError

from store.models import StockItem, StockMovement, StockReservation


def reserve(order, items):
    """Caller owns the order transaction. Lock stock in a stable global order."""
    quantities = defaultdict(int)
    for item in items:
        quantities[item.variant_id] += item.quantity
    stocks = list(
        StockItem.objects.select_for_update().filter(variant_id__in=quantities).order_by("pk")
    )
    if len(stocks) != len(quantities):
        raise ValidationError("Залишки товару недоступні.")
    for stock in stocks:
        quantity = quantities[stock.variant_id]
        if stock.available < quantity:
            raise ValidationError("Недостатньо товару на складі. Оновіть кошик.")
        stock.reserved += quantity
        stock.save(update_fields=["reserved"])
        StockReservation.objects.update_or_create(
            order=order, stock=stock, defaults={"quantity": quantity, "state": "held"}
        )


def move_reservations(order, target):
    reservations = list(order.reservations.select_related("stock").order_by("stock_id"))
    stocks = {
        s.pk: s
        for s in StockItem.objects.select_for_update()
        .filter(pk__in=[r.stock_id for r in reservations])
        .order_by("pk")
    }
    for reservation in reservations:
        stock = stocks[reservation.stock_id]
        delta = 0
        if reservation.state == "held" and target == "consumed":
            stock.reserved -= reservation.quantity
            stock.on_hand -= reservation.quantity
            delta = -reservation.quantity
        elif reservation.state == "held" and target == "released":
            stock.reserved -= reservation.quantity
        elif reservation.state == "consumed" and target == "returned":
            stock.on_hand += reservation.quantity
            delta = reservation.quantity
        else:
            continue
        stock.save(update_fields=["reserved", "on_hand"])
        reservation.state = target
        reservation.save(update_fields=["state"])
        StockMovement.objects.create(stock=stock, order=order, delta=delta, reason=target)
