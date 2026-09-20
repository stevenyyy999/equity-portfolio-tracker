from datetime import datetime, timezone

import stripe
from django.conf import settings
from portfolios.models import Holdings

from subscriptions.models import StripeCustomer, Subscription

from . import models

####################
#
#   HELPER FUNCTIONS
#
####################


def validate_billing_end(subscription):
    """Extract and normalise billing end datetime from Stripe subscription data."""
    subscription_end = getattr(subscription, "current_period_end", None)

    # First try get Stripe subscription end date (failed previously)
    if subscription_end is None:
        try:
            subscription_end = subscription["current_period_end"]
        except (KeyError, TypeError):
            subscription_end = None

    # Second try to ensure get billing end date
    if subscription_end is None:
        try:
            subscription_end = subscription["items"]["data"][0]["current_period_end"]
        except (KeyError, IndexError, TypeError):
            subscription_end = None

    if subscription_end is not None:
        billing_end = datetime.fromtimestamp(subscription_end, tz=timezone.utc)
    else:
        billing_end = None

    return billing_end


def validate_stripe_subscription_plan(subscription):
    """Map Stripe subscription price id to local Subscription plan."""
    # Gets price ID from Stripe and matches it to a plan in our DB
    stripe_price_id = subscription["items"]["data"][0]["price"]["id"]
    try:
        plan = Subscription.objects.get(stripe_price_id=stripe_price_id)
    except Subscription.DoesNotExist:
        plan = None

    return plan


def get_stripe_subscription_product(stripeSubscriptionId):
    """Fetch product and pricing details for a Stripe subscription."""
    # get subscription from stripe so price matches customer portal / dashboard
    stripe_sub = stripe.Subscription.retrieve(stripeSubscriptionId)
    line = stripe_sub["items"]["data"][0]
    price = line["price"]
    product = stripe.Product.retrieve(price["product"])

    # Stripe uses cents
    cents = price["unit_amount"]
    monthly_price = cents / 100

    return {
        "subscription_price": monthly_price,
        "product": product,
        "billing_interval": price["recurring"]["interval"],
    }


# assigns a premium subscription record to the user if missing, or just reuse it if it already exists
def get_or_create_local_subscription(name):
    """Get or create local free/premium subscription record."""
    if name == "free":
        max_portfolios = models.FREE_MAX_PORTFOLIOS
        max_equities = models.FREE_MAX_EQUITIES
        badge = ""
        stripe_price_id = None
    elif name == "premium":
        max_portfolios = models.PREMIUM_MAX_PORTFOLIOS
        max_equities = models.PREMIUM_MAX_EQUITIES
        badge = "Premium"
        stripe_price_id = settings.STRIPE_PREMIUM_PRICE_ID
    else:
        raise ValueError(f"Unknown plan name: {name}")

    subscription, _ = Subscription.objects.get_or_create(
        name=name,
        defaults={
            "badge": badge,
            "max_portfolios": max_portfolios,
            "max_equities": max_equities,
            "stripe_price_id": stripe_price_id,
        },
    )
    return subscription


def user_delete_stripe_customer(user):
    """Cancel and delete Stripe records linked to a user."""
    stripe_customer = StripeCustomer.objects.filter(user=user).first()

    # Free/debug users may not have any Stripe customer data to clean up.
    if stripe_customer is None:
        return

    subscription_id = stripe_customer.stripeSubscriptionId
    customer_id = stripe_customer.stripeCustomerId

    # The customer didnt have a stripe customer id or a subscription id
    if not subscription_id and not customer_id:
        return

    # we need to set the proper API key so stripe library can be authenticated
    # as our stripe's account so then we call delete methods
    stripe.api_key = settings.STRIPE_SECRET_KEY

    # delete relevant objects from stripe
    if subscription_id:
        stripe.Subscription.cancel(subscription_id)

    if customer_id:
        stripe.Customer.delete(customer_id)


# Move all users equities and transactions to default portfolio
def user_cancels_stripe(user):
    """Move data to default portfolio when user subscription is canceled."""
    default_portfolio = user.portfolios.filter(is_default=True).first()
    if not default_portfolio:
        return

    move_portfolios = user.portfolios.filter(is_default=False)

    for portfolio in move_portfolios:
        move_holdings = list(portfolio.holdings.select_related("equity").all())

        for holding in move_holdings:
            default_holding, is_created = Holdings.objects.get_or_create(
                portfolio=default_portfolio,
                equity=holding.equity,
                defaults={"quantity": 0},
            )
            default_holding.quantity += holding.quantity
            default_holding.save()

        portfolio.transactions.update(portfolio=default_portfolio)
        portfolio.delete()
