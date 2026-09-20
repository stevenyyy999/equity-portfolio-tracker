from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

JULY_MONTH = 7
JUNE_MONTH = 6
CGT_DISCOUNT_RATE = Decimal("0.5")
PROFIT_LOSS = 0
DISCOUNTABLE = 1
PRE_CGT_DATE = date(1985, 9, 20)
CURRENT_FY_YEAR = 2026
LOWEST_FY_YEAR_RANGE = 17

# Tax brackets for Australian residents from 2010 to 2026 (inclusive)
# FY year is from previous year 1st July to current year 30th June
# FY year: [(upper_income_limit, base_tax, tax_rate)]
TAX_BRACKETS = {
    2010: [
        (6_000, 0, 0),
        (37_000, 0, 0.15),
        (80_000, 4_650, 0.30),
        (180_000, 17_550, 0.37),
        (float("inf"), 54_550, 0.45),
    ],
    2011: [
        (6_000, 0, 0),
        (37_000, 0, 0.15),
        (80_000, 4_650, 0.30),
        (180_000, 17_550, 0.37),
        (float("inf"), 54_550, 0.45),
    ],
    2012: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (80_000, 3_572, 0.325),
        (180_000, 17_547, 0.37),
        (float("inf"), 54_547, 0.45),
    ],
    2013: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (80_000, 3_572, 0.325),
        (180_000, 17_547, 0.37),
        (float("inf"), 54_547, 0.45),
    ],
    2014: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (80_000, 3_572, 0.325),
        (180_000, 17_547, 0.37),
        (float("inf"), 54_547, 0.45),
    ],
    2015: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (80_000, 3_572, 0.325),
        (180_000, 17_547, 0.37),
        (float("inf"), 54_547, 0.45),
    ],
    2016: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (87_000, 3_572, 0.325),
        (180_000, 19_822, 0.37),
        (float("inf"), 54_232, 0.45),
    ],
    2017: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (87_000, 3_572, 0.325),
        (180_000, 19_822, 0.37),
        (float("inf"), 54_232, 0.45),
    ],
    2018: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (90_000, 3_572, 0.325),
        (180_000, 20_797, 0.37),
        (float("inf"), 54_097, 0.45),
    ],
    2019: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (90_000, 3_572, 0.325),
        (180_000, 20_797, 0.37),
        (float("inf"), 54_097, 0.45),
    ],
    2020: [
        (18_200, 0, 0),
        (37_000, 0, 0.19),
        (90_000, 3_572, 0.325),
        (180_000, 20_797, 0.37),
        (float("inf"), 54_097, 0.45),
    ],
    2021: [
        (18_200, 0, 0),
        (45_000, 0, 0.19),
        (120_000, 5_092, 0.325),
        (180_000, 29_467, 0.37),
        (float("inf"), 51_667, 0.45),
    ],
    2022: [
        (18_200, 0, 0),
        (45_000, 0, 0.19),
        (120_000, 5_092, 0.325),
        (180_000, 29_467, 0.37),
        (float("inf"), 51_667, 0.45),
    ],
    2023: [
        (18_200, 0, 0),
        (45_000, 0, 0.19),
        (120_000, 5_092, 0.325),
        (180_000, 29_467, 0.37),
        (float("inf"), 51_667, 0.45),
    ],
    2024: [
        (18_200, 0, 0),
        (45_000, 0, 0.19),
        (120_000, 5_092, 0.325),
        (180_000, 29_467, 0.37),
        (float("inf"), 51_667, 0.45),
    ],
    2025: [
        (18_200, 0, 0),
        (45_000, 0, 0.16),
        (135_000, 4_288, 0.30),
        (190_000, 31_288, 0.37),
        (float("inf"), 51_638, 0.45),
    ],
    2026: [
        (18_200, 0, 0),
        (45_000, 0, 0.16),
        (135_000, 4_288, 0.30),
        (190_000, 31_288, 0.37),
        (float("inf"), 51_638, 0.45),
    ],
}


#####
#
# HELPER FUNCTION
#
#####


# Gets current financial year period (Australian FY runs July 1 to June 30)
def get_fy_dates(fy_year):
    """Return start and end dates for an Australian financial year."""
    return date(fy_year - 1, JULY_MONTH, 1), date(fy_year, JUNE_MONTH, 30)


def get_available_fy_labels():
    """Build the financial year labels as shown in the CGT dropdown."""
    current_fy_start = CURRENT_FY_YEAR
    return [
        f"{current_fy_start - i - 1}-{current_fy_start - i}"
        for i in range(LOWEST_FY_YEAR_RANGE)
    ]


