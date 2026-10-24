import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, connection, transaction
from django.db.models import Min, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from PIL import Image, UnidentifiedImageError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from store.forms import AddressForm, CheckoutForm, ProfileForm, RegisterForm
from store.models import (
    Address,
    BrewingMethod,
    Cart,
    ContentPage,
    CustomerProfile,
    GrindOption,
    Order,
    PaymentAttempt,
    Product,
    ProductVariant,
    User,
)
from store.services.accounts import queue_verification, rate_limit
from store.services.cart import add_item, get_cart, merge_carts, summarize
from store.services.checkout import create_order, has_guest_access
from store.services.notifications import queue_event
from store.services.orders import create_shipment, transition, update_shipment
from store.services.payments import begin_attempt, queue_simulated_callback
from store.services.payments import provider as payment_provider
from store.services.shipping import CITIES, create_quote
from store.services.shipping import provider as shipping_provider
from store.tasks import process_one


def products():
    return Product.objects.filter(active=True).prefetch_related(
        "variants__stock", "images", "methods"
    )


def home(request):
    return render(request, "shop/home.html", {"products": products().filter(featured=True)[:4]})


def catalog(request):
    query = products().annotate(min_price=Min("variants__price"))
    term = request.GET.get("q", "").strip()[:100]
    if term:
        query = query.filter(
            Q(name__icontains=term) | Q(notes__icontains=term) | Q(origin__icontains=term)
        )
    for key in ["origin", "roast"]:
        if request.GET.get(key):
            query = query.filter(**{key: request.GET[key]})
    if request.GET.get("method"):
        query = query.filter(methods__slug=request.GET["method"])
    query = query.order_by(
        {"price": "min_price", "-price": "-min_price", "name": "name"}.get(
            request.GET.get("sort"), "id"
        )
    ).distinct()
    page = Paginator(query, 9).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "page": page,
        "origins": Product.objects.filter(active=True)
        .values_list("origin", flat=True)
        .distinct()
        .order_by("origin"),
        "roasts": Product.ROASTS,
        "methods": BrewingMethod.objects.all(),
        "querystring": params.urlencode(),
        "term": term,
    }
    template = (
        "shop/catalog_results.html"
        if request.headers.get("HX-Request")
        and request.headers.get("HX-Target") == "catalog-results"
        else "shop/catalog.html"
    )
    response = render(request, template, context)
    response["Vary"] = "HX-Request,HX-Target,Cookie"
    return response


def product(request, slug):
    item = get_object_or_404(products(), slug=slug)
    if request.method == "POST":
        try:
            variant = get_object_or_404(
                ProductVariant.objects.select_related("stock", "product"),
                pk=request.POST.get("variant"),
                product=item,
            )
            grind = get_object_or_404(GrindOption, pk=request.POST.get("grind"))
            add_item(get_cart(request), variant, grind, int(request.POST.get("quantity", "1")))
            messages.success(request, "Каву додано до кошика.")
            return redirect("cart")
        except (ValidationError, ValueError) as exc:
            messages.error(
                request,
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Неправильна кількість.",
            )
    return render(
        request,
        "shop/product.html",
        {
            "product": item,
            "grinds": GrindOption.objects.all(),
            "related": products().exclude(pk=item.pk)[:3],
        },
    )


def cart_view(request):
    cart = get_cart(request)
    if request.method == "POST":
        try:
            with transaction.atomic():
                cart = Cart.objects.select_for_update().get(pk=cart.pk)
                if "promo" in request.POST:
                    cart.promo_code = request.POST["promo"].strip().upper()[:40]
                    summarize(cart)
                    cart.save(update_fields=["promo_code", "updated_at"])
                else:
                    line = get_object_or_404(
                        cart.items.select_related("variant__stock"), pk=request.POST.get("item")
                    )
                    quantity = int(request.POST.get("quantity", "0"))
                    others = (
                        cart.items.filter(variant=line.variant)
                        .exclude(pk=line.pk)
                        .aggregate(total=Sum("quantity"))["total"]
                        or 0
                    )
                    if (
                        quantity < 0
                        or quantity > 30
                        or quantity + others > line.variant.stock.available
                    ):
                        raise ValidationError("Ця кількість недоступна.")
                    if quantity == 0:
                        line.delete()
                    else:
                        line.quantity = quantity
                        line.save(update_fields=["quantity"])
        except (ValidationError, ValueError) as exc:
            messages.error(
                request,
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Неправильна кількість.",
            )
        return redirect("cart")
    return render(
        request, "shop/cart.html", {"summary": summarize(cart, strict=False), "cart": cart}
    )


