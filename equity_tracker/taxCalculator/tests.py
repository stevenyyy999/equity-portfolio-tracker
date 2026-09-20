import csv
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from portfolios.models import Portfolio
from subscriptions.models import StripeCustomer
from subscriptions.utils import get_or_create_local_subscription
from transactions.models import Equity, Transaction
from users.models import Profile

from .utils import calculate_income_tax, is_discount_eligible


class CalculateIncomeTaxTests(TestCase):
    def test_progressive_tax_handles_income_crossing_into_next_bracket(self):
        self.assertEqual(calculate_income_tax(44000, 2025), Decimal("4128.00"))
        self.assertEqual(calculate_income_tax(49000, 2025), Decimal("5488.00"))

    def test_discount_eligibility_requires_day_after_anniversary(self):
        self.assertFalse(is_discount_eligible(date(2024, 8, 1), date(2025, 8, 1)))
        self.assertTrue(is_discount_eligible(date(2024, 8, 1), date(2025, 8, 2)))

    def test_discount_eligibility_handles_leap_year_acquisition_dates(self):
        self.assertFalse(is_discount_eligible(date(2024, 2, 29), date(2025, 3, 1)))
        self.assertTrue(is_discount_eligible(date(2024, 2, 29), date(2025, 3, 2)))


class CgtCalculatorViewTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.user = User.objects.create_user(
            username="cgtuser",
            email="cgt@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)
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
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="CGT Portfolio",
            description="Used for CGT tests",
        )
        self.profile.portfolios.add(self.portfolio)
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
            currency="AUD",
            exchange="ASX",
        )

    def test_calculator_accepts_exact_income_and_handles_bracket_crossover(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=100,
            price=Decimal("100.00"),
            trade_date="2025-07-01",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=100,
            price=Decimal("150.00"),
            trade_date="2025-08-01",
            currency="AUD",
        )

        response = self.client.post(
            f"{reverse('cgt-calculator')}?fy_year=2025",
            data={
                "prev_losses": "0",
                "income": "44000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1360.0")

    def test_calculator_rejects_negative_income(self):
        response = self.client.post(
            f"{reverse('cgt-calculator')}?fy_year=2025",
            data={
                "prev_losses": "0",
                "income": "-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Taxable income cannot be negative.")

    def test_assets_bought_on_pre_cgt_cutoff_date_are_taxable(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            price=Decimal("100.00"),
            trade_date="1985-09-20",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=10,
            price=Decimal("150.00"),
            trade_date="2025-08-01",
            currency="AUD",
        )

        response = self.client.post(
            f"{reverse('cgt-calculator')}?fy_year=2025",
            data={
                "prev_losses": "0",
                "income": "20000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Net Capital Gain")
        self.assertContains(response, "Before the CGT discount is applied")
        self.assertContains(response, "500.00")

    def test_discount_does_not_apply_on_exact_anniversary_date(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=100,
            price=Decimal("100.00"),
            trade_date="2024-08-01",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=100,
            price=Decimal("150.00"),
            trade_date="2025-08-01",
            currency="AUD",
        )

        response = self.client.post(
            f"{reverse('cgt-calculator')}?fy_year=2025",
            data={
                "prev_losses": "0",
                "income": "20000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "5000.00")
        self.assertContains(response, "800.0")

    def test_discount_applies_day_after_one_year_anniversary(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=100,
            price=Decimal("100.00"),
            trade_date="2024-08-01",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=100,
            price=Decimal("150.00"),
            trade_date="2025-08-02",
            currency="AUD",
        )

        response = self.client.post(
            f"{reverse('cgt-calculator')}?fy_year=2025",
            data={
                "prev_losses": "0",
                "income": "20000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "5000.00")
        self.assertContains(response, "400.0")

    def test_post_result_keeps_financial_year_years_in_header(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            price=Decimal("100.00"),
            trade_date="2025-07-01",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=10,
            price=Decimal("150.00"),
            trade_date="2025-08-01",
            currency="AUD",
        )

        response = self.client.post(
            f"{reverse('cgt-calculator')}?fy_year=2025",
            data={
                "prev_losses": "0",
                "income": "20000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2025-2026")

    def test_export_cgt_csv_uses_submitted_form_values(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=100,
            price=Decimal("100.00"),
            trade_date="2024-08-01",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=100,
            price=Decimal("150.00"),
            trade_date="2025-08-02",
            currency="AUD",
        )

        response = self.client.get(
            reverse("export_cgt_csv"),
            {
                "fy_year": "2025",
                "prev_losses": "1000",
                "income": "50000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")

        rows = list(csv.reader(StringIO(response.content.decode("utf-8"))))
        self.assertEqual(rows[1][1], "50000")
        self.assertEqual(rows[1][2], "1000")

    def test_calculator_results_page_keeps_export_link_inputs(self):
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            price=Decimal("100.00"),
            trade_date="2025-07-01",
            currency="AUD",
        )
        Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=10,
            price=Decimal("150.00"),
            trade_date="2025-08-01",
            currency="AUD",
        )

        response = self.client.post(
            f"{reverse('cgt-calculator')}?fy_year=2025",
            data={
                "prev_losses": "1000.25",
                "income": "50000",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"{reverse('export_cgt_csv')}?fy_year=2025&prev_losses=1000.25&income=50000",
        )