# Hardcode resident tax on income
# 0 – $18,200 (first tax bracket)
# $18,201 – $45,000 (second tax bracket)
# $45,001 – $135,000 (third tax bracket)
# $135,001 – $190,000 (fourth tax bracket)
# $190,001 and over (fifth tax bracket)
# The above rates do not include the Medicare levy of 2%.
# 2025 - 2026 Australian resident tax rates
# https://www.ato.gov.au/tax-rates-and-codes/tax-rates-australian-residents
def calculate_income_tax(income, fy_year):
    """Calculate Australian resident income tax for a given FY."""
    income = Decimal(income)
    brackets = TAX_BRACKETS[fy_year]
    prev_limit = Decimal("0")

    for limit, base, rate in brackets:
        curr_limit = Decimal(str(limit))
        curr_base = Decimal(str(base))
        curr_rate = Decimal(str(rate))

        if income <= curr_limit:
            tax = curr_base + (income - prev_limit) * curr_rate
            return tax
        prev_limit = curr_limit

    return Decimal("0")


# Now accounts for leap years
def is_discount_eligible(acquired_on, cgt_event_date):
    """Verify if a trade passes the 12 month CGT discount rule."""
    try:
        anniversary = acquired_on.replace(year=acquired_on.year + 1)
    except ValueError:
        anniversary = acquired_on.replace(year=acquired_on.year + 1, month=3, day=1)

    eligible_from = anniversary + timedelta(days=1)
    return cgt_event_date >= eligible_from


def build_cgt_summary(user, carry_over_losses, income, fy_year):
    """Build the final CGT summary values"""
    transactionHistory = user.transactions.all()

    fy_beginning, fy_end = get_fy_dates(int(fy_year))

    sellTransactions = transactionHistory.filter(transaction_type="SELL").order_by(
        "equity__ticker", "trade_date"
    )
    buyTransactions = transactionHistory.filter(transaction_type="BUY").order_by(
        "equity__ticker", "trade_date"
    )

    sellTransactions = sellTransactions.filter(
        trade_date__gte=fy_beginning, trade_date__lte=fy_end
    )

    tradePnL = []

    buys_by_equity = defaultdict(list)
    for buy in buyTransactions:
        if buy.equity is not None:
            buys_by_equity[buy.equity].append(buy)

    buy_index = {equity: 0 for equity in buys_by_equity.keys()}
    buy_remaining = {buy.id: int(buy.quantity) for buy in buyTransactions}

    for sell in sellTransactions:
        if sell.equity is None:
            continue

        equity_buys = buys_by_equity.get(sell.equity, [])
        if not equity_buys:
            continue

        quantityToMatch = int(sell.quantity)
        i = buy_index.get(sell.equity, 0)

        while quantityToMatch > 0 and i < len(equity_buys):
            buy = equity_buys[i]

            if buy.trade_date > sell.trade_date:
                break

            available = buy_remaining.get(buy.id, 0)
            if available <= 0:
                i += 1
                continue

            matched = min(available, quantityToMatch)
            buy_remaining[buy.id] = available - matched
            quantityToMatch -= matched

            if buy.trade_date >= PRE_CGT_DATE:
                profitLoss = (sell.price - buy.price) * matched
                isDiscountable = is_discount_eligible(buy.trade_date, sell.trade_date)
                tradePnL.append((profitLoss, isDiscountable))

            if buy_remaining[buy.id] == 0:
                i += 1

        buy_index[sell.equity] = i

    discountableTradeProfit = []
    notDiscountableTradeProfit = []
    for trade in tradePnL:
        if trade[PROFIT_LOSS] > 0 and trade[DISCOUNTABLE]:
            discountableTradeProfit.append(trade[PROFIT_LOSS])
        elif trade[PROFIT_LOSS] > 0 and not trade[DISCOUNTABLE]:
            notDiscountableTradeProfit.append(trade[PROFIT_LOSS])

    tradeLoss = []
    for trade in tradePnL:
        if trade[PROFIT_LOSS] < 0:
            tradeLoss.append(trade[PROFIT_LOSS])

    total_loss = carry_over_losses - sum(tradeLoss, Decimal("0"))

    totalTradeProfit = (
        sum(notDiscountableTradeProfit, Decimal("0"))
        + sum(discountableTradeProfit, Decimal("0"))
    ) - total_loss

    if totalTradeProfit < 0:
        forward_losses = abs(totalTradeProfit)
        taxed_profit = Decimal("0")
    else:
        forward_losses = Decimal("0")
        taxed_profit = totalTradeProfit

    discountable_profit_total = sum(discountableTradeProfit, Decimal("0"))
    if taxed_profit <= discountable_profit_total:
        cgt_taxed_profit = taxed_profit * CGT_DISCOUNT_RATE
    else:
        cgt_taxed_profit = discountable_profit_total * CGT_DISCOUNT_RATE + (
            taxed_profit - discountable_profit_total
        )

    base_tax = calculate_income_tax(income, fy_year)
    tax_with_gain = calculate_income_tax(income + cgt_taxed_profit, fy_year)
    capital_gains_tax = tax_with_gain - base_tax

    two_dp = Decimal("0.01")
    return {
        "fy_beginning": fy_beginning,
        "fy_end": fy_end,
        "previous_year_losses": carry_over_losses,
        "taxable_income": income,
        "current_net_capital_gain": taxed_profit.quantize(
            two_dp, rounding=ROUND_HALF_UP
        ),
        "forward_losses": forward_losses.quantize(two_dp, rounding=ROUND_HALF_UP),
        "capital_gains_tax": capital_gains_tax,
    }
