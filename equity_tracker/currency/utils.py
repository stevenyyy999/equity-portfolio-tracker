from decimal import ROUND_HALF_UP, Decimal

from .models import ExchangeRate

####################
#
#   HELPER FUNCTIONS
#
####################


def get_user_currency(user):
    """Return user's preferred currency, defaulting to AUD."""
    # Default to AUD if the user is not logged in
    if not getattr(user, "is_authenticated", False):
        return "AUD"

    # Otherwise get currency from users profile
    try:
        return user.profile.currency.upper()
    except Exception:
        return "AUD"


def currency_conversion(amount, from_currency, to_currency):
    """Convert an amount between currencies using ExchangeRate rows."""
    amount = Decimal(str(amount or "0"))
    from_currency = (from_currency or "AUD").upper()
    to_currency = (to_currency or "AUD").upper()

    if from_currency == to_currency:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # AUD is base currency, so no conversion needed
    if from_currency == "AUD":
        conversion = ExchangeRate.objects.get(to_currency=to_currency)

        converted_amount = amount * Decimal(str(conversion.rate))
    elif to_currency == "AUD":
        conversion = ExchangeRate.objects.get(to_currency=from_currency)

        converted_amount = amount / Decimal(str(conversion.rate))
    else:
        from_rate = ExchangeRate.objects.get(to_currency=from_currency)
        to_rate = ExchangeRate.objects.get(to_currency=to_currency)

        convert_aud_amount = amount / Decimal(str(from_rate.rate))
        converted_amount = convert_aud_amount * Decimal(str(to_rate.rate))

    return converted_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_currency(amount, currency):
    """Format amount with currency symbol and two decimal places."""
    currency = (currency or "AUD").upper()
    symbols = {
        "AUD": "A$",
        "USD": "US$",
        "EUR": "€",
        "CNY": "¥",
        "JPY": "¥",
        "GBP": "£",
        "INR": "₹",
        "CAD": "C$",
        "CHF": "CHF ",
        "NZD": "NZ$",
        "BRL": "R$",
        "RUB": "₽",
        "KRW": "₩",
        "MXN": "Mex$",
        "IDR": "Rp",
        "TRY": "₺",
        "SAR": "﷼",
        "AED": "د.إ",
        "ZAR": "R",
        "SEK": "kr",
        "NOK": "kr",
        "DKK": "kr",
        "PLN": "zł",
        "THB": "฿",
        "MYR": "RM",
        "PHP": "₱",
        "VND": "₫",
        "EGP": "E£",
        "NGN": "₦",
        "PKR": "₨",
        "BDT": "৳",
        "ILS": "₪",
        "ARS": "$",
        "CLP": "$",
        "COP": "$",
        "PEN": "S/",
    }

    symbol = symbols.get(currency, f"{currency} ")
    amount = Decimal(str(amount or "0")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return f"{symbol}{amount}"


def format_signed_currency(amount, currency):
    """Format amount as signed currency string."""
    amount = Decimal(str(amount or "0"))
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{format_currency(abs(amount), currency)}"
