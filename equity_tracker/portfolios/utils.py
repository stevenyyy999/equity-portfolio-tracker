from decimal import ROUND_HALF_UP, Decimal

from currency.utils import (
    currency_conversion,
    format_currency,
    format_signed_currency,
)
from django.shortcuts import render
from django.utils import timezone
from transactions.models import EquityPrice, Transaction, TransactionMatch

from .models import Holdings

####################
#
#   HELPER FUNCTIONS
#
####################


def get_latest_saved_closing_price(equity):
    """Return the latest stored closing price for an equity."""
    latest_price = EquityPrice.objects.filter(equity=equity).order_by("-date").first()
    return latest_price.closing_price if latest_price else None


def get_holding_cost_basis(portfolio, equity):
    """Calculate remaining cost basis for open buy lots of an equity."""
    buy_lots = Transaction.objects.filter(
        portfolio=portfolio,
        equity=equity,
        transaction_type="BUY",
        remaining_quantity__gt=0,
    )

    total_cost = Decimal("0.00")

    for buy in buy_lots:
        total_cost += Decimal(buy.remaining_quantity) * buy.price

    return total_cost


def get_holding_unrealised_pnl(holding):
    """Calculate unrealised gain/loss values for one holding."""
    latest_price = get_latest_saved_closing_price(holding.equity)

    if latest_price is None:
        return {
            "current_price": None,
            "current_value": None,
            "cost_basis": None,
            "unrealised_pnl": None,
        }

    current_value = Decimal(holding.quantity) * latest_price
    cost_basis = get_holding_cost_basis(holding.portfolio, holding.equity)
    unrealised_pnl = current_value - cost_basis

    return {
        "current_price": latest_price,
        "current_value": current_value,
        "cost_basis": cost_basis,
        "unrealised_pnl": unrealised_pnl,
    }


def get_portfolio_realised_pnl(portfolio):
    """Sum realised PnL from matched buy/sell transactions in a portfolio."""
    matches = TransactionMatch.objects.filter(
        sell_transaction__portfolio=portfolio
    ).select_related("buy_transaction", "sell_transaction")

    total_realised = Decimal("0.00")

    for match in matches:
        buy_price = match.buy_transaction.price
        sell_price = match.sell_transaction.price
        qty = Decimal(match.matched_quantity)

        total_realised += qty * (sell_price - buy_price)

    return total_realised


def _convert_from_aud(amount, display_currency="AUD"):
    """Convert stored AUD value into the user's display currency."""
    return currency_conversion(amount, "AUD", display_currency)


def _format_closing_price(closing_price, display_currency="AUD"):
    """Format a closing price value for display."""
    return format_currency(closing_price, display_currency)


def _format_gain_display(gain_total, gain_percent, display_currency="AUD"):
    """Format gain/loss amount and percent for the UI."""
    absolute_percent = abs(Decimal(str(gain_percent))).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    arrow = "\u25b2" if gain_total >= 0 else "\u25bc"
    return (
        f"{format_signed_currency(gain_total, display_currency)} "
        f"({absolute_percent}% {arrow})"
    )


