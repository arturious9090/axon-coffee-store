from datetime import timedelta
from typing import Protocol

from django.core.exceptions import ValidationError
from django.utils import timezone

from store.models import PickupLocation, ShippingQuote, ShopSettings
from store.services.cart import summarize

CITIES = {
    "odesa": "Одеса",
    "kyiv": "Київ",
    "lviv": "Львів",
    "dnipro": "Дніпро",
    "kharkiv": "Харків",
    "vinnytsia": "Вінниця",
    "chernivtsi": "Чернівці",
    "poltava": "Полтава",
    "zhytomyr": "Житомир",
    "ivano-frankivsk": "Івано-Франківськ",
}
SERVICES = [
    ("warehouse", "Нова пошта · відділення"),
    ("locker", "Нова пошта · поштомат"),
    ("courier", "Нова пошта · кур'єр"),
    ("pickup", "Самовивіз в Одесі"),
]


class ShippingProvider(Protocol):
    def branches(self, city: str, service: str) -> list[dict]: ...
    def quote(self, service: str, weight: int, goods: int, payment: str) -> tuple[int, int]: ...
    def create_shipment(self, order) -> dict: ...
    def track(self, shipment) -> str: ...


class MockNovaPoshta:
    """Local, synthetic subset of Nova Poshta v2 JSON requests. No network I/O."""

    def branches(self, city, service):
        if city not in CITIES or service not in {"warehouse", "locker"}:
            return []
        label = "відділення" if service == "warehouse" else "поштомат"
        return [
            {
                "Ref": f"demo-{city}-{service}-{n}",
                "Description": f"Демо-{label} №{n} · вигадана локація",
            }
            for n in range(1, 4)
        ]

    def quote(self, service, weight, goods, payment):
        if service == "pickup":
            return 0, 0
        config, _ = ShopSettings.objects.get_or_create(pk=1)
        shipping = {"warehouse": 7500, "locker": 6500, "courier": 11000}[service]
        shipping += max(0, (weight + 999) // 1000 - 1) * 1500
        if goods >= config.free_shipping_threshold:
            shipping = 0
        commission = 2000 + (goods * 2 + 50) // 100 if payment == "cod" else 0
        return shipping, commission

    def create_shipment(self, order, scenario="normal"):
        if scenario == "timeout":
            raise TimeoutError("Simulated Nova Poshta API timeout")
        return {
            "Ref": "demo-" + str(order.pk),
            "IntDocNumber": "DEMO-NP-" + str(order.pk).replace("-", "")[:12].upper(),
        }

    def track(self, shipment):
        age = (timezone.now() - shipment.created_at).total_seconds()
        next_steps = {
            "created": (60, "in_transit"),
            "in_transit": (120, "arrived"),
            "arrived": (240, "delivered"),
        }
        threshold, next_status = next_steps.get(shipment.status, (float("inf"), shipment.status))
        return next_status if age >= threshold else shipment.status

    def request(self, payload):
        model, method = payload.get("modelName"), payload.get("calledMethod")
        props = payload.get("methodProperties", {})
        if model == "Address" and method == "getCities":
            data = [{"Ref": key, "Description": name} for key, name in CITIES.items()]
        elif model == "Address" and method == "getWarehouses":
            data = self.branches(props.get("CityRef"), props.get("ServiceType", "warehouse"))
        else:
            return {
                "success": False,
                "data": [],
                "errors": [
                    "Mock supports address directories only; shipping operations use authenticated order services."
                ],
            }
        return {"success": True, "data": data, "errors": [], "warnings": ["Synthetic demo data"]}


provider = MockNovaPoshta()


def validate_destination(data):
    service, payment = data["delivery_method"], data["payment_method"]
    if service == "pickup":
        if payment not in {"online", "cash", "card"}:
            raise ValidationError(
                "Для самовивозу оберіть онлайн-оплату, готівку або карту на місці."
            )
        location = PickupLocation.objects.filter(active=True).first()
        if not location:
            raise ValidationError("Самовивіз зараз недоступний.")
        return {
            "country": "UA",
            "city": "Одеса",
            "address": location.address,
            "pickup_id": location.pk,
            "holding_days": location.holding_days,
        }
    if payment not in {"online", "cod"}:
        raise ValidationError("Для доставки оберіть онлайн-оплату або післяплату.")
    city = data.get("city")
    if city not in CITIES:
        raise ValidationError("Оберіть місто з демонстраційного довідника.")
    destination = {"country": "UA", "city": CITIES[city], "city_ref": city}
    if service in {"warehouse", "locker"}:
        branches = {b["Ref"]: b["Description"] for b in provider.branches(city, service)}
        if data.get("branch") not in branches:
            raise ValidationError("Оберіть відділення або поштомат для цього міста.")
        destination.update(branch_ref=data["branch"], address=branches[data["branch"]])
    elif service == "courier":
        if len(data.get("street", "").strip()) < 5:
            raise ValidationError("Вкажіть адресу кур'єрської доставки.")
        destination["address"] = data["street"].strip()
    else:
        raise ValidationError("Невідомий спосіб доставки.")
    return destination


def create_quote(cart, data):
    summary = summarize(cart)
    if not summary.items:
        raise ValidationError("Кошик порожній.")
    destination = validate_destination(data)
    estimate = "Після підтвердження готовності" if data["delivery_method"] == "pickup" else "1–3 дні (демо)"
    destination["estimated_delivery"] = estimate
    weight = sum(i.variant.weight * i.quantity for i in summary.items)
    shipping, commission = provider.quote(
        data["delivery_method"], weight, summary.total, data["payment_method"]
    )
    return ShippingQuote.objects.create(
        cart=cart,
        service=data["delivery_method"],
        destination=destination,
        payment_method=data["payment_method"],
        shipping=shipping,
        commission=commission,
        subtotal=summary.subtotal,
        discount=summary.discount,
        fingerprint=summary.fingerprint,
        expires_at=timezone.now() + timedelta(minutes=10),
        estimated_delivery=estimate,
    )
