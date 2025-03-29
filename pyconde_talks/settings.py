"""Django settings for pyconde_talks project."""

from pathlib import Path

import environ


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Django-environ
# Take environment variables from .env file
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# --------------------------------------------------------------------------------------------------
# GENERAL
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#debug
DEBUG = env.bool("DJANGO_DEBUG", False)
SITE_ID = env.int("SITE_ID", default=1)


# --------------------------------------------------------------------------------------------------
# INTERNATIONALIZATION
# https://docs.djangoproject.com/en/dev/topics/i18n/
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#language-code
LANGUAGE_CODE = env("LANGUAGE_CODE", default="en-us")
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
TIME_ZONE = env("TIME_ZONE", default="Europe/Berlin")
# https://docs.djangoproject.com/en/dev/ref/settings/#use-i18n
USE_I18N = env.bool("USE_I18N", default=True)
# https://docs.djangoproject.com/en/dev/ref/settings/#use-tz
USE_TZ = env.bool("USE_TZ", default=True)


# --------------------------------------------------------------------------------------------------
# DATABASES
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#databases
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

# Default primary key field type
# https://docs.djangoproject.com/en/dev/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --------------------------------------------------------------------------------------------------
# URLS
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#root-urlconf
ROOT_URLCONF = "pyconde_talks.urls"

# https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = "pyconde_talks.wsgi.application"
# https://docs.djangoproject.com/en/dev/howto/deployment/asgi/
ASGI_APPLICATION = "pyconde_talks.asgi.application"


# --------------------------------------------------------------------------------------------------
# APPS
# --------------------------------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.forms",
]
THIRD_PARTY_APPS = [
    # https://docs.djangoproject.com/en/dev/howto/deployment/asgi/daphne/
    "daphne",
    "allauth",
    "allauth.account",
    "django_htmx",
]
LOCAL_APPS = [
    "users",
    "talks",
]
# https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = LOCAL_APPS + THIRD_PARTY_APPS + DJANGO_APPS


# --------------------------------------------------------------------------------------------------
# SECURITY
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env("DJANGO_SECRET_KEY")
# https://docs.djangoproject.com/en/dev/ref/settings/#allowed-hosts
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")
# https://docs.djangoproject.com/en/dev/ref/settings/#session-cookie-httponly
SESSION_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#csrf-cookie-httponly
CSRF_COOKIE_HTTPONLY = True
# https://docs.djangoproject.com/en/dev/ref/settings/#x-frame-options
X_FRAME_OPTIONS = "DENY"


# --------------------------------------------------------------------------------------------------
# ADMIN
# --------------------------------------------------------------------------------------------------
# Django Admin URL
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")

# https://docs.djangoproject.com/en/dev/ref/settings/#admins
ADMIN_NAMES = env.list("ADMIN_NAMES")
ADMIN_EMAILS = env.list("ADMIN_EMAILS")
ADMINS = list(zip(ADMIN_NAMES, ADMIN_EMAILS, strict=False))

# https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS


# --------------------------------------------------------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#authentication-backends
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# https://docs.djangoproject.com/en/dev/ref/settings/#auth-user-model
AUTH_USER_MODEL = "users.CustomUser"

# Redirect after login
# https://docs.djangoproject.com/en/dev/ref/settings/#login-redirect-url
LOGIN_REDIRECT_URL = env("LOGIN_REDIRECT_URL", default="home")

# https://docs.djangoproject.com/en/dev/ref/settings/#login-url
LOGIN_URL = "/accounts/login/"

# ---------------------
# E-mail validation API
# ---------------------
EMAIL_VALIDATION_API_URL = env(
    "EMAIL_VALIDATION_API_URL",
    default="https://val.pycon.de/tickets/validate_email/",
)
EMAIL_VALIDATION_API_TIMEOUT = env.int("EMAIL_VALIDATION_API_TIMEOUT", default=10)

# E-mails that will bypass API validation
AUTHORIZED_EMAILS_WHITELIST = env.list(
    "AUTHORIZED_EMAILS_WHITELIST",
    default=ADMIN_EMAILS,
)

# --------------
# django-allauth
# https://docs.allauth.org/en/latest/
# --------------

# Regular accounts: passwordless authentication
# https://docs.allauth.org/en/latest/account/index.html
ACCOUNT_ADAPTER = "users.adapters.AccountAdapter"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 180
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_PREVENT_ENUMERATION = True


# --------------------------------------------------------------------------------------------------
# PASSWORDS
# User authentication is passwordless, but the admins do have passwords
# --------------------------------------------------------------------------------------------------
# Password hashers
# Use Argon2 for password hashing
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
# https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

# Password validation
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --------------------------------------------------------------------------------------------------
# MIDDLEWARE
# --------------------------------------------------------------------------------------------------
# Order matters!
# https://docs.djangoproject.com/en/dev/ref/settings/#middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]


# --------------------------------------------------------------------------------------------------
# STATIC
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = BASE_DIR / "staticfiles"

# https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = env("STATIC_URL", default="static/")

# https://docs.djangoproject.com/en/dev/ref/settings/#std-setting-STATICFILES_DIRS
STATICFILES_DIRS = [BASE_DIR / "static"]

# https://docs.djangoproject.com/en/dev/ref/settings/#staticfiles-finders
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]


# --------------------------------------------------------------------------------------------------
# MEDIA
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = BASE_DIR / env("MEDIA_ROOT", default="media")
MEDIA_URL = env("MEDIA_URL", default="/media/")


# --------------------------------------------------------------------------------------------------
# TEMPLATES
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#templates
TEMPLATES = [
    {
        # https://docs.djangoproject.com/en/dev/ref/settings/#std-setting-TEMPLATES-BACKEND
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",
        ],
        # https://docs.djangoproject.com/en/dev/ref/settings/#app-dirs
        "APP_DIRS": True,
        "OPTIONS": {
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# --------------------------------------------------------------------------------------------------
# EMAIL
# --------------------------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND")
# https://docs.djangoproject.com/en/dev/ref/settings/#email-timeout
EMAIL_TIMEOUT = 5
