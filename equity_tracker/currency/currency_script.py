from apscheduler.schedulers.background import BackgroundScheduler

from currency.views import update_exchange_rates

# Creates an APScheduler schedular and registers it as a recurring job.
scheduler = BackgroundScheduler()


def start_schedule():
    """Start scheduled daily exchange rate updates at 6pm if not running."""
    if not scheduler.running:
        scheduler.add_job(
            update_exchange_rates,
            trigger="cron",
            hour=18,
            minute=0,
            id="update_exchange_rates",
            replace_existing=True,
        )
        scheduler.start()
        print("scheduler started")
