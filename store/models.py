import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.urls import reverse

from store.validators import validate_product_image


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, is_active=True, email_verified=True)
        return self.create_user(email, password, **extra)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    email_verified = models.BooleanField(default=False)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_email_lower")]

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        super().save(*args, **kwargs)


class CustomerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=20, blank=True)


class Address(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=80, default="Дім")
    city = models.CharField(max_length=100)
    street = models.CharField(max_length=200)
    recipient = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)

    def __str__(self):
        return f"{self.label}: {self.city}, {self.street}"


class BrewingMethod(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=80)

    def __str__(self):
        return self.name


class GrindOption(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return self.name


class Product(models.Model):
    ROASTS = [("light", "Світле"), ("medium", "Середнє"), ("dark", "Темне")]
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    origin = models.CharField(max_length=80)
    region = models.CharField(max_length=120, blank=True)
    roast = models.CharField(max_length=20, choices=ROASTS, default="medium")
    process = models.CharField(max_length=80)
    notes = models.CharField(max_length=160)
    description = models.TextField()
    methods = models.ManyToManyField(BrewingMethod)
    color = models.CharField(max_length=20, default="sea")
    featured = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("product", args=[self.slug])

    @property
    def starting_price(self):
        return min((v.price for v in self.variants.all()), default=0)

    @property
    def in_stock(self):
        return any(v.stock.available > 0 for v in self.variants.all())


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/%Y/%m", blank=True, validators=[validate_product_image])
    static_path = models.CharField(max_length=200, blank=True)
    alt = models.CharField(max_length=200)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    sku = models.CharField(max_length=80, unique=True)
    weight = models.PositiveIntegerField(choices=[(250, "250 г"), (500, "500 г"), (1000, "1 кг")])
    price = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    regular_price = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["weight"]
        constraints = [
            models.UniqueConstraint(fields=["product", "weight"], name="unique_product_weight"),
            models.CheckConstraint(condition=Q(price__gt=0), name="variant_positive_price"),
        ]

    def __str__(self):
        return f"{self.product.name} · {self.get_weight_display()}"


class StockItem(models.Model):
    variant = models.OneToOneField(ProductVariant, on_delete=models.PROTECT, related_name="stock")
    on_hand = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(reserved__lte=F("on_hand")), name="stock_reserved_lte_on_hand"
            )
        ]

    @property
    def available(self):
        return self.on_hand - self.reserved

    def __str__(self):
        return f"{self.variant}: {self.available} доступно"


class Cart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="cart"
    )
    promo_code = models.CharField(max_length=40, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE)
    grind = models.ForeignKey(GrindOption, on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(30)]
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "variant", "grind"], name="unique_cart_variant_grind"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=1, quantity__lte=30), name="cart_quantity_range"
            ),
        ]


class PromoCode(models.Model):
    code = models.CharField(max_length=40, unique=True)
    kind = models.CharField(max_length=10, choices=[("percent", "Відсоток"), ("fixed", "Сума")])
    value = models.PositiveIntegerField()
    minimum = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=100)
    active = models.BooleanField(default=True)
    allow_sale = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="promo_valid_dates"
            ),
            models.CheckConstraint(
                condition=Q(kind="fixed") | Q(value__lte=100), name="promo_percent_max"
            ),
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class PickupLocation(models.Model):
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=200)
    hours = models.CharField(max_length=120, default="Щодня 10:00–19:00")
    active = models.BooleanField(default=True)
    holding_days = models.PositiveSmallIntegerField(default=3)

    def __str__(self):
        return f"{self.name} · {self.address} (вигадана адреса)"


