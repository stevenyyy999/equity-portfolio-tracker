from django.db import models


# Create your models here.
class ExchangeRate(models.Model):
    """Stores conversion rate from AUD to another currency."""

    # Base currency is AUD
    to_currency = models.CharField(max_length=3)
    rate = models.FloatField()
    date_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Rate and currency code as text."""
        return f"{self.rate} ({self.to_currency})"
