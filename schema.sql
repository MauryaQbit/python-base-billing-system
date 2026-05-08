-- products, invoices, and invoice items
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS invoice_items;

CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    image_url TEXT
);

CREATE TABLE invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT,
    total REAL,
    created TEXT
);

CREATE TABLE invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    method TEXT,
    note TEXT,
    created TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id)
);

CREATE TABLE attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    uploaded_at TEXT,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id)
);
