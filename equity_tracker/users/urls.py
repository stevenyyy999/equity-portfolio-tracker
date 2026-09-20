from django.urls import path

from .views import delete_account, email_change_page, profile_view

urlpatterns = [
    path("profile/", profile_view, name="profile"),
    path("email/", email_change_page, name="email-change"),
    path("delete-account/", delete_account, name="delete-account"),
]
