import sqlite3
conn = sqlite3.connect('data/database.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
for (t,) in cur.fetchall():
    cur.execute(f'PRAGMA table_info({t})')
    cols = [r[1] for r in cur.fetchall()]
    print(f'{t}: {cols}')
conn.close()
