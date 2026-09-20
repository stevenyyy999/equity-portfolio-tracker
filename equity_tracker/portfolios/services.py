from currency.utils import currency_conversion
from transactions.models import EquityPrice


# function calculates total portfolio value. Iterates through each equity and current holdings
def get_portfolio_data(tickers, amounts, display_currency="AUD"):
    """Calculate latest prices, per equity totals, and portfolio total value."""
    prices = []
    equity_totals = []

    for i, ticker in enumerate(tickers):
        latest_price = (
            EquityPrice.objects.filter(equity__ticker=ticker).order_by("-date").first()
        )

        if latest_price is None:
            price = 0
        else:
            price = float(
                currency_conversion(
                    latest_price.closing_price,
                    "AUD",
                    display_currency,
                )
            )

        prices.append(price)
        equity_totals.append(price * amounts[i])

    total_portfolio_value = sum(equity_totals)

    return {
        "tickers": tickers,
        "prices": prices,
        "equity_totals": equity_totals,
        "total_portfolio_value": total_portfolio_value,
    }
