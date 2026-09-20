from django.contrib.auth.models import User
from django.db import models


class Portfolio(models.Model):
    """Portfolio owned by a user."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="portfolios")
    name = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False)
    description = models.CharField(max_length=255, null=True, blank=True)
    creation_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Short label with owner and portfolio name."""
        return f"{self.user} - {self.name}"


# To store how many shares user currently owns
# e.g. Retirement | CBA | 10
class Holdings(models.Model):
    """Current quantity of an equity in a portfolio."""

    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE, related_name="holdings"
    )
    equity = models.ForeignKey("transactions.Equity", on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)

    def __str__(self):
        """Short label with portfolio, ticker, and quantity."""
        return f"{self.portfolio.name} - {self.equity.ticker} (Qty: {self.quantity})"


# system logs for daily equity price update results
class PriceUpdateLog(models.Model):
    """Log entry for equity price update runs."""

    STATUS_CHOICES = [
        ("SUCCESS", "SUCCESS"),
        ("FAILED", "FAILED"),
        ("SKIPPED", "SKIPPED"),
    ]

    ticker = models.CharField(max_length=10)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    message = models.TextField(blank=True)
    run_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Short label with ticker, status, and run time."""
        return f"{self.ticker} - {self.status} - {self.run_at}"
