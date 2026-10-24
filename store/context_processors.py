from django.conf import settings
from django.db.models import Sum

from store.models import PickupLocation
from store.services.cart import get_cart


def shop(request):
    cart = get_cart(request)
    return {
        "cart_count": cart.items.aggregate(total=Sum("quantity"))["total"] or 0,
        "demo_mode": settings.DEMO_MODE,
        "pickup": PickupLocation.objects.filter(active=True).first(),
    }
