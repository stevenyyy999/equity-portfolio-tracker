import csv
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from users.models import Profile

from .utils import (
    CURRENT_FY_YEAR,
    build_cgt_summary,
    get_available_fy_labels,
    get_fy_dates,
)


@login_required
def calculate_capital_gains_taxes(request):
    """Render CGT calculator page and process submitted CGT form values"""
    #     FY2024-25
    # ├── Gross capital gains (all gains before discount)
    # Assume that if there is a 'SELL' transaction, there was a 'BUY' transaction
    # Assume that you can't 'SELL' more than you 'BUY'
    # Assume 'SELL' transaction dates are valid
    # Assume 'SELL' date cannot be before available amount of shares that were bought
    # All assumptions depend on valid transactions
    # Filter for 'SELL' transactions (sorted by ticker and date)
    user = request.user
    user_profile = Profile.objects.get(user=user)
    user_currency = user_profile.currency.strip()
    user_country = user_profile.country.strip()

    # check if user is in australia + based on australian currency
    if user_country != "Australia" or user_currency != "AUD":
        return render(
            request,
            "cgt_calculator_page.html",
            {
                "CGT_enabled": False,
                "country": user_country,
                "currency": user_currency,
            },
        )

    fy_year_raw = request.GET.get("fy_year") or request.POST.get("fy_year")

    if not fy_year_raw:
        return render(
            request,
            "cgt_calculator_page.html",
            {
                "CGT_enabled": True,
                "fy_years": get_available_fy_labels(),
                "form_data": {
                    "prev_losses": "0.00",
                    "income": "",
                },
                "selected_fy": None,
            },
        )

    if "-" in fy_year_raw:
        fy_year = int(fy_year_raw.split("-")[-1])
    else:
        fy_year = int(fy_year_raw) + 1
    beginning_fy, end_fy = get_fy_dates(fy_year)

    fy_beginning = str(beginning_fy).split("-")
    fy_end = str(end_fy).split("-")

    if request.method == "GET":
        return render(
            request,
            "cgt_calculator_page.html",
            {
                "CGT_enabled": True,
                "fy_years": get_available_fy_labels(),
                "selected_fy": fy_year_raw,
                "fy_beginning": fy_beginning[0],
                "fy_end": fy_end[0],
                "form_data": {
                    "prev_losses": "0.00",
                    "income": "",
                },
            },
        )

    # Grabs submitted form values and store them into dictionary form_data
    form_data = {
        "prev_losses": request.POST.get("prev_losses", "0.00"),
        "income": request.POST.get("income", ""),
    }
    errors = {}

    # Tries to convert user input into Decimal, makes sure not negative and valid number
    try:
        carry_over_losses = Decimal(form_data["prev_losses"] or "0")
        if carry_over_losses < 0:
            errors["prev_losses"] = "Previous year capital losses cannot be negative."
    except InvalidOperation:
        errors["prev_losses"] = "Previous year capital losses must be a valid number."
        # Falls back to 0 so function won't crash
        carry_over_losses = Decimal("0")

    try:
        income = Decimal(form_data["income"] or "0")
        if income < 0:
            errors["income"] = "Taxable income cannot be negative."
    except InvalidOperation:
        errors["income"] = "Taxable income must be a valid number."
        income = Decimal("0")

    if errors:
        return render(
            request,
            "cgt_calculator_page.html",
            {
                "CGT_enabled": True,
                "fy_years": get_available_fy_labels(),
                "selected_fy": fy_year_raw,
                "fy_beginning": fy_beginning[0],
                "fy_end": fy_end[0],
                "form_data": form_data,
                "errors": errors,
            },
        )

    summary = build_cgt_summary(user, carry_over_losses, income, fy_year)

    return render(
        request,
        "cgt_calculator_page.html",
        {
            "capital_gains_tax": summary["capital_gains_tax"],
            "forward_losses": summary["forward_losses"],
            "current_net_capital_gain": summary["current_net_capital_gain"],
            "CGT_enabled": True,
            "fy_beginning": str(summary["fy_beginning"].year),
            "fy_end": str(summary["fy_end"].year),
            "form_data": form_data,
            "fy_years": get_available_fy_labels(),
            "selected_fy": fy_year_raw,
        },
    )


@login_required
def export_cgt_csv(request):
    """Export the current CGT summary as a CSV download."""
    user = request.user
    user_profile = Profile.objects.get(user=user)
    user_currency = user_profile.currency.strip()
    user_country = user_profile.country.strip()
    fy_raw = request.GET.get("fy_year")
    if fy_raw:
        if "-" in fy_raw:
            fy_year = int(fy_raw.split("-")[-1])
        else:
            fy_year = int(fy_raw) + 1
    else:
        fy_year = CURRENT_FY_YEAR

    if user_country != "Australia" or user_currency != "AUD":
        return render(
            request,
            "cgt_calculator_page.html",
            {
                "CGT_enabled": False,
                "country": user_country,
                "currency": user_currency,
            },
        )

    prev_losses_raw = request.GET.get("prev_losses", "0.00")
    income_raw = request.GET.get("income", "")

    try:
        carry_over_losses = Decimal(prev_losses_raw or "0")
        if carry_over_losses < 0:
            carry_over_losses = Decimal("0")
    except InvalidOperation:
        carry_over_losses = Decimal("0")

    try:
        income = Decimal(income_raw or "0")
        if income < 0:
            income = Decimal("0")
    except InvalidOperation:
        income = Decimal("0")

    summary = build_cgt_summary(user, carry_over_losses, income, fy_year)

    fy_label = f"{summary['fy_beginning'].year}-{summary['fy_end'].year}"

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="cgt_summary_{fy_label}.csv"'
    )

    writer = csv.writer(response)
    writer.writerow(
        [
            "financial_year",
            "taxable_income",
            "previous_year_losses",
            "current_net_capital_gain",
            "forward_losses",
            "capital_gains_tax",
        ]
    )
    writer.writerow(
        [
            fy_label,
            summary["taxable_income"],
            summary["previous_year_losses"],
            summary["current_net_capital_gain"],
            summary["forward_losses"],
            summary["capital_gains_tax"],
        ]
    )

    return response
