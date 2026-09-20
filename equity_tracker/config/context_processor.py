import stripe
from django.conf import settings
from subscriptions.models import StripeCustomer


def subscription_context(request):
    if not request.user.is_authenticated:
        return {
            "subscription": None,
            "product": None,
            "local_subscription": None,
        }
    try:
        # copied from subscription/veiws.py with minor changes
        # Retrieve the subscription & product
        stripe_customer = StripeCustomer.objects.get(user=request.user)
        stripe.api_key = settings.STRIPE_SECRET_KEY
        if stripe_customer.stripeSubscriptionId is None:
            return {
                "subscription": None,
                "product": None,
                "local_subscription": stripe_customer.subscription,
            }
        # get subscription from stripe so price matches customer portal / dashboard
        stripe_sub = stripe.Subscription.retrieve(stripe_customer.stripeSubscriptionId)
        # cant use .items here — stripe objects are dict-like and .items is dict.items()
        line = stripe_sub["items"]["data"][0]
        price = line["price"]
        product = stripe.Product.retrieve(price["product"])

        return {
            "subscription": stripe_sub,
            "product": product,
            "local_subscription": stripe_customer.subscription,
        }

    except StripeCustomer.DoesNotExist:
        # User has no subscription yet -> free plan
        # but it'll probs work fine without this bit code code below
        return {
            "subscription": None,
            "product": None,
        }
