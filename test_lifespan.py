import logging
from app.main import init_db, start_scheduler
from app.config import settings

logging.basicConfig(level=logging.INFO)
print("Initializing DB...")
init_db()
print("Starting scheduler...")
start_scheduler()
print("All good!")
import time
time.sleep(2)
print("Done.")
