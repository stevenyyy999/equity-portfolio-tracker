from django.urls import path

from .views import (
    create_portfolio,
    create_portfolio_page,
    delete_equity_data,
    delete_portfolio_data,
    equity_price_his,
    export_portfolio_holdings_csv,
    get_dashboard_chart_data,
    get_user_portfolios,
    move_equity_to_portfolio,
    portfolio_detail,
    portfolio_settings,
    set_default_portfolio,
    update_equity_view,
)

urlpatterns = [
    path("equities/<str:ticker>/history", equity_price_his, name="equity_price_his"),
    path("equities/<str:ticker>/update", update_equity_view, name="update_equity_view"),
    path("create/", create_portfolio, name="create_portfolio"),
    path("create-portfolio-page/", create_portfolio_page, name="create_portfolio_page"),
    path("my-portfolios/", get_user_portfolios, name="get_user_portfolios"),
    path("<int:portfolio_id>", portfolio_detail, name="portfolio_detail"),
    path(
        "<int:portfolio_id>/export-holdings-csv/",
        export_portfolio_holdings_csv,
        name="export_portfolio_holdings_csv",
    ),
    path("delete-portfolio", delete_portfolio_data, name="delete_portfolio_data"),
    path("settings/<int:portfolio_id>", portfolio_settings, name="portfolio_settings"),
    path("delete-equity-data/", delete_equity_data, name="delete_equity_data"),
    path(
        "dashboard-chart-data/", get_dashboard_chart_data, name="dashboard_chart_data"
    ),
    path(
        "<int:portfolio_id>/set-default/",
        set_default_portfolio,
        name="set_default_portfolio",
    ),
    path("move-equity/", move_equity_to_portfolio, name="move_equity_to_portfolio"),
]
