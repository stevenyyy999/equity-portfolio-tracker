import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from portfolios.models import Holdings, Portfolio
from portfolios.yfinance.yfinance_server import store_equity_price_history
from subscriptions.models import FREE_MAX_EQUITIES, UNLIMITED_EQUITIES, StripeCustomer
from users.models import Profile

from .models import ASXEquityList, Equity, Transaction, TransactionMatch

MIN_TRADE_DATE = date(1987, 4, 1)

####################
#
#   HELPER FUNCTIONS
#
####################


def validate_transaction_data_helper(
    ticker, transaction_type, quantity_raw, price_raw, trade_date_raw
):
    """Validate transaction form fields and return parsed values plus errors."""
    ticker = (ticker or "").strip().upper()
    transaction_type = (transaction_type or "").strip().upper()
    quantity_raw = (quantity_raw or "").strip()
    price_raw = (price_raw or "").strip()
    trade_date = (trade_date_raw or "").strip()

    errors = {}

    if not ticker:
        errors["ticker"] = "Ticker is required"
    elif len(ticker) > 5:
        errors["ticker"] = "Ticker must be 5 characters or fewer"

    if not transaction_type:
        errors["transaction_type"] = "Transaction type is required"
    elif transaction_type not in ["BUY", "SELL"]:
        errors["transaction_type"] = "Transaction type must be BUY or SELL"

    if not quantity_raw:
        errors["quantity"] = "Quantity is required"

    if not price_raw:
        errors["price"] = "Price is required"

    if not trade_date_raw:
        errors["trade_date"] = "Trade date is required"

    quantity = None
    price = None

    if "quantity" not in errors:
        try:
            quantity = Decimal(quantity_raw)
            if quantity <= 0:
                errors["quantity"] = "Quantity must be greater than 0"
        except InvalidOperation:
            errors["quantity"] = "Quantity must be valid number"

    if "price" not in errors:
        try:
            price = Decimal(price_raw)
            if price <= 0:
                errors["price"] = "Price must be greater than 0"
        except InvalidOperation:
            errors["price"] = "Price must be valid number"

    if "trade_date" not in errors:
        try:
            trade_date = datetime.strptime(trade_date_raw, "%Y-%m-%d").date()

            today = timezone.localdate()

            if trade_date > today:
                errors["trade_date"] = "Transaction date cannot be in the future"
            elif trade_date < MIN_TRADE_DATE:
                errors["trade_date"] = "Transaction date is too far in the past"
        except ValueError:
            errors["trade_date"] = "Trade date must be in YYYY-MM-DD"

    return {
        "errors": errors,
        "ticker": ticker,
        "transaction_type": transaction_type,
        "quantity": quantity,
        "price": price,
        "trade_date": trade_date,
    }


def save_transaction(user, validated_data, equity, portfolio=None):
    """Create and save a Transaction from validated data."""
    transaction = Transaction.objects.create(
        user=user,
        equity=equity,
        portfolio=portfolio,
        transaction_type=validated_data["transaction_type"],
        quantity=validated_data["quantity"],
        price=validated_data["price"],
        trade_date=validated_data["trade_date"],
        currency=equity.currency,
    )

    return transaction


# 3rd helper
def get_or_create_equity_from_ticker(ticker):
    """Fetch existing equity or create one from ASX list ticker."""
    equity = Equity.objects.filter(ticker=ticker).first()

    if equity:
        return equity, None

    if not ASXEquityList.objects.exists():
        return (
            None,
            "The ASX equity list has not been loaded yet. Please import it before creating new equities.",
        )

    asx_entry = ASXEquityList.objects.filter(ticker=ticker).first()
    if asx_entry is None:
        return None, f"{ticker} is not an existing ASX ticker"

    equity = Equity.objects.create(
        ticker=ticker,
        name=asx_entry.name,
        currency="AUD",
        exchange="ASX",
    )
    store_equity_price_history(equity)

    return equity, None


def get_user_portfolio_or_error(user, portfolio_id_raw):
    """Get user's portfolio from raw id or return an error message."""
    if not portfolio_id_raw:
        return None, "Portfolio is required"

    try:
        portfolio_id = int(portfolio_id_raw)
    except (TypeError, ValueError):
        return None, "Invalid portfolio id"

    portfolio = Portfolio.objects.filter(id=portfolio_id, user=user).first()
    if portfolio is None:
        return None, "Portfolio not found for this user"

    return portfolio, None


# 4th helper
# Get existing holding for this portfolio/equity pair, or create a new one with 0 quantity.
def get_or_create_holding(portfolio, equity):
    """Get holding for portfolio/equity, creating one with zero quantity if needed."""
    holding, _ = Holdings.objects.get_or_create(
        portfolio=portfolio,
        equity=equity,
        defaults={"quantity": 0},
    )
    return holding


