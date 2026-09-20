from importlib import import_module

from django.apps import AppConfig


class StripeConfig(AppConfig):
    name = "subscriptions"

    def ready(self):
        import_module("subscriptions.signals")
