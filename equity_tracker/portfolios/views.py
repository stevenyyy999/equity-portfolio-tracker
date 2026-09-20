import csv
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from currency.utils import (
    currency_conversion,
    format_currency,
    format_signed_currency,
    get_user_currency,
)
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from subscriptions.models import (
    FREE_MAX_PORTFOLIOS,
    StripeCustomer,
    UNLIMITED_PORTFOLIOS,
)
from transactions.models import Equity, EquityPrice, Transaction
from transactions.utils import remove_unused_equity

from .models import Holdings, Portfolio
from .services import get_portfolio_data
from .utils import (
    _build_trade_gain_state,
    _format_closing_price,
    _format_gain_display,
    get_average_buy_price,
    get_closing_price_state,
    get_holding_unrealised_pnl,
    get_portfolio_realised_pnl,
    get_portfolio_total_invested_cost,
    rebuild_holding_for_equity,
    render_create_portfolio_form,
)
from .yfinance.yfinance_server import update_equity_price

PERIOD_TO_DAYS = {
    "5 days": 5,
    "1 month": 30,
    "6 months": 180,
    "1 year": 365,
}


def equity_price_his(request, ticker):
    """See the historical price for the equity."""
    equity = get_object_or_404(Equity, ticker=ticker)
    display_currency = get_user_currency(request.user)
    period = request.GET.get("period", "5 days")
    if period not in {"5 days", "1 month", "6 months", "1 year"}:
        period = "5 days"

    days = PERIOD_TO_DAYS[period]
    cutoff = date.today() - timedelta(days=days)
    price_rows = EquityPrice.objects.filter(
        equity=equity,
        date__gte=cutoff,
    ).order_by("date")
    price_list = [
        {
            "date": price.date.isoformat(),
            "closing_price": float(
                currency_conversion(price.closing_price, "AUD", display_currency)
            ),
        }
        for price in price_rows
    ]

    data = {
        "ticker": equity.ticker,
        "name": equity.name,
        "price": price_list,
    }
    return JsonResponse(data)