class ShopSettings(models.Model):
    free_shipping_threshold = models.PositiveIntegerField(default=150000)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class ShippingQuote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE)
    service = models.CharField(max_length=20)
    destination = models.JSONField(default=dict)
    payment_method = models.CharField(max_length=20)
    shipping = models.PositiveIntegerField()
    commission = models.PositiveIntegerField(default=0)
    subtotal = models.PositiveIntegerField()
    discount = models.PositiveIntegerField(default=0)
    fingerprint = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    estimated_delivery = models.CharField(max_length=80, default="1–3 дні (демо)")

    @property
    def total(self):
        return self.subtotal - self.discount + self.shipping + self.commission


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Очікує оплати"
        CONFIRMED = "confirmed", "Підтверджено"
        PACKING = "packing", "Готуємо замовлення"
        SHIPPED = "shipped", "Передано перевізнику"
        READY = "ready", "Готове до самовивозу"
        COMPLETED = "completed", "Завершено"
        CANCELLED = "cancelled", "Скасовано"
        REVIEW = "review", "Потребує перевірки"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Не оплачено"
        PROCESSING = "processing", "Обробляється"
        PAID = "paid", "Оплачено"
        FAILED = "failed", "Неуспішно"
        REFUNDED = "refunded", "Повернено"

    PAYMENT_METHODS = [
        ("online", "Онлайн · LiqPay"),
        ("cod", "Післяплата"),
        ("cash", "Готівка при самовивозі"),
        ("card", "Карта при самовивозі"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checkout_key = models.UUIDField(unique=True)
    cart = models.ForeignKey(Cart, null=True, on_delete=models.SET_NULL)
    customer = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    paid_attempt = models.ForeignKey(
        "PaymentAttempt", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    delivery_method = models.CharField(max_length=20)
    destination = models.JSONField(default=dict)
    subtotal = models.PositiveIntegerField()
    discount = models.PositiveIntegerField(default=0)
    shipping = models.PositiveIntegerField(default=0)
    commission = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="UAH", editable=False)
    notes = models.TextField(blank=True, max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    reserved_until = models.DateTimeField(null=True)
    pickup_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total=F("subtotal") - F("discount") + F("shipping") + F("commission")),
                name="order_consistent_total",
            )
        ]

    @property
    def number(self):
        return "PL-" + str(self.id)[:8].upper()

    def get_absolute_url(self):
        return reverse("order", args=[self.id])

    def __str__(self):
        return self.number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    weight = models.PositiveIntegerField()
    grind = models.CharField(max_length=80)
    quantity = models.PositiveSmallIntegerField()
    unit_price = models.PositiveIntegerField()
    discount = models.PositiveIntegerField(default=0)

    @property
    def total(self):
        return self.unit_price * self.quantity - self.discount


class StockReservation(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="reservations")
    stock = models.ForeignKey(StockItem, on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField()
    state = models.CharField(
        max_length=20,
        default="held",
        choices=[
            ("held", "Резерв"),
            ("consumed", "Виділено"),
            ("released", "Звільнено"),
            ("returned", "Повернуто"),
        ],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "stock"], name="unique_order_stock_reservation"
            )
        ]


class StockMovement(models.Model):
    stock = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="movements")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL)
    delta = models.IntegerField()
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)


class PromoRedemption(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="redemption")
    promo = models.ForeignKey(PromoCode, on_delete=models.PROTECT, related_name="redemptions")
    state = models.CharField(
        max_length=20,
        default="reserved",
        choices=[("reserved", "Резерв"), ("redeemed", "Використано"), ("released", "Звільнено")],
    )


class PaymentAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payment_attempts")
    key = models.UUIDField(unique=True)
    status = models.CharField(max_length=30, default="created")
    created_at = models.DateTimeField(auto_now_add=True)


class PaymentEvent(models.Model):
    external_id = models.CharField(max_length=120, unique=True)
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name="events")
    status = models.CharField(max_length=30)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class Refund(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="refunds")
    attempt = models.OneToOneField(
        PaymentAttempt, null=True, blank=True, on_delete=models.PROTECT, related_name="refund"
    )
    purpose = models.CharField(
        max_length=20,
        default="full",
        choices=[("full", "Повне повернення"), ("duplicate", "Повторний платіж")],
    )
    amount = models.PositiveIntegerField()
    status = models.CharField(max_length=20, default="pending")
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order"], condition=Q(purpose="full"), name="one_full_refund_per_order"
            )
        ]


class Shipment(models.Model):
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="shipment")
    tracking_number = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=30, default="created", choices=[("created", "ТТН створено"), ("in_transit", "У дорозі"), ("arrived", "Прибуло до локації"), ("delivered", "Отримано"), ("returned", "Повернено відправнику"), ("cancelled", "Скасовано")])
    created_at = models.DateTimeField(auto_now_add=True)


class ShipmentEvent(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="events")
    external_id = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
    status = models.CharField(max_length=30)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderComment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="comments")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)


class GuestOrderAccess(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="access_links")
    token_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class OutboxEvent(models.Model):
    key = models.CharField(max_length=180, unique=True)
    topic = models.CharField(max_length=50)
    payload = models.JSONField()
    processed_at = models.DateTimeField(null=True, blank=True)
    available_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["processed_at", "available_at"])]


class Notification(models.Model):
    key = models.CharField(max_length=180, unique=True)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    sent_at = models.DateTimeField(null=True, blank=True)


class AuditLog(models.Model):
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ContentPage(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=120)
    body = models.TextField()

    def __str__(self):
        return self.title
