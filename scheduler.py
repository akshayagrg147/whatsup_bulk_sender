from apscheduler.schedulers.background import BackgroundScheduler
import logging
import datetime
from database import get_db_connection

logger = logging.getLogger(__name__)

def weekly_report():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        seven_days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        cur.execute('''
            SELECT SUM(total_sent) as s, SUM(total_delivered) as d, 
                   SUM(total_read) as r, SUM(total_replied) as rp 
            FROM daily_stats WHERE date >= ?
        ''', (seven_days_ago,))
        row = cur.fetchone()
        
        logger.info("=== WEEKLY REPORT ===")
        logger.info(f"Messages Sent: {row['s'] or 0}")
        logger.info(f"Messages Delivered: {row['d'] or 0}")
        logger.info(f"Messages Read: {row['r'] or 0}")
        logger.info(f"Replies Received: {row['rp'] or 0}")
        logger.info("=====================")
    finally:
        conn.close()

def midnight_reset():
    logger.info("Midnight Event: Daily message limit tracking logically reset for the new calendar day.")

def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    
    # Run weekly report every Sunday at 09:00 AM
    scheduler.add_job(weekly_report, 'cron', day_of_week='sun', hour=9, minute=0)
    
    # Run midnight reset note
    scheduler.add_job(midnight_reset, 'cron', hour=0, minute=0)
    
    scheduler.start()
    logger.info("Scheduler started successfully.")
    return scheduler
