import logging
import sys
from app.database import init_db
from app.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)

print("Step 1: Calling init_db()...", flush=True)
init_db()
print("Step 1 complete.", flush=True)

print("Step 2: Calling start_scheduler()...", flush=True)
start_scheduler()
print("Step 2 complete.", flush=True)

print("All tests passed.", flush=True)
