import sqlite3

path = "data/demo.db"
conn = sqlite3.connect(path)
cur = conn.cursor()

print(f"Inspecting: {path}\n")

# --- tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [t[0] for t in cur.fetchall()]

print("=== TABLES ===")
for t in tables:
    print("-", t)

# --- schema
print("\n=== SCHEMA ===")
for table in tables:
    print(f"\n--- {table} ---")
    cur.execute(f"PRAGMA table_info('{table}');")
    for col in cur.fetchall():
        print(col)

# --- sample rows
print("\n=== SAMPLE ROWS (LIMIT 5) ===")
for table in tables:
    print(f"\n--- {table} ---")
    cur.execute(f"SELECT * FROM {table} LIMIT 5;")
    rows = cur.fetchall()
    for r in rows:
        print(r)

conn.close()
