from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.test import SimpleTestCase, TestCase
from users.models import Profile

from .models import ExchangeRate
from .utils import (
    currency_conversion,
    format_currency,
    format_signed_currency,
    get_user_currency,
)
from .views import update_exchange_rates


class CurrencyConversionTests(TestCase):
    # Set fixed exchange rates to be used by each test
    @classmethod
    def setUpTestData(cls):
        ExchangeRate.objects.create(to_currency="USD", rate=0.65)
        ExchangeRate.objects.create(to_currency="NZD", rate=1.08)

    def test_same_currency_returns_rounded_amount(self):
        result = currency_conversion("12.345", "AUD", "AUD")

        self.assertEqual(result, Decimal("12.35"))

    def test_converts_from_aud_to_target_currency(self):
        result = currency_conversion("100.00", "AUD", "USD")

        self.assertEqual(result, Decimal("65.00"))

    def test_converts_from_target_currency_to_aud(self):
        result = currency_conversion("65.00", "USD", "AUD")

        self.assertEqual(result, Decimal("100.00"))

    def test_converts_between_two_non_aud_currencies_via_aud(self):
        result = currency_conversion("65.00", "USD", "NZD")

        self.assertEqual(result, Decimal("108.00"))

    def test_currency_codes_are_case_insensitive(self):
        result = currency_conversion("100.00", "aud", "usd")

        self.assertEqual(result, Decimal("65.00"))


class CurrencyFormattingTests(SimpleTestCase):
    def test_format_currency_uses_known_symbol_and_rounds(self):
        result = format_currency("12.345", "AUD")

        self.assertEqual(result, "A$12.35")

    def test_format_currency_falls_back_to_currency_code(self):
        result = format_currency("12", "XYZ")

        self.assertEqual(result, "XYZ 12.00")

    def test_format_signed_currency_adds_plus_and_minus_signs(self):
        self.assertEqual(format_signed_currency("12.30", "USD"), "+US$12.30")
        self.assertEqual(format_signed_currency("-12.30", "USD"), "-US$12.30")


class ExchangeRateUpdateTests(SimpleTestCase):
    @patch("currency.views.requests.get")
    def test_update_exchange_rates_skips_when_api_key_missing(self, mock_get):
        for api_key in ("", None):
            with self.subTest(api_key=api_key), self.settings(EXCHANGE_RATE_API_KEY=api_key):
                update_exchange_rates()

        mock_get.assert_not_called()


class UserCurrencyTests(TestCase):
    def test_get_user_currency_defaults_to_aud_for_anonymous_user(self):
        self.assertEqual(get_user_currency(AnonymousUser()), "AUD")

    def test_get_user_currency_uses_profile_currency(self):
        user = User.objects.create_user(
            username="currency-user",
            email="currency@example.com",
            password="testpass123",
        )
        Profile.objects.create(
            user=user,
            country="Australia",
            currency="usd",
        )

        self.assertEqual(get_user_currency(user), "USD")
