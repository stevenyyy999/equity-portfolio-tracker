import stripe
from django.conf import settings

from subscriptions.models import StripeCustomer


def isUserSubscribed(request):
    """Check whether the current user's Stripe subscription is active."""
    try:
        stripe_customer = StripeCustomer.objects.get(user=request.user)
        stripe.api_key = settings.STRIPE_SECRET_KEY
        subscription = stripe.Subscription.retrieve(
            stripe_customer.stripeSubscriptionId
        )

        if subscription.status == "active":
            return True
        else:
            return False

    except StripeCustomer.DoesNotExist:
        # For never subscribed Stripe users
        return False