def update_equity_view(request, ticker):
    """Manually update and return the latest price for an equity."""
    display_currency = get_user_currency(request.user)
    try:
        equity = Equity.objects.get(ticker=ticker)
    except Equity.DoesNotExist:
        return JsonResponse({"error": "Equity not found"}, status=404)

    result = update_equity_price(equity)

    if result["status"] == "failed":
        status_code = 429 if result["reason"] == "rate_limited" else 500
        return JsonResponse(
            {
                "error": result["message"],
                "reason": result["reason"],
            },
            status=status_code,
        )

    result["closing_price"] = currency_conversion(
        result["closing_price"],
        "AUD",
        display_currency,
    )
    result["display_closing_price"] = _format_closing_price(
        result["closing_price"],
        display_currency,
    )

    portfolio_id = request.GET.get("portfolio_id")
    equity_id = request.GET.get("equity_id")
    if portfolio_id and equity_id:
        portfolio = Portfolio.objects.filter(id=portfolio_id, user=request.user).first()
        holding = Holdings.objects.filter(
            portfolio=portfolio,
            equity_id=equity_id,
        ).first()

        if portfolio and holding:
            average_buy_price = get_average_buy_price(portfolio, equity)
            average_buy_price = currency_conversion(
                average_buy_price,
                "AUD",
                display_currency,
            )
            quantity = Decimal(str(holding.quantity))
            latest_price = Decimal(str(result["closing_price"]))

            if average_buy_price > 0 and quantity > 0:
                gain_total = ((latest_price - average_buy_price) * quantity).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                gain_percent = (
                    ((latest_price - average_buy_price) / average_buy_price)
                    * Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                result["display_gain"] = _format_gain_display(
                    gain_total,
                    gain_percent,
                    display_currency,
                )
                result["gain_class"] = (
                    "text-green-500" if gain_total >= 0 else "text-red-500"
                )
    return JsonResponse(result)


@login_required
@require_GET
def get_user_portfolios(request):
    """Return the current user's portfolios as JSON."""
    portfolios = Portfolio.objects.filter(user=request.user)

    data = [
        {
            "id": portfolio.id,
            "name": portfolio.name,
            "description": portfolio.description,
        }
        for portfolio in portfolios
    ]

    return JsonResponse({"portfolios": data}, status=200)


@login_required
def create_portfolio_page(request):
    """Render the create portfolio page."""
    return render_create_portfolio_form(request)


# Helper function to validate a portfolio's name and description
def _validate_portfolio_fields(name, description, user=None, current_portfolio=None):
    """Validate portfolio name/description and return field errors."""
    errors = {}

    if not name:
        errors["name"] = "Portfolio name is required."
    elif len(name) > Portfolio._meta.get_field("name").max_length:
        errors["name"] = "Portfolio name must be 100 characters or fewer."
    elif user is not None:
        duplicate_names = Portfolio.objects.filter(user=user, name=name)
        if current_portfolio is not None:
            duplicate_names = duplicate_names.exclude(id=current_portfolio.id)

        if duplicate_names.exists():
            errors["name"] = "You already have a portfolio with this name."

    if (
        description
        and len(description) > Portfolio._meta.get_field("description").max_length
    ):
        errors["description"] = "Description must be 255 characters or fewer."

    return errors


@login_required
@require_POST
def create_portfolio(request):
    """Create a new portfolio if input and plan limits are valid."""
    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()
    user = request.user
    errors = _validate_portfolio_fields(name, description, user=user)

    # Error in name or description or both, return
    if errors:
        return render_create_portfolio_form(
            request,
            errors=errors,
            form_data=request.POST,
            status=400,
        )

    # Error checking max portfolios
    try:
        stripe_customer = StripeCustomer.objects.get(user=user)
        subscription = stripe_customer.subscription
        max_portfolios = subscription.max_portfolios
    except StripeCustomer.DoesNotExist:
        max_portfolios = FREE_MAX_PORTFOLIOS

    current_portfolio_count = user.portfolios.count()
    if (
        max_portfolios != UNLIMITED_PORTFOLIOS
        and current_portfolio_count >= max_portfolios
    ):
        return render_create_portfolio_form(
            request,
            errors={"name": "Portfolio limit reached"},
            form_data=request.POST,
            status=400,
        )

    portfolio = Portfolio.objects.create(
        user=user,
        name=name,
        description=description or None,
    )

    # Adding portfolio to user profile
    profile = user.profile
    profile.portfolios.add(portfolio)

    return redirect("portfolio_detail", portfolio_id=portfolio.id)


# Builds the data for one portfolio's detail page
@login_required
def portfolio_detail(request, portfolio_id):
    """Build and render the detail page for one portfolio."""
    table = []
    total_portfolio_value = Decimal("0.00")
    total_profit_loss = Decimal("0.00")
    total_profit_loss_percent = Decimal("0.00")

    portfolio = get_object_or_404(Portfolio, id=portfolio_id, user=request.user)
    display_currency = get_user_currency(request.user)

    trades = list(
        portfolio.transactions.select_related("equity").all().order_by("-trade_date")
    )
    has_trades = bool(trades)

    closing_price_cache = {}
    equity_summaries = {}

    for trade in trades:
        if trade.equity_id is None:
            continue

        # Group our trades by equity id and track their current share count, total shares bought, and total money spent
        if trade.equity_id not in equity_summaries:
            equity_summaries[trade.equity_id] = {
                "equity": trade.equity,
                "net_quantity": Decimal("0"),
                "buy_quantity": Decimal("0"),
                "cost": Decimal("0"),
            }

        trade_quantity = Decimal(str(abs(int(trade.quantity))))

        if trade.transaction_type == "BUY":
            equity_summaries[trade.equity_id]["net_quantity"] += trade_quantity
            equity_summaries[trade.equity_id]["buy_quantity"] += trade_quantity
            equity_summaries[trade.equity_id]["cost"] += trade_quantity * trade.price
        elif trade.transaction_type == "SELL":
            equity_summaries[trade.equity_id]["net_quantity"] -= trade_quantity

        if trade.equity_id not in closing_price_cache:
            closing_price_cache[trade.equity_id] = get_closing_price_state(
                trade.equity,
                display_currency,
            )

        closing_price_state = closing_price_cache[trade.equity_id]
        trade.trade_price_value = currency_conversion(
            trade.price,
            "AUD",
            display_currency,
        )
        trade.trade_price_display = format_currency(
            trade.trade_price_value,
            display_currency,
        )
        trade.closing_price_display = closing_price_state["display"]
        trade.closing_price_needs_refresh = closing_price_state["needs_refresh"]
        trade.closing_price_update_url = reverse(
            "update_equity_view", args=[trade.equity.ticker]
        )
        trade_gain_state = _build_trade_gain_state(
            trade,
            closing_price_state,
            display_currency,
        )
        trade.gain_display = trade_gain_state["display"]
        trade.gain_class = trade_gain_state["class"]
        trade.matched_cost_total = trade_gain_state.get("matched_cost_total", "NaN")

    equity_rows = []

    for equity_summary in sorted(
        equity_summaries.values(),
        key=lambda summary: summary["equity"].ticker,
    ):
        if equity_summary["net_quantity"] <= 0:
            continue

        equity = equity_summary["equity"]

        latest_saved_price = (
            EquityPrice.objects.filter(equity=equity).order_by("-date").first()
        )
        last_pulled_formatted = (
            latest_saved_price.date.strftime("%d %b %Y")
            if latest_saved_price is not None
            else "N/A"
        )

        if equity.id not in closing_price_cache:
            closing_price_cache[equity.id] = get_closing_price_state(
                equity,
                display_currency,
            )

        closing_price_state = closing_price_cache[equity.id]
        average_buy_price = None
        current_value_display = "Retrieving"
        gain_display = "Retrieving"
        gain_class = "text-gray-500"

        if equity_summary["buy_quantity"] > 0:
            average_buy_price = (
                equity_summary["cost"] / equity_summary["buy_quantity"]
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if closing_price_state["closing_price"] is not None:
            latest_closing_price = Decimal(str(closing_price_state["closing_price"]))
            current_value = (
                latest_closing_price * equity_summary["net_quantity"]
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            current_value_display = _format_closing_price(
                current_value,
                display_currency,
            )

        if average_buy_price is not None:
            average_buy_price_display_value = currency_conversion(
                average_buy_price,
                "AUD",
                display_currency,
            )
            if closing_price_state["closing_price"] is not None:
                gain_total = (
                    (latest_closing_price - average_buy_price_display_value)
                    * equity_summary["net_quantity"]
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                gain_percent = (
                    (
                        (latest_closing_price - average_buy_price_display_value)
                        / average_buy_price_display_value
                    )
                    * Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                gain_display = _format_gain_display(
                    gain_total,
                    gain_percent,
                    display_currency,
                )
                gain_class = "text-green-500" if gain_total >= 0 else "text-red-500"
            average_buy_price_str = str(average_buy_price_display_value)
        else:
            average_buy_price_str = ""

        equity_rows.append(
            {
                "equity": equity,
                "quantity": int(equity_summary["net_quantity"]),
                "average_buy_price_display": (
                    _format_closing_price(
                        currency_conversion(average_buy_price, "AUD", display_currency),
                        display_currency,
                    )
                    if average_buy_price is not None
                    else "N/A"
                ),
                "average_buy_price_value": average_buy_price_str,
                "closing_price_display": closing_price_state["display"],
                "closing_price_needs_refresh": closing_price_state["needs_refresh"],
                "closing_price_update_url": reverse(
                    "update_equity_view", args=[equity.ticker]
                ),
                "last_pulled_formatted": last_pulled_formatted,
                "current_value_display": current_value_display,
                "gain_display": gain_display,
                "gain_class": gain_class,
            }
        )

    holdings = list(portfolio.holdings.select_related("equity").filter(quantity__gt=0))
    tickers = [h.equity.ticker for h in holdings]
    quantities = [h.quantity for h in holdings]
    holding_rows = []

    if tickers:
        data = get_portfolio_data(tickers, quantities, display_currency)
        chart_tickers = data["tickers"]
        chart_values = data["equity_totals"]
        total_portfolio_value = Decimal(str(data["total_portfolio_value"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_cost_basis = Decimal("0.00")
        for holding, equity_total in zip(holdings, data["equity_totals"]):
            equity = holding.equity
            average_buy_price = get_average_buy_price(portfolio, equity)
            cost_basis = currency_conversion(
                average_buy_price * holding.quantity,
                "AUD",
                display_currency,
            )
            current_value = Decimal(str(equity_total)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            gain = (current_value - cost_basis).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_cost_basis += cost_basis
            total_profit_loss += gain
            holding_rows.append(
                {
                    "equity": equity,
                    "quantity": holding.quantity,
                    "current_value": current_value,
                    "gain": gain,
                }
            )

        if total_cost_basis > 0:
            total_profit_loss_percent = (
                (total_profit_loss / total_cost_basis) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    has_trades = portfolio.transactions.exists()

    holdings = list(portfolio.holdings.select_related("equity").filter(quantity__gt=0))

    portfolio_current_value = Decimal("0.00")
    portfolio_unrealised_pnl = Decimal("0.00")

    for holding in holdings:
        pnl_data = get_holding_unrealised_pnl(holding)

        holding.current_price = pnl_data["current_price"]
        holding.current_value = (
            currency_conversion(pnl_data["current_value"], "AUD", display_currency)
            if pnl_data["current_value"] is not None
            else None
        )
        holding.cost_basis = (
            currency_conversion(pnl_data["cost_basis"], "AUD", display_currency)
            if pnl_data["cost_basis"] is not None
            else None
        )
        holding.unrealised_pnl = (
            currency_conversion(pnl_data["unrealised_pnl"], "AUD", display_currency)
            if pnl_data["unrealised_pnl"] is not None
            else None
        )

        if holding.current_value is not None:
            portfolio_current_value += holding.current_value

        if holding.unrealised_pnl is not None:
            portfolio_unrealised_pnl += holding.unrealised_pnl

    portfolio_realised_pnl = currency_conversion(
        get_portfolio_realised_pnl(portfolio),
        "AUD",
        display_currency,
    )
    portfolio_total_pnl = portfolio_realised_pnl + portfolio_unrealised_pnl

    portfolio.current_value = portfolio_current_value
    portfolio.profit = portfolio_total_pnl
    portfolio.current_value_display = format_currency(
        portfolio_current_value,
        display_currency,
    )
    portfolio.profit_display = (
        f"{format_signed_currency(portfolio_total_pnl, display_currency)}"
    )

    portfolio_total_invested_cost = currency_conversion(
        get_portfolio_total_invested_cost(portfolio),
        "AUD",
        display_currency,
    )

    if portfolio_total_invested_cost > 0:
        portfolio.profit_percent = (
            (portfolio_total_pnl / portfolio_total_invested_cost) * Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        portfolio.profit_percent_display = f"{portfolio.profit_percent}%"
    else:
        portfolio.profit_percent = None
        portfolio.profit_percent_display = "N/A"

    return render(
        request,
        "portfolio_detail.html",
        {
            "portfolio": portfolio,
            "has_trades": has_trades,
            "equity_rows": equity_rows,
            "trades": trades,
            "holding_rows": holding_rows,
            "chart_tickers": chart_tickers if tickers else [],
            "chart_values": chart_values if tickers else [],
            "total_portfolio_value": total_portfolio_value,
            "total_profit_loss": total_profit_loss,
            "total_profit_loss_percent": total_profit_loss_percent,
            "table": table,
            "holdings": holdings,
            "portfolio_realised_pnl": portfolio_realised_pnl,
            "portfolio_unrealised_pnl": portfolio_unrealised_pnl,
            "display_currency": display_currency,
            "other_portfolios": Portfolio.objects.filter(user=request.user).exclude(
                id=portfolio.id
            ),
        },
    )


# Deletes user equity data
# Go into portfolio details page -> portfolio -> right click equity
# Deletes the equity and the related transaction data
@login_required
@require_POST
def delete_equity_data(request):
    """Delete an equity and its transactions from a portfolio."""
    user = request.user
    portfolio_id = request.POST.get("portfolio_id")
    equity_id = request.POST.get("equity_id")

    # Check if portfolio_id and equity_id are provided
    if not portfolio_id or not equity_id:
        messages.error(request, "Missing portfolio or equity details for deletion.")
        if portfolio_id:
            return redirect("portfolio_detail", portfolio_id=portfolio_id)
        return redirect("dashboard")

    portfolio = get_object_or_404(Portfolio, id=portfolio_id, user=user)
    equity = get_object_or_404(Equity, id=equity_id)

    # Delete all transactions for this equity in this portfolio
    transactions = Transaction.objects.filter(portfolio=portfolio, equity=equity)
    transactions.delete()

    # Also delete the holding for this equity in this portfolio
    Holdings.objects.filter(portfolio=portfolio, equity=equity).delete()

    # If the user no longer holds this equity in any portfolio, remove it from their profile
    remove_unused_equity(user, equity)

    messages.warning(
        request,
        f"Equity '{equity.name}' deleted successfully.",
        extra_tags="equity-delete-success",
    )
    return redirect("portfolio_detail", portfolio_id=portfolio.id)


# deletes portfolio
@login_required
@require_POST
def delete_portfolio_data(request):
    """Delete a portfolio and move data to the default portfolio."""
    user = request.user
    portfolio_id_raw = request.POST.get("portfolio_id")

    if not portfolio_id_raw:
        return JsonResponse({"error": "Missing portfolio_id"}, status=400)

    try:
        portfolio_id = int(portfolio_id_raw)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid portfolio_id"}, status=400)

    portfolio = get_object_or_404(Portfolio, id=portfolio_id, user=user)

    if portfolio.is_default:
        messages.error(request, "Default portfolio cannot be deleted.")
        return redirect("dashboard")

    default_portfolio = Portfolio.objects.filter(user=user, is_default=True).first()
    if not default_portfolio:
        return JsonResponse(
            {"error": "No default portfolio found after deletion."}, status=400
        )

    # Move all holdings to default portfolio before deleting the portfolio
    move_equities = list(portfolio.holdings.select_related("equity").all())

    for holding in move_equities:
        default_holding, _created = Holdings.objects.get_or_create(
            portfolio=default_portfolio,
            equity=holding.equity,
            defaults={"quantity": 0},
        )
        default_holding.quantity += holding.quantity
        default_holding.save()

    portfolio.transactions.update(portfolio=default_portfolio)
    portfolio.delete()

    messages.warning(
        request,
        f"Portfolio '{portfolio.name}' deleted successfully.",
        extra_tags="portfolio-delete-success",
    )

    return redirect("dashboard")


@login_required
def export_portfolio_holdings_csv(request, portfolio_id):
    """Export current portfolio holdings as a CSV file."""
    portfolio = get_object_or_404(Portfolio, id=portfolio_id, user=request.user)
    display_currency = get_user_currency(request.user)

    holdings = portfolio.holdings.select_related("equity").filter(quantity__gt=0)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="portfolio_{portfolio.id}_holdings.csv"'
    )

    writer = csv.writer(response)
    writer.writerow(
        [
            "portfolio",
            "ticker",
            "equity_name",
            "quantity",
            f"latest_closing_price_{display_currency}",
            f"total_purchase_cost_{display_currency}",
            "currency",
        ]
    )

    for holding in holdings:
        pnl_data = get_holding_unrealised_pnl(holding)

        latest_price = (
            currency_conversion(pnl_data["current_price"], "AUD", display_currency)
            if pnl_data["current_price"] is not None
            else None
        )
        total_purchase_cost = (
            currency_conversion(pnl_data["cost_basis"], "AUD", display_currency)
            if pnl_data["cost_basis"] is not None
            else None
        )

        writer.writerow(
            [
                portfolio.name,
                holding.equity.ticker,
                holding.equity.name,
                holding.quantity,
                latest_price if latest_price is not None else "",
                total_purchase_cost if total_purchase_cost is not None else "",
                display_currency,
            ]
        )

    return response


@login_required
def portfolio_settings(request, portfolio_id):
    """View and update portfolio settings."""
    user = request.user

    portfolio = get_object_or_404(Portfolio, id=portfolio_id, user=user)

    # Update portfolio name or description or both
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        errors = _validate_portfolio_fields(
            name,
            description,
            user=user,
            current_portfolio=portfolio,
        )

        # Error! not good so we return
        if errors:
            return render(
                request,
                "portfolio_settings.html",
                {
                    "portfolio": portfolio,
                    "portfolio_id": portfolio_id,
                    "errors": errors,
                },
                status=400,
            )

        # OK! Lets update
        portfolio.name = name
        portfolio.description = description or None
        portfolio.save(update_fields=["name", "description"])
        return redirect("dashboard")

    return render(
        request,
        "portfolio_settings.html",
        {"portfolio": portfolio, "portfolio_id": portfolio_id},
    )


# for portfolios graph on dashboard, will move helpers to utils.py or somewhere else later
def get_portfolio_current_value(portfolio, display_currency="AUD"):
    """Calculate portfolio value for dashboard chart use."""
    holdings = portfolio.holdings.select_related("equity").filter(quantity__gt=0)

    total = Decimal("0.00")
    for holding in holdings:
        cp = get_closing_price_state(holding.equity, display_currency)
        if cp["closing_price"] is not None:
            total += Decimal(str(cp["closing_price"])) * holding.quantity

    return float(total)


def price_list_to_map(price_list):
    """Convert a list of price rows into a map from dates to prices."""
    return {p["date"]: p["closing_price"] for p in price_list}


def get_portfolio_equities(portfolio):
    """Get equities that have transactions in this portfolio."""
    equity_ids = (
        Transaction.objects.filter(portfolio=portfolio, equity__isnull=False)
        .values_list("equity_id", flat=True)
        .distinct()
    )
    return Equity.objects.filter(id__in=equity_ids)


def get_quantity_on_date(transactions, date):
    """Calculate how many shares were held on one chart date."""
    quantity = 0
    for trade in transactions:
        if trade.trade_date > date:
            continue

        if trade.transaction_type == "BUY":
            quantity += int(trade.quantity)
        elif trade.transaction_type == "SELL":
            quantity -= int(trade.quantity)

    return max(quantity, 0)


def get_all_dates_from_equities(equities, period="1 month", display_currency="AUD"):
    """Collect sorted price dates for all equities in the selected period."""
    all_dates = set()
    days = PERIOD_TO_DAYS[period]
    cutoff = date.today() - timedelta(days=days)
    for equity in equities:
        price_rows = EquityPrice.objects.filter(
            equity=equity,
            date__gte=cutoff,
        ).order_by("date")

        for p in price_rows:
            all_dates.add(p.date.isoformat())

    return sorted(all_dates)


def compute_portfolio_timeseries(portfolio, period="1 month", display_currency="AUD"):
    """Compute portfolio value time series from stored equity prices."""
    equities = get_portfolio_equities(portfolio)

    days = PERIOD_TO_DAYS[period]
    cutoff = date.today() - timedelta(days=days)

    dates = get_all_dates_from_equities(equities, period, display_currency)
    totals = {d: 0.0 for d in dates}
    for equity in equities:
        price_rows = EquityPrice.objects.filter(
            equity=equity,
            date__gte=cutoff,
        ).order_by("date")
        transactions = list(
            Transaction.objects.filter(
                portfolio=portfolio,
                equity=equity,
            ).order_by("trade_date")
        )

        price_map = {
            p.date.isoformat(): float(
                currency_conversion(p.closing_price, "AUD", display_currency)
            )
            for p in price_rows
        }

        for d in dates:
            if d in price_map:
                chart_date = date.fromisoformat(d)
                quantity = get_quantity_on_date(transactions, chart_date)
                totals[d] += price_map[d] * float(quantity)

    return dates, [totals[d] for d in dates]


def build_dashboard_chart_data(user, period="1 month"):
    """Build chart labels and datasets for the dashboard graph."""
    display_currency = get_user_currency(user)
    portfolios = Portfolio.objects.filter(user=user)
    all_dates = set()
    portfolio_data = []
    datasets = []

    for p in portfolios:
        dates, values = compute_portfolio_timeseries(p, period, display_currency)
        portfolio_data.append(
            {
                "portfolio": p,
                "dates": dates,
                "values": values,
            }
        )
        all_dates.update(dates)

    all_labels = sorted(all_dates)

    for data in portfolio_data:
        value_map = dict(zip(data["dates"], data["values"]))
        values = [value_map.get(date) for date in all_labels]

        datasets.append(
            {
                "name": data["portfolio"].name,
                "values": values,
            }
        )

    return all_labels, datasets


def portfolios_total_value_today(user):
    """Return total value today across all of the user's portfolios."""
    display_currency = get_user_currency(user)
    total = Decimal("0.00")
    portfolios = Portfolio.objects.filter(user=user)

    for p in portfolios:
        total += Decimal(str(get_portfolio_current_value(p, display_currency)))

    return float(total)


# allow user to switch default portfolio
@login_required
@require_POST
def set_default_portfolio(request, portfolio_id):
    """Set one of the user's portfolios as default."""
    user = request.user
    portfolio = get_object_or_404(Portfolio, id=portfolio_id, user=user)

    # Remove default from all user's portfolios
    Portfolio.objects.filter(user=user).update(is_default=False)

    # set the new default
    portfolio.is_default = True
    portfolio.save(update_fields=["is_default"])

    messages.success(request, f"{portfolio.name} is now your default portfolio.")
    return redirect("portfolio_settings", portfolio_id=portfolio_id)


@login_required
@require_POST
def move_equity_to_portfolio(request):
    """Move one equity's transactions from one portfolio to another."""
    user = request.user
    source_portfolio_id = request.POST.get("source_portfolio_id")
    target_portfolio_id = request.POST.get("target_portfolio_id")
    equity_id = request.POST.get("equity_id")

    if not target_portfolio_id:
        messages.error(request, "Please select a target portfolio.")
        return redirect("portfolio_detail", portfolio_id=source_portfolio_id)

    source_portfolio = get_object_or_404(Portfolio, id=source_portfolio_id, user=user)
    target_portfolio = get_object_or_404(Portfolio, id=target_portfolio_id, user=user)
    equity = get_object_or_404(Equity, id=equity_id)

    if source_portfolio.id == target_portfolio.id:
        messages.error(request, "Source and target portfolio cannot be the same.")
        return redirect("portfolio_detail", portfolio_id=source_portfolio.id)

    source_transactions = Transaction.objects.filter(
        user=user,
        portfolio=source_portfolio,
        equity=equity,
    )

    if not source_transactions.exists():
        messages.error(request, "This equity does not exist in the source portfolio.")
        return redirect("portfolio_detail", portfolio_id=source_portfolio.id)

    target_conflict = Transaction.objects.filter(
        user=user,
        portfolio=target_portfolio,
        equity=equity,
    ).exists()

    if target_conflict:
        messages.error(
            request,
            f"{equity.ticker} already exists in the target portfolio.",
        )
        return redirect("portfolio_detail", portfolio_id=source_portfolio.id)

    with transaction.atomic():
        source_transactions.update(portfolio=target_portfolio)

        rebuild_holding_for_equity(source_portfolio, equity)
        rebuild_holding_for_equity(target_portfolio, equity)

    messages.success(
        request,
        f"{equity.ticker} was moved from '{source_portfolio.name}' to '{target_portfolio.name}'.",
    )
    return redirect("portfolio_detail", portfolio_id=target_portfolio.id)


def get_dashboard_chart_data(request):
    """Return dashboard chart data as JSON."""
    period = request.GET.get("period", "1 month")
    labels, datasets = build_dashboard_chart_data(request.user, period)
    return JsonResponse(
        {
            "labels": labels,
            "datasets": datasets,
            "display_currency": get_user_currency(request.user),
        }
    )
