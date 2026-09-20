from decimal import Decimal

import stripe
from currency.utils import (
    currency_conversion,
    format_currency,
    format_signed_currency,
    get_user_currency,
)
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from portfolios.models import Portfolio
from portfolios.utils import (
    get_closing_price_state,
    get_holding_cost_basis,
    get_holding_unrealised_pnl,
    get_portfolio_realised_pnl,
)
from portfolios.views import build_dashboard_chart_data, portfolios_total_value_today
from subscriptions.models import (
    FREE_MAX_PORTFOLIOS,
    UNLIMITED_PORTFOLIOS,
    StripeCustomer,
)
from transactions.models import Transaction
from users.models import Profile


def landing(request):
    """Render landing page or redirect authenticated users to dashboard."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    # get rid of the subscription stuff that is
    # in global context processor
    context = {}
    return render(request, "landing.html", context)


def _format_currency(amount, display_currency="AUD"):
    """Format currency value for dashboard display."""
    return format_currency(amount, display_currency)


def _format_signed_currency(amount, display_currency="AUD"):
    """Format signed currency value for dashboard display."""
    return format_signed_currency(amount, display_currency)


def _get_profit_display_and_class(amount, display_currency="AUD"):
    """Return formatted profit string and CSS class."""
    if amount is None:
        return "Retrieving", "text-gray-500"

    return (
        _format_signed_currency(amount, display_currency),
        "text-green-600" if amount >= 0 else "text-red-600",
    )


def _build_dashboard_holding_row(portfolio, holding, display_currency="AUD"):
    """Build one holding row and profit/loss figures for dashboard cards."""
    pnl_data = get_holding_unrealised_pnl(holding)
    holding_profit = (
        currency_conversion(pnl_data["unrealised_pnl"], "AUD", display_currency)
        if pnl_data["unrealised_pnl"] is not None
        else None
    )
    closing_price_state = get_closing_price_state(holding.equity, display_currency)
    raw_holding_cost_basis = pnl_data["cost_basis"] or get_holding_cost_basis(
        portfolio, holding.equity
    )
    holding_cost_basis = (
        currency_conversion(raw_holding_cost_basis, "AUD", display_currency)
        if raw_holding_cost_basis is not None
        else None
    )
    current_value = (
        currency_conversion(pnl_data["current_value"], "AUD", display_currency)
        if pnl_data["current_value"] is not None
        else None
    )
    holding_profit_display, holding_profit_class = _get_profit_display_and_class(
        holding_profit,
        display_currency,
    )

    pnl_data["current_value"] = current_value
    pnl_data["unrealised_pnl"] = holding_profit

    return {
        "ticker": holding.equity.ticker,
        "name": holding.equity.name,
        "quantity": holding.quantity,
        "cost_basis_value": str(holding_cost_basis)
        if holding_cost_basis is not None
        else "",
        "current_value_value": (
            str(current_value) if current_value is not None else ""
        ),
        "profit_value": str(holding_profit) if holding_profit is not None else "",
        "profit_display": holding_profit_display,
        "profit_class": holding_profit_class,
        "closing_price_needs_refresh": closing_price_state["needs_refresh"],
        "closing_price_update_url": reverse(
            "update_equity_view", args=[holding.equity.ticker]
        ),
    }, pnl_data


def _build_dashboard_portfolio_summaries(portfolios, display_currency="AUD"):
    """Build dashboard summary objects for each portfolio."""
    portfolio_rows = []

    for portfolio in portfolios:
        holdings = list(
            portfolio.holdings.select_related("equity").filter(quantity__gt=0)
        )
        holding_rows = []
        portfolio_current_value = Decimal("0.00")
        portfolio_unrealised_pnl = Decimal("0.00")
        has_priced_holdings = False

        for holding in holdings:
            holding_row, pnl_data = _build_dashboard_holding_row(
                portfolio,
                holding,
                display_currency,
            )
            holding_unrealised_pnl = pnl_data["unrealised_pnl"]
            if pnl_data["current_value"] is not None:
                portfolio_current_value += pnl_data["current_value"]
                has_priced_holdings = True

            if holding_unrealised_pnl is not None:
                portfolio_unrealised_pnl += holding_unrealised_pnl

            holding_rows.append(holding_row)

        portfolio_realised_pnl = currency_conversion(
            get_portfolio_realised_pnl(portfolio),
            "AUD",
            display_currency,
        )
        portfolio_total_pnl = portfolio_realised_pnl + portfolio_unrealised_pnl
        portfolio_profit_display, portfolio_profit_class = (
            _get_profit_display_and_class(
                portfolio_total_pnl,
                display_currency,
            )
        )

        portfolio.dashboard_current_value_display = (
            _format_currency(portfolio_current_value, display_currency)
            if has_priced_holdings or not holdings
            else "Retrieving"
        )
        portfolio.dashboard_profit_display = (
            portfolio_profit_display
            if has_priced_holdings
            or portfolio_realised_pnl != Decimal("0.00")
            or not holdings
            else "Retrieving"
        )
        portfolio.dashboard_profit_class = portfolio_profit_class
        portfolio.dashboard_realised_pnl_value = str(portfolio_realised_pnl)
        portfolio.dashboard_holdings = holding_rows
        portfolio_rows.append(portfolio)

    return portfolio_rows


@login_required
def dashboard(request):
    """Render dashboard with trades, portfolio summaries, and chart data."""
    display_currency = get_user_currency(request.user)
    user_trades = (
        Transaction.objects.filter(user=request.user)
        .select_related("equity", "portfolio")
        .order_by("-trade_date", "-created_at")
    )
    has_trades = user_trades.exists()
    recent_trades = user_trades[:5]
    user_portfolio = Portfolio.objects.filter(user=request.user).order_by(
        "-is_default", "-creation_date"
    )
    has_portfolio = user_portfolio.exists()

    # if user has no portfolio, create a default one automatically
    if not has_portfolio:
        default_portfolio = Portfolio.objects.create(
            user=request.user,
            description="Let's start with your first portfolio!",
            name="Default Portfolio",
            is_default=True,
        )
        profile = Profile.objects.filter(user=request.user).first()
        if profile:
            profile.portfolios.add(default_portfolio)

        user_portfolio = Portfolio.objects.filter(user=request.user).order_by(
            "-creation_date"
        )
        has_portfolio = True

    # recent tx table — sell value = qty * trade price, buy = qty * latest close
    for transaction in recent_trades:
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

    dashboard_portfolios = _build_dashboard_portfolio_summaries(
        user_portfolio,
        display_currency,
    )
    portfolio_count = len(dashboard_portfolios)
    context = {
        "has_trades": has_trades,
        "user_trades": user_trades,
        "recent_trades": recent_trades,
        "has_portfolio": has_portfolio,
        "user_portfolio": dashboard_portfolios,
        "portfolio_count": portfolio_count,
        "display_currency": display_currency,
    }

    # This calculates the portfolio limit stuff once, then add it into the template
    # context so dashboard.html can check relevant django context variables
    def add_portfolio_limit_context(context, local_plan=None):
        """Add portfolio limit values to dashboard template context."""
        max_portfolio_count = (
            local_plan.max_portfolios if local_plan else FREE_MAX_PORTFOLIOS
        )

        # Update the django context variables
        context.update(
            {
                "local_subscription": local_plan,
                "max_portfolio_count": max_portfolio_count,
                "portfolio_limit_reached": (
                    max_portfolio_count != UNLIMITED_PORTFOLIOS
                    and portfolio_count >= max_portfolio_count
                ),
            }
        )

    def add_graph_data(context):
        """Add dashboard chart labels, datasets, and totals to context."""
        labels, datasets = build_dashboard_chart_data(request.user)
        portfolios_total = portfolios_total_value_today(request.user)
        context.update(
            {
                "dashboard_portfolio_chart_labels": labels,
                "dashboard_portfolio_chart_datasets": datasets,
                "dashboard_portfolios_total": portfolios_total,
                "dashboard_portfolios_total_display": format_currency(
                    portfolios_total,
                    display_currency,
                ),
            }
        )

    try:
        # copied from subscription/views.py with minor changes
        # Retrieve the subscription & product
        stripe_customer = StripeCustomer.objects.get(user=request.user)
        stripe.api_key = settings.STRIPE_SECRET_KEY
        local_plan = stripe_customer.subscription
        add_portfolio_limit_context(context, local_plan)

        if stripe_customer.stripeSubscriptionId is None:
            context.update({"subscription": None, "product": None})

            add_graph_data(context)
            return render(request, "dashboard.html", context)

        # get subscription from stripe so price matches customer portal / dashboard
        stripe_sub = stripe.Subscription.retrieve(stripe_customer.stripeSubscriptionId)
        # cant use .items here — stripe objects are dict-like and .items is dict.items()
        line = stripe_sub["items"]["data"][0]
        price = line["price"]
        product = stripe.Product.retrieve(price["product"])
        context.update({"subscription": stripe_sub, "product": product})

        add_graph_data(context)
        return render(request, "dashboard.html", context)

    except StripeCustomer.DoesNotExist:
        # User has no subscription yet -> free plan
        # but it'll probs work fine without this bit code code below
        context.update({"subscription": None, "product": None})
        add_portfolio_limit_context(context)

        add_graph_data(context)
        return render(request, "dashboard.html", context)


@login_required
def features(request):
    """Render features page."""
    return render(request, "features.html")
