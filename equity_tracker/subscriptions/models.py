from django.contrib.auth.models import User
from django.db import models

FREE_MAX_PORTFOLIOS = 1
FREE_MAX_EQUITIES = 5
FREE_PRICE = 0
PREMIUM_MAX_PORTFOLIOS = -1
PREMIUM_MAX_EQUITIES = -1
UNLIMITED_PORTFOLIOS = -1
UNLIMITED_EQUITIES = -1


class Subscription(models.Model):
    """Subscription plan limits and Stripe price mapping."""

    PLANS = [
        ("free", "Free"),
        ("premium", "Premium"),
    ]

    name = models.CharField(max_length=10, choices=PLANS, unique=True)
    badge = models.CharField(max_length=10, blank=True, default="")

    # to keep plans flexible
    max_portfolios = models.IntegerField(null=True, blank=True)
    max_equities = models.IntegerField(null=True, blank=True)
    stripe_price_id = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        """Plan name as text."""
        return self.name


class StripeCustomer(models.Model):
    """Stores Stripe customer/subscription data linked to a user."""

    STATUS = [
        ("active", "Active"),
        ("canceled", "Canceled"),
        ("expired", "Expired"),
        ("unpaid", "Unpaid"),
        ("inactive", "Inactive"),
    ]

    user = models.OneToOneField(
        to=User, on_delete=models.CASCADE, related_name="subscription"
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True
    )
    stripeCustomerId = models.CharField(
        max_length=255, blank=True, null=True, db_index=True
    )
    stripeSubscriptionId = models.CharField(
        max_length=255, blank=True, null=True, db_index=True
    )
    status = models.CharField(max_length=10, choices=STATUS, default="inactive")
    startDate = models.DateTimeField(auto_now_add=True)
    billingEndDate = models.DateTimeField(
        null=True, blank=True
    )  # when current billing cycle ends
    updatedAt = models.DateTimeField(auto_now=True)  # when Stripe updates subscription

    def __str__(self):
        """Username and subscription status as text."""
        return f"{self.user.username} ({self.status})"


class StripeTransaction(models.Model):
    """Stores Stripe payment transaction records."""

    customer = models.ForeignKey(
        StripeCustomer, on_delete=models.CASCADE, related_name="transactions"
    )
    stripePaymentId = models.CharField(max_length=255)
    currency = models.CharField(max_length=10, default="AUD")
    amount = models.IntegerField()
    status = models.CharField(max_length=20)
    createdAt = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Payment amount, currency, and status as text."""
        return f"{self.customer.user.username} - {self.amount / 100:.2f} {self.currency} ({self.status})"
