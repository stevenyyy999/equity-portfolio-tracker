import csv

from currency.utils import currency_conversion, format_currency, get_user_currency
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from portfolios.models import Holdings, Portfolio
from portfolios.utils import get_closing_price_state
from subscriptions.models import FREE_MAX_EQUITIES, UNLIMITED_EQUITIES, StripeCustomer

from .models import ASXEquityList, Transaction
from .utils import (
    check_csv_equity_limit,
    check_equity_in_other_portfolio,
    check_user_equity_limit,
    create_buy_transaction,
    create_sell_transaction,
    get_or_create_equity_from_ticker,
    get_or_create_holding,
    get_user_portfolio_or_error,
    get_user_profile,
    process_csv_row,
    remove_unused_equity,
    validate_and_apply_holding_change,
    validate_csv_file,
    validate_positive_holdings,
    validate_transaction_data_helper,
)


def get_transaction_limit_context(user):
    """Build equity limit context used by transaction forms."""
    profile = get_user_profile(user)
    subscription = profile.subscription

    if subscription is None:
        subscription_name = "free"
        max_equities = FREE_MAX_EQUITIES
    else:
        subscription_name = subscription.name
        max_equities = subscription.max_equities
        if max_equities is None:
            max_equities = (
                FREE_MAX_EQUITIES if subscription_name == "free" else UNLIMITED_EQUITIES
            )

    tracked_tickers = sorted(
        profile.equities.order_by("ticker").values_list("ticker", flat=True)
    )
    limit_reached_for_new_tickers = (
        subscription_name == "free"
        and max_equities != UNLIMITED_EQUITIES
        and len(tracked_tickers) >= max_equities
    )

    return {
        "limit_reached_for_new_tickers": limit_reached_for_new_tickers,
        "tracked_tickers": tracked_tickers,
        "max_equities": max_equities,
    }


def _apply_transaction_history_filters(queryset, ticker, start_date, end_date):
    """Apply ticker/date filters to a transaction queryset."""
    if ticker:
        queryset = queryset.filter(equity__ticker__icontains=ticker)

    if start_date:
        queryset = queryset.filter(trade_date__gte=start_date)

    if end_date:
        queryset = queryset.filter(trade_date__lte=end_date)

    return queryset


def _apply_transaction_history_filters_portfolio(
    queryset, portfolio, ticker, start_date, end_date
):
    """Apply portfolio, ticker, and date filters to transaction history."""
    if portfolio:
        queryset = queryset.filter(portfolio__name__icontains=portfolio)

    if ticker:
        queryset = queryset.filter(equity__ticker__icontains=ticker)

    if start_date:
        queryset = queryset.filter(trade_date__gte=start_date)

    if end_date:
        queryset = queryset.filter(trade_date__lte=end_date)

    return queryset


def render_transaction_form(request, user_portfolio, errors, form_data, equity):
    """Render manual transaction form with dropdown and validation state."""
    portfolios = Portfolio.objects.filter(user=request.user)

    forced_portfolio = None

    # Ticker already belongs to a portfolio, lock portfolio dropdown
    if equity:
        holding = Holdings.objects.filter(
            equity=equity, portfolio__user=request.user
        ).first()
        if holding:
            forced_portfolio = holding.portfolio

    # Inside portfolio page, preselect (does not work i think)
    preselected_portfolio_id = None
    if user_portfolio:
        preselected_portfolio_id = user_portfolio.id
    # the user currently entered portfolio
    elif form_data.get("portfolio_id"):
        preselected_portfolio_id = form_data.get("portfolio_id")
    else:
        # find the default
        default_portfolio = Portfolio.objects.filter(
            user=request.user, is_default=True
        ).first()
        if default_portfolio:
            preselected_portfolio_id = default_portfolio.id

    # make dropdown
    if forced_portfolio:
        # LOCKED because equity already belongs to a portfolio
        dropdown = {
            "portfolios": [forced_portfolio],
            "selected": forced_portfolio.id,
            "locked": True,
        }
    else:
        # NOT locked — user can choose
        dropdown = {
            "portfolios": portfolios,
            "selected": preselected_portfolio_id,
            "locked": False,
        }

    transaction_limit_context = get_transaction_limit_context(request.user)

    return render(
        request,
        "transactions/enter_manual.html",
        {
            "dropdown": dropdown,
            "errors": errors,
            "form_data": form_data,
            **transaction_limit_context,
        },
    )


# def render_csv_upload_form(
#     request,
#     portfolio,
#     errors=None,
#     row_errors=None,
#     created_count=0,
# ):
#     context = {"portfolio": portfolio}
#     if errors:
#         context["errors"] = errors
#     if row_errors:
#         context["row_errors"] = row_errors
#     if created_count:
#         context["created_count"] = created_count
#     return render(request, "transactions/upload_csv.html", context)


