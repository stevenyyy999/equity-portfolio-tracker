from django.contrib import admin
from transactions.models import Equity, EquityPrice

from .models import Holdings, Portfolio, PriceUpdateLog

admin.site.register(Portfolio)
admin.site.register(Equity)
admin.site.register(EquityPrice)
admin.site.register(Holdings)


@admin.register(PriceUpdateLog)
class PriceUpdateLogAdmin(admin.ModelAdmin):
    """Admin display configuration for price update logs."""

    list_display = ("ticker", "status", "run_at", "message")
    list_filter = ("status", "run_at")
    search_fields = ("ticker", "message")
