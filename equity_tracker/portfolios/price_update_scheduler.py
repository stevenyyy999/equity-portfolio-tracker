from apscheduler.schedulers.background import BackgroundScheduler

from portfolios.yfinance.yfinance_server import update_all_equity_prices

# Creates an APScheduler schedular and registers it as a recurring job. The job will call the closing-price updater.
# Everyday at 6pm, run the update_all_equity_price_daily function to refresh saved closing prices.

scheduler = BackgroundScheduler()


def start_schedule():
    """Start daily equity price refresh scheduler at 6pm if not running."""
    if not scheduler.running:
        scheduler.add_job(
            update_all_equity_prices,
            trigger="cron",
            hour=18,
            minute=0,
            id="update_all_equity_price_daily",
            replace_existing=True,
        )
        scheduler.start()
        print("scheduler started")