# 5th helper
# Validates a BUY/SELL transaction against current holdings
# and updates the holding quantity if the transaction is allowed
def validate_and_apply_holding_change(
    portfolio, equity, transaction_type, quantity, trade_date
):
    """Validate whether a holding change is allowed for this transaction."""
    holding = get_or_create_holding(portfolio, equity)

    if transaction_type == "BUY":
        return None

    if transaction_type == "SELL":
        if holding.quantity < int(quantity):
            return "Not enough shares available to sell."

        if not has_enough_shares_by_trade_date(portfolio, equity, quantity, trade_date):
            return "Sell date cannot be earlier than the available bought shares."

        return None

    return "Invalid transaction type."


##6th helper
# Checks whether a SELL transaction is valid based on cumulative shares
# up to and including the given trade date.
def has_enough_shares_by_trade_date(portfolio, equity, sell_quantity, trade_date):
    """Check if enough shares existed by trade date for a sell."""
    transactions = Transaction.objects.filter(
        portfolio=portfolio,
        equity=equity,
        trade_date__lte=trade_date,
    )

    shares = 0

    for transaction in transactions:
        if transaction.transaction_type == "BUY":
            shares += int(transaction.quantity)
        elif transaction.transaction_type == "SELL":
            shares -= int(transaction.quantity)

    return shares >= int(sell_quantity)


##7th helper
# Matches a SELL transaction to earlier BUY lots using FIFO.
def apply_fifo_sell(
    portfolio, equity, sell_quantity, sell_trade_date, sell_transaction
):
    """Match a sell transaction against buys using FIFO and save matches."""
    buy_lots = Transaction.objects.filter(
        portfolio=portfolio,
        equity=equity,
        transaction_type="BUY",
        trade_date__lte=sell_trade_date,
        remaining_quantity__gt=0,
    ).order_by("trade_date", "id")

    quantity_to_match = int(sell_quantity)
    matches = []

    for buy in buy_lots:
        if quantity_to_match <= 0:
            break

        available = buy.remaining_quantity

        if available <= quantity_to_match:
            matched = available
        else:
            matched = quantity_to_match

        matches.append((buy, matched))
        quantity_to_match -= matched

    if quantity_to_match > 0:
        return "Not enough FIFO buy lots available to sell."

    for buy, matched in matches:
        buy.remaining_quantity -= matched
        buy.save(update_fields=["remaining_quantity"])

        TransactionMatch.objects.create(
            buy_transaction=buy,
            sell_transaction=sell_transaction,
            matched_quantity=matched,
        )

    return None


# Tests if a transaction would result in negative holdings.
def validate_positive_holdings(holding, quantity_change):
    """Reject changes that would make holdings negative, otherwise apply change."""
    if holding.quantity + quantity_change < 0:
        return "Cannot delete this transaction as it would result in negative holdings."

    holding.quantity += quantity_change
    holding.save()

    return None


def get_user_profile(user):
    """Get or create a profile row for the given user."""
    profile, _ = Profile.objects.get_or_create(
        user=user,
        defaults={
            "country": "Australia",
            "currency": "AUD",
        },
    )
    return profile


# Ensures that if the user no longer holds an equity in any portfolio, it gets removed from their profile
def remove_unused_equity(user, equity):
    """Remove equity from profile if user no longer holds it anywhere."""
    still_holds = Holdings.objects.filter(portfolio__user=user, equity=equity).exists()
    if not still_holds:
        get_user_profile(user).equities.remove(equity)


# Checks if adding a new equity would go over the user's subscription limit
def check_user_equity_limit(user, equity):
    """Check if adding this equity would exceed the user's plan limit."""
    profile = get_user_profile(user)

    # Already tracking this equity so no limit issue
    if profile.equities.filter(id=equity.id).exists():
        return None

    try:
        stripe_customer = StripeCustomer.objects.get(user=user)
        max_equities = stripe_customer.subscription.max_equities
    except StripeCustomer.DoesNotExist:
        max_equities = FREE_MAX_EQUITIES

    if max_equities != UNLIMITED_EQUITIES and profile.equities.count() >= max_equities:
        return "Equity limit reached"

    return None