def checkout(request):
    cart = get_cart(request)
    if request.method == "POST" and request.POST.get("action") == "confirm":
        draft = request.session.get("checkout_draft", {})
        if request.POST.get("key") != draft.get("key"):
            messages.error(request, "Сесія оформлення змінилася. Перевірте кошик.")
            return redirect("checkout")
        try:
            order, token = create_order(
                cart,
                draft["quote"],
                uuid.UUID(draft["key"]),
                draft["data"],
                request.user if request.user.is_authenticated else None,
            )
            if token:
                access = request.session.get("order_access", {})
                access[str(order.pk)] = token
                request.session["order_access"] = access
            return redirect("order", order_id=order.pk)
        except (ValidationError, ValueError, KeyError) as exc:
            messages.error(
                request,
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Перевірте дані замовлення ще раз.",
            )
            return redirect("checkout")
    summary = summarize(cart, strict=False)
    if not summary.items:
        return redirect("cart")
    initial = {"delivery_method": "warehouse", "payment_method": "online"}
    if request.user.is_authenticated:
        initial.update(
            name=request.user.first_name,
            email=request.user.email,
            phone=getattr(getattr(request.user, "profile", None), "phone", ""),
        )
    address = (
        request.user.addresses.filter(pk=request.GET.get("address")).first()
        if request.user.is_authenticated and request.GET.get("address", "").isdigit()
        else None
    )
    if address:
        city_ref = next(
            (key for key, name in CITIES.items() if name.lower() == address.city.lower()), ""
        )
        initial.update(
            delivery_method="courier",
            name=address.recipient,
            phone=address.phone,
            city=city_ref,
            street=address.street,
        )
    form = CheckoutForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            quote = create_quote(cart, form.cleaned_data)
            key = str(uuid.uuid4())
            request.session["checkout_draft"] = {
                "quote": str(quote.pk),
                "key": key,
                "data": form.cleaned_data,
            }
            return render(
                request,
                "shop/review.html",
                {"quote": quote, "summary": summary, "data": form.cleaned_data, "key": key},
            )
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "shop/checkout.html", {"form": form, "summary": summary})


def accessible_order(request, order_id):
    order = get_object_or_404(
        Order.objects.prefetch_related("items", "history", "payment_attempts"), pk=order_id
    )
    if request.user.is_authenticated and (
        order.customer_id == request.user.pk or request.user.has_perm("store.view_order")
    ):
        return order
    token = request.GET.get("access") or request.session.get("order_access", {}).get(str(order.pk))
    if has_guest_access(order, token):
        access = request.session.get("order_access", {})
        access[str(order.pk)] = token
        request.session["order_access"] = access
        return order
    raise Http404


def order_view(request, order_id):
    order = accessible_order(request, order_id)
    if request.GET.get("access"):
        return redirect("order", order_id=order.pk)
    return render(request, "shop/order.html", {"order": order, "payment_key": str(uuid.uuid4())})


