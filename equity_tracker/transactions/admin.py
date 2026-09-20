import csv
import io

from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path

from .models import ASXEquityList, Transaction, TransactionMatch


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Admin configuration for Transaction records."""

    list_display = (
        "id",
        "user",
        "equity",
        "transaction_type",
        "quantity",
        "price",
        "trade_date",
        "created_at",
    )
    list_filter = ("transaction_type", "trade_date")
    search_fields = ("equity__ticker", "equity__name", "user__username")


@admin.register(TransactionMatch)
class TransactionMatchAdmin(admin.ModelAdmin):
    """Admin configuration for TransactionMatch records."""

    list_display = (
        "id",
        "buy_transaction_id_display",
        "buy_trade_date",
        "buy_price",
        "sell_transaction_id_display",
        "sell_trade_date",
        "sell_price",
        "matched_quantity",
    )

    def buy_transaction_id_display(self, obj):
        """Show matched BUY transaction id."""
        return obj.buy_transaction.id

    def buy_trade_date(self, obj):
        """Show matched BUY trade date."""
        return obj.buy_transaction.trade_date

    def buy_price(self, obj):
        """Show matched BUY price."""
        return obj.buy_transaction.price

    def sell_transaction_id_display(self, obj):
        """Show matched SELL transaction id."""
        return obj.sell_transaction.id

    def sell_trade_date(self, obj):
        """Show matched SELL trade date."""
        return obj.sell_transaction.trade_date

    def sell_price(self, obj):
        """Show matched SELL price."""
        return obj.sell_transaction.price


@admin.register(ASXEquityList)
class ASXEquityListAdmin(admin.ModelAdmin):
    """Admin configuration for ASX equity list import and browsing."""

    list_display = ("ticker", "name", "industry")
    search_fields = ("ticker", "name")
    list_filter = ("industry",)
    change_list_template = "transactions/asxequitylist/change_list.html"

    def get_urls(self):
        """Add custom admin route for CSV import."""
        urls = super().get_urls()
        custom_urls = [
            path(
                ("import-csv/"),
                self.admin_site.admin_view(self.import_csv_view),
                name="asx_import_csv",
            )
        ]
        return custom_urls + urls

    def import_csv_view(self, request):
        """Handle ASX list CSV upload from admin page."""
        if request.method == "GET":
            # context = {
            #     **self.admin_site.each_context(request),
            # }
            return render(request, "transactions/asxequitylist/import_csv.html")

        uploaded_file = request.FILES.get("file")

        if not uploaded_file or not uploaded_file.name.endswith(".csv"):
            messages.error(request, "Only csv file is allowed")
            return redirect("..")

        try:
            decoded = uploaded_file.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(decoded))

            required = {"ASX code", "Company name"}
            if not required.issubset(set(reader.fieldnames or [])):
                messages.error(request, f"CSV must contain headers: {required}")
                return redirect("..")

            clear = request.POST.get("clear_existing") == "on"
            if clear:
                ASXEquityList.objects.all().delete()

            created = 0
            updated = 0
            failed = 0
            failed_ASX = []
            for row_num, row in enumerate(reader, start=2):
                ticker = (row.get("ASX code") or "").strip().upper()
                name = (row.get("Company name") or "").strip()
                industry = (row.get("GICs industry group") or "").strip() or None

                if not ticker or not name:
                    failed_ASX.append(row_num)
                    continue

                _, was_created = ASXEquityList.objects.update_or_create(
                    ticker=ticker,
                    defaults={"name": name, "industry": industry},
                )

                if was_created:
                    created += 1
                else:
                    updated += 1

            messages.success(
                request,
                f"Import done — created: {created}, updated: {updated}, failed: {failed} failed ASX {failed_ASX}",
            )

        except Exception as e:
            messages.error(request, f"Import failed: {e}")

        return redirect("..")
