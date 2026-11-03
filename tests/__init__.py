import django
from django.conf import settings

settings.configure(
    DEBUG=True,
    USE_TZ=True,
    SECRET_KEY="only-for-tests",
    ALLOWED_HOSTS=["testserver"],
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:"
        }
    },
    INSTALLED_APPS=[
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "rest_framework",
        "django_simple_api_proxy",
    ],
    MIDDLEWARE=[
        'django.middleware.common.CommonMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
    ],
    # Templates as in a new Django project; Django REST framework uses them for
    # the error pages it shows to browsers.
    TEMPLATES=[{
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ]},
    }],
    # The project URLs are set up the way the README tells users to.
    ROOT_URLCONF="tests.urls",
    ###################################
    ######### API PROXY SETTINGS #########
    ###################################

    TARGET_API_URL = 'https://httpbin.org',
    PROXY_ROUTE_PATH = 'my_test_route',
    PROXY_TARGET_PATH = 'get',
    # The stock APIProxy of these tests runs in the JSON mode of 1.x, so the 1.x
    # tests keep checking it unchanged. Pass-through mode, the default since 2.0,
    # is tested through subclasses (tests/urls_pass_through.py) and in a fresh
    # process without this setting (tests/test_settings.py).
    PROXY_PASS_THROUGH = False,

    ###################################
)

django.setup()
