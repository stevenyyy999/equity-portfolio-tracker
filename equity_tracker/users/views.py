import json

import stripe
from currency.models import ExchangeRate
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from subscriptions.utils import user_delete_stripe_customer

from .account import build_email_change_context, process_email_change_request
from .models import Profile


# Australia-only rule for enabling the CGT calculator.
def is_cgt_enabled(country, currency):
    """Check if CGT calculator should be enabled for this user."""
    check_country = (country or "").strip().lower()
    check_currency = (currency or "").strip().upper()
    return check_country == "australia" and check_currency == "AUD"


def get_available_currency_options():
    """Get currency codes for the profile settings currency dropdown."""
    try:
        currency_options = list(
            ExchangeRate.objects.values_list("to_currency", flat=True)
            .distinct()
            .order_by("to_currency")
        )
    except Exception:
        return ["AUD"]

    if "AUD" not in currency_options:
        currency_options.insert(0, "AUD")

    return currency_options


# HTTP route API data handler for /users/profile/.
@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request):
    """Load and update the logged in user profile details."""
    # Ensure every logged-in user has a profile row to read and update.
    profile, _ = Profile.objects.get_or_create(
        user=request.user,
        defaults={
            "country": "Australia",
            "currency": "AUD",
        },
    )

    # Send the currently saved profile data back to the frontend.
    if request.method == "GET":
        return JsonResponse(
            {
                "first_name": request.user.first_name,
                "last_name": request.user.last_name,
                "address": profile.address,
                "country": profile.country,
                "currency": profile.currency,
                "currency_options": get_available_currency_options(),
                "cgt_enabled": is_cgt_enabled(profile.country, profile.currency),
            }
        )

    # Read the updated fields submitted by the frontend.
    data = json.loads(request.body)

    # extract data from request body
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    address = data.get("address")
    country = data.get("country")
    currency = data.get("currency")
    password = data.get("password")

    # Update built-in Django User fields.
    if first_name is not None:
        request.user.first_name = first_name

    if last_name is not None:
        request.user.last_name = last_name

    if password:
        request.user.set_password(password)

    request.user.save()
    # Keep the user logged in after a password change.
    update_session_auth_hash(request, request.user)

    # Update custom profile fields stored in the Profile model.
    if address is not None:
        profile.address = address

    if country is not None:
        profile.country = country

    if currency is not None:
        profile.currency = currency

    profile.save()

    # Return the saved data so the frontend can confirm the update.
    return JsonResponse(
        {
            "message": "Profile updated successfully",
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "address": profile.address,
            "country": profile.country,
            "currency": profile.currency,
            "currency_options": get_available_currency_options(),
            "cgt_enabled": is_cgt_enabled(profile.country, profile.currency),
        }
    )


# The actual screen to represent the API data above
@login_required
@require_http_methods(["GET", "POST"])
def profile_page(request):
    """Render frontend profile screen and handle email change posts."""
    success_message = ""
    error_message = ""

    if request.method == "POST":
        success_message, error_message = process_email_change_request(request)

    return render(
        request,
        "users/profile.html",
        build_email_change_context(
            request.user,
            success_message=success_message,
            error_message=error_message,
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def email_change_page(request):
    """Redirect old email change route to the profile email panel."""
    return redirect("/accounts/profile/#email-panel")


@login_required
@require_POST
def delete_account(request):
    """Delete the all data and cancel the Stripe subscription from logged in user account"""
    user = request.user

    try:
        user_delete_stripe_customer(user)
    except stripe.error.StripeError:
        return JsonResponse(
            {
                "error": "Account could not be deleted because Stripe cancellation failed. Please try again later."
            },
            status=400,
        )
    with transaction.atomic():
        logout(request)
        user.delete()

    return JsonResponse(
        {
            "message": "Account deleted successfully.",
            "redirect_url": "/",
        }
    )
