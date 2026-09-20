import requests
from django.conf import settings

from .models import ExchangeRate


# Updates all of the exchange rates
def update_exchange_rates():
    """Fetch latest exchange rates from Exchange Rate API and update the ExchangeRate model"""
    base_currency = "AUD"
    api_key = settings.EXCHANGE_RATE_API_KEY
    if not api_key:
        print("EXCHANGE_RATE_API_KEY is not set; skipping exchange rate update.")
        return

    url = "https://v6.exchangerate-api.com/v6/" + api_key + "/latest/" + base_currency

    try:
        response = requests.get(url)
        data = response.json()
        if data["result"] != "success":
            print("Exchange Rate API went wrong")
            return

        rates = data["conversion_rates"]
        for to_currency, rate in rates.items():
            ExchangeRate.objects.update_or_create(
                to_currency=to_currency, defaults={"rate": rate}
            )

        print("Exchange rates updated")
    except Exception as e:
        print("Exchange Rate API went wrong")
        print(e)
