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
    # The project URLs are set up the way the README tells users to.
    ROOT_URLCONF="tests.urls",
    ###################################
    ######### API PROXY SETTINGS #########
    ###################################

    TARGET_API_URL = 'https://httpbin.org',
    PROXY_ROUTE_PATH = 'my_test_route',
    PROXY_TARGET_PATH = 'get'

    ###################################
)

django.setup()
