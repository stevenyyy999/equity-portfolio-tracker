import csv
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from portfolios.models import Holdings, Portfolio
from subscriptions.models import StripeCustomer
from subscriptions.utils import get_or_create_local_subscription
from users.models import Profile

from .admin import ASXEquityListAdmin, TransactionMatchAdmin
from .models import ASXEquityList, Equity, Transaction, TransactionMatch
from .views import validate_transaction_data_helper


class ValidateTransactionDataHelperTests(SimpleTestCase):
    def test_rejects_ticker_longer_than_five_characters(self):
        result = validate_transaction_data_helper(
            "WDDWDW",
            "BUY",
            "23",
            "234",
            "2026-04-02",
        )

        self.assertEqual(
            result["errors"]["ticker"],
            "Ticker must be 5 characters or fewer",
        )


class ASXEquitySeedTests(TestCase):
    def test_asx_equity_list_is_loaded_by_migrations(self):
        self.assertGreater(ASXEquityList.objects.count(), 1000)
        self.assertTrue(
            ASXEquityList.objects.filter(
                ticker="CBA",
                name="COMMONWEALTH BANK OF AUSTRALIA.",
            ).exists()
        )


class TickerSuggestionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="suggestions",
            email="suggestions@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)
        ASXEquityList.objects.all().delete()
        ASXEquityList.objects.bulk_create(
            [
                ASXEquityList(ticker="CBA", name="Commonwealth Bank of Australia"),
                ASXEquityList(ticker="CBL", name="Control Bionics Limited"),
                ASXEquityList(ticker="BHP", name="BHP Group"),
            ]
        )

    def test_returns_matching_ticker_prefixes(self):
        response = self.client.get(
            reverse("transactions:ticker_suggestions"),
            {"q": "CB"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "results": [
                    {"ticker": "CBA", "name": "Commonwealth Bank of Australia"},
                    {"ticker": "CBL", "name": "Control Bionics Limited"},
                ]
            },
        )

    def test_lowercase_query_matches_uppercase_tickers(self):
        response = self.client.get(
            reverse("transactions:ticker_suggestions"),
            {"q": "cb"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"][0],
            {"ticker": "CBA", "name": "Commonwealth Bank of Australia"},
        )

    def test_blank_query_returns_empty_results(self):
        response = self.client.get(
            reverse("transactions:ticker_suggestions"),
            {"q": "   "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"results": []})

    def test_requires_login(self):
        self.client.logout()

        response = self.client.get(
            reverse("transactions:ticker_suggestions"),
            {"q": "CB"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class TransactionFlowTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.user = User.objects.create_user(
            username="flowuser",
            email="flow@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

        self.profile = Profile.objects.create(
            user=self.user,
            country="Australia",
            subscription=self.free_subscription,
        )
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
        )
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Test Portfolio",
            description="Flow test portfolio",
        )
        self.profile.portfolios.add(self.portfolio)
        ASXEquityList.objects.all().delete()
        ASXEquityList.objects.bulk_create(
            [
                ASXEquityList(ticker="CBA", name="Commonwealth Bank of Australia"),
                ASXEquityList(ticker="BHP", name="BHP Group"),
            ]
        )

    def test_manual_entry_creates_transaction_for_portfolio(self):
        response = self.client.post(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "ticker": "CBA",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        self.assertRedirects(
            response,
            reverse("portfolio_detail", args=[self.portfolio.id]),
        )
        transaction = Transaction.objects.get()
        self.assertEqual(transaction.portfolio, self.portfolio)
        self.assertEqual(transaction.equity.ticker, "CBA")
        self.assertEqual(transaction.transaction_type, "BUY")

    def test_manual_entry_form_includes_equity_limit_feedback_controls(self):
        response = self.client.get(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="equity-limit-message"')
        self.assertContains(response, 'id="add-transaction-button"')

    # there was a bug where making an account did not create a corresponding profile row.
    # this test exists to make sure that doesn't happen
    def test_manual_entry_creates_missing_profile(self):
        # make user
        user = User.objects.create_user(
            username="testuser2",
            email="missingprofile@example.com",
            password="test123",
        )
        self.client.force_login(user)
        portfolio = Portfolio.objects.create(
            user=user,
            name="No Profile Portfolio",
        )

        # we should be able to add
        response = self.client.post(
            reverse("transactions:create_transaction", args=[portfolio.id]),
            data={
                "portfolio_id": str(portfolio.id),
                "ticker": "CBA",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        # OK, CBA gets added and no problems
        self.assertRedirects(response, reverse("portfolio_detail", args=[portfolio.id]))
        profile = Profile.objects.get(user=user)
        self.assertTrue(profile.equities.filter(ticker="CBA").exists())
        self.assertEqual(Transaction.objects.filter(user=user).count(), 1)

    def test_manual_entry_blocks_equity_already_held_in_another_portfolio(self):
        other_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Income Portfolio",
        )
        self.profile.portfolios.add(other_portfolio)
        equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        Holdings.objects.create(
            portfolio=other_portfolio,
            equity=equity,
            quantity=5,
        )

        response = self.client.post(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "ticker": "CBA",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "CBA is already held in Income Portfolio. An equity can only belong to one portfolio.",
        )
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertFalse(
            Holdings.objects.filter(portfolio=self.portfolio, equity=equity).exists()
        )

    def test_manual_entry_page_exposes_ticker_suggestion_endpoint(self):
        response = self.client.get(
            reverse("transactions:create_transaction", args=[self.portfolio.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="ticker-input"')
        self.assertContains(
            response,
            reverse("transactions:ticker_suggestions"),
        )

    def test_csv_upload_creates_transactions_for_portfolio(self):
        csv_file = SimpleUploadedFile(
            "transactions.csv",
            (
                "ticker,transaction_type,quantity,price,trade_date\n"
                "CBA,BUY,10,123.45,2026-04-02\n"
                "BHP,BUY,5,50.00,2026-04-03\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("transactions:upload_transactions_csv", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "file": csv_file,
            },
        )

        self.assertRedirects(
            response,
            reverse("portfolio_detail", args=[self.portfolio.id]),
        )
        self.assertEqual(
            Transaction.objects.filter(portfolio=self.portfolio).count(),
            2,
        )

    def test_csv_upload_blocks_equity_already_held_in_another_portfolio(self):
        other_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Income Portfolio",
        )
        self.profile.portfolios.add(other_portfolio)
        equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        Holdings.objects.create(
            portfolio=other_portfolio,
            equity=equity,
            quantity=5,
        )
        csv_file = SimpleUploadedFile(
            "transactions.csv",
            (
                "ticker,transaction_type,quantity,price,trade_date\n"
                "CBA,BUY,10,123.45,2026-04-02\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("transactions:upload_transactions_csv", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "file": csv_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Row 2:")
        self.assertContains(
            response,
            "ticker - CBA is already held in Income Portfolio. An equity can only belong to one portfolio.",
        )
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertFalse(
            Holdings.objects.filter(portfolio=self.portfolio, equity=equity).exists()
        )

    def test_csv_upload_without_file_rerenders_form_with_error(self):
        response = self.client.post(
            reverse("transactions:upload_transactions_csv", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please choose a CSV file to upload.")
        self.assertEqual(
            Transaction.objects.filter(portfolio=self.portfolio).count(),
            0,
        )

    def test_free_user_can_add_fifth_distinct_equity_manually(self):
        for index in range(4):
            equity = Equity.objects.create(
                ticker=f"EQ{index}",
                name=f"Equity {index}",
                currency="AUD",
                exchange="ASX",
            )
            self.profile.equities.add(equity)

        response = self.client.post(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "ticker": "CBA",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        self.assertRedirects(
            response,
            reverse("portfolio_detail", args=[self.portfolio.id]),
        )
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(self.profile.equities.count(), 5)

    def test_free_user_cannot_add_sixth_distinct_equity_manually(self):
        for index in range(5):
            equity = Equity.objects.create(
                ticker=f"EQ{index}",
                name=f"Equity {index}",
                currency="AUD",
                exchange="ASX",
            )
            self.profile.equities.add(equity)

        response = self.client.post(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "ticker": "CBA",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "transactions/enter_manual.html")
        self.assertContains(response, "You have reached the Free plan limit.")
        self.assertContains(response, 'data-limit-reached="true"', html=False)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(self.profile.equities.count(), 5)

    def test_ticker_portfolio_check_reports_equity_limit_for_new_equity(self):
        for index in range(5):
            equity = Equity.objects.create(
                ticker=f"EQ{index}",
                name=f"Equity {index}",
                currency="AUD",
                exchange="ASX",
            )
            self.profile.equities.add(equity)

        response = self.client.get(
            reverse("transactions:check_ticker_portfolio"),
            {"ticker": "CBA"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "exists": False,
                "is_new_equity": True,
                "equity_limit_reached": True,
                "equity_limit_error": "Equity limit reached",
            },
        )

    def test_ticker_portfolio_check_allows_already_tracked_equity_at_limit(self):
        cba_equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        self.profile.equities.add(cba_equity)
        for index in range(4):
            equity = Equity.objects.create(
                ticker=f"EQ{index}",
                name=f"Equity {index}",
                currency="AUD",
                exchange="ASX",
            )
            self.profile.equities.add(equity)

        response = self.client.get(
            reverse("transactions:check_ticker_portfolio"),
            {"ticker": "CBA"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "exists": False,
                "is_new_equity": False,
                "equity_limit_reached": False,
                "equity_limit_error": "",
            },
        )

    def test_export_transactions_csv_respects_active_filters(self):
        cba_equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        bhp_equity = Equity.objects.create(
            ticker="BHP",
            name="BHP Group",
            currency="AUD",
            exchange="ASX",
        )
        Transaction.objects.create(
            user=self.user,
            equity=cba_equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            price="123.45",
            trade_date="2026-04-02",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=bhp_equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=5,
            price="50.00",
            trade_date="2026-04-03",
            currency="AUD",
        )

        response = self.client.get(
            reverse("transactions:export_transactions_csv"),
            {
                "ticker": "CBA",
                "start_date": "2026-04-01",
                "end_date": "2026-04-02",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")

        rows = list(csv.reader(StringIO(response.content.decode("utf-8"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "CBA")
        self.assertNotIn("BHP", response.content.decode("utf-8"))

    def test_free_user_can_upload_csv_when_it_adds_only_one_new_equity(self):
        existing_equities = []
        for index in range(4):
            equity = Equity.objects.create(
                ticker=f"EQ{index}",
                name=f"Equity {index}",
                currency="AUD",
                exchange="ASX",
            )
            self.profile.equities.add(equity)
            existing_equities.append(equity)

        csv_file = SimpleUploadedFile(
            "transactions.csv",
            (
                "ticker,transaction_type,quantity,price,trade_date\n"
                f"{existing_equities[0].ticker},BUY,10,123.45,2026-04-02\n"
                "CBA,BUY,5,50.00,2026-04-03\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("transactions:upload_transactions_csv", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "file": csv_file,
            },
        )

        self.assertRedirects(
            response,
            reverse("portfolio_detail", args=[self.portfolio.id]),
        )
        self.assertEqual(
            Transaction.objects.filter(portfolio=self.portfolio).count(), 2
        )
        self.assertEqual(self.profile.equities.count(), 5)

    def test_free_user_cannot_upload_csv_that_exceeds_equity_limit(self):
        for index in range(4):
            equity = Equity.objects.create(
                ticker=f"EQ{index}",
                name=f"Equity {index}",
                currency="AUD",
                exchange="ASX",
            )
            self.profile.equities.add(equity)

        csv_file = SimpleUploadedFile(
            "transactions.csv",
            (
                "ticker,transaction_type,quantity,price,trade_date\n"
                "CBA,BUY,10,123.45,2026-04-02\n"
                "BHP,BUY,5,50.00,2026-04-03\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("transactions:upload_transactions_csv", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "file": csv_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "transactions/upload_csv.html")

        view_context = response.context[-1]
        self.assertIn("error_message", view_context)
        self.assertEqual(
            view_context["error_message"],
            "Adding these equities would exceed your allowed limit.",
        )
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(self.profile.equities.count(), 4)

    def test_manual_entry_uses_company_name_from_asx_list_when_creating_equity(self):
        response = self.client.post(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "ticker": "CBA",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        self.assertRedirects(
            response,
            reverse("portfolio_detail", args=[self.portfolio.id]),
        )
        equity = Equity.objects.get(ticker="CBA")
        self.assertEqual(equity.name, "Commonwealth Bank of Australia")

    def test_manual_entry_rejects_ticker_not_in_asx_list(self):
        response = self.client.post(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "ticker": "XXXX",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "XXXX is not an existing ASX ticker")
        self.assertEqual(Transaction.objects.count(), 0)

    def test_manual_entry_shows_clear_error_when_asx_list_is_not_loaded(self):
        ASXEquityList.objects.all().delete()

        response = self.client.post(
            reverse("transactions:create_transaction", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "ticker": "CBA",
                "transaction_type": "BUY",
                "quantity": "10",
                "price": "123.45",
                "trade_date": "2026-04-02",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "The ASX equity list has not been loaded yet. Please import it before creating new equities.",
        )
        self.assertEqual(Transaction.objects.count(), 0)

    def test_csv_upload_skips_ticker_not_in_asx_list(self):
        csv_file = SimpleUploadedFile(
            "transactions.csv",
            (
                "ticker,transaction_type,quantity,price,trade_date\n"
                "CBA,BUY,10,123.45,2026-04-02\n"
                "XXXX,BUY,5,50.00,2026-04-03\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("transactions:upload_transactions_csv", args=[self.portfolio.id]),
            data={
                "portfolio_id": str(self.portfolio.id),
                "file": csv_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Imported 1 transaction successfully.")
        self.assertContains(response, "Row 3:")
        self.assertContains(response, "ticker - XXXX is not an existing ASX ticker")
        self.assertEqual(
            Transaction.objects.filter(portfolio=self.portfolio).count(), 1
        )
        self.assertTrue(
            Transaction.objects.filter(
                portfolio=self.portfolio,
                equity__ticker="CBA",
                transaction_type="BUY",
            ).exists()
        )
        self.assertFalse(
            Transaction.objects.filter(
                portfolio=self.portfolio,
                equity__ticker="XXXX",
            ).exists()
        )


class DeleteTransactionTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.user = User.objects.create_user(
            username="jamie",
            password="testpass123",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            country="Australia",
            currency="AUD",
            subscription=self.free_subscription,
        )
        self.client.login(username="jamie", password="testpass123")

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

        self.profile.portfolios.add(self.portfolio)
        ASXEquityList.objects.all().delete()
        ASXEquityList.objects.bulk_create(
            [
                ASXEquityList(ticker="CBA", name="Commonwealth Bank of Australia"),
                ASXEquityList(ticker="BHP", name="BHP Group"),
            ]
        )

    def test_delete_transaction_removes_transaction(self):
        transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(id=transaction.id).exists())

    def test_delete_matched_buy_transaction(self):
        buy_transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )
        sell_transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="SELL",
            quantity=10,
            price=110.00,
            trade_date="2026-05-02",
        )
        TransactionMatch.objects.create(
            buy_transaction=buy_transaction,
            sell_transaction=sell_transaction,
            matched_quantity=10,
        )

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": buy_transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(id=buy_transaction.id).exists())

    def test_delete_unmatched_buy_transaction(self):
        buy_transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": buy_transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(id=buy_transaction.id).exists())

    def test_delete_sell_transaction(self):
        sell_transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="SELL",
            quantity=10,
            price=110.00,
            trade_date="2026-05-02",
        )

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": sell_transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(id=sell_transaction.id).exists())

    def test_delete_sell_transaction_restores_holding_quantity(self):
        buy_transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            remaining_quantity=0,
            price=100.00,
            trade_date="2026-04-02",
        )
        sell_transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="SELL",
            quantity=10,
            price=110.00,
            trade_date="2026-05-02",
        )
        TransactionMatch.objects.create(
            buy_transaction=buy_transaction,
            sell_transaction=sell_transaction,
            matched_quantity=10,
        )

        # simulate that the sell already reduced the holding to 0
        self.holding.quantity = 0
        self.holding.save()

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": sell_transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.filter(id=sell_transaction.id).exists())
        holding = Holdings.objects.get(portfolio=self.portfolio, equity=self.equity)
        self.assertEqual(holding.quantity, 10)

    def test_delete_transaction_frees_equity_if_last_transaction(self):
        self.profile.equities.add(self.equity)
        transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )

        self.assertEqual(self.profile.equities.count(), 1)

        self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(self.profile.equities.count(), 0)

    def test_delete_transaction_preserves_equity_if_other_transactions_exist(self):
        self.profile.equities.add(self.equity)

        other_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Other Portfolio",
        )
        Holdings.objects.create(
            portfolio=other_portfolio,
            equity=self.equity,
            quantity=10,
        )
        transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )
        Transaction.objects.create(
            user=self.user,
            portfolio=other_portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )

        self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(self.profile.equities.count(), 1)

    def test_delete_transaction_requires_login(self):
        self.client.logout()
        transaction = Transaction.objects.create(
            user=self.user,
            portfolio=self.portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(id=transaction.id).exists())

    def test_delete_transaction_from_another_user(self):
        other_user = User.objects.create_user(
            username="intruder",
            password="testpass123",
        )
        other_portfolio = Portfolio.objects.create(
            user=other_user,
            name="Other Portfolio",
        )
        other_transaction = Transaction.objects.create(
            user=other_user,
            portfolio=other_portfolio,
            equity=self.equity,
            transaction_type="BUY",
            quantity=10,
            price=100.00,
            trade_date="2026-04-02",
        )

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": other_transaction.id},
            HTTP_REFERER="/transactions/",
        )

        self.assertIn(response.status_code, [403, 404])
        self.assertTrue(Transaction.objects.filter(id=other_transaction.id).exists())

    def test_delete_transaction_missing_transaction_id(self):
        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {},
        )

        self.assertEqual(response.status_code, 400)

    def test_delete_transaction_invalid_transaction_id(self):
        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": "invalid"},
        )

        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("transactions:delete_transaction_data"),
            {"transaction_id": 9999},
        )

        self.assertEqual(response.status_code, 404)


class TransactionAdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testadmin",
            email="testadmin@example.com",
            password="testpass123",
        )
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Admin Portfolio",
        )
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )
        self.buy = Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            price="10.00",
            trade_date="2026-04-01",
            currency="AUD",
        )
        self.sell = Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=4,
            price="12.00",
            trade_date="2026-04-02",
            currency="AUD",
        )

    def test_transaction_match_admin_display_helpers_read_related_transactions(self):
        match = self.buy.sell_matches_from_buy.create(
            sell_transaction=self.sell,
            matched_quantity=4,
        )
        admin_model = TransactionMatchAdmin(TransactionMatch, admin.site)

        self.assertEqual(admin_model.buy_transaction_id_display(match), self.buy.id)
        self.assertEqual(admin_model.buy_trade_date(match), self.buy.trade_date)
        self.assertEqual(admin_model.buy_price(match), self.buy.price)
        self.assertEqual(admin_model.sell_transaction_id_display(match), self.sell.id)
        self.assertEqual(admin_model.sell_trade_date(match), self.sell.trade_date)
        self.assertEqual(admin_model.sell_price(match), self.sell.price)

    def test_asx_admin_import_csv_creates_and_updates_rows(self):
        ASXEquityList.objects.create(
            ticker="ZZZ",
            name="Old ZZZ",
            industry="Old industry",
        )
        csv_file = SimpleUploadedFile(
            "asx.csv",
            (
                b"ASX code,Company name,GICs industry group\n"
                b"ZZZ,Example Company,Banks\n"
                b"BHP,BHP Group,Materials\n"
                b",Missing Ticker,Banks\n"
            ),
            content_type="text/csv",
        )
        request = SimpleNamespace(
            method="POST",
            FILES={"file": csv_file},
            POST={"clear_existing": ""},
        )
        admin_model = ASXEquityListAdmin(ASXEquityList, admin.site)

        with patch("transactions.admin.messages.success") as success_message:
            with patch("transactions.admin.redirect") as redirect_response:
                redirect_response.return_value = "redirected"
                response = admin_model.import_csv_view(request)

        self.assertEqual(response, "redirected")
        self.assertEqual(
            ASXEquityList.objects.get(ticker="ZZZ").name, "Example Company"
        )
        self.assertTrue(ASXEquityList.objects.filter(ticker="BHP").exists())
        success_message.assert_called_once()

    def test_asx_admin_import_csv_rejects_non_csv_files(self):
        request = SimpleNamespace(
            method="POST",
            FILES={"file": SimpleUploadedFile("asx.txt", b"not csv")},
            POST={},
        )
        admin_model = ASXEquityListAdmin(ASXEquityList, admin.site)

        with patch("transactions.admin.messages.error") as error_message:
            with patch("transactions.admin.redirect") as redirect_response:
                redirect_response.return_value = "redirected"
                response = admin_model.import_csv_view(request)

        self.assertEqual(response, "redirected")
        error_message.assert_called_once_with(request, "Only csv file is allowed")
