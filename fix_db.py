import sqlite3

conn = sqlite3.connect('data/database.db')
cur = conn.cursor()

def fix_table(table):
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]

# ── cart_items ──
cols = fix_table('cart_items')
if 'quantity' not in cols:
    cur.execute('ALTER TABLE cart_items ADD COLUMN quantity INTEGER DEFAULT 1')
    print("✅ cart_items.quantity добавлена")
if 'added_at' not in cols:
    cur.execute('ALTER TABLE cart_items ADD COLUMN added_at DATETIME')
    print("✅ cart_items.added_at добавлена")

# ── orders ──
cols = fix_table('orders')
if 'contact_info' not in cols:
    cur.execute('ALTER TABLE orders ADD COLUMN contact_info TEXT')
    print("✅ orders.contact_info добавлена")
if 'confirmed_at' not in cols:
    cur.execute('ALTER TABLE orders ADD COLUMN confirmed_at DATETIME')
    print("✅ orders.confirmed_at добавлена")
if 'confirmed_by' not in cols:
    cur.execute('ALTER TABLE orders ADD COLUMN confirmed_by INTEGER')
    print("✅ orders.confirmed_by добавлена")

# ── order_items ──
cols = fix_table('order_items')
print("order_items колонки:", cols)
if 'quantity' not in cols:
    cur.execute('ALTER TABLE order_items ADD COLUMN quantity INTEGER DEFAULT 1')
    print("✅ order_items.quantity добавлена")
if 'product_name' not in cols:
    cur.execute('ALTER TABLE order_items ADD COLUMN product_name TEXT')
    print("✅ order_items.product_name добавлена")
if 'flavor_name' not in cols:
    cur.execute('ALTER TABLE order_items ADD COLUMN flavor_name TEXT')
    print("✅ order_items.flavor_name добавлена")

conn.commit()
conn.close()
print("\n✅ Готово, перезапускай бота")
