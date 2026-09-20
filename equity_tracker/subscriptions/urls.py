from django.urls import path

from . import views

# URLConf
urlpatterns = [
    path("", views.home, name="subscriptions-home"),
    path("config/", views.stripe_config, name="stripe_config"),
    path(
        "create-checkout-session/",
        views.create_checkout_session,
        name="create_checkout_session",
    ),
    path("success/", views.success, name="stripe_success"),
    path("cancel/", views.cancel, name="stripe_cancel"),
    path("webhook/", views.stripe_webhook, name="stripe_webhook"),
    path("dashboard/", views.create_portal_session, name="stripe_dashboard"),
]