# Checks if the CSV tickers would exceed the user's equity limit
def check_csv_equity_limit(user, csv_rows):
    """Check if CSV upload would exceed the user's equity limit."""
    profile = get_user_profile(user)

    try:
        stripe_customer = StripeCustomer.objects.get(user=user)
        max_equities = stripe_customer.subscription.max_equities
    except StripeCustomer.DoesNotExist:
        max_equities = FREE_MAX_EQUITIES

    # Gather all unique tickers from the CSV
    csv_tickers = set()
    for row in csv_rows:
        ticker = row.get("ticker")
        if ticker is not None:
            ticker = ticker.strip().upper()
            if ticker != "":
                csv_tickers.add(ticker)

    # Figure out how many are brand new (not already tracked)
    already_tracked = set()
    for eq in profile.equities.filter(ticker__in=csv_tickers):
        already_tracked.add(eq.ticker)

    new_count = 0
    for ticker in csv_tickers:
        if ticker not in already_tracked:
            new_count += 1

    if (
        max_equities != UNLIMITED_EQUITIES
        and (profile.equities.count() + new_count) > max_equities
    ):
        return "Adding these equities would exceed your allowed limit."

    return None


# Reads and validates an uploaded CSV file, returns the rows or an error
def validate_csv_file(uploaded_file):
    """Validate uploaded CSV file structure and return parsed rows."""
    if not uploaded_file:
        return None, "Please choose a CSV file to upload."

    if not uploaded_file.name.endswith(".csv"):
        return None, "Only CSV files can be uploaded."

    try:
        decoded = uploaded_file.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(decoded))
    except Exception:
        return None, "Failed to read CSV file."

    expected_headers = ["ticker", "transaction_type", "quantity", "price", "trade_date"]
    if reader.fieldnames != expected_headers:
        return None, f"Invalid CSV format. Expected headers: {expected_headers}"

    return list(reader), None


# Processes a single CSV row - validates, checks holdings, creates the transaction
def process_csv_row(user, portfolio, row):
    """Validate and create one transaction from a CSV row."""
    result = validate_transaction_data_helper(
        row.get("ticker"),
        row.get("transaction_type"),
        row.get("quantity"),
        row.get("price"),
        row.get("trade_date"),
    )

    if result["errors"]:
        return None, result["errors"]

    equity, equity_error = get_or_create_equity_from_ticker(result["ticker"])
    if equity_error:
        return None, {"ticker": equity_error}

    # Now THIS should check that no other portfolio currently contains this equity
    portfolio_conflict = check_equity_in_other_portfolio(user, equity, portfolio)
    if portfolio_conflict:
        return None, {"ticker": portfolio_conflict}

    holding_error = validate_and_apply_holding_change(
        portfolio=portfolio,
        equity=equity,
        transaction_type=result["transaction_type"],
        quantity=result["quantity"],
        trade_date=result["trade_date"],
    )
    if holding_error:
        return None, {"quantity": holding_error}

    if result["transaction_type"] == "BUY":
        transaction, error = create_buy_transaction(user, portfolio, equity, result)
    else:
        transaction, error = create_sell_transaction(user, portfolio, equity, result)

    if error:
        return None, {"quantity": error}

    return transaction, None


def create_buy_transaction(user, portfolio, equity, result):
    """Create a BUY transaction and update holding quantity."""
    transaction = save_transaction(
        user=user,
        validated_data=result,
        equity=equity,
        portfolio=portfolio,
    )

    transaction.remaining_quantity = transaction.quantity
    transaction.save(update_fields=["remaining_quantity"])

    holding = get_or_create_holding(portfolio, equity)
    holding.quantity += int(result["quantity"])
    holding.save()

    return transaction, None


def create_sell_transaction(user, portfolio, equity, result):
    """Create a SELL transaction, apply FIFO matching, and update holdings."""
    transaction = save_transaction(
        user=user,
        validated_data=result,
        equity=equity,
        portfolio=portfolio,
    )

    fifo_error = apply_fifo_sell(
        portfolio=portfolio,
        equity=equity,
        sell_quantity=result["quantity"],
        sell_trade_date=result["trade_date"],
        sell_transaction=transaction,
    )

    if fifo_error:
        transaction.delete()
        return None, fifo_error

    holding = get_or_create_holding(portfolio, equity)
    holding.quantity -= int(result["quantity"])
    holding.save()

    return transaction, None


# helper that checks whether the equity has already been included in other portoflio
# to make sure that one equity can only be assigned in ont portfolio
def check_equity_in_other_portfolio(user, equity, portfolio):
    """Check whether this equity is already held in another user portfolio."""
    # search through Holdings where the portfolio belongs to this user, the equity
    # is the ticker we're trying to add and the quantity > 0
    existing_holding = (
        Holdings.objects.select_related("portfolio")
        .filter(
            portfolio__user=user,
            equity=equity,
            quantity__gt=0,
        )
        .exclude(portfolio=portfolio)
        .first()
    )

    # Such a holding as been found, we return an error
    if existing_holding:
        return (
            f"{equity.ticker} is already held in {existing_holding.portfolio.name}. "
            "An equity can only belong to one portfolio."
        )
    return None
