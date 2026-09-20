from datetime import datetime, timezone

import stripe
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http.response import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from users.models import Profile

from subscriptions.models import StripeCustomer

from . import models
from .utils import (
    get_or_create_local_subscription,
    get_stripe_subscription_product,
    user_cancels_stripe,
    validate_billing_end,
    validate_stripe_subscription_plan,
)


@login_required
def home(request):
    """Render subscription home with current plan and Stripe details."""
    try:
        stripe_customer = StripeCustomer.objects.get(user=request.user)
        stripe.api_key = settings.STRIPE_SECRET_KEY

        # free users won't have a stripe subscription yet
        if stripe_customer.stripeSubscriptionId is None:
            price = models.FREE_PRICE
            # In production, every user should have an associated stripe account,
            # the local subscription is a fallback
            local = stripe_customer.subscription or get_or_create_local_subscription(
                "free"
            )
            return render(
                request,
                "home.html",
                {
                    "subscription": None,
                    "product": None,
                    "local_subscription": local,
                    "subscription_price": price,
                    "billing_interval": "month",
                },
            )

        # get subscription from stripe so price matches customer portal / dashboard
        stripe_sub = stripe.Subscription.retrieve(stripe_customer.stripeSubscriptionId)

        billing_end_date = stripe_customer.billingEndDate
        if billing_end_date is None:
            billing_end_date = validate_billing_end(stripe_sub)

        if stripe_customer.billingEndDate is None and billing_end_date is not None:
            stripe_customer.billingEndDate = billing_end_date
            stripe_customer.save(update_fields=["billingEndDate"])

        billing_end_date_formatted = None
        if billing_end_date is not None:
            billing_end_date_formatted = billing_end_date.strftime("%B %d, %Y")

        stripe_sub_info = get_stripe_subscription_product(
            stripe_customer.stripeSubscriptionId
        )
        return render(
            request,
            "home.html",
            {
                "subscription": stripe_sub,
                "product": stripe_sub_info["product"],
                "local_subscription": stripe_customer.subscription,
                "expiry_date": billing_end_date_formatted,
                "subscription_price": stripe_sub_info["subscription_price"],
                "billing_interval": stripe_sub_info["billing_interval"],
            },
        )

    except StripeCustomer.DoesNotExist:
        local = get_or_create_local_subscription("free")
        return render(
            request,
            "home.html",
            {
                "local_subscription": local,
                "subscription_price": 0,
                "billing_interval": "month",
            },
        )


@csrf_exempt
def stripe_config(request):
    """Return Stripe publishable key for frontend setup."""
    if request.method == "GET":
        stripe_config = {"publicKey": settings.STRIPE_PUBLISHABLE_KEY}
        return JsonResponse(stripe_config, safe=False)


@csrf_exempt
def create_checkout_session(request):
    """Create a Stripe Checkout session for premium subscription."""
    if request.method == "GET":
        domain_url = "http://localhost:8000/stripe/"
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            checkout_session = stripe.checkout.Session.create(
                client_reference_id=request.user.id
                if request.user.is_authenticated
                else None,
                success_url=domain_url + "success?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=domain_url + "cancel/",
                payment_method_types=["card"],
                mode="subscription",
                # Line that defaults to premium cost
                line_items=[
                    {
                        "price": settings.STRIPE_PREMIUM_PRICE_ID,
                        "quantity": 1,
                    }
                ],
            )
            return JsonResponse({"sessionId": checkout_session["id"]})
        except Exception as e:
            return JsonResponse({"error": str(e)})


@login_required
def success(request):
    """Render checkout success page."""
    return render(request, "success.html")


@login_required
def cancel(request):
    """Render checkout cancel page."""
    return render(request, "cancel.html")


# Don't put any keys in code. Use a secrets vault or environment
# variable to supply keys to your integration. This example
# shows how to set a secret key for illustration purposes only.
#
# See https://docs.stripe.com/keys-best-practices and find your
# keys at https://dashboard.stripe.com/apikeys.
@login_required
async def create_portal_session(request):
    """Create and redirect to Stripe billing portal session."""
    try:
        stripe_customer = await StripeCustomer.objects.aget(user=request.user)
        customer_id = stripe_customer.stripeCustomerId

        portal_session = await stripe.billing_portal.Session.create_async(
            customer=customer_id,
            return_url="http://localhost:8000/stripe/",
        )

        return redirect(portal_session.url)

    except StripeCustomer.DoesNotExist:
        return HttpResponse(status=400)


