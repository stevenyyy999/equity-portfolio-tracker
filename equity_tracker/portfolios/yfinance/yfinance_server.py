import time
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf
from currency.utils import currency_conversion
from django.utils import timezone
from transactions.models import Equity, EquityPrice
from yfinance.exceptions import YFRateLimitError

from portfolios.models import PriceUpdateLog


def get_last_close_price(ticker_sym):
    """Fetch most recent close price/date from yfinance API for a ticker."""

    # Appends .AX to the ticker if needed
    ticker = ticker_sym
    if not ticker_sym.endswith(".AX"):
        ticker = f"{ticker_sym}.AX"

    # Create the initial object for the Ticker to pull data from API
    ticker_obj = yf.Ticker(ticker)
    try:
        # Creates a yfinance.Ticker object containing history from 5 days ago
        history = ticker_obj.history(period="5d")
        if not history.empty:
            # Takes the most recent closing price from the history
            close_price = Decimal(str(history.iloc[-1]["Close"]))
            close_date = history.index[-1].date()

            # OK
            return close_price, close_date, None
    except YFRateLimitError:
        # If history is rate limited, don't bother trying .info since it will also be
        # rate limited and making extra requests just makes it worse
        print(f"Yahoo Finance rate limited request for (history) {ticker}")
        return None, None, "rate_limited"
    except Exception as e:
        print(f"error fetching price for (history) {ticker}:{e}")

    # If there is error with fetching through history way then you can also try fetch through info
    # But this is current price (scheduled to fetch at 6pm so it suppose to fetch the close price anyways)
    try:
        price = ticker_obj.info.get("regularMarketPrice")
        if price is not None:
            print(f"[{ticker_sym}] Got price via info: {price}")
            return Decimal(str(price)), date.today(), None
    except YFRateLimitError:
        print(f"Yahoo Finance rate limited request for (info) {ticker}")
        return None, None, "rate_limited"

    except Exception as e:
        print(f"error fetching price for (info) {ticker}:{e}")
        return None, None, "fetch_failed"
    return None, None, "fetch error"


