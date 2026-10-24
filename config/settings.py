import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)
APP_ENV = os.environ.get("APP_ENV", "local")
DEMO_MODE = APP_ENV != "live"
DEBUG = os.environ.get("DEBUG", "1" if APP_ENV == "local" else "0") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY", "local-demo-only-plyn-change-before-hosting")
PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "mock")
SHIPPING_PROVIDER = os.environ.get("SHIPPING_PROVIDER", "mock")
if not DEMO_MODE:
    raise ImproperlyConfigured(
        "Live mode requires real payment and shipping adapters; not implemented."
    )
if APP_ENV != "local" and SECRET_KEY.startswith("local-demo"):
    raise ImproperlyConfigured("Set a unique SECRET_KEY outside the local environment.")
if PAYMENT_PROVIDER != "mock" or SHIPPING_PROVIDER != "mock":
    raise ImproperlyConfigured("This demo implements only mock providers.")

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
CSRF_TRUSTED_ORIGINS = [
    value for value in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if value
]
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "store.apps.StoreConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "store.middleware.DemoHeadersMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "store.context_processors.shop",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {
    "default": dj_database_url.config(
        default="postgresql://plyn:local-demo-password@127.0.0.1:55432/plyn",
        conn_max_age=60,
    )
}
AUTH_USER_MODEL = "store.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "uk"
TIME_ZONE = "Europe/Kyiv"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "data/media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
if os.environ.get("S3_BUCKET"):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["S3_BUCKET"],
            "default_acl": None,
            "querystring_auth": False,
            "file_overwrite": False,
        },
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/account/login/"
LOGIN_REDIRECT_URL = "/account/"
LOGOUT_REDIRECT_URL = "/"
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.filebased.EmailBackend")
EMAIL_FILE_PATH = BASE_DIR / "data/mail"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "127.0.0.1")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "1025"))
if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and EMAIL_HOST not in {"127.0.0.1", "localhost", "mail"}:
    raise ImproperlyConfigured("Demo SMTP is restricted to the local mail viewer.")
DEFAULT_FROM_EMAIL = "Плин <demo@plyn.example>"
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:56379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_TIMEOUT = 2
CELERY_BEAT_SCHEDULE = {
    "process-outbox": {"task": "store.tasks.process_outbox", "schedule": 5.0},
    "expire-reservations": {"task": "store.tasks.expire_reservations", "schedule": 60.0},
    "poll-shipments": {"task": "store.tasks.poll_shipments", "schedule": 60.0},
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
if os.environ.get("CACHE_URL"):
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ["CACHE_URL"],
    }
RESERVATION_MINUTES = int(os.environ.get("RESERVATION_MINUTES", "20"))
GUEST_LINK_SECONDS = 7 * 24 * 60 * 60
MOCK_LIQPAY_KEY = os.environ.get("MOCK_LIQPAY_KEY", SECRET_KEY + "-liqpay-simulation")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
X_FRAME_OPTIONS = "DENY"
if APP_ENV != "local":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
