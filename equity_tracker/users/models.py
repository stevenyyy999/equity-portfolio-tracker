from django.contrib.auth.models import User
from django.db import models
from portfolios.models import Portfolio
from subscriptions.models import Subscription
from transactions.models import Equity


class Profile(models.Model):
    """Stores account details linked to a Django user."""

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    address = models.TextField(blank=True, null=True)
    country = models.TextField()
    currency = models.CharField(max_length=3, default="AUD")
    creation_date = models.DateTimeField(auto_now_add=True)

    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True
    )
    portfolios = models.ManyToManyField(Portfolio, blank=True)
    equities = models.ManyToManyField(Equity, blank=True)

    def __str__(self):
        """Short label with username and email for lists and admin."""
        return f"{self.user.username} ({self.user.email})"
