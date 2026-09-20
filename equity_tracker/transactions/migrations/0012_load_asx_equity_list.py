import csv
from pathlib import Path

from django.db import migrations


def load_asx_equity_list(apps, schema_editor):
    ASXEquityList = apps.get_model("transactions", "ASXEquityList")
    # the place where all the equities csv are stored
    csv_dir = Path(__file__).resolve().parents[1] / "fixtures" / "stock_csvs"
    csv_paths = sorted(csv_dir.glob("*.csv"))

    # should never happen unless csv file is deleted
    if not csv_paths:
        raise FileNotFoundError(f"No stock CSV files found in {csv_dir}")

    equities = []
    seen_tickers = set()

    # loop through every csv inside the directory, and extract their data to import into the django db
    for csv_path in csv_paths:
        with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)

            for row in reader:
                ticker = (row.get("ASX code") or "").strip().upper()
                name = (row.get("Company name") or "").strip()
                industry = (row.get("GICs industry group") or "").strip() or None

                if not ticker or not name or ticker in seen_tickers:
                    continue

                seen_tickers.add(ticker)
                equities.append(
                    ASXEquityList(
                        ticker=ticker,
                        name=name,
                        industry=industry,
                    )
                )

    ASXEquityList.objects.bulk_create(equities, ignore_conflicts=True)


# Django only recognises a file as a migration if it contains THIS class
class Migration(migrations.Migration):
    # We just wait for the previous migration file to finish since django loads stuff in a chain
    dependencies = [
        ("transactions", "0011_alter_transaction_portfolio"),
    ]

    operations = [
        migrations.RunPython(load_asx_equity_list, migrations.RunPython.noop),
    ]
