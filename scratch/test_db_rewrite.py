import os
import re

def rewrite_query(query: str, is_postgres: bool) -> str:
    if is_postgres:
        query = query.replace("?", "%s")
        query = query.replace("AUTOINCREMENT", "")
        # For Postgres, INTEGER PRIMARY KEY AUTOINCREMENT should be SERIAL PRIMARY KEY
        query = query.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
        query = query.replace("datetime('now')", "CURRENT_TIMESTAMP")
        query = query.replace("INSERT OR IGNORE", "INSERT") # Not perfect, needs ON CONFLICT DO NOTHING
        query = query.replace("ON CONFLICT DO NOTHING", "") # clean up
        query = re.sub(r'INSERT INTO seen_pmids \((.*?)\) VALUES \((.*?)\)', r'INSERT INTO seen_pmids (\1) VALUES (\2) ON CONFLICT DO NOTHING', query)
    return query

print(rewrite_query("INSERT INTO users (email) VALUES (?)", True))
print(rewrite_query("INTEGER PRIMARY KEY AUTOINCREMENT", True))
print(rewrite_query("INSERT OR IGNORE INTO seen_pmids (a, b) VALUES (?, ?)", True))
