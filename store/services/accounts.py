import hashlib

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

from store.services.notifications import queue_mail


def rate_limit(request, scope, identity="", limit=10, seconds=600):
    # Never trust arbitrary forwarding headers from visitors.
    for value in {request.META.get("REMOTE_ADDR", "unknown"), identity.strip().lower()} - {""}:
        key = "rate:" + scope + ":" + hashlib.sha256(value.encode()).hexdigest()
        if cache.add(key, 1, seconds):
            continue
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, seconds)
            count = 1
        if count > limit:
            raise PermissionDenied("Забагато спроб. Спробуйте пізніше.")


def queue_verification(user):
    token = signing.dumps({"id": user.pk, "email": user.email}, salt="verify-email")
    url = settings.SITE_URL + "/account/verify/" + token + "/"
    queue_mail(
        "verify:" + str(user.pk),
        user.email,
        "Плин · підтвердьте email",
        "Підтвердіть email протягом 24 годин:\n" + url,
    )