@login_required
@require_GET
def ticker_suggestions(request):
    """Return ticker suggestions for autocomplete as JSON."""
    # reads the typed text so far
    query = (request.GET.get("q") or "").strip()

    if not query:
        return JsonResponse({"results": []}, status=200)

    # searches the imported ASXEquityList
    suggestions = list(
        ASXEquityList.objects.filter(ticker__istartswith=query)
        .order_by("ticker")
        .values("ticker", "name")[:10]
    )

    # returns matching tickers as JSON
    return JsonResponse({"results": suggestions}, status=200)


@login_required
def create_transaction(request, portfolio_id=None):
    """Create a manual BUY/SELL transaction from form input."""
    user_portfolio = None
    if portfolio_id:
        user_portfolio = get_object_or_404(
            Portfolio, id=portfolio_id, user=request.user
        )

    if request.method == "GET":
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors={},
            form_data={},
            equity=None,
        )

    # validate fields
    result = validate_transaction_data_helper(
        request.POST.get("ticker"),
        request.POST.get("transaction_type"),
        request.POST.get("quantity"),
        request.POST.get("price"),
        request.POST.get("trade_date"),
    )

    if result["errors"]:
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors=result["errors"],
            form_data=request.POST,
            equity=None,
        )

    # Get or create equity
    equity, equity_error = get_or_create_equity_from_ticker(result["ticker"])
    if equity_error:
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors={"ticker": equity_error},
            form_data=request.POST,
            equity=None,
        )

    # Use the portfolio selected by the user/form, then validate the equity can belong there.
    portfolio, portfolio_error = get_user_portfolio_or_error(
        request.user,
        request.POST.get("portfolio_id"),
    )
    if portfolio_error:
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors={"portfolio": portfolio_error},
            form_data=request.POST,
            equity=equity,
        )

    # Check if equity is in another portfolio
    portfolio_conflict = check_equity_in_other_portfolio(
        request.user, equity, portfolio
    )
    if portfolio_conflict:
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors={"ticker": portfolio_conflict},
            form_data=request.POST,
            equity=equity,
        )

    # Check equity limit
    equity_limit_error = check_user_equity_limit(request.user, equity)
    if equity_limit_error:
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors={
                "ticker": (
                    "You have reached the Free plan limit. "
                    "You can still add trades for your existing tracked equities, "
                    "but you need to upgrade to add a new ticker."
                )
            },
            form_data=request.POST,
            equity=equity,
        )

    # Validate holdings
    holding_error = validate_and_apply_holding_change(
        portfolio=portfolio,
        equity=equity,
        transaction_type=result["transaction_type"],
        quantity=result["quantity"],
        trade_date=result["trade_date"],
    )
    if holding_error:
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors={"quantity": holding_error},
            form_data=request.POST,
            equity=equity,
        )

    # Create transaction
    if result["transaction_type"] == "BUY":
        transaction, error = create_buy_transaction(
            request.user, portfolio, equity, result
        )
    else:
        transaction, error = create_sell_transaction(
            request.user, portfolio, equity, result
        )

    if error:
        return render_transaction_form(
            request,
            user_portfolio=user_portfolio,
            errors={"quantity": error},
            form_data=request.POST,
            equity=equity,
        )

    get_user_profile(request.user).equities.add(equity)

    messages.success(
        request,
        f"Transaction added successfully: {transaction.transaction_type} {transaction.quantity} shares of {transaction.equity.ticker}.",
        extra_tags="transaction-success",
    )

    return redirect("portfolio_detail", portfolio_id=portfolio.id)


def build_csv_dropdown_state(user, user_portfolio, forced_portfolio=None):
    """Build dropdown state for CSV upload portfolio selection."""
    portfolios = Portfolio.objects.filter(user=user)

    if forced_portfolio:
        return {
            "portfolios": [forced_portfolio],
            "selected": forced_portfolio.id,
            "locked": True,
        }

    if user_portfolio:
        return {
            "portfolios": portfolios,
            "selected": user_portfolio.id,
            "locked": False,
        }

    default_portfolio = Portfolio.objects.filter(user=user, is_default=True).first()

    return {
        "portfolios": portfolios,
        "selected": default_portfolio.id if default_portfolio else None,
        "locked": False,
    }


