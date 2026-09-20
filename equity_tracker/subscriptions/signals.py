from allauth.account.signals import user_signed_up
from allauth.socialaccount.signals import social_account_added
from django.dispatch import receiver
from users.models import Profile

from .models import StripeCustomer
from .utils import get_or_create_local_subscription


def initial_user_setup(user):
    """Create default StripeCustomer and Profile records for new user."""
    free_subscription = get_or_create_local_subscription("free")

    # Initialise StripeCustomer free subscription
    StripeCustomer.objects.get_or_create(
        user=user,
        defaults={
            "subscription": free_subscription,
            "stripeCustomerId": None,
            "stripeSubscriptionId": None,
            "status": "active",
        },
    )

    # Create user Profile
    Profile.objects.get_or_create(
        user=user,
        defaults={
            "subscription": free_subscription,
            "country": "Australia",
            "currency": "AUD",
        },
    )


# Django normal signup
@receiver(user_signed_up)
def on_email_signup(request, user, **kwargs):
    """Run initial setup after standard signup."""
    initial_user_setup(user)


# Google signup
@receiver(social_account_added)
def on_social_signup(request, sociallogin, **kwargs):
    """Run initial setup after social signup."""
    initial_user_setup(sociallogin.user)
