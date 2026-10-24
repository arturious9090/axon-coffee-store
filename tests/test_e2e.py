import os
import re
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from playwright.sync_api import expect, sync_playwright

from store.models import Order, User

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_E2E") != "1",
        reason="Run with RUN_E2E=1 after playwright install chromium",
    ),
]


@pytest.fixture(scope="module")
def browser():
    # Playwright's synchronous greenlet runs an event loop on the test thread.
    # ORM calls here remain synchronous; never set this in the application runtime.
    with (
        patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"}),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    yield page
    context.close()


def add_coffee(page, base, coffee):
    variant, _ = coffee
    page.goto(base + variant.product.get_absolute_url())
    page.locator("#quantity").fill("2")
    page.get_by_role("button", name="Додати до кошика").click()
    expect(page).to_have_url(base + "/cart/")


def fill_checkout(page, service="warehouse", payment="online"):
    page.get_by_role("link", name="Оформити замовлення").click()
    page.locator("#id_name").fill("Олена Демонстраційна")
    page.locator("#id_email").fill("browser@example.com")
    page.locator("#id_phone").fill("+380501234567")
    page.locator("#id_delivery_method").select_option(service)
    if service != "pickup":
        page.locator("#id_city").select_option("odesa")
        if service == "courier":
            page.locator("#id_street").fill("Вигадана вулиця, 7")
        else:
            expect(page.locator("#id_branch option")).to_have_count(4)
            page.locator("#id_branch").select_option(f"demo-odesa-{service}-1")
    page.locator("#id_payment_method").select_option(payment)
    page.locator("#id_consent").check()
    page.get_by_role("button", name="Перевірити замовлення").click()
    expect(page.get_by_role("heading", name="Все на своєму місці?")).to_be_visible()
    page.get_by_role("button", name="Підтвердити замовлення").click()
    expect(page).to_have_url(re.compile(r"/orders/[\da-f-]+/$"))


def login_manager(page, base):
    call_command("seed_demo")
    manager = User.objects.create_user(
        f"manager-{uuid.uuid4()}@example.com",
        "ManagerC0ffee!2049",
        is_staff=True,
        email_verified=True,
    )
    manager.groups.add(Group.objects.get(name="Менеджер магазину"))
    page.goto(base + "/account/login/")
    page.locator("#email").fill(manager.email)
    page.locator("#password").fill("ManagerC0ffee!2049")
    page.get_by_role("button", name="Увійти").click()
    expect(page).to_have_url(base + "/account/")


def test_guest_online_failure_retry_and_cancel(page, live_server, coffee, promo):
    add_coffee(page, live_server.url, coffee)
    page.locator("#promo").fill("PLYN10")
    page.get_by_role("button", name="Застосувати").click()
    fill_checkout(page)
    order_url = page.url
    page.get_by_role("button", name="Перейти до оплати").click()
    page.locator("#status").select_option("failure")
    page.get_by_role("button", name="Відтворити результат").click()
    expect(page.get_by_role("button", name="Повторити оплату")).to_be_visible()
    page.get_by_role("button", name="Повторити оплату").click()
    page.get_by_role("button", name="Відтворити результат").click()
    expect(page.locator(".status-row")).to_contain_text("Оплачено")
    assert page.url == order_url
    page.get_by_role("button", name="Скасувати замовлення").click()
    expect(page.locator(".status-row")).to_contain_text("Скасовано")
    order = Order.objects.get()
    assert order.discount == 7200 and order.total == 72300


@pytest.mark.parametrize("payment", ["cash", "card"])
def test_pickup_manager_handover(page, live_server, coffee, payment):
    add_coffee(page, live_server.url, coffee)
    fill_checkout(page, "pickup", payment)
    order_url = page.url
    login_manager(page, live_server.url)
    page.goto(order_url)
    page.get_by_role("button", name="Готове до самовивозу", exact=True).click()
    page.get_by_role("button", name="Підтвердити оплату при отриманні", exact=True).click()
    page.get_by_role("button", name="Видано", exact=True).click()
    expect(page.locator(".status-row")).to_contain_text("Завершено")
    expect(page.locator(".status-row")).to_contain_text("Оплачено")


def test_registered_cod_and_delivery(page, live_server, coffee, customer):
    page.goto(live_server.url + "/account/login/")
    page.locator("#email").fill(customer.email)
    page.locator("#password").fill("CloudyC0ffee!2048")
    page.get_by_role("button", name="Увійти").click()
    add_coffee(page, live_server.url, coffee)
    fill_checkout(page, "warehouse", "cod")
    order_url = page.url
    assert Order.objects.get().customer_id == customer.pk
    login_manager(page, live_server.url)
    page.goto(order_url)
    for action in ["Створити демо-ТТН", "У дорозі", "Прибуло", "Доставлено"]:
        page.get_by_role("button", name=action, exact=True).click()
    expect(page.locator(".status-row")).to_contain_text("Не оплачено")
    page.get_by_role("button", name="Підтвердити оплату при отриманні").click()
    expect(page.locator(".status-row")).to_contain_text("Оплачено")


def test_mobile_keyboard_and_no_javascript_purchase(browser, live_server, coffee):
    context = browser.new_context(viewport={"width": 390, "height": 844}, java_script_enabled=False)
    page = context.new_page()
    page.goto(live_server.url)
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Перейти до вмісту")).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    add_coffee(page, live_server.url, coffee)
    page.get_by_role("link", name="Оформити замовлення").click()
    page.locator("#id_name").fill("Демо Гість")
    page.locator("#id_email").fill("nojs@example.com")
    page.locator("#id_phone").fill("+380501234567")
    page.locator("#id_delivery_method").select_option("pickup")
    page.locator("#id_payment_method").select_option("cash")
    page.locator("#id_consent").check()
    page.get_by_role("button", name="Перевірити замовлення").click()
    page.get_by_role("button", name="Підтвердити замовлення").click()
    expect(page.locator(".status-row")).to_contain_text("Підтверджено")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    context.close()
