import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB = BASE_DIR / "billing.db"

U = "https://images.unsplash.com/{}?auto=format&fit=crop&w=600&q=80"

PRODUCTS = [
    ("Premium Notebook", "Hardcover dotted notebook, 192 pages, lay-flat binding.", "Stationery", 12.50, 120, "STN-001",
     U.format("photo-1531346878377-a5be20888e57")),
    ("Fountain Pen Set", "Smooth-flow fountain pen with 3 ink cartridges.", "Stationery", 24.99, 80, "STN-002",
     U.format("photo-1583485088034-697b5bc54ccd")),
    ("Wireless Headphones", "Noise-cancelling over-ear headphones, 40h battery.", "Electronics", 89.99, 35, "ELC-001",
     U.format("photo-1505740420928-5e560c06d30e")),
    ("Ergo Backpack", "Water-resistant 25L laptop backpack with USB port.", "Accessories", 59.99, 45, "ACC-001",
     U.format("photo-1553062407-98eeb64c6a62")),
    ("Mechanical Keyboard", "Hot-swap RGB mechanical keyboard, blue switches.", "Electronics", 74.50, 28, "ELC-002",
     U.format("photo-1587829741301-dc798b83add3")),
    ("Wireless Mouse", "Ergonomic 2.4GHz wireless mouse, silent clicks.", "Electronics", 29.99, 90, "ELC-003",
     U.format("photo-1527864550417-7fd91fc51a46")),
    ("Desk Lamp Pro", "Dimmable LED desk lamp with 3 color modes.", "Office", 34.99, 60, "OFC-001",
     U.format("photo-1507473885765-e6ed057f782c")),
    ("Steel Bottle 1L", "Insulated stainless steel bottle, 24h cold / 12h hot.", "Accessories", 22.00, 150, "ACC-002",
     U.format("photo-1602143407151-7111542de6e8")),
    ("Ceramic Coffee Mug", "350ml ceramic mug, dishwasher & microwave safe.", "Office", 9.99, 200, "OFC-002",
     U.format("photo-1514228742587-6b1558fcca3d")),
    ("Heavy-Duty Stapler", "50-sheet stapler with ergonomic grip.", "Stationery", 14.25, 70, "STN-003",
     U.format("photo-1580894894513-541e068a3e2b")),
    ("USB-C Hub 7-in-1", "HDMI, 2x USB-A, USB-C PD, SD/microSD hub.", "Electronics", 45.99, 40, "ELC-004",
     U.format("photo-1618410320928-25228d811631")),
    ("A4 Paper Pack", "500 sheets premium 80gsm A4 paper.", "Stationery", 8.50, 300, "STN-004",
     U.format("photo-1568205612837-017257d2310a")),
]


def seed():
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    with open(BASE_DIR / "schema.sql") as f:
        cur.executescript(f.read())
    cur.executemany(
        "INSERT INTO products (name, description, category, price, stock, sku, image_url) VALUES (?,?,?,?,?,?,?)",
        PRODUCTS,
    )
    conn.commit()
    conn.close()
    print(f"Seeded {len(PRODUCTS)} products at {DB}")


if __name__ == '__main__':
    seed()
