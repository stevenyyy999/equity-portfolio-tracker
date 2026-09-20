from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from portfolios.models import Portfolio
from transactions.models import Equity, Transaction
from users.models import Profile

from subscriptions.models import StripeCustomer
from subscriptions.services import isUserSubscribed
from subscriptions.signals import initial_user_setup
from subscriptions.utils import (
    get_or_create_local_subscription,
    get_stripe_subscription_product,
    user_cancels_stripe,
    user_delete_stripe_customer,
    validate_billing_end,
    validate_stripe_subscription_plan,
)


# Test helper functions for simulating Stripe subscription data
def stripe_subscription(
    price_id="price_premium",
    product_id="prod_premium",
    status="active",
    subscription_id="sub_test",
    current_period_end=1893456000,
):
    return {
        "id": subscription_id,
        "status": status,
        "current_period_end": current_period_end,
        "items": {
            "data": [
                {
                    "price": {
                        "id": price_id,
                        "unit_amount": 1299,
                        "recurring": {"interval": "month"},
                        "product": product_id,
                    }
                }
            ]
        },
    }


# Actual tests
class SubscriptionUtilityTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.premium_subscription = get_or_create_local_subscription("premium")
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="test123",
        )

    def test_billing_end_from_timestamp(self):
        subscription = SimpleNamespace(current_period_end=1893456000)

        billing_end = validate_billing_end(subscription)

        self.assertEqual(billing_end.year, 2030)
        self.assertEqual(billing_end.tzinfo.utcoffset(billing_end).total_seconds(), 0)

    def test_billing_end_from_dict(self):
        billing_end = validate_billing_end({"current_period_end": 1893456000})

        self.assertEqual(billing_end.year, 2030)
        self.assertEqual(billing_end.tzinfo.utcoffset(billing_end).total_seconds(), 0)

    def test_billing_end_from_subscription_item(self):
        subscription = stripe_subscription(current_period_end=None)
        subscription["items"]["data"][0]["current_period_end"] = 1893456000

        billing_end = validate_billing_end(subscription)

        self.assertEqual(billing_end.year, 2030)
        self.assertEqual(billing_end.tzinfo.utcoffset(billing_end).total_seconds(), 0)

    def test_billing_end_missing(self):
        billing_end = validate_billing_end(SimpleNamespace())

        self.assertIsNone(billing_end)

    def test_plan_from_price_id(self):
        stripe_plan = stripe_subscription(
            price_id=self.premium_subscription.stripe_price_id
        )

        plan = validate_stripe_subscription_plan(stripe_plan)

        self.assertEqual(plan, self.premium_subscription)

    def test_unknown_price_id(self):
        stripe_plan = stripe_subscription(price_id="price_missing")

        plan = validate_stripe_subscription_plan(stripe_plan)

        self.assertIsNone(plan)

    def test_unknown_local_plan(self):
        with self.assertRaises(ValueError):
            get_or_create_local_subscription("enterprise")

    @patch("subscriptions.utils.stripe.Product.retrieve")
    @patch("subscriptions.utils.stripe.Subscription.retrieve")
    def test_subscription_product_data(
        self,
        retrieve_subscription,
        retrieve_product,
    ):
        retrieve_subscription.return_value = stripe_subscription()
        retrieve_product.return_value = SimpleNamespace(
            name="Premium",
            description="Unlimited portfolios",
        )

        result = get_stripe_subscription_product("sub_test")

        self.assertEqual(result["subscription_price"], 12.99)
        self.assertEqual(result["billing_interval"], "month")
        self.assertEqual(result["product"].name, "Premium")
        retrieve_subscription.assert_called_once_with("sub_test")
        retrieve_product.assert_called_once_with("prod_premium")

    @patch("subscriptions.utils.stripe.Customer.delete")
    @patch("subscriptions.utils.stripe.Subscription.cancel")
    def test_delete_stripe_customer_ids(
        self,
        cancel_subscription,
        delete_customer,
    ):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.premium_subscription,
            status="active",
            stripeCustomerId="cus_test",
            stripeSubscriptionId="sub_test",
        )

        user_delete_stripe_customer(self.user)

        cancel_subscription.assert_called_once_with("sub_test")
        delete_customer.assert_called_once_with("cus_test")

    @patch("subscriptions.utils.stripe.Customer.delete")
    @patch("subscriptions.utils.stripe.Subscription.cancel")
    def test_delete_stripe_customer_no_ids(
        self,
        cancel_subscription,
        delete_customer,
    ):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
        )

        user_delete_stripe_customer(self.user)

        cancel_subscription.assert_not_called()
        delete_customer.assert_not_called()

    def test_cancel_moves_paid_data(self):
        default_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Default Portfolio",
            is_default=True,
        )
        old_portfolio = Portfolio.objects.create(
            user=self.user,
            name="Big Dawg Portfolio",
        )
        equity = Equity.objects.create(
            ticker="TLS",
            name="Telstra",
            currency="AUD",
            exchange="ASX",
        )
        default_portfolio.holdings.create(equity=equity, quantity=2)
        old_portfolio.holdings.create(equity=equity, quantity=3)
        transaction = Transaction.objects.create(
            user=self.user,
            equity=equity,
            portfolio=old_portfolio,
            transaction_type="BUY",
            quantity=3,
            price="4.00",
            trade_date="2026-04-01",
            currency="AUD",
        )

        user_cancels_stripe(self.user)

        self.assertFalse(Portfolio.objects.filter(id=old_portfolio.id).exists())
        default_holding = default_portfolio.holdings.get(equity=equity)
        transaction.refresh_from_db()
        self.assertEqual(default_holding.quantity, 5)
        self.assertEqual(transaction.portfolio, default_portfolio)


class SubscriptionViewTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.premium_subscription = get_or_create_local_subscription("premium")
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="test123",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            country="Australia",
            currency="AUD",
            subscription=self.free_subscription,
        )
        self.client.login(username="testuser", password="test123")

    def test_home_without_customer(self):
        response = self.client.get(reverse("subscriptions-home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plans and Billing")
        self.assertContains(response, "Free")

    def test_home_with_free_customer(self):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
        )

        response = self.client.get(reverse("subscriptions-home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are paying")
        self.assertContains(response, "Free")

    @patch("subscriptions.views.stripe.Product.retrieve")
    @patch("subscriptions.views.stripe.Subscription.retrieve")
    def test_home_with_active_stripe_plan(
        self,
        retrieve_subscription,
        retrieve_product,
    ):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.premium_subscription,
            status="active",
            stripeSubscriptionId="sub_test",
        )
        retrieve_subscription.return_value = stripe_subscription(
            price_id=self.premium_subscription.stripe_price_id
        )
        retrieve_product.return_value = SimpleNamespace(
            name="Premium",
            description="Unlimited portfolios",
        )

        response = self.client.get(reverse("subscriptions-home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Premium")
        self.assertContains(response, "$12.99/month")
        self.assertContains(response, "Renews on")

    @override_settings(STRIPE_PUBLISHABLE_KEY="test_publishable_key")
    def test_stripe_config_key(self):
        response = self.client.get("/stripe/config/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["publicKey"], "test_publishable_key")

    @patch("subscriptions.views.stripe.checkout.Session.create")
    def test_checkout_session_id(self, create_session):
        create_session.return_value = {"id": "cs_test_123"}

        response = self.client.get("/stripe/create-checkout-session/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sessionId"], "cs_test_123")
        create_session.assert_called_once()
        self.assertEqual(
            create_session.call_args.kwargs["line_items"][0]["price"],
            self.premium_subscription.stripe_price_id,
        )

    @patch("subscriptions.views.stripe.checkout.Session.create")
    def test_checkout_session_error(self, create_session):
        create_session.side_effect = RuntimeError("stripe unavailable")

        response = self.client.get("/stripe/create-checkout-session/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"], "stripe unavailable")

    # Little Easter Egg hehe
    # Nvm helloworld had to get removed
    def test_static_stripe_pages(self):
        success = self.client.get("/stripe/success/")
        cancel = self.client.get("/stripe/cancel/")

        self.assertContains(success, "successfully subscribed")
        self.assertContains(cancel, "cancelled the checkout")

    @patch("subscriptions.views.stripe.Subscription.retrieve")
    @patch("subscriptions.views.stripe.Webhook.construct_event")
    def test_webhook_checkout_customer(self, construct_event, retrieve_subscription):
        session = SimpleNamespace(
            client_reference_id=self.user.id,
            customer="cus_test",
            subscription="sub_test",
        )
        construct_event.return_value = {
            "type": "checkout.session.completed",
            "data": {"object": session},
        }
        retrieve_subscription.return_value = stripe_subscription(
            price_id=self.premium_subscription.stripe_price_id
        )

        response = self.client.post(
            "/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        self.profile.refresh_from_db()
        self.assertEqual(stripe_customer.stripeCustomerId, "cus_test")
        self.assertEqual(stripe_customer.stripeSubscriptionId, "sub_test")
        self.assertEqual(stripe_customer.subscription.name, "premium")
        self.assertEqual(stripe_customer.billingEndDate.year, 2030)
        self.assertEqual(self.profile.subscription.name, "premium")
        retrieve_subscription.assert_called_once_with("sub_test")

    @patch("subscriptions.views.stripe.Webhook.construct_event")
    def test_webhook_subscription_update(self, construct_event):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
            stripeSubscriptionId="sub_test",
        )
        construct_event.return_value = {
            "type": "customer.subscription.updated",
            "data": {
                "object": stripe_subscription(
                    price_id=self.premium_subscription.stripe_price_id
                )
            },
        }

        response = self.client.post(
            "/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        self.profile.refresh_from_db()
        self.assertEqual(stripe_customer.subscription.name, "premium")
        self.assertEqual(stripe_customer.billingEndDate.year, 2030)
        self.assertEqual(self.profile.subscription.name, "premium")

    @patch("subscriptions.views.stripe.Webhook.construct_event")
    def test_webhook_expired_subscription(self, construct_event):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.premium_subscription,
            status="active",
            stripeSubscriptionId="sub_test",
        )
        construct_event.return_value = {
            "type": "customer.subscription.deleted",
            "data": {
                "object": stripe_subscription(
                    status="canceled",
                    current_period_end=None,
                )
            },
        }

        response = self.client.post(
            "/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        stripe_customer = StripeCustomer.objects.get(user=self.user)
        self.assertEqual(stripe_customer.status, "canceled")
        self.assertEqual(stripe_customer.subscription.name, "free")

    @patch("subscriptions.views.stripe.Webhook.construct_event")
    def test_webhook_bad_payload(self, construct_event):
        construct_event.side_effect = ValueError("invalid payload")

        response = self.client.post(
            "/stripe/webhook/",
            data=b"not-json",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 400)


class SubscriptionServiceAndSignalTests(TestCase):
    def setUp(self):
        self.free_subscription = get_or_create_local_subscription("free")
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="test123",
        )

    @patch("subscriptions.services.stripe.Subscription.retrieve")
    def test_subscribed_active(self, retrieve_subscription):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
            stripeSubscriptionId="sub_test",
        )
        retrieve_subscription.return_value = SimpleNamespace(status="active")
        request = SimpleNamespace(user=self.user)

        self.assertTrue(isUserSubscribed(request))

    @patch("subscriptions.services.stripe.Subscription.retrieve")
    def test_subscribed_inactive(self, retrieve_subscription):
        StripeCustomer.objects.create(
            user=self.user,
            subscription=self.free_subscription,
            status="active",
            stripeSubscriptionId="sub_test",
        )
        retrieve_subscription.return_value = SimpleNamespace(status="canceled")
        request = SimpleNamespace(user=self.user)

        self.assertFalse(isUserSubscribed(request))

    def test_subscribed_no_customer(self):
        request = SimpleNamespace(user=self.user)

        self.assertFalse(isUserSubscribed(request))

    def test_initial_user_setup(self):
        initial_user_setup(self.user)

        stripe_customer = StripeCustomer.objects.get(user=self.user)
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(stripe_customer.subscription.name, "free")
        self.assertEqual(stripe_customer.status, "active")
        self.assertEqual(profile.subscription.name, "free")
        self.assertEqual(profile.country, "Australia")
