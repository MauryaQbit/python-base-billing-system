import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB = BASE_DIR / "billing.db"

def seed():
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    with open(BASE_DIR / "schema.sql") as f:
        cur.executescript(f.read())
    products = [
        ("Notebook", 2.50),
        ("Pen", 0.99),
        ("Stapler", 5.25),
        ("Envelope (10)", 1.50),
        ("USB Drive 16GB", 8.99),
    ]
    cur.executemany("INSERT INTO products (name, price) VALUES (?,?)", products)
    conn.commit()
    conn.close()
    print("Seeded database at", DB)


if __name__ == '__main__':
    seed()
