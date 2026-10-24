from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from store.models import (
    Cart,
    CartItem,
    GrindOption,
    PickupLocation,
    Product,
    ProductImage,
    ProductVariant,
    PromoCode,
    ShopSettings,
    StockItem,
    User,
)


@pytest.fixture(autouse=True)
def test_settings(settings, tmp_path):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.MEDIA_ROOT = tmp_path / "media"
    cache.clear()


@pytest.fixture
def coffee(db):
    product = Product.objects.create(
        name="Демо кава",
        slug="demo-coffee",
        origin="Бразилія",
        process="Мита",
        notes="Какао · горіх",
        description="Синтетична кава",
    )
    ProductImage.objects.create(
        product=product, static_path="images/product-plyn-800.webp", alt="Упаковка"
    )
    variant = ProductVariant.objects.create(
        product=product, sku="DEMO-250", weight=250, price=36000
    )
    StockItem.objects.create(variant=variant, on_hand=20)
    grind = GrindOption.objects.create(slug="beans", name="У зернах")
    PickupLocation.objects.create(name="Плин", address="Одеса, вигадана адреса, 7")
    ShopSettings.objects.create(pk=1)
    return variant, grind


@pytest.fixture
def cart(coffee):
    variant, grind = coffee
    cart = Cart.objects.create()
    CartItem.objects.create(cart=cart, variant=variant, grind=grind, quantity=2)
    return cart


@pytest.fixture
def promo(db):
    return PromoCode.objects.create(
        code="PLYN10",
        kind="percent",
        value=10,
        minimum=50000,
        starts_at=timezone.now() - timedelta(days=1),
        ends_at=timezone.now() + timedelta(days=1),
        max_uses=100,
    )


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        "buyer@example.com", "CloudyC0ffee!2048", email_verified=True, first_name="Олена"
    )


@pytest.fixture
def checkout_data():
    return {
        "name": "Олена Демонстраційна",
        "email": "guest@example.com",
        "phone": "+380501234567",
        "delivery_method": "warehouse",
        "city": "odesa",
        "branch": "demo-odesa-warehouse-1",
        "street": "",
        "payment_method": "online",
        "notes": "",
        "consent": True,
    }