@csrf_exempt
def stripe_webhook(request):
    """Handle incoming Stripe webhook events."""
    stripe.api_key = settings.STRIPE_SECRET_KEY
    endpoint_secret = settings.STRIPE_ENDPOINT_SECRET
    payload = request.body
    sig_header = request.META["HTTP_STRIPE_SIGNATURE"]
    event = None

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError:
        # Invalid payload
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        # Invalid signature
        return HttpResponse(status=400)

    session = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        # look up the actual plan that was purchased instead of hardcoding premium
        purchased_plan = get_or_create_local_subscription("premium")
        handle_create_stripe_customer(session, purchased_plan)
    elif event["type"] == "customer.subscription.updated":
        handle_subscription_updated(session)
    elif event["type"] == "customer.subscription.deleted":
        # Fetch Stripe customer user and cancel their subscription
        stripe_customer = StripeCustomer.objects.filter(
            stripeSubscriptionId=session["id"]
        ).first()
        if stripe_customer:
            user_cancels_stripe(stripe_customer.user)
        handle_subscription_cancelled(session)

    return HttpResponse(status=200)


# Creates stripe customer with default plan of premium
def handle_create_stripe_customer(session, subscription):
    """Create or update StripeCustomer after checkout completion."""
    # Fetch all the required data from session
    client_reference_id = session.client_reference_id
    stripe_customer_id = session.customer
    stripe_subscription_id = session.subscription
    stripe_subscription = stripe.Subscription.retrieve(stripe_subscription_id)
    billing_end = validate_billing_end(stripe_subscription)

    # Get the user and create a new StripeCustomer
    # User can't be none because allauth requires you to log in before going into stripe
    user = User.objects.get(id=client_reference_id)
    StripeCustomer.objects.update_or_create(
        user=user,
        defaults={
            "stripeCustomerId": stripe_customer_id,
            "stripeSubscriptionId": stripe_subscription_id,
            "status": "active",
            "subscription": subscription,
            "billingEndDate": billing_end,
        },
    )

    # Updates user profile subscription too
    Profile.objects.update_or_create(
        user=user,
        defaults={"subscription": subscription},
    )


# Assuming updating between Stripe plans
def handle_subscription_updated(subscription):
    """Sync local subscription status when Stripe updates subscription."""
    # Updates subscription ending date
    billing_end = validate_billing_end(subscription)

    # Gets price ID from Stripe and matches it to a plan in our DB
    new_plan = validate_stripe_subscription_plan(subscription)

    # Find the stripe customer we are gonna update
    stripe_customer = StripeCustomer.objects.filter(
        stripeSubscriptionId=subscription["id"]
    )

    # Update stripe status and subscription alongside user profile
    if stripe_customer.exists() and new_plan:
        stripe_customer.update(
            status=subscription["status"],
            billingEndDate=billing_end,
            subscription=new_plan,
        )

        # get the actual object to access .user
        stripe_customer_obj = stripe_customer.first()
        Profile.objects.update_or_create(
            user=stripe_customer_obj.user,
            defaults={"subscription": new_plan},
        )
    else:
        stripe_customer.update(
            status=subscription["status"],
            billingEndDate=billing_end,
        )


def handle_subscription_cancelled(subscription):
    """Handle Stripe cancellation while respecting billing end date."""
    # Issue is caused when you cancel subscription and it only changes status to canceled
    # Have to check if subscription is over
    billing_end = validate_billing_end(subscription)
    current_time = datetime.now(tz=timezone.utc)
    free_subscription = get_or_create_local_subscription("free")

    # Subscription is over
    if billing_end is None or current_time >= billing_end:
        StripeCustomer.objects.filter(stripeSubscriptionId=subscription["id"]).update(
            status="canceled",
            subscription=free_subscription,
            billingEndDate=current_time,
        )
    else:
        # Billing period still active
        StripeCustomer.objects.filter(stripeSubscriptionId=subscription["id"]).update(
            status="canceled",
            billingEndDate=billing_end,
        )
