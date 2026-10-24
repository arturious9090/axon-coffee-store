from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.sitemaps import Sitemap
from django.contrib.sitemaps.views import sitemap
from django.urls import path

from store import views
from store.models import Product


class ProductSitemap(Sitemap):
    protocol = "https"

    def items(self):
        return Product.objects.filter(active=True)


urlpatterns = [
    path("", views.home, name="home"),
    path("coffee/", views.catalog, name="catalog"),
    path("coffee/<slug:slug>/", views.product, name="product"),
    path("cart/", views.cart_view, name="cart"),
    path("checkout/", views.checkout, name="checkout"),
    path("orders/<uuid:order_id>/", views.order_view, name="order"),
    path("orders/<uuid:order_id>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<uuid:order_id>/pay/", views.start_payment, name="start_payment"),
    path("orders/<uuid:order_id>/claim/", views.claim_order, name="claim_order"),
    path("orders/<uuid:order_id>/manage/", views.staff_action, name="staff_action"),
    path("payments/mock/<uuid:attempt_id>/", views.mock_payment, name="mock_payment"),
    path("payments/liqpay/callback/", views.payment_callback, name="payment_callback"),
    path("shipping/branches/", views.branches, name="branches"),
    path("account/", views.account, name="account"),
    path("account/register/", views.register, name="register"),
    path("account/verify/<str:token>/", views.verify_email, name="verify_email"),
    path("account/login/", views.login_view, name="login"),
    path("account/logout/", views.logout_view, name="logout"),
    path("account/addresses/new/", views.address_view, name="address_new"),
    path("account/addresses/<int:address_id>/", views.address_view, name="address_edit"),
    path(
        "account/password/reset/", views.LimitedPasswordResetView.as_view(), name="password_reset"
    ),
    path(
        "account/password/sent/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "account/password/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(template_name="accounts/reset_confirm.html"),
        name="password_reset_confirm",
    ),
    path(
        "account/password/complete/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/reset_complete.html"),
        name="password_reset_complete",
    ),
    path("pages/<slug:slug>/", views.content, name="content"),
    path("demo/", views.demo, name="demo"),
    path("demo/admin/", views.demo_admin, name="demo_admin"),
    path("health/", views.health, name="health"),
    path("robots.txt", views.robots),
    path("media/<path:path>", views.media_image, name="media_image"),
    path("sitemap.xml", sitemap, {"sitemaps": {"products": ProductSitemap}}),
    path("admin/", admin.site.urls),
]
