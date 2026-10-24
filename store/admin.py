from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html

from store import models
from store.services.orders import create_shipment, transition

admin.site.site_header = "Плин · керування магазином"
admin.site.site_title = "Плин"
admin.site.index_title = "Демонстраційний магазин"


class CreateUserForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = models.User
        fields = ["email"]


class ChangeUserForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = models.User
        fields = "__all__"


@admin.register(models.User)
class ShopUserAdmin(UserAdmin):
    form = ChangeUserForm
    add_form = CreateUserForm
    list_display = ["email", "first_name", "email_verified", "is_staff"]
    ordering = ["email"]
    search_fields = ["email", "first_name"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Профіль", {"fields": ["first_name", "last_name", "email_verified"]}),
        (
            "Доступ",
            {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]},
        ),
        ("Дати", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [(None, {"fields": ["email", "password1", "password2", "is_staff"]})]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class AuditedAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        before = self.model.objects.filter(pk=obj.pk).values().first() if change else None
        super().save_model(request, obj, form, change)
        changed = ", ".join(form.changed_data)
        models.AuditLog.objects.create(
            actor=request.user,
            action=f"{self.model.__name__}: {'update' if change else 'create'}",
            object_id=str(obj.pk),
            detail=f"Поля: {changed}; до: {before}",
        )

    def has_delete_permission(self, request, obj=None):
        return False


class VariantInline(admin.TabularInline):
    model = models.ProductVariant
    extra = 0
    show_change_link = True
    can_delete = False


class ImageInline(admin.TabularInline):
    model = models.ProductImage
    extra = 0


@admin.register(models.Product)
class ProductAdmin(AuditedAdmin):
    list_display = ["name", "origin", "roast", "active", "featured"]
    list_filter = ["active", "origin", "roast", "featured"]
    search_fields = ["name", "notes"]
    prepopulated_fields = {"slug": ["name"]}
    filter_horizontal = ["methods"]
    inlines = [VariantInline, ImageInline]

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()
        for instance in instances:
            instance.save()
            if isinstance(instance, models.ProductVariant):
                models.StockItem.objects.get_or_create(variant=instance)
                models.AuditLog.objects.create(
                    actor=request.user,
                    action="Фасовка / ціна",
                    object_id=str(instance.pk),
                    detail=f"Ціна: {instance.price}",
                )
        formset.save_m2m()


@admin.register(models.ProductVariant)
class VariantAdmin(AuditedAdmin):
    list_display = ["sku", "product", "weight", "price", "regular_price"]
    list_filter = ["weight"]
    search_fields = ["sku", "product__name"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        models.StockItem.objects.get_or_create(variant=obj)


class StockForm(forms.ModelForm):
    correction_reason = forms.CharField(label="Причина коригування", required=True, max_length=200)

    class Meta:
        model = models.StockItem
        fields = ["on_hand"]

    def clean_on_hand(self):
        value = self.cleaned_data["on_hand"]
        if self.instance.pk and value < models.StockItem.objects.get(pk=self.instance.pk).reserved:
            raise ValidationError("Залишок не може бути меншим за активний резерв.")
        return value


@admin.register(models.StockItem)
class StockAdmin(admin.ModelAdmin):
    form = StockForm
    list_display = ["variant", "on_hand", "reserved", "available"]
    readonly_fields = ["variant", "reserved", "available"]
    search_fields = ["variant__sku", "variant__product__name"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        original = models.StockItem.objects.select_for_update().get(pk=obj.pk)
        if obj.on_hand < original.reserved:
            raise ValidationError("Залишок не може бути меншим за активний резерв.")
        delta = obj.on_hand - original.on_hand
        original.on_hand = obj.on_hand
        original.save(update_fields=["on_hand"])
        models.StockMovement.objects.create(
            stock=original, delta=delta, reason=form.cleaned_data["correction_reason"]
        )
        models.AuditLog.objects.create(
            actor=request.user,
            action="Коригування залишку",
            object_id=str(obj.pk),
            detail=f"{delta:+d}: {form.cleaned_data['correction_reason']}",
        )


class ReadOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ItemInline(ReadOnlyInline):
    model = models.OrderItem


class HistoryInline(ReadOnlyInline):
    model = models.OrderStatusHistory


class CommentInline(admin.TabularInline):
    model = models.OrderComment
    fields = ["text", "actor", "created_at"]
    readonly_fields = ["actor", "created_at"]
    extra = 0
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(models.Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "number",
        "created_at",
        "name",
        "total",
        "status",
        "payment_status",
        "delivery_method",
        "manage_link",
    ]
    list_filter = ["status", "payment_status", "payment_method", "delivery_method"]
    search_fields = ["id", "email", "name", "phone"]
    inlines = [ItemInline, HistoryInline, CommentInline]
    actions = ["prepare_orders", "ready_orders", "cancel_orders", "ship_orders"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_formset(self, request, form, formset, change):
        for instance in formset.save(commit=False):
            if isinstance(instance, models.OrderComment):
                instance.actor = request.user
                instance.save()
                models.AuditLog.objects.create(
                    actor=request.user,
                    action="Коментар до замовлення",
                    object_id=str(instance.order_id),
                    detail=instance.text,
                )
        formset.save_m2m()

    @admin.display(description="Обробка")
    def manage_link(self, obj):
        return format_html(
            '<a href="{}">Відкрити замовлення →</a>', reverse("order", args=[obj.pk])
        )

    def run_action(self, request, queryset, action):
        if not request.user.has_perm("store.change_order"):
            return
        for order in queryset:
            try:
                if action == "ship":
                    create_shipment(order.pk, request.user)
                else:
                    transition(order.pk, action, request.user, "Дія з адміністративної панелі")
            except ValidationError as exc:
                self.message_user(
                    request, f"{order.number}: {'; '.join(exc.messages)}", messages.ERROR
                )

    @admin.action(description="Почати підготовку", permissions=["change"])
    def prepare_orders(self, request, queryset):
        self.run_action(request, queryset, "packing")

    @admin.action(description="Готове до самовивозу", permissions=["change"])
    def ready_orders(self, request, queryset):
        self.run_action(request, queryset, "ready")

    @admin.action(description="Скасувати та звільнити товар", permissions=["change"])
    def cancel_orders(self, request, queryset):
        self.run_action(request, queryset, "cancel")

    @admin.action(description="Створити демонстраційну ТТН", permissions=["change"])
    def ship_orders(self, request, queryset):
        self.run_action(request, queryset, "ship")


@admin.register(models.PromoCode)
class PromoAdmin(AuditedAdmin):
    list_display = ["code", "kind", "value", "minimum", "max_uses", "active", "ends_at"]
    list_filter = ["active", "kind"]
    search_fields = ["code"]


@admin.register(models.AuditLog)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "actor", "action", "object_id"]
    list_filter = ["action"]
    search_fields = ["object_id", "detail"]


@admin.register(models.StockMovement)
class MovementAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "stock", "delta", "reason", "order"]


@admin.register(models.ShopSettings)
class SettingsAdmin(AuditedAdmin):
    def has_add_permission(self, request):
        return request.user.is_superuser and not models.ShopSettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser


for model in [models.BrewingMethod, models.GrindOption, models.PickupLocation, models.ContentPage]:
    admin.site.register(model, AuditedAdmin)

for model in [models.Shipment, models.PaymentAttempt, models.PaymentEvent, models.Refund]:
    admin.site.register(model, ReadOnlyAdmin)