@require_POST
def order_cancel(request, order_id):
    order = accessible_order(request, order_id)
    try:
        transition(
            order.pk,
            "cancel",
            request.user if request.user.is_authenticated else None,
            "Скасовано покупцем",
        )
        messages.success(request, "Замовлення скасовано.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("order", order_id=order.pk)


@require_POST
def start_payment(request, order_id):
    order = accessible_order(request, order_id)
    try:
        attempt = begin_attempt(order.pk, uuid.UUID(request.POST.get("key", "")))
        return redirect("mock_payment", attempt_id=attempt.pk)
    except (ValidationError, ValueError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else "Оновіть сторінку та повторіть оплату.",
        )
        return redirect("order", order_id=order.pk)


def mock_payment(request, attempt_id):
    attempt = get_object_or_404(PaymentAttempt.objects.select_related("order"), pk=attempt_id)
    order = accessible_order(request, attempt.order_id)
    if request.method == "POST":
        try:
            status = request.POST.get("status", "success")
            if status == "cancel":
                status = "failure"
            delay = 30 if request.POST.get("delay") else 0
            event = queue_simulated_callback(attempt, status, delay)
            if not delay:
                process_one(event.pk)
            messages.info(
                request,
                "Демонстраційний результат оплати надіслано."
                if not delay
                else "Результат надійде за 30 секунд через фоновий обробник.",
            )
            return redirect("order", order_id=order.pk)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    return render(request, "shop/payment.html", {"attempt": attempt, "order": order})


@csrf_exempt
@require_POST
def payment_callback(request):
    try:
        payload = payment_provider.decode(
            request.POST.get("data", ""), request.POST.get("signature", "")
        )
        event_id = str(payload.get("event_id", ""))
        if not event_id or len(event_id) > 120:
            raise ValidationError("Неправильний ідентифікатор події.")
        from store.services.payments import apply_event

        with transaction.atomic():
            event = queue_event("callback:" + event_id, "payment", payload)
            if event.payload != payload:
                raise ValidationError("Конфлікт події.")
            apply_event(payload)
        process_one(event.pk)
    except (ValidationError, ValueError) as exc:
        return JsonResponse(
            {
                "error": "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Invalid payload"
            },
            status=400,
        )
    return JsonResponse({"status": "ok", "demo": True})


@require_GET
def branches(request):
    return JsonResponse(
        {
            "data": shipping_provider.branches(
                request.GET.get("city"), request.GET.get("service", "warehouse")
            ),
            "demo": True,
        }
    )


def register(request):
    form = RegisterForm(request.POST or None)
    if request.method == "POST":
        rate_limit(request, "register", request.POST.get("email", ""), limit=5)
        if form.is_valid():
            email = form.cleaned_data["email"].lower()
            try:
                with transaction.atomic():
                    if not User.objects.filter(email__iexact=email).exists():
                        user = User.objects.create_user(
                            email,
                            form.cleaned_data["password1"],
                            first_name=form.cleaned_data["name"],
                        )
                        queue_verification(user)
            except IntegrityError:
                # Concurrent registration returns the same generic notice.
                pass
            return render(
                request,
                "accounts/notice.html",
                {
                    "title": "Перевірте пошту",
                    "text": "Якщо адреса ще не зареєстрована, ми надіслали посилання для підтвердження. У локальному демо листи доступні в поштовому переглядачі.",
                },
            )
    return render(
        request,
        "accounts/form.html",
        {
            "form": form,
            "title": "Раді знайомству",
            "eyebrow": "СТВОРИТИ АКАУНТ",
            "button": "Зареєструватися",
        },
    )


def verify_email(request, token):
    try:
        data = signing.loads(token, salt="verify-email", max_age=86400)
        user = get_object_or_404(User, pk=data["id"], email=data["email"])
        if request.method == "POST":
            user.email_verified = True
            user.save(update_fields=["email_verified"])
            messages.success(request, "Email підтверджено. Тепер можна увійти.")
            return redirect("login")
    except (signing.BadSignature, KeyError):
        raise Http404 from None
    return render(request, "accounts/verify.html", {"email": user.email})


def login_view(request):
    error = ""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        rate_limit(request, "login", email)
        user = authenticate(request, email=email, password=request.POST.get("password", ""))
        if user and user.email_verified:
            guest_id = request.session.get("cart_id")
            login(request, user)
            merge_carts(guest_id, user)
            return redirect("account")
        error = "Невірні дані або email ще не підтверджено."
    return render(request, "accounts/login.html", {"error": error})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("home")


class LimitedPasswordResetView(PasswordResetView):
    template_name = "accounts/form.html"
    email_template_name = "accounts/reset_email.txt"
    subject_template_name = "accounts/reset_subject.txt"
    extra_context = {"title": "Відновити пароль", "button": "Надіслати посилання"}

    def post(self, request, *args, **kwargs):
        rate_limit(request, "password-reset", request.POST.get("email", ""), limit=5)
        return super().post(request, *args, **kwargs)


@login_required
def account(request):
    profile, _ = CustomerProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(
        request.POST or None, initial={"name": request.user.first_name, "phone": profile.phone}
    )
    if request.method == "POST" and form.is_valid():
        request.user.first_name = form.cleaned_data["name"]
        request.user.save(update_fields=["first_name"])
        profile.phone = form.cleaned_data["phone"]
        profile.save(update_fields=["phone"])
        messages.success(request, "Профіль збережено.")
        return redirect("account")
    return render(
        request, "accounts/account.html", {"orders": request.user.orders.all(), "form": form}
    )


@login_required
def address_view(request, address_id=None):
    instance = get_object_or_404(Address, pk=address_id, user=request.user) if address_id else None
    form = AddressForm(request.POST or None, instance=instance)
    if request.method == "POST":
        if request.POST.get("action") == "delete" and instance:
            instance.delete()
            return redirect("account")
        if form.is_valid():
            address = form.save(commit=False)
            address.user = request.user
            address.save()
            return redirect("account")
    return render(
        request,
        "accounts/form.html",
        {
            "form": form,
            "title": "Адреса доставки",
            "button": "Зберегти",
            "can_delete": bool(instance),
        },
    )


@login_required
@require_POST
def claim_order(request, order_id):
    order = accessible_order(request, order_id)
    if (
        request.user.email_verified
        and request.user.email.lower() == order.email.lower()
        and order.customer_id is None
    ):
        order.customer = request.user
        order.save(update_fields=["customer"])
        messages.success(request, "Замовлення додано до вашого кабінету.")
    else:
        messages.error(request, "Потрібен підтверджений email одержувача та гостьове посилання.")
    return redirect("order", order_id=order.pk)


def content(request, slug):
    return render(request, "shop/content.html", {"page": get_object_or_404(ContentPage, slug=slug)})


def demo(request):
    return render(request, "shop/demo.html")


@require_POST
def demo_admin(request):
    if not settings.DEMO_MODE:
        raise Http404
    rate_limit(request, "demo-admin", limit=20)
    viewer = get_object_or_404(User, email="viewer@plyn.example", is_staff=True, is_superuser=False)
    permissions = viewer.get_all_permissions()
    if not permissions or any(
        not permission.startswith("store.view_") for permission in permissions
    ):
        raise Http404
    login(request, viewer, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("admin:index")


@require_POST
def staff_action(request, order_id):
    if not request.user.is_authenticated or not request.user.has_perm("store.change_order"):
        raise Http404
    try:
        action = request.POST.get("action", "")
        if action in {"ship", "ship_timeout"}:
            create_shipment(order_id, request.user, "timeout" if action == "ship_timeout" else "normal")
        elif action.startswith("delivery:"):
            status = action.split(":", 1)[1]
            update_shipment(order_id, status, f"demo-{order_id}-{status}", request.user)
        else:
            transition(order_id, action, request.user, request.POST.get("note", "")[:200])
        messages.success(request, "Стан замовлення оновлено.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    except TimeoutError:
        messages.error(request, "Демонстраційний таймаут Нової пошти. ТТН не створено; повторіть звичайне створення.")
    return redirect("order", order_id=order_id)


@require_GET
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok", "mode": "demo"})


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


@require_GET
def media_image(request, path):
    root = Path(settings.MEDIA_ROOT).resolve()
    file = (root / path).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise Http404
    try:
        with Image.open(file) as image:
            content_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image.format)
        if not content_type:
            raise Http404
    except (UnidentifiedImageError, OSError):
        raise Http404 from None
    response = FileResponse(file.open("rb"), content_type=content_type)
    response["Cache-Control"] = "public, max-age=86400"
    return response
