from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from portfolios.models import Portfolio
from subscriptions.models import StripeCustomer
from subscriptions.utils import get_or_create_local_subscription
from transactions.models import Equity, EquityPrice, Transaction
from users.models import Profile


class AuthCacheHeadersTests(TestCase):
    # We make user here
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="jamie@example.com",
            password="test123",
        )

    def test_dashboard_disables_browser_cache_for_authenticated_users(self):
        # Test login
        self.client.login(username="testuser", password="test123")

        # Sends a test HTTP GET request to /dashboard/ and makes sure its success (we're logged in)
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        cache_control = response.headers.get("Cache-Control", "")

        # Make sure we're not storing any cache
        self.assertIn("no-cache", cache_control)
        self.assertIn("no-store", cache_control)
        self.assertIn("must-revalidate", cache_control)
        self.assertIn("private", cache_control)

    def test_protected_page_redirects_after_logout(self):
        # Login and logout
        self.client.login(username="testuser", password="test123")
        self.client.get("/dashboard/")
        self.client.logout()

        # We shouldn't be able to access /dashboard/ because we're not logged in
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class DefaultPortfolioDashboardTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.premium_subscription = get_or_create_local_subscription("premium")
        self.user = User.objects.create_user(
            username="testuser",
            email="portfolio@example.com",
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

    def test_dashboard_creates_default_portfolio_when_user_has_none(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        default_portfolio = Portfolio.objects.get(user=self.user)
        self.assertEqual(default_portfolio.name, "Default Portfolio")
        self.assertTrue(default_portfolio.is_default)
        self.assertEqual(
            default_portfolio.description, "Let's start with your first portfolio!"
        )
        self.assertTrue(
            self.profile.portfolios.filter(id=default_portfolio.id).exists()
        )
        self.assertContains(response, "Default Portfolio")

    def test_dashboard_does_not_create_duplicate_default_portfolios(self):
        self.client.get(reverse("dashboard"))
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Portfolio.objects.filter(user=self.user, is_default=True).count(), 1
        )

    def test_dashboard_disables_create_portfolio_button_when_free_limit_reached(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are using 1 of 1 available portfolios.")
        self.assertContains(response, "Limit reached")
        self.assertNotContains(response, "+ Add a New Portfolio")

    def test_dashboard_shows_create_portfolio_button_when_premium(self):
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        stripe_customer.subscription = self.premium_subscription
        stripe_customer.save(update_fields=["subscription"])
        self.profile.subscription = self.premium_subscription
        self.profile.save(update_fields=["subscription"])

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage all of your portfolios in one place.")
        self.assertContains(response, "+ Add a New Portfolio")
        self.assertNotContains(response, "Limit reached")


class DashboardTransactionVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="jamie@example.com",
            password="test123",
        )
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Growth Portfolio",
            description="Main holdings",
        )
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        self.client.login(username="testuser", password="test123")

    def test_dashboard_shows_recent_transactions_section(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            price="123.45",
            trade_date="2026-04-02",
            currency="AUD",
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent Transactions")
        self.assertContains(response, "CBA")
        self.assertContains(response, "Growth Portfolio")
        self.assertContains(response, reverse("transactions:transactions_history"))

    def test_dashboard_only_shows_logged_in_users_transactions(self):
        other_user = User.objects.create_user(
            username="testuser2",
            email="other@example.com",
            password="test123",
        )
        other_portfolio = Portfolio.objects.create(
            user=other_user,
            name="Other Portfolio",
        )
        other_equity = Equity.objects.create(
            ticker="BHP",
            name="BHP Group",
            currency="AUD",
            exchange="ASX",
        )
        Transaction.objects.create(
            user=other_user,
            equity=other_equity,
            portfolio=other_portfolio,
            transaction_type="BUY",
            quantity=4,
            price="50.00",
            trade_date="2026-04-01",
            currency="AUD",
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BHP")
        self.assertNotContains(response, "Other Portfolio")


class DashboardPortfolioSummaryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="test123",
        )
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Income Portfolio",
            description="Dividend focus",
        )
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        self.client.login(username="testuser", password="test123")

    def test_dashboard_shows_portfolio_value_and_profit_loss(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            remaining_quantity=10,
            price="10.00",
            trade_date="2026-04-01",
            currency="AUD",
        )
        self.portfolio.holdings.create(equity=self.equity, quantity=10)
        EquityPrice.objects.create(
            equity=self.equity,
            date="2026-04-02",
            closing_price="15.00",
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current Value")
        self.assertContains(response, "Profit / Loss")
        self.assertContains(response, "A$150.00")
        self.assertContains(response, "+A$50.00")

    def test_dashboard_shows_expandable_holdings_breakdown(self):
        second_equity = Equity.objects.create(
            ticker="BHP",
            name="BHP Group",
            currency="AUD",
            exchange="ASX",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            remaining_quantity=10,
            price="10.00",
            trade_date="2026-04-01",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=second_equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=2,
            remaining_quantity=2,
            price="20.00",
            trade_date="2026-04-01",
            currency="AUD",
        )
        self.portfolio.holdings.create(equity=self.equity, quantity=10)
        self.portfolio.holdings.create(equity=second_equity, quantity=2)
        EquityPrice.objects.create(
            equity=self.equity,
            date="2026-04-02",
            closing_price="15.00",
        )
        EquityPrice.objects.create(
            equity=second_equity,
            date="2026-04-02",
            closing_price="18.00",
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View stocks")
        self.assertContains(response, "Commonwealth Bank of Australia")
        self.assertContains(response, "BHP Group")
        self.assertContains(response, "10 shares")
        self.assertContains(response, "2 shares")
        self.assertContains(response, "+A$50.00")
        self.assertContains(response, "-A$4.00")

    def test_dashboard_marks_unpriced_holdings_for_background_refresh(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            remaining_quantity=10,
            price="10.00",
            trade_date="2026-04-01",
            currency="AUD",
        )
        self.portfolio.holdings.create(equity=self.equity, quantity=10)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-needs-refresh="true"', html=False)
        self.assertContains(
            response,
            reverse("update_equity_view", args=[self.equity.ticker]),
        )
        self.assertContains(response, "Retrieving")
