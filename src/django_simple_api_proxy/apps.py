from django.apps import AppConfig


class ApiProxyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'django_simple_api_proxy'

    def ready(self) -> None:
        from django.core import checks

        from django_simple_api_proxy.checks import check_settings

        checks.register(check_settings)
