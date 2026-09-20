from django.contrib.auth.models import User
from django.db import models


# This class contains the stats for the equity itself
class Equity(models.Model):
    """Stores basic details for a tradable equity."""

    ticker = models.CharField(max_length=5)  # e.g. CBA
    name = models.CharField(max_length=100)  # e.g. Commonwealth Bank of Australia
    currency = models.CharField(max_length=3, default="AUD")
    exchange = models.CharField(max_length=10, default="ASX")

    def __str__(self):
        """Name and exchange as text."""
        return f"{self.name} ({self.exchange})"


# EquityPrice is tied to each Equity
class EquityPrice(models.Model):
    """Stores closing price history for one equity."""

    equity = models.ForeignKey(Equity, on_delete=models.CASCADE, related_name="prices")
    date = models.DateField()
    closing_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    class Meta:
        """One closing price row per equity per day (no duplicates)."""

        unique_together = (
            "equity",
            "date",
        )  # pulls in each equity once everyday --> prevents duplicates

    def __str__(self):
        """Ticker, name, and closing price as text."""
        return f"{self.equity.ticker} ({self.equity.name}) - {self.closing_price}"


class Transaction(models.Model):
    """Represents one BUY or SELL transaction made by a user."""

    TRANSACTION_TYPES = [
        ("BUY", "BUY"),
        ("SELL", "SELL"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="transactions"
    )
    equity = models.ForeignKey(Equity, on_delete=models.CASCADE, null=True, blank=True)
    portfolio = models.ForeignKey(
        "portfolios.Portfolio",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transactions",
    )
    transaction_type = models.CharField(max_length=4, choices=TRANSACTION_TYPES)
    quantity = models.PositiveIntegerField()
    remaining_quantity = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    trade_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    currency = models.CharField(max_length=3, default="AUD")

    def __str__(self):
        """User, trade type, and ticker as text."""
        equity_ticker = self.equity.ticker if self.equity else "NO_EQUITY"
        return f"{self.user.username} - {self.transaction_type} {equity_ticker}"

    @property
    def value(self):
        """Formatted transaction value as currency string."""
        total = self.quantity * self.price
        return f"${total:.2f}"


# List of all ASX equities to be imported from CSV file
class ASXEquityList(models.Model):
    """Imported ASX ticker reference list used for validation."""

    ticker = models.CharField(max_length=10, unique=True)  # e.g. CBA
    name = models.CharField(max_length=100)  # e.g. Commonwealth Bank of Australia
    industry = models.CharField(max_length=100, null=True, blank=True)  # e.g. Banks

    def __str__(self):
        """Ticker and company name as text."""
        return f"{self.ticker} - {self.name}"


# Match each SELL to BUY
class TransactionMatch(models.Model):
    """Links sell transactions to matched buy lots."""

    buy_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="sell_matches_from_buy",
    )
    sell_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="buy_matches_for_sell",
    )
    matched_quantity = models.PositiveIntegerField()

    def __str__(self):
        """Linked buy/sell ids and matched quantity as text."""
        return (
            f"BUY {self.buy_transaction.id} -> "
            f"SELL {self.sell_transaction.id} "
            f"({self.matched_quantity})"
        )
