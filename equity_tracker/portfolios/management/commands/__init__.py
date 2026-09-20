from django.core.management.base import BaseCommand

from portfolios.yfinance.yfinance_server import update_all_equity_prices

# Custom Django management command for debugging. Ran by `docker compose exec web python manage.py update_price`

# Calls the backend function update_all_equity_prices to update closing prices for all equities.


class Command(BaseCommand):
    help = "Update closing prices for all tracked equities"

    def handle(self, *args, **kwargs):
        result = update_all_equity_prices()

        self.stdout.write(
            self.style.SUCCESS(
                f"Total {result['total']} equities, "
                f"Created {result['created']}, "
                f"Updated {result['updated']}, "
                f"Skipped {result['skipped']}, "
                f"Failed {result['failed']}"
            )
        )

        if result["failed_tickers"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Failed tickers: {', '.join(result['failed_tickers'])}"
                )
            )
