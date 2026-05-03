from app.database import init_db, _conn
import os

if __name__ == "__main__":
    init_db()
    conn = _conn()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cur.fetchall()]
    print(f"Tables: {tables}")
    
    # Check columns of subscriptions
    cur = conn.execute("PRAGMA table_info(subscriptions);")
    cols = [r[1] for r in cur.fetchall()]
    print(f"Subscription columns: {cols}")
    conn.close()
