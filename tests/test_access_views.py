import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.utils import timezone

from store.models import Address, Cart, GuestOrderAccess, User
from store.services.checkout import create_order
from store.services.shipping import create_quote
from store.tasks import process_outbox

pytestmark = pytest.mark.django_db


@pytest.fixture
def guest_order(cart, checkout_data):
    return create_order(cart, create_quote(cart, checkout_data).pk, uuid.uuid4(), checkout_data)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/coffee/",
        "/coffee/demo-coffee/",
        "/cart/",
        "/demo/",
        "/account/login/",
        "/account/register/",
        "/account/password/reset/",
        "/robots.txt",
        "/health/",
        "/sitemap.xml",
    ],
)
def test_public_pages_render(client, coffee, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_search_filter_sort_and_pagination_preserve_url(client, coffee):
    response = client.get("/coffee/?q=Какао&origin=Бразилія&roast=medium&sort=price")
    assert response.status_code == 200 and response.context["page"].paginator.count == 1
    assert "origin=" in response.context["querystring"]
    response = client.get("/coffee/?q=Немає")
    assert response.context["page"].paginator.count == 0


def test_htmx_returns_results_fragment_only(client, coffee):
    response = client.get("/coffee/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="catalog-results")
    assert b"catalog-results" in response.content and b"<!doctype" not in response.content
    assert "HX-Request" in response.headers["Vary"]


def test_guest_order_requires_token_and_expiry(client, guest_order):
    order, token = guest_order
    assert client.get(order.get_absolute_url()).status_code == 404
    assert client.get(order.get_absolute_url() + "?access=wrong").status_code == 404
    response = client.get(order.get_absolute_url() + "?access=" + token)
    assert response.status_code == 302
    assert client.get(order.get_absolute_url()).status_code == 200
    GuestOrderAccess.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.get(order.get_absolute_url()).status_code == 404


def test_account_cannot_view_other_order(client, guest_order, customer):
    order, _ = guest_order
    client.force_login(customer)
    assert client.get(order.get_absolute_url()).status_code == 404
    assert order.number.encode() not in client.get("/account/").content
    order.customer = customer
    order.save()
    assert client.get(order.get_absolute_url()).status_code == 200
    assert order.number.encode() in client.get("/account/").content


def test_claim_requires_guest_link_and_verified_matching_email(client, guest_order, customer):
    order, token = guest_order
    customer.email = order.email
    customer.save()
    client.force_login(customer)
    assert client.post(f"/orders/{order.pk}/claim/").status_code == 404
    client.get(order.get_absolute_url() + "?access=" + token)
    customer.email_verified = False
    customer.save()
    client.post(f"/orders/{order.pk}/claim/")
    order.refresh_from_db()
    assert order.customer_id is None
    customer.email_verified = True
    customer.save()
    client.post(f"/orders/{order.pk}/claim/")
    order.refresh_from_db()
    assert order.customer_id == customer.pk


def test_registration_verification_login_and_cart_merge(client, coffee, mailoutbox):
    variant, grind = coffee
    client.post(
        variant.product.get_absolute_url(),
        {"variant": variant.pk, "grind": grind.pk, "quantity": 2},
    )
    data = {
        "name": "Олена",
        "email": "new@example.com",
        "password1": "CloudyC0ffee!2048",
        "password2": "CloudyC0ffee!2048",
    }
    assert client.post("/account/register/", data).status_code == 200
    user = User.objects.get(email=data["email"])
    assert not user.email_verified
    client.post("/account/login/", {"email": data["email"], "password": data["password1"]})
    assert not client.session.get("_auth_user_id")
    process_outbox()
    assert len(mailoutbox) == 1
    url = re.search(r"http://[^\s]+", mailoutbox[0].body)[0]
    path = url.split("8000", 1)[1]
    assert client.get(path).status_code == 200
    assert client.post(path).status_code == 302
    response = client.post(
        "/account/login/", {"email": data["email"], "password": data["password1"]}
    )
    assert response.status_code == 302
    assert Cart.objects.get(user=user).items.get().quantity == 2


def test_password_reset_does_not_disclose_account_and_token_is_single_use(
    client, customer, mailoutbox
):
    first = client.post("/account/password/reset/", {"email": "absent@example.com"})
    second = client.post("/account/password/reset/", {"email": customer.email})
    assert first.status_code == second.status_code == 302
    assert first.url == second.url
    assert len(mailoutbox) == 1
    url = re.search(r"http://[^\s]+", mailoutbox[0].body)[0]
    path = "/" + url.split("/", 3)[3]
    response = client.get(path)
    assert response.status_code == 302
    response = client.post(
        response.url, {"new_password1": "UpdatedC0ffee!2050", "new_password2": "UpdatedC0ffee!2050"}
    )
    assert response.status_code == 302
    customer.refresh_from_db()
    assert customer.check_password("UpdatedC0ffee!2050")
    assert not client.get(path, follow=True).context["validlink"]


def test_login_rate_limit(client, customer):
    for _ in range(10):
        assert (
            client.post(
                "/account/login/", {"email": customer.email, "password": "wrong"}
            ).status_code
            == 200
        )
    assert (
        client.post("/account/login/", {"email": customer.email, "password": "wrong"}).status_code
        == 403
    )


def test_address_isolation(client, customer):
    other = User.objects.create_user("other@example.com", "OtherC0ffee!2049", email_verified=True)
    address = Address.objects.create(
        user=other, city="Одеса", street="Вигадана, 7", recipient="Демо", phone="+380501234567"
    )
    client.force_login(customer)
    assert client.get(f"/account/addresses/{address.pk}/").status_code == 404
    assert client.post(f"/account/addresses/{address.pk}/", {"action": "delete"}).status_code == 404
    assert Address.objects.filter(pk=address.pk).exists()


def test_readonly_admin_cannot_change_data_or_access_orders(client):
    call_command("seed_demo")
    response = client.post("/demo/admin/")
    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/store/product/").status_code == 200
    assert client.post("/admin/store/product/add/", {"name": "Forged"}).status_code == 403
    assert client.get("/admin/store/order/").status_code == 403
    assert client.get("/admin/store/user/").status_code == 403


def test_manager_has_order_actions_without_user_management(client, guest_order):
    call_command("seed_demo")
    manager = User.objects.create_user(
        "manager@example.com", "ManagerC0ffee!2049", is_staff=True, email_verified=True
    )
    manager.groups.add(Group.objects.get(name="Менеджер магазину"))
    client.force_login(manager)
    assert client.get("/admin/store/order/").status_code == 200
    assert client.get("/admin/store/user/").status_code == 403
    order, _ = guest_order
    assert client.get(order.get_absolute_url()).status_code == 200
    assert client.post(f"/orders/{order.pk}/manage/", {"action": "cancel"}).status_code == 302
    order.refresh_from_db()
    assert order.status == "cancelled"


def test_csrf_protects_purchase_and_staff_actions(coffee):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    variant, grind = coffee
    assert (
        client.post(
            variant.product.get_absolute_url(),
            {"variant": variant.pk, "grind": grind.pk, "quantity": 1},
        ).status_code
        == 403
    )
    assert client.post("/demo/admin/").status_code == 403


def test_seed_is_repeatable_without_resetting_stock(db):
    from store.models import Product, StockItem

    call_command("seed_demo")
    stock = StockItem.objects.first()
    stock.on_hand = 7
    stock.save()
    call_command("seed_demo")
    stock.refresh_from_db()
    assert stock.on_hand == 7 and Product.objects.count() == 12