def _build_equity_limit_state(user, ticker, *, user_already_holds=False):
    """Return equity limit flags for ticker checks."""
    # user already holds equity, so we return false because no new equities are added
    if user_already_holds:
        return {
            "is_new_equity": False,
            "equity_limit_reached": False,
            "equity_limit_error": "",
        }

    # otherwise, lets check if the user contains the equity just in case
    profile = get_user_profile(user)
    if profile.equities.filter(ticker=ticker).exists():
        return {
            "is_new_equity": False,
            "equity_limit_reached": False,
            "equity_limit_error": "",
        }

    # the equity is new, lets grab the user from stripe and get their max allowed equities
    try:
        stripe_customer = StripeCustomer.objects.get(user=user)
        max_equities = (
            stripe_customer.subscription.max_equities
            if stripe_customer.subscription
            # if the user has a subscription, use the subscription's limit, otherwise default to FREE's limit
            else FREE_MAX_EQUITIES
        )
    except StripeCustomer.DoesNotExist:
        max_equities = FREE_MAX_EQUITIES

    # boolean to chec if the limit is reached or not
    limit_reached = (
        max_equities != UNLIMITED_EQUITIES and profile.equities.count() >= max_equities
    )

    return {
        "is_new_equity": True,
        "equity_limit_reached": limit_reached,
        "equity_limit_error": "Equity limit reached" if limit_reached else "",
    }


@login_required
@require_GET
def check_ticker_portfolio(request):
    """Check whether a ticker already exists in one of the user's portfolios."""
    ticker = request.GET.get("ticker", "").strip().upper()

    if not ticker:
        return JsonResponse({"exists": False})

    equity = ASXEquityList.objects.filter(ticker=ticker).first()
    if not equity:
        return JsonResponse({"exists": False})

    holding = (
        Holdings.objects.filter(equity__ticker=ticker, portfolio__user=request.user)
        .select_related("portfolio")
        .first()
    )

    if holding:
        return JsonResponse(
            {
                "exists": True,
                "portfolio_id": holding.portfolio.id,
                "portfolio_name": holding.portfolio.name,
                # ** unpacks the dictionary
                **_build_equity_limit_state(
                    request.user,
                    ticker,
                    user_already_holds=True,
                ),
            }
        )

    return JsonResponse(
        {
            "exists": False,
            **_build_equity_limit_state(request.user, ticker),
        }
    )


@login_required
def upload_transactions_csv(request, portfolio_id=None):
    """Upload CSV transactions, validate rows, and create valid records."""
    user_portfolio = None
    if portfolio_id:
        user_portfolio = get_object_or_404(
            Portfolio, id=portfolio_id, user=request.user
        )

    if request.method == "GET":
        return render(
            request,
            "transactions/upload_csv.html",
            {
                "portfolio": user_portfolio,
                "dropdown": build_csv_dropdown_state(request.user, user_portfolio),
            },
        )

    # Validate CSV file
    csv_rows, file_error = validate_csv_file(request.FILES.get("file"))
    if file_error:
        return render(
            request,
            "transactions/upload_csv.html",
            {
                "portfolio": user_portfolio,
                "error_message": file_error,
                "dropdown": build_csv_dropdown_state(request.user, user_portfolio),
            },
        )

    # Use the selected portfolio; each row still validates that its equity can belong there.
    portfolio, portfolio_error = get_user_portfolio_or_error(
        request.user,
        request.POST.get("portfolio_id"),
    )
    if portfolio_error:
        return render(
            request,
            "transactions/upload_csv.html",
            {
                "portfolio": user_portfolio,
                "error_message": portfolio_error,
                "dropdown": build_csv_dropdown_state(request.user, user_portfolio),
            },
        )

    # Check equity limit
    limit_error = check_csv_equity_limit(request.user, csv_rows)
    if limit_error:
        return render(
            request,
            "transactions/upload_csv.html",
            {
                "portfolio": user_portfolio,
                "error_message": limit_error,
                "dropdown": build_csv_dropdown_state(request.user, user_portfolio),
            },
        )

    created = []
    row_errors = []

    # Process each row
    for row_num, row in enumerate(csv_rows, start=2):
        transaction, errors = process_csv_row(request.user, portfolio, row)

        if errors:
            row_errors.append({"row": row_num, "errors": errors})
            continue

        get_user_profile(request.user).equities.add(transaction.equity)

        created.append(
            {
                "id": transaction.id,
                "ticker": transaction.equity.ticker,
                "transaction_type": transaction.transaction_type,
                "quantity": str(transaction.quantity),
                "price": str(transaction.price),
                "trade_date": str(transaction.trade_date),
            }
        )

    # If any has errors, display
    if row_errors:
        return render(
            request,
            "transactions/upload_csv.html",
            {
                "portfolio": user_portfolio,
                "dropdown": build_csv_dropdown_state(request.user, user_portfolio),
                "row_errors": row_errors,
                "created_count": len(created),
            },
        )

    return redirect("portfolio_detail", portfolio_id=portfolio.id)