# Helper to set the value of the gain/loss field
def _build_trade_gain_state(trade, closing_price_state, display_currency="AUD"):
    """Build gain/loss display state for a single trade row."""
    # No equity somehow
    if trade.equity_id is None:
        return {
            "display": "Error",
            "class": "text-gray-500",
        }

    # SELL should use realised gain from matched buy lots
    # SELL absolute gain is the difference between the sell price and the matched buy price
    # SELL percentage gain is the absolute gain divided by the matched cost total
    if trade.transaction_type == "SELL":
        sell_matches = TransactionMatch.objects.filter(
            sell_transaction=trade
        ).select_related("buy_transaction")

        if not sell_matches.exists():
            return {
                "display": "Error",
                "class": "text-gray-500",
            }

        realised_gain_total = Decimal("0.00")
        matched_cost_total = Decimal("0.00")

        for match in sell_matches:
            matched_qty = Decimal(str(match.matched_quantity))
            buy_price = _convert_from_aud(match.buy_transaction.price, display_currency)
            sell_price = _convert_from_aud(trade.price, display_currency)
            realised_gain_total += (sell_price - buy_price) * matched_qty
            matched_cost_total += buy_price * matched_qty

        realised_gain_total = realised_gain_total.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        matched_cost_total = matched_cost_total.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        if matched_cost_total == 0:
            realised_gain_percent = Decimal("0.00")
        else:
            realised_gain_percent = (
                (realised_gain_total / matched_cost_total) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if realised_gain_total >= 0:
            class_name = "text-green-500"
        else:
            class_name = "text-red-500"

        return {
            "display": _format_gain_display(
                realised_gain_total,
                realised_gain_percent,
                display_currency,
            ),
            "class": class_name,
            "matched_cost_total": matched_cost_total,
        }

    # No closing price or waiting for closing price
    if closing_price_state["closing_price"] is None:
        if closing_price_state["needs_refresh"]:
            display_text = "Retrieving"
        else:
            display_text = "Error"

        return {
            "display": display_text,
            "class": "text-gray-500",
        }

    # Gain vs latest close:
    # - BUY: gain% = (close / trade_price) - 1
    latest_closing_price = Decimal(str(closing_price_state["closing_price"]))
    trade_price = _convert_from_aud(trade.price, display_currency)
    qty = Decimal(str(abs(int(trade.quantity))))
    price_diff = latest_closing_price - trade_price
    gain_total = (price_diff * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if trade_price == 0:
        gain_percent = Decimal("0.00")
    else:
        gain_percent = (
            ((latest_closing_price / trade_price) - Decimal("1")) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if gain_total >= 0:
        class_name = "text-green-500"
    else:
        class_name = "text-red-500"

    return {
        "display": _format_gain_display(gain_total, gain_percent, display_currency),
        "class": class_name,
    }


# Gets the closing price
def get_closing_price_state(equity, display_currency="AUD"):
    """Return frontend closing price state for an equity row."""
    # Find latest saved local price
    latest_saved_price = (
        EquityPrice.objects.filter(equity=equity).order_by("-date").first()
    )
    current_time = timezone.localtime()
    today = timezone.localdate()
    is_after_refresh_time = current_time.hour >= 18

    # If the latest price is outdated, update and return new
    if latest_saved_price is not None and (
        latest_saved_price.date == today or not is_after_refresh_time
    ):
        display_closing_price = _convert_from_aud(
            latest_saved_price.closing_price,
            display_currency,
        )
        return {
            "display": _format_closing_price(display_closing_price, display_currency),
            "needs_refresh": False,
            "closing_price": display_closing_price,
        }

    # Unable to fetch any price, likely does not exist, so display retrieving status to try again later
    return {
        "display": "Retrieving",
        "needs_refresh": True,
        "closing_price": None,
    }


def get_average_buy_price(portfolio, equity):
    """Calculate average buy price for all BUY trades of an equity."""
    buy_transactions = portfolio.transactions.filter(
        equity=equity,
        transaction_type="BUY",
    )

    total_quantity = 0
    total_cost = Decimal("0")

    for transaction in buy_transactions:
        quantity = int(transaction.quantity)
        total_quantity += quantity
        total_cost += Decimal(str(transaction.price)) * quantity

    if total_quantity == 0:
        return Decimal("0.00")

    return total_cost / Decimal(str(total_quantity))


def render_create_portfolio_form(request, *, errors=None, form_data=None, status=200):
    """Render the create portfolio form"""
    return render(
        request,
        "create_portfolio.html",
        {
            "errors": errors or {},
            "form_data": form_data or {},
        },
        status=status,
    )


# Rebuilds the holding quantity for one equity in one portfolio.
def rebuild_holding_for_equity(portfolio, equity):
    """Rebuilds the holding quantity for one equity in one portfolio."""
    transactions = Transaction.objects.filter(
        portfolio=portfolio,
        equity=equity,
    )

    quantity = 0
    for transaction in transactions:
        if transaction.transaction_type == "BUY":
            quantity += int(transaction.quantity)
        elif transaction.transaction_type == "SELL":
            quantity -= int(transaction.quantity)

    holding = Holdings.objects.filter(portfolio=portfolio, equity=equity).first()

    if quantity <= 0:
        if holding:
            holding.delete()
        return

    if holding is None:
        Holdings.objects.create(
            portfolio=portfolio,
            equity=equity,
            quantity=quantity,
        )
    else:
        holding.quantity = quantity
        holding.save(update_fields=["quantity"])


def get_portfolio_total_invested_cost(portfolio):
    """Return total invested cost across sold shares and current holdings"""
    matches = TransactionMatch.objects.filter(
        sell_transaction__portfolio=portfolio
    ).select_related("buy_transaction")

    realised_cost = Decimal("0.00")
    for match in matches:
        realised_cost += Decimal(match.matched_quantity) * match.buy_transaction.price

    remaining_cost = Decimal("0.00")
    holdings = portfolio.holdings.select_related("equity").filter(quantity__gt=0)
    for holding in holdings:
        remaining_cost += get_holding_cost_basis(holding.portfolio, holding.equity)

    return realised_cost + remaining_cost
