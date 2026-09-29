"""
Django settings for the fuel route planner project.

Most values can be overridden through environment variables (see .env.example).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-dev-only-key-change-me-in-production-0123456789",
)

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fuelroute",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CACHES = {
    "default": {
        "BACKEND": os.environ.get("CACHE_BACKEND", "django.core.cache.backends.locmem.LocMemCache"),
        "LOCATION": os.environ.get("CACHE_LOCATION", "fuelroute"),
        "TIMEOUT": int(os.environ.get("ROUTE_CACHE_SECONDS", 60 * 60 * 24)),
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# Django defaults to "same-origin", which strips the Referer from cross-site requests.
# OpenStreetMap's tile servers block tile requests without a Referer, so use the browser default.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "EXCEPTION_HANDLER": "fuelroute.exceptions.api_exception_handler",
}


# --- Fuel route planner -----------------------------------------------------

DATA_DIR = BASE_DIR / "data"
FUEL_PRICES_CSV = DATA_DIR / "fuel-prices-for-be-assessment.csv"
GEOCODED_STATIONS_CSV = DATA_DIR / "fuel_stations_geocoded.csv"
US_PLACES_CSV = DATA_DIR / "us_places.csv"
US_ZIPCODES_CSV = DATA_DIR / "us_zipcodes.csv"
US_BOUNDARY_JSON = DATA_DIR / "us_boundary.json"

# "osrm" (no key needed) or "ors" (OpenRouteService, requires ORS_API_KEY).
ROUTING_PROVIDER = os.environ.get("ROUTING_PROVIDER", "osrm").strip().lower()
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://routing.openstreetmap.de/routed-car").rstrip("/")
ORS_BASE_URL = os.environ.get("ORS_BASE_URL", "https://api.openrouteservice.org").rstrip("/")
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
ORS_PROFILE = os.environ.get("ORS_PROFILE", "driving-hgv")

NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")
HTTP_USER_AGENT = os.environ.get("HTTP_USER_AGENT", "fuel-route-planner/1.0 (Django assessment project)")
HTTP_CONNECT_TIMEOUT = float(os.environ.get("HTTP_CONNECT_TIMEOUT", 5))
HTTP_READ_TIMEOUT = float(os.environ.get("HTTP_READ_TIMEOUT", 25))

VEHICLE_RANGE_MILES = float(os.environ.get("VEHICLE_RANGE_MILES", 500))
VEHICLE_MPG = float(os.environ.get("VEHICLE_MPG", 10))
DEFAULT_CORRIDOR_MILES = float(os.environ.get("DEFAULT_CORRIDOR_MILES", 10))
# An extra fuel stop is only worth making if it saves at least this much (USD).
DEFAULT_STOP_PENALTY_USD = float(os.environ.get("DEFAULT_STOP_PENALTY_USD", 10))
ROUTE_CACHE_SECONDS = int(os.environ.get("ROUTE_CACHE_SECONDS", 60 * 60 * 24))


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "fuelroute": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    },
}
