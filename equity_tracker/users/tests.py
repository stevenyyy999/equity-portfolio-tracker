import json
from unittest.mock import patch

import stripe
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from portfolios.models import Holdings, Portfolio
from subscriptions.models import StripeCustomer, StripeTransaction
from subscriptions.utils import get_or_create_local_subscription
from transactions.models import Equity, Transaction, TransactionMatch

from .models import Profile


class ProfileViewTests(TestCase):
    # Create a logged-in user for each test case.
    def setUp(self):
        self.user = User.objects.create_user(username="jamie", password="testpass123")
        self.client.login(username="jamie", password="testpass123")

    # Check that GET returns the saved profile data for the logged-in user.
    def test_get_profile_returns_user_data(self):
        Profile.objects.create(
            user=self.user,
            address="123 George St",
            country="Australia",
            currency="AUD",
        )

        response = self.client.get("/users/profile/")

        # ensures everything is OK
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["country"], "Australia")
        self.assertEqual(response.json()["currency"], "AUD")
        self.assertTrue(response.json()["cgt_enabled"])

    # Check that POST updates both User and Profile model fields.
    def test_post_profile_updates_user_data(self):
        Profile.objects.create(
            user=self.user,
            address="Old address",
            country="Australia",
            currency="AUD",
        )

        response = self.client.post(
            "/users/profile/",
            data=json.dumps(
                {
                    "first_name": "Jamie",
                    "last_name": "Bong",
                    "address": "456 Pitt St",
                    "country": "Australia",
                    "currency": "AUD",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cgt_enabled"])

        self.user.refresh_from_db()
        profile = Profile.objects.get(user=self.user)

        # OK
        self.assertEqual(self.user.first_name, "Jamie")
        self.assertEqual(self.user.last_name, "Bong")
        self.assertEqual(profile.address, "456 Pitt St")

    # Check that CGT is disabled when the saved country is not Australia.
    def test_cgt_disabled_for_non_australian_country(self):
        Profile.objects.create(
            user=self.user,
            address="123 George St",
            country="New Zealand",
            currency="NZD",
        )

        response = self.client.get("/users/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["cgt_enabled"])


class EmailChangePageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jamie",
            email="jamie@example.com",
            password="testpass123",
        )
        self.client.login(username="jamie", password="testpass123")

    def test_save_new_email_creates_pending_record(self):
        response = self.client.post(
            "/accounts/profile/",
            {
                "email": "updated@example.com",
                "action": "save",
            },
        )

        self.assertEqual(response.status_code, 200)

        pending_email = EmailAddress.objects.get(
            user=self.user, email="updated@example.com"
        )
        self.assertFalse(pending_email.verified)
        self.assertFalse(pending_email.primary)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "jamie@example.com")

    def test_cannot_add_second_pending_email(self):
        EmailAddress.objects.create(
            user=self.user,
            email="pending@example.com",
            primary=False,
            verified=False,
        )

        response = self.client.post(
            "/accounts/profile/",
            {
                "email": "another@example.com",
                "action": "save",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "You already have a pending email change. Verify or cancel it before entering another email.",
        )
        self.assertTrue(
            EmailAddress.objects.filter(
                user=self.user, email="pending@example.com"
            ).exists()
        )
        self.assertFalse(
            EmailAddress.objects.filter(
                user=self.user, email="another@example.com"
            ).exists()
        )

    def test_can_delete_non_primary_email_record(self):
        EmailAddress.objects.create(
            user=self.user,
            email="old@example.com",
            primary=False,
            verified=True,
        )

        response = self.client.post(
            "/accounts/profile/",
            {
                "action": "delete_record",
                "delete_email": "old@example.com",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            EmailAddress.objects.filter(
                user=self.user, email="old@example.com"
            ).exists()
        )

    def test_verified_existing_email_is_reused_without_pending(self):
        EmailAddress.objects.create(
            user=self.user,
            email="oldverified@example.com",
            primary=False,
            verified=True,
        )

        response = self.client.post(
            "/accounts/profile/",
            {
                "email": "oldverified@example.com",
                "action": "save",
            },
        )

        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        reused_email = EmailAddress.objects.get(
            user=self.user, email="oldverified@example.com"
        )

        self.assertEqual(self.user.email, "oldverified@example.com")
        self.assertTrue(reused_email.primary)
        self.assertTrue(reused_email.verified)
        self.assertFalse(
            EmailAddress.objects.filter(
                user=self.user,
                email="oldverified@example.com",
                verified=False,
            ).exists()
        )


class ProfilePageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jamie",
            email="jamie@example.com",
            password="testpass123",
        )
        self.client.login(username="jamie", password="testpass123")

    def test_profile_page_includes_delete_account_action(self):
        response = self.client.get(reverse("account-profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delete Account")
        self.assertContains(response, reverse("delete-account"))


class SocialLoginSettingsTests(TestCase):
    def test_google_social_login_skips_confirmation_page(self):
        self.assertTrue(settings.SOCIALACCOUNT_LOGIN_ON_GET)


class AccountDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="jamie",
            email="jamie@example.com",
            password="testpass123",
        )
        self.client.login(username="jamie", password="testpass123")

        self.profile = Profile.objects.create(
            user=self.user,
            address="123 George St",
            country="Australia",
            currency="AUD",
        )
        self.portfolio = Portfolio.objects.create(
            user=self.user,
            name="Growth",
            description="Long term holdings",
        )
        self.equity = Equity.objects.create(
            ticker="CBA",
            name="Commonwealth Bank of Australia",
        )
        self.holding = Holdings.objects.create(
            portfolio=self.portfolio,
            equity=self.equity,
            quantity=10,
        )
        self.buy_transaction = Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="BUY",
            quantity=10,
            remaining_quantity=0,
            price="100.00",
            trade_date="2024-01-01",
        )
        self.sell_transaction = Transaction.objects.create(
            user=self.user,
            equity=self.equity,
            portfolio=self.portfolio,
            transaction_type="SELL",
            quantity=5,
            remaining_quantity=0,
            price="110.00",
            trade_date="2024-02-01",
        )
        self.transaction_match = TransactionMatch.objects.create(
            buy_transaction=self.buy_transaction,
            sell_transaction=self.sell_transaction,
            matched_quantity=5,
        )
        self.email_address = EmailAddress.objects.create(
            user=self.user,
            email="jamie@example.com",
            primary=True,
            verified=True,
        )

    def test_delete_account_removes_user_and_related_data(self):
        response = self.client.post(reverse("delete-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "Account deleted successfully.")
        self.assertEqual(response.json()["redirect_url"], "/")

        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Profile.objects.filter(pk=self.profile.pk).exists())
        self.assertFalse(Portfolio.objects.filter(pk=self.portfolio.pk).exists())
        self.assertFalse(Holdings.objects.filter(pk=self.holding.pk).exists())
        self.assertFalse(
            Transaction.objects.filter(pk=self.buy_transaction.pk).exists()
        )
        self.assertFalse(
            Transaction.objects.filter(pk=self.sell_transaction.pk).exists()
        )
        self.assertFalse(
            TransactionMatch.objects.filter(pk=self.transaction_match.pk).exists()
        )
        self.assertFalse(EmailAddress.objects.filter(pk=self.email_address.pk).exists())

    def test_deleted_user_cannot_access_dashboard_or_log_back_in(self):
        self.client.post(reverse("delete-account"))

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.assertFalse(self.client.login(username="jamie", password="testpass123"))

    def test_delete_account_requires_login(self):
        self.client.logout()

        response = self.client.post(reverse("delete-account"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    # Ensures Stripe API doesn't actually call
    @patch("subscriptions.utils.stripe.Customer.delete")
    @patch("subscriptions.utils.stripe.Subscription.cancel")
    def test_delete_account_cancels_stripe_subscription_and_deletes_customer(
        self,
        mock_cancel,
        mock_delete_customer,
    ):
        subscription = get_or_create_local_subscription("premium")

        customer = StripeCustomer.objects.create(
            user=self.user,
            subscription=subscription,
            stripeCustomerId="cus_test",
            stripeSubscriptionId="sub_test",
            status="active",
        )
        stripe_transaction = StripeTransaction.objects.create(
            customer=customer,
            stripePaymentId="price_test",
            currency="AUD",
            amount=1,
            status="paid",
        )

        response = self.client.post(reverse("delete-account"))

        self.assertEqual(response.status_code, 200)
        mock_cancel.assert_called_once_with("sub_test")
        mock_delete_customer.assert_called_once_with("cus_test")
        self.assertFalse(StripeCustomer.objects.filter(pk=customer.pk).exists())
        self.assertFalse(
            StripeTransaction.objects.filter(pk=stripe_transaction.pk).exists()
        )

    # This test proves we don't delete the local django account if stripe cancelleation fails
    @patch(
        "subscriptions.utils.stripe.Subscription.cancel",
        side_effect=stripe.error.StripeError("Stripe unavailable"),
    )
    def test_delete_account_keeps_local_account_when_stripe_cancel_fails(
        self,
        mock_cancel,
    ):
        subscription = get_or_create_local_subscription("premium")
        StripeCustomer.objects.create(
            user=self.user,
            subscription=subscription,
            stripeCustomerId="cus_test",
            stripeSubscriptionId="sub_test",
            status="active",
        )

        # the user clicks delete account
        response = self.client.post(reverse("delete-account"))

        # the backend should've tried to cancel/delete stripe record, but fail
        self.assertEqual(response.status_code, 400)
        self.assertIn("Stripe cancellation failed", response.json()["error"])
        # Make sure the mock function was actually called
        mock_cancel.assert_called_once_with("sub_test")
        # Make sure the local Django user and local StripeCustomer row still exist.
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(StripeCustomer.objects.filter(user=self.user).exists())

    def test_delete_account_never_subscribed(self):
        response = self.client.post(reverse("delete-account"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
