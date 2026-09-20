from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import pandas as pd
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from subscriptions.models import StripeCustomer
from subscriptions.utils import get_or_create_local_subscription
from transactions.models import Equity, EquityPrice, Transaction, TransactionMatch
from users.models import Profile

from portfolios.models import Holdings, Portfolio, PriceUpdateLog

from .utils import _build_trade_gain_state
from .yfinance import yfinance_server
from .yfinance.yfinance_server import update_equity_price


# Test helper functions for yfinance
def price_history(rows):
    dates = []
    prices = []
    for close_date, price in rows:
        dates.append(pd.Timestamp(close_date))
        prices.append(price)

    return pd.DataFrame({"Close": prices}, index=dates)


def empty_price_history():
    return pd.DataFrame(columns=["Close"])


def ticker_response(history, info=None, error=None):
    ticker = Mock()
    if error:
        ticker.history.side_effect = error
    else:
        ticker.history.return_value = history

    ticker.info = info or {}
    return ticker


def ticker_with_info_error(history):
    ticker = ticker_response(history)
    ticker.info = Mock()
    ticker.info.get.side_effect = Exception("boom")
    return ticker


# Actual tests
class PortfolioValidationTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.premium_subscription = get_or_create_local_subscription("premium")
        self.user = User.objects.create_user(
            username="testuser",
            password="test123",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            country="Australia",
            currency="AUD",
            subscription=self.free_subscription,
        )
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
        )
        self.client.login(username="testuser", password="test123")

    def create_portfolio_through_app(self, name, description):
        return self.client.post(
            reverse("create_portfolio"),
            {
                "name": name,
                "description": description,
            },
        )

    def test_create_portfolio_requires_name(self):
        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "",
                "description": "Test description",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Portfolio name is required.", status_code=400)

    def test_create_portfolio_rejects_name_too_long(self):
        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "A" * 101,
                "description": "Test description",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, "Portfolio name must be 100 characters or fewer.", status_code=400
        )

    def test_create_portfolio_rejects_duplicate_name(self):
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        stripe_customer.subscription = self.premium_subscription
        stripe_customer.save(update_fields=["subscription"])
        self.profile.subscription = self.premium_subscription
        self.profile.save(update_fields=["subscription"])

        self.create_portfolio_through_app(
            "test Portfolio",
            "Existing portfolio",
        )

        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "test Portfolio",
                "description": "Duplicate name",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "You already have a portfolio with this name.",
            status_code=400,
        )
        self.assertEqual(Portfolio.objects.filter(user=self.user).count(), 1)

    def test_free_user_can_create_first_portfolio(self):
        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "First Portfolio",
                "description": "Allowed for free users",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First Portfolio")
        self.assertContains(response, "Start building your portfolio")

    def test_free_user_cannot_create_non_default_portfolio_when_default_exists(self):
        default_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Default Portfolio",
            description="System-created portfolio",
            is_default=True,
        )
        self.profile.portfolios.add(default_portfolio)

        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "First Custom Portfolio",
                "description": "Should be blocked because the default counts toward the limit",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Portfolio limit reached", status_code=400)
        self.assertEqual(Portfolio.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            Portfolio.objects.filter(user=self.user, is_default=False).count(), 0
        )

    def test_free_user_cannot_create_more_than_one_portfolio(self):
        self.create_portfolio_through_app(
            "Existing Portfolio",
            "Already using the free slot",
        )

        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "Second Portfolio",
                "description": "Should be blocked for free users",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Portfolio limit reached", status_code=400)
        self.assertContains(response, "Create Portfolio", status_code=400)
        self.assertContains(response, "Second Portfolio", status_code=400)

    def test_premium_user_can_create_second_portfolio(self):
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        stripe_customer.subscription = self.premium_subscription
        stripe_customer.save(update_fields=["subscription"])
        self.profile.subscription = self.premium_subscription
        self.profile.save(update_fields=["subscription"])

        self.create_portfolio_through_app(
            "Existing Premium Portfolio",
            "Premium users can have more than one",
        )

        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "Second Premium Portfolio",
                "description": "This should be allowed",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Second Premium Portfolio")
        self.assertContains(response, "Start building your portfolio")

    def test_downgraded_user_cannot_add_another_portfolio_after_returning_to_free(self):
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        stripe_customer.subscription = self.premium_subscription
        stripe_customer.save(update_fields=["subscription"])
        self.profile.subscription = self.premium_subscription
        self.profile.save(update_fields=["subscription"])

        self.create_portfolio_through_app(
            "Premium Portfolio",
            "Created while premium",
        )

        stripe_customer.subscription = self.free_subscription
        stripe_customer.save(update_fields=["subscription"])
        self.profile.subscription = self.free_subscription
        self.profile.save(update_fields=["subscription"])

        response = self.client.post(
            reverse("create_portfolio"),
            {
                "name": "Post Downgrade Portfolio",
                "description": "Should not be allowed on free",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Portfolio limit reached", status_code=400)
        self.assertContains(response, "Create Portfolio", status_code=400)
        self.assertContains(response, "Post Downgrade Portfolio", status_code=400)

    def test_free_user_can_update_existing_portfolio_from_settings_page(self):
        self.create_portfolio_through_app(
            "Existing Portfolio",
            "Already using the free slot",
        )
        portfolio = Portfolio.objects.get(user=self.user)

        response = self.client.post(
            reverse("portfolio_settings", args=[portfolio.id]),
            {
                "name": "Updated Portfolio Name",
                "description": "Updated portfolio description",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("dashboard"))
        portfolio.refresh_from_db()
        self.assertEqual(portfolio.name, "Updated Portfolio Name")
        self.assertEqual(portfolio.description, "Updated portfolio description")
        self.assertEqual(Portfolio.objects.filter(user=self.user).count(), 1)

    def test_portfolio_settings_rejects_invalid_name_without_creating_new_portfolio(
        self,
    ):
        self.create_portfolio_through_app(
            "Existing Portfolio",
            "Already using the free slot",
        )
        portfolio = Portfolio.objects.get(user=self.user)

        response = self.client.post(
            reverse("portfolio_settings", args=[portfolio.id]),
            {
                "name": "",
                "description": "Updated portfolio description",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Portfolio name is required.", status_code=400)
        portfolio.refresh_from_db()
        self.assertEqual(portfolio.name, "Existing Portfolio")
        self.assertEqual(Portfolio.objects.filter(user=self.user).count(), 1)

    def test_portfolio_settings_rejects_duplicate_name(self):
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        stripe_customer.subscription = self.premium_subscription
        stripe_customer.save(update_fields=["subscription"])
        self.profile.subscription = self.premium_subscription
        self.profile.save(update_fields=["subscription"])

        self.create_portfolio_through_app(
            "Long Term",
            "First portfolio",
        )
        self.create_portfolio_through_app(
            "Short Term",
            "Second portfolio",
        )

        portfolio = Portfolio.objects.get(user=self.user, name="Short Term")
        response = self.client.post(
            reverse("portfolio_settings", args=[portfolio.id]),
            {
                "name": "Long Term",
                "description": "Trying to duplicate another portfolio",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "You already have a portfolio with this name.",
            status_code=400,
        )
        portfolio.refresh_from_db()
        self.assertEqual(portfolio.name, "Short Term")


class EquityPriceHistoryTests(TestCase):
    @patch(
        "portfolios.yfinance.yfinance_server.get_last_close_price",
        return_value=(Decimal("151.25"), date(2026, 4, 7), None),
    )
    def test_update_equity_price_preserves_existing_historical_rows(self, mocked_price):
        equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        older_price = EquityPrice.objects.create(
            equity=equity,
            date=date(2026, 4, 6),
            closing_price=Decimal("149.00"),
        )

        result = update_equity_price(equity)

        self.assertEqual(result["status"], "created")
        self.assertEqual(EquityPrice.objects.filter(equity=equity).count(), 2)
        self.assertTrue(
            EquityPrice.objects.filter(
                equity=equity,
                date=older_price.date,
                closing_price=older_price.closing_price,
            ).exists()
        )
        self.assertTrue(
            EquityPrice.objects.filter(
                equity=equity,
                date=date(2026, 4, 7),
                closing_price=Decimal("151.25"),
            ).exists()
        )
        mocked_price.assert_called_once_with("CBA")


class TradeGainDisplayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="test123",
        )
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Trade Gain Portfolio",
        )
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )

    def build_trade(self, transaction_type, price, quantity=1):
        return Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type=transaction_type,
            quantity=quantity,
            price=Decimal(price),
            trade_date=date(2026, 4, 7),
            currency="AUD",
        )

    def test_buy_gain_percent_uses_close_over_trade_price(self):
        # _build_trade_gain_state takes a transaction + latest closing price, calculates gain/loss
        # amount and percentage, then returns display info for the UI
        state = _build_trade_gain_state(
            self.build_trade("BUY", "10.00", quantity=2),
            {
                "closing_price": Decimal("15.00"),
                "needs_refresh": False,
            },
        )

        # now we go check that the dictionary elements return are good
        self.assertEqual(state["display"], "+A$10.00 (50.00% \u25b2)")
        self.assertEqual(state["class"], "text-green-500")

    def test_sell_gain_percent_uses_matched_buy_cost(self):
        buy_trade = self.build_trade("BUY", "1.00", quantity=1)
        sell_trade = self.build_trade("SELL", "10.00", quantity=1)
        TransactionMatch.objects.create(
            buy_transaction=buy_trade,
            sell_transaction=sell_trade,
            matched_quantity=1,
        )

        state = _build_trade_gain_state(
            sell_trade,
            {
                "closing_price": Decimal("1.00"),
                "needs_refresh": False,
            },
        )

        self.assertEqual(state["display"], "+A$9.00 (900.00% \u25b2)")
        self.assertEqual(state["class"], "text-green-500")

    def test_sell_loss_percent_uses_matched_buy_cost(self):
        buy_trade = self.build_trade("BUY", "10.00", quantity=1)
        sell_trade = self.build_trade("SELL", "1.00", quantity=1)
        TransactionMatch.objects.create(
            buy_transaction=buy_trade,
            sell_transaction=sell_trade,
            matched_quantity=1,
        )

        state = _build_trade_gain_state(
            sell_trade,
            {
                "closing_price": Decimal("10.00"),
                "needs_refresh": False,
            },
        )

        self.assertEqual(state["display"], "-A$9.00 (90.00% \u25bc)")
        self.assertEqual(state["class"], "text-red-500")


class PortfolioPerformanceDisplayTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.user = User.objects.create_user(
            username="testuser",
            password="test123",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            country="Australia",
            currency="AUD",
            subscription=self.free_subscription,
        )
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
        )
        self.client.login(username="testuser", password="test123")
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Sold Out Portfolio",
            description="Used for realised P&L display test",
        )
        self.profile.portfolios.add(self.portfolio)
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )

    def test_fully_sold_portfolio_shows_realised_profit_percent(self):
        buy_transaction = Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            remaining_quantity=0,
            price=Decimal("100.00"),
            trade_date=date(2026, 4, 6),
            currency="AUD",
        )
        sell_transaction = Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=10,
            price=Decimal("120.00"),
            trade_date=date(2026, 4, 7),
            currency="AUD",
        )
        TransactionMatch.objects.create(
            buy_transaction=buy_transaction,
            sell_transaction=sell_transaction,
            matched_quantity=10,
        )
        Holdings.objects.create(
            portfolio=self.portfolio,
            equity=self.equity,
            quantity=0,
        )

        response = self.client.get(
            reverse("portfolio_detail", args=[self.portfolio.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "+A$200.00")
        self.assertContains(response, "20.00%")
        self.assertContains(response, "&#9650;", html=False)


class EquityModificationTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.user = User.objects.create_user(
            username="testuser",
            password="test123",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            country="Australia",
            currency="AUD",
            subscription=self.free_subscription,
        )
        self.client.login(username="testuser", password="test123")

        # Set up portfolio and equity
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Test Portfolio",
        )
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        self.holding = Holdings.objects.create(
            portfolio=self.portfolio,
            equity=self.equity,
            quantity=10,
        )

    def test_delete_equity_removes_holding(self):
        response = self.client.post(
            reverse("delete_equity_data"),
            {
                "portfolio_id": self.portfolio.id,
                "equity_id": self.equity.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Holdings.objects.filter(
                portfolio=self.portfolio,
                equity=self.equity,
            ).exists()
        )

    def test_delete_equity_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("delete_equity_data"),
            {
                "portfolio_id": self.portfolio.id,
                "equity_id": self.equity.id,
            },
        )
        # Should redirect to login, not delete anything
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Holdings.objects.filter(
                portfolio=self.portfolio,
                equity=self.equity,
            ).exists()
        )

    def test_delete_equity_from_another_users_portfolio_is_blocked(self):
        other_user = User.objects.create_user(
            username="testuser2",
            password="test123",
        )
        other_portfolio = Portfolio.objects.create(
            user=other_user,
            name="Other Portfolio",
        )
        other_holding = Holdings.objects.create(
            portfolio=other_portfolio,
            equity=self.equity,
            quantity=5,
        )

        response = self.client.post(
            reverse("delete_equity_data"),
            {
                "portfolio_id": other_portfolio.id,
                "equity_id": self.equity.id,
            },
        )

        self.assertIn(response.status_code, [403, 404])
        self.assertTrue(Holdings.objects.filter(id=other_holding.id).exists())


class PortfolioModificationTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.user = User.objects.create_user(
            username="testuser",
            password="test123",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            country="Australia",
            currency="AUD",
            subscription=self.free_subscription,
        )
        self.client.login(username="testuser", password="test123")

        self.default_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Default Portfolio",
            is_default=True,
        )

        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Test Portfolio",
        )
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        self.holding = Holdings.objects.create(
            portfolio=self.portfolio,
            equity=self.equity,
            quantity=10,
        )

    def test_delete_portfolio_removes_portfolio(self):
        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard"),
            fetch_redirect_response=False,
        )
        self.assertFalse(Portfolio.objects.filter(id=self.portfolio.id).exists())

    def test_delete_portfolio_cascades_to_holdings(self):
        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard"),
            fetch_redirect_response=False,
        )
        self.assertFalse(Holdings.objects.filter(portfolio=self.portfolio).exists())

    def test_delete_portfolio_cascades_to_transactions(self):
        transaction = Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=6,
            remaining_quantity=7,
            price=Decimal("67.00"),
            trade_date=date(2026, 4, 6),
            currency="AUD",
        )

        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard"),
            fetch_redirect_response=False,
        )
        self.assertFalse(Transaction.objects.filter(portfolio=self.portfolio).exists())
        transaction.refresh_from_db()
        self.assertEqual(transaction.portfolio, self.default_portfolio)

    def test_delete_portfolio_moves_holding_to_default_portfolio(self):
        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard"),
            fetch_redirect_response=False,
        )
        moved_holding = Holdings.objects.filter(
            portfolio=self.default_portfolio,
            equity=self.equity,
        ).first()
        self.assertIsNotNone(moved_holding)
        self.assertEqual(moved_holding.quantity, 10)

    def test_delete_portfolio_keeps_equities_when_moved_to_default(self):
        self.profile.equities.add(self.equity)
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=6,
            remaining_quantity=7,
            price=Decimal("67.00"),
            trade_date=date(2026, 4, 6),
            currency="AUD",
        )

        self.assertEqual(self.profile.equities.count(), 1)

        self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        self.assertEqual(self.profile.equities.count(), 1)

    def test_delete_portfolio_frees_select_portfolio_equities(self):
        self.profile.equities.add(self.equity)

        other_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Other Portfolio",
        )
        Holdings.objects.create(
            portfolio=other_portfolio,
            equity=self.equity,
            quantity=6,
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=6,
            remaining_quantity=7,
            price=Decimal("67.00"),
            trade_date=date(2026, 4, 6),
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=other_portfolio,
            transaction_type="BUY",
            quantity=6,
            remaining_quantity=7,
            price=Decimal("67.00"),
            trade_date=date(2026, 4, 6),
            currency="AUD",
        )
        self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        # Test if other portfolio still has the equity
        self.assertEqual(self.profile.equities.count(), 1)

    def test_delete_portfolio_requires_login(self):
        self.client.logout()
        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Portfolio.objects.filter(id=self.portfolio.id).exists())

    def test_delete_portfolio_from_another_user(self):
        other_user = User.objects.create_user(
            username="testuser2",
            password="test123",
        )
        other_portfolio = Portfolio.objects.create(
            user=other_user,
            name="Other Portfolio",
        )

        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": other_portfolio.id,
            },
        )

        self.assertIn(response.status_code, [403, 404])
        self.assertTrue(Portfolio.objects.filter(id=other_portfolio.id).exists())

    def test_delete_portfolio_missing_portfolio_id(self):
        response = self.client.post(
            reverse("delete_portfolio_data"),
            {},
        )

        self.assertEqual(response.status_code, 400)

    def test_delete_portfolio_invalid_portfolio_id(self):
        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": "invalid",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Portfolio.objects.filter(id=self.portfolio.id).exists())

    def test_delete_default_portfolio_is_blocked(self):
        self.portfolio.is_default = True
        self.portfolio.save(update_fields=["is_default"])

        response = self.client.post(
            reverse("delete_portfolio_data"),
            {
                "portfolio_id": self.portfolio.id,
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard"),
            fetch_redirect_response=False,
        )
        self.assertTrue(Portfolio.objects.filter(id=self.portfolio.id).exists())


class YFinancePriceUpdateTests(TestCase):
    def setUp(self):
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )

    def test_last_close_from_history(self):
        history = price_history([(date(2026, 4, 1), "123.45")])

        with patch.object(
            yfinance_server.yf,
            "Ticker",
            return_value=ticker_response(history),
        ):
            close_price, close_date, error = yfinance_server.get_last_close_price("CBA")

        self.assertEqual(close_price, Decimal("123.45"))
        self.assertEqual(close_date, date(2026, 4, 1))
        self.assertIsNone(error)

    def test_last_close_from_info(self):
        ticker = ticker_response(
            empty_price_history(),
            info={"regularMarketPrice": "9.87"},
        )

        with patch.object(yfinance_server.yf, "Ticker", return_value=ticker):
            close_price, close_date, error = yfinance_server.get_last_close_price(
                "TLS.AX"
            )

        self.assertEqual(close_price, Decimal("9.87"))
        self.assertEqual(close_date, date.today())
        self.assertIsNone(error)

    def test_last_close_rate_limited(self):
        ticker = ticker_response(
            empty_price_history(),
            error=yfinance_server.YFRateLimitError(),
        )

        with patch.object(yfinance_server.yf, "Ticker", return_value=ticker):
            close_price, close_date, error = yfinance_server.get_last_close_price("CBA")

        self.assertIsNone(close_price)
        self.assertIsNone(close_date)
        self.assertEqual(error, "rate_limited")

    def test_last_close_info_error(self):
        ticker = ticker_with_info_error(empty_price_history())

        with patch.object(yfinance_server.yf, "Ticker", return_value=ticker):
            close_price, close_date, error = yfinance_server.get_last_close_price("CBA")

        self.assertIsNone(close_price)
        self.assertIsNone(close_date)
        self.assertEqual(error, "fetch_failed")

    def test_update_skips_current_price(self):
        EquityPrice.objects.create(
            equity=self.equity,
            date=timezone.localdate(),
            closing_price="101.00",
        )

        result = yfinance_server.update_equity_price(self.equity)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_up_to_date")
        self.assertEqual(PriceUpdateLog.objects.get().status, "SKIPPED")

    @patch.object(yfinance_server, "get_last_close_price")
    def test_update_creates_price(self, get_last_close_price):
        get_last_close_price.return_value = (
            Decimal("101.25"),
            timezone.localdate(),
            None,
        )

        result = yfinance_server.update_equity_price(self.equity)

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["closing_price"], "101.25")
        self.assertTrue(EquityPrice.objects.filter(equity=self.equity).exists())
        self.assertEqual(PriceUpdateLog.objects.get().status, "SUCCESS")

    @patch.object(yfinance_server.time, "sleep")
    @patch.object(yfinance_server, "get_last_close_price")
    def test_update_uses_cached_rate_limit(self, get_last_close_price, sleep):
        yesterday = timezone.localdate() - timedelta(days=1)
        EquityPrice.objects.create(
            equity=self.equity,
            date=yesterday,
            closing_price="99.00",
        )
        get_last_close_price.return_value = (None, None, "rate_limited")

        result = yfinance_server.update_equity_price(self.equity)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "rate_limited_using_cached")
        self.assertEqual(result["closing_price"], "99.00")
        self.assertEqual(sleep.call_count, 2)

    @patch.object(yfinance_server.time, "sleep")
    @patch.object(yfinance_server, "get_last_close_price")
    def test_update_rate_limit_no_cache(self, get_last_close_price, sleep):
        get_last_close_price.return_value = (None, None, "rate_limited")

        result = yfinance_server.update_equity_price(self.equity)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "rate_limited")
        self.assertEqual(PriceUpdateLog.objects.get().status, "FAILED")
        self.assertEqual(sleep.call_count, 2)

    @patch.object(yfinance_server, "get_last_close_price")
    def test_update_no_data(self, get_last_close_price):
        get_last_close_price.return_value = (None, None, "no_data")

        result = yfinance_server.update_equity_price(self.equity)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "no_data")

    @patch.object(yfinance_server, "get_last_close_price")
    def test_update_fetch_error(self, get_last_close_price):
        get_last_close_price.return_value = (None, None, "fetch_failed")

        result = yfinance_server.update_equity_price(self.equity)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "fetch_failed")

    @patch.object(yfinance_server, "delete_old_equity_prices")
    @patch.object(yfinance_server.time, "sleep")
    @patch.object(yfinance_server, "update_equity_price")
    def test_update_all_counts_statuses(self, update_equity_price, sleep, delete_old):
        Equity.objects.create(
            ticker="BHP",
            name="BHP Group",
            currency="AUD",
            exchange="ASX",
        )
        Equity.objects.create(
            ticker="TLS",
            name="Telstra",
            currency="AUD",
            exchange="ASX",
        )
        Equity.objects.create(
            ticker="NAB",
            name="National Australia Bank",
            currency="AUD",
            exchange="ASX",
        )
        update_equity_price.side_effect = [
            {"status": "created"},
            {"status": "updated"},
            {"status": "skipped"},
            {"status": "failed"},
        ]
        delete_old.return_value = {"deleted": 2}

        summary = yfinance_server.update_all_equity_prices()

        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failed_tickers"], ["NAB"])
        self.assertEqual(summary["deleted_old_prices"], 2)
        self.assertEqual(sleep.call_count, 4)

    def test_history_returns_prices(self):
        history = price_history(
            [
                (date(2026, 4, 1), Decimal("10.00")),
                (date(2026, 4, 2), Decimal("12.00")),
            ]
        )

        with patch.object(
            yfinance_server.yf,
            "Ticker",
            return_value=ticker_response(history),
        ):
            prices, error = yfinance_server.get_equity_price_history(
                "CBA",
                period="5 days",
                display_currency="AUD",
            )

        self.assertIsNone(error)
        self.assertEqual(len(prices), 2)
        self.assertEqual(prices[0]["date"], "2026-04-01")
        self.assertEqual(prices[1]["closing_price"], 12.0)

    def test_history_no_data(self):
        with patch.object(
            yfinance_server.yf,
            "Ticker",
            return_value=ticker_response(empty_price_history()),
        ):
            prices, error = yfinance_server.get_equity_price_history("CBA")

        self.assertIsNone(prices)
        self.assertEqual(error, "no_data")

    def test_history_rate_limit(self):
        ticker = ticker_response(
            empty_price_history(),
            error=yfinance_server.YFRateLimitError(),
        )

        with patch.object(yfinance_server.yf, "Ticker", return_value=ticker):
            prices, error = yfinance_server.get_equity_price_history("CBA")

        self.assertIsNone(prices)
        self.assertEqual(error, "rate_limited")

    def test_history_fetch_error(self):
        ticker = ticker_response(empty_price_history(), error=Exception("boom"))

        with patch.object(yfinance_server.yf, "Ticker", return_value=ticker):
            prices, error = yfinance_server.get_equity_price_history("CBA")

        self.assertIsNone(prices)
        self.assertEqual(error, "fetch failed")

    def test_store_history_saves_prices(self):
        history = price_history(
            [
                (date(2026, 4, 1), Decimal("10.003")),
                (date(2026, 4, 2), Decimal("11.236")),
            ]
        )

        with patch.object(
            yfinance_server.yf,
            "Ticker",
            return_value=ticker_response(history),
        ):
            result = yfinance_server.store_equity_price_history(self.equity)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["saved"], 2)
        self.assertEqual(EquityPrice.objects.filter(equity=self.equity).count(), 2)
        self.assertEqual(PriceUpdateLog.objects.get().status, "SUCCESS")

    def test_store_history_no_data(self):
        with patch.object(
            yfinance_server.yf,
            "Ticker",
            return_value=ticker_response(empty_price_history()),
        ):
            result = yfinance_server.store_equity_price_history(self.equity)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "no_data")
        self.assertEqual(PriceUpdateLog.objects.get().status, "FAILED")

    def test_store_history_rate_limit(self):
        ticker = ticker_response(
            empty_price_history(),
            error=yfinance_server.YFRateLimitError(),
        )

        with patch.object(yfinance_server.yf, "Ticker", return_value=ticker):
            result = yfinance_server.store_equity_price_history(self.equity)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "rate_limited")
        self.assertEqual(PriceUpdateLog.objects.get().status, "FAILED")

    def test_store_history_fetch_error(self):
        ticker = ticker_response(empty_price_history(), error=Exception("boom"))

        with patch.object(yfinance_server.yf, "Ticker", return_value=ticker):
            result = yfinance_server.store_equity_price_history(self.equity)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "fetch_failed")
        self.assertEqual(PriceUpdateLog.objects.get().status, "FAILED")

    @patch.object(yfinance_server.time, "sleep")
    @patch.object(yfinance_server, "store_equity_price_history")
    def test_store_all_ounts_statuses(self, store_history, sleep):
        Equity.objects.create(
            ticker="BHP",
            name="BHP Group",
            currency="AUD",
            exchange="ASX",
        )
        store_history.side_effect = [
            {"status": "success", "saved": 2},
            {"status": "failed", "saved": 0},
        ]

        summary = yfinance_server.store_all_price_history()

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["total_saved"], 2)
        self.assertEqual(summary["failed_tickers"], ["BHP"])
        self.assertEqual(sleep.call_count, 2)

    @patch.object(yfinance_server.time, "sleep")
    @patch.object(yfinance_server, "store_equity_price_history")
    def test_store_all_counts_exceptions(self, store_history, sleep):
        store_history.side_effect = Exception("boom")

        summary = yfinance_server.store_all_price_history()

        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["success"], 0)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failed_tickers"], ["CBA"])
        sleep.assert_called_once()

    def test_delete_old_prices(self):
        old_date = timezone.localdate() - timedelta(days=371)
        recent_date = timezone.localdate()
        EquityPrice.objects.create(
            equity=self.equity,
            date=old_date,
            closing_price="10.00",
        )
        EquityPrice.objects.create(
            equity=self.equity,
            date=recent_date,
            closing_price="12.00",
        )

        result = yfinance_server.delete_old_equity_prices()

        self.assertEqual(result["deleted"], 1)
        self.assertTrue(EquityPrice.objects.filter(date=recent_date).exists())