# Takes in an equity object and updates the closing price locally
def update_equity_price(equity):
    """Update one equity's latest close price and return status details."""
    latest_saved_price = (
        EquityPrice.objects.filter(equity=equity).order_by("-date").first()
    )
    today = timezone.localdate()

    # If there already is an EquityPrice saved for today, skip and don't call yfinance
    if latest_saved_price and latest_saved_price.date == today:
        PriceUpdateLog.objects.create(
            ticker=equity.ticker,
            status="SKIPPED",
            message="Already up to date with a closing price for today.",
        )
        return {
            "status": "skipped",
            "reason": "already_up_to_date",
            "message": f"{equity.ticker} already has a closing price saved for today.",
            "date": latest_saved_price.date.isoformat(),
            "closing_price": str(latest_saved_price.closing_price),
        }

    # Try to get the closing price, retry a few times if we get rate limited
    max_retries = 3
    price = None
    close_date = None
    error_reason = None

    for attempt in range(max_retries):
        price, close_date, error_reason = get_last_close_price(equity.ticker)

        if error_reason != "rate_limited":
            break

        # Wait a bit before retrying so Yahoo Finance rate limit can cool down
        if attempt < max_retries - 1:
            wait_time = 3 * (2**attempt)
            print(
                f"Rate limited for {equity.ticker}, retrying in {wait_time}s "
                f"(attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(wait_time)

    # Error handling
    if price is None or close_date is None:
        if error_reason == "rate_limited":
            # If yfinance rate limit then use latest saved price
            if latest_saved_price is not None:
                print(
                    f"Rate limited for {equity.ticker}, "
                    f"falling back to saved price from {latest_saved_price.date}"
                )
                PriceUpdateLog.objects.create(
                    ticker=equity.ticker,
                    status="SKIPPED",
                    message=(
                        f"Rate limited, used cached price from "
                        f"{latest_saved_price.date}: {latest_saved_price.closing_price}"
                    ),
                )
                return {
                    "status": "skipped",
                    "reason": "rate_limited_using_cached",
                    "message": f"Rate limited, showing last saved price for {equity.ticker}.",
                    "date": latest_saved_price.date.isoformat(),
                    "closing_price": str(latest_saved_price.closing_price),
                }

            PriceUpdateLog.objects.create(
                ticker=equity.ticker,
                status="FAILED",
                message="Yahoo Finance rate limited this request.",
            )
            return {
                "status": "failed",
                "reason": "rate_limited",
                "message": "Yahoo Finance rate limited this request. Please wait a bit and try again.",
            }

        if error_reason == "no_data":
            PriceUpdateLog.objects.create(
                ticker=equity.ticker,
                status="FAILED",
                message=f"No recent closing price data was returned for {equity.ticker}.",
            )
            return {
                "status": "failed",
                "reason": "no_data",
                "message": f"No recent closing price data was returned for {equity.ticker}.",
            }

        PriceUpdateLog.objects.create(
            ticker=equity.ticker,
            status="FAILED",
            message=f"Failed to fetch a closing price for {equity.ticker}.",
        )
        return {
            "status": "failed",
            "reason": "fetch_failed",
            "message": f"Failed to fetch a closing price for {equity.ticker}.",
        }

    # Keep one EquityPrice row per equity per trading day so historical prices remain available.
    obj, created = EquityPrice.objects.update_or_create(
        equity=equity,
        date=close_date,
        defaults={"closing_price": price},
    )

    PriceUpdateLog.objects.create(
        ticker=equity.ticker,
        status="SUCCESS",
        message=f"Closing price saved: {obj.closing_price} on {obj.date}",
    )

    return {
        "status": "created" if created else "updated",
        "reason": None,
        "message": f"{equity.ticker} closing price saved successfully.",
        "date": obj.date.isoformat(),
        "closing_price": str(obj.closing_price),
    }


# Grabs all equities in the database and update their closing prices
def update_all_equity_prices():
    """Update all equities and return an aggregate status summary."""
    # Grabs all equities
    equities = Equity.objects.all()

    # Set counters
    created = 0
    updated = 0
    skipped = 0
    failed = 0
    failed_tickers = []

    # oop through each equity and update their closing price
    for equity in equities:
        try:
            result = update_equity_price(equity)
            if result["status"] == "created":
                created += 1
            elif result["status"] == "updated":
                updated += 1
            elif result["status"] == "skipped":
                skipped += 1
            elif result["status"] == "failed":
                failed += 1
                failed_tickers.append(equity.ticker)

        except Exception as e:
            # If an unexpected error occur, consider it as a failed equity update and increment relevant count
            print(f"error updating price for {equity.ticker}: {e}")
            failed += 1
            failed_tickers.append(equity.ticker)

        # Wait two seconds between equities to reduce chances of being rate limited
        time.sleep(2)

    cleanup_prices = delete_old_equity_prices()

    # OK, return summary
    return {
        "total": equities.count(),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "failed_tickers": failed_tickers,
        "deleted_old_prices": cleanup_prices["deleted"],
    }


# Get equity price over a time-frame passed in as a variable
# and returns close_price, close_date, and error_reason
def get_equity_price_history(ticker_sym, period="1 month", display_currency="AUD"):
    """Fetch historical prices for a period and convert to display currency."""

    # options
    VALID_PERIODS = {
        "5 days": "5d",
        "1 month": "1mo",
        "6 months": "6mo",
        "1 year": "1y",
    }

    yf_period = VALID_PERIODS[period]

    # Appends .AX to the ticker if needed
    ticker = ticker_sym
    if not ticker_sym.endswith(".AX"):
        ticker = f"{ticker_sym}.AX"

    # Create the initial object for the Ticker to pull data from API
    ticker_obj = yf.Ticker(ticker)
    try:
        # Creates a yfinance.Ticker object for the selected range
        history = ticker_obj.history(period=yf_period)
        if history.empty:
            return None, "no_data"

        price_list = []

        for index, row in history.iterrows():
            closing_price = currency_conversion(
                row["Close"],
                "AUD",
                display_currency,
            )
            price_list.append(
                {
                    "date": index.date().isoformat(),
                    "closing_price": float(closing_price),
                }
            )
        return price_list, None
    except YFRateLimitError:
        # If history is rate limited, don't bother trying .info since it will also be
        # rate limited and making extra requests just makes it worse
        print(f"Yahoo Finance rate limited request for (history) {ticker}")
        return None, "rate_limited"
    except Exception as e:
        print(f"error fetching price for (history) {ticker}:{e}")
        return None, "fetch failed"


def store_equity_price_history(equity):
    """Store up to one year of historical daily prices for an equity."""
    saved = 0

    ticker = equity.ticker
    if not ticker.endswith(".AX"):
        ticker = f"{ticker}.AX"

    ticker_obj = yf.Ticker(ticker)

    try:
        history = ticker_obj.history(period="1y", interval="1d")

        if history.empty:
            PriceUpdateLog.objects.create(
                ticker=equity.ticker,
                status="FAILED",
                message=f"No historical price data return for {equity.ticker}.",
            )
            return {
                "status": "failed",
                "reason": "no_data",
                "saved": 0,
                "message": f"No historical price data return for {equity.ticker}.",
            }

        history = history.dropna(subset=["Close"])

        for index, row in history.iterrows():
            close_price = row["Close"]

            if close_price is None:
                continue

            clean_price = Decimal(str(close_price)).quantize(Decimal("0.01"))

            EquityPrice.objects.update_or_create(
                equity=equity,
                date=index.date(),
                defaults={"closing_price": clean_price},
            )
            saved += 1

        PriceUpdateLog.objects.create(
            ticker=equity.ticker,
            status="SUCCESS",
            message=f"Stored {saved} historical priced for{equity.ticker}.",
        )
        return {
            "status": "success",
            "reason": None,
            "saved": saved,
            "message": f"Stored {saved} historical priced for{equity.ticker}.",
        }

    except YFRateLimitError:
        PriceUpdateLog.objects.create(
            ticker=equity.ticker,
            status="FAILED",
            message="Yahoo Finance rate limited this request",
        )
        return {
            "status": "failed",
            "reason": "rate_limited",
            "saved": saved,
            "message": "Yahoo Finance rate limited this request.",
        }
    except Exception as e:
        print(f"error ftching historifal priced for {equity.ticker}: {e}")

        PriceUpdateLog.objects.create(
            ticker=equity.ticker,
            status="FAILED",
            message=f"error ftching historifal priced for {equity.ticker}",
        )
        return {
            "status": "failed",
            "reason": "fetch_failed",
            "saved": saved,
            "message": f"error ftching historifal priced for {equity.ticker}",
        }


def store_all_price_history():
    """Store historical prices for all equities and return summary."""
    equities = Equity.objects.all()

    success = 0
    failed = 0
    total_saved = 0
    failed_tickers = []

    for equity in equities:
        try:
            result = store_equity_price_history(equity)

            if result["status"] == "success":
                success += 1
                total_saved += result["saved"]
            else:
                failed += 1
                failed_tickers.append(equity.ticker)

        except Exception as e:
            print(f"errpr storing historical price for {equity.ticker}: {e}")
            failed += 1
            failed_tickers.append(equity.ticker)

        time.sleep(2)
    print(
        f"total {equities.count()}, success {success}, failed {failed}, total saved {total_saved}, failed tickers {failed_tickers}"
    )
    return {
        "total": equities.count(),
        "success": success,
        "failed": failed,
        "total_saved": total_saved,
        "failed_tickers": failed_tickers,
    }


def delete_old_equity_prices():
    """Delete EquityPrice rows older than retention window."""
    cutoff_date = timezone.localdate() - timedelta(days=370)

    deleted_count, deleted_details = EquityPrice.objects.filter(
        date__lt=cutoff_date
    ).delete()

    return {
        "deleted": deleted_count,
        "cutoff_date": cutoff_date,
        "details": deleted_details,
    }
