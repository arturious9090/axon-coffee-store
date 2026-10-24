"""Capture synthetic portfolio material against an explicitly local demo server."""

import argparse
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8018")
    parser.add_argument("--output", default="docs/portfolio")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if urlparse(base).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Capture is limited to a local demo server")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base, wait_until="networkidle")
        page.screenshot(path=str(output / "home-desktop.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "home-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        context.close()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            record_video_dir=str(output / "raw"),
            record_video_size={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        video = page.video
        page.goto(base, wait_until="networkidle")
        page.wait_for_timeout(1000)
        page.get_by_role("link", name="Обрати свою каву").click()
        page.wait_for_timeout(700)
        page.get_by_role("heading", name="Ранок біля моря").get_by_role("link").click()
        page.screenshot(path=str(output / "product.png"), full_page=True)
        page.locator("#quantity").fill("2")
        page.wait_for_timeout(700)
        page.get_by_role("button", name="Додати до кошика").click()
        page.locator("#promo").fill("PLYN10")
        page.get_by_role("button", name="Застосувати").click()
        page.wait_for_timeout(700)
        page.get_by_role("link", name="Оформити замовлення").click()
        page.locator("#id_name").fill("Олена Демо")
        page.locator("#id_email").fill("portfolio@example.com")
        page.locator("#id_phone").fill("+380500000001")
        page.locator("#id_city").select_option("odesa")
        expect(page.locator("#id_branch option")).to_have_count(4)
        page.locator("#id_branch").select_option("demo-odesa-warehouse-1")
        page.locator("#id_consent").check()
        page.get_by_role("button", name="Перевірити замовлення").click()
        page.screenshot(path=str(output / "checkout.png"), full_page=True)
        page.wait_for_timeout(900)
        page.get_by_role("button", name="Підтвердити замовлення").click()
        page.get_by_role("button", name="Перейти до оплати").click()
        page.wait_for_timeout(900)
        page.get_by_role("button", name="Відтворити результат").click()
        expect(page.locator(".status-row")).to_contain_text("Оплачено")
        page.screenshot(path=str(output / "order.png"), full_page=True)
        page.wait_for_timeout(1500)
        # Preserve stock after capture; no real transaction occurred.
        page.get_by_role("button", name="Скасувати замовлення").click()
        context.close()
        video.save_as(str(output / "walkthrough.webm"))
        video.delete()
        browser.close()
        if errors:
            raise RuntimeError("Browser script errors: " + "; ".join(errors))
    print(f"Portfolio screenshots and walkthrough saved to {output.resolve()}")


if __name__ == "__main__":
    main()