@login_required
def transaction_history(request):
    """Render transaction history page with optional filters."""
    display_currency = get_user_currency(request.user)
    user_trades = (
        Transaction.objects.filter(user=request.user)
        .select_related("equity", "portfolio")
        .order_by("-trade_date")
    )
    has_trades = user_trades.exists()

    portfolio = request.GET.get("portfolio")
    ticker = request.GET.get("ticker")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    user_trades = _apply_transaction_history_filters_portfolio(
        user_trades, portfolio, ticker, start_date, end_date
    )

    # value column: sells use trade price, buys use latest close (same idea as portfolio page)
    for transaction in user_trades:
        transaction.price_display_value = currency_conversion(
            transaction.price,
            "AUD",
            display_currency,
        )
        transaction.price_display = format_currency(
            transaction.price_display_value,
            display_currency,
        )
        if transaction.transaction_type == "SELL" or transaction.equity_id is None:
            transaction.value_display = format_currency(
                transaction.price_display_value * transaction.quantity,
                display_currency,
            )
        else:
            cp = get_closing_price_state(transaction.equity, display_currency)
            if cp["closing_price"] is not None:
                transaction.value_display = format_currency(
                    cp["closing_price"] * transaction.quantity,
                    display_currency,
                )
            else:
                transaction.value_display = "Retrieving"

    return render(
        request,
        "transactions/transaction_history.html",
        {
            "has_trades": has_trades,
            "user_trades": user_trades,
            "portfolio": portfolio or "",
            "ticker": ticker or "",
            "start_date": start_date or "",
            "end_date": end_date or "",
            "display_currency": display_currency,
        },
    )


@login_required
def export_transactions_csv(request):
    """Export filtered transaction history to CSV."""
    display_currency = get_user_currency(request.user)
    ticker = request.GET.get("ticker")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    transactions = (
        Transaction.objects.filter(user=request.user)
        .select_related("portfolio", "equity")
        .order_by("-trade_date", "-created_at")
    )
    transactions = _apply_transaction_history_filters(
        transactions, ticker, start_date, end_date
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="transactions_export.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "portfolio",
            "ticker",
            "transaction_type",
            "quantity",
            f"price_{display_currency}",
            "trade_date",
            "currency",
        ]
    )

    for transaction in transactions:
        converted_price = currency_conversion(
            transaction.price,
            "AUD",
            display_currency,
        )
        writer.writerow(
            [
                transaction.portfolio.name if transaction.portfolio else "",
                transaction.equity.ticker if transaction.equity else "",
                transaction.transaction_type,
                transaction.quantity,
                converted_price,
                transaction.trade_date,
                display_currency,
            ]
        )

    return response


@login_required
@require_POST
def delete_transaction_data(request):
    """Delete one transaction and update related holdings safely."""
    user = request.user
    transaction_id_raw = request.POST.get("transaction_id")

    if not transaction_id_raw:
        return JsonResponse({"error": "Missing transaction_id"}, status=400)

    try:
        transaction_id = int(transaction_id_raw)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid transaction_id"}, status=400)

    transaction = get_object_or_404(Transaction, id=transaction_id, user=user)

    quantity = transaction.quantity
    portfolio = transaction.portfolio
    equity = transaction.equity

    # Figure out how the holding changes based on type
    if transaction.transaction_type == "BUY":
        if transaction.sell_matches_from_buy.exists():
            messages.error(
                request,
                "Cannot delete a BUY transaction that has already been matched to a SELL.",
            )
            return redirect(
                request.META.get("HTTP_REFERER")
                or reverse("transactions:transaction_history")
            )

        holding_change = -quantity
    else:
        # Restore remaining_quantity on the matched BUY transactions
        for match in transaction.buy_matches_for_sell.all():
            buy_tx = match.buy_transaction
            buy_tx.remaining_quantity += match.matched_quantity
            buy_tx.save(update_fields=["remaining_quantity"])
        holding_change = quantity

    holding = get_or_create_holding(portfolio, equity)

    holding_error = validate_positive_holdings(holding, holding_change)
    if holding_error:
        return JsonResponse({"error": holding_error}, status=400)

    transaction.delete()

    # If the holding is now empty, delete it
    if holding.quantity == 0:
        holding.delete()

    # If the user doesn't hold this equity in any portfolio anymore, remove it from their profile
    remove_unused_equity(user, equity)

    messages.warning(
        request,
        f"{quantity} Transactions for '{equity.ticker}' traded at '{transaction.trade_date}' deleted successfully.",
        extra_tags="transaction-delete-success",
    )

    return redirect(
        request.META.get("HTTP_REFERER") or reverse("transactions:transaction_history")
    )
