from django.urls import path

from .views import (
    check_ticker_portfolio,
    create_transaction,
    delete_transaction_data,
    export_transactions_csv,
    ticker_suggestions,
    transaction_history,
    upload_transactions_csv,
)

app_name = "transactions"

urlpatterns = [
    path("ticker-suggestions/", ticker_suggestions, name="ticker_suggestions"),
    path("create/", create_transaction, name="create_transaction"),
    path("create/<int:portfolio_id>/", create_transaction, name="create_transaction"),
    path("upload-csv/", upload_transactions_csv, name="upload_transactions_csv"),
    path(
        "upload-csv/<int:portfolio_id>/",
        upload_transactions_csv,
        name="upload_transactions_csv",
    ),
    path("transaction_history/", transaction_history, name="transactions_history"),
    path("export-csv/", export_transactions_csv, name="export_transactions_csv"),
    path(
        "delete_transaction/", delete_transaction_data, name="delete_transaction_data"
    ),
    path(
        "check_ticker_portfolio/", check_ticker_portfolio, name="check_ticker_portfolio"
    ),
]
