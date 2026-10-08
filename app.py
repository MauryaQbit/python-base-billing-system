import os
import secrets
import sqlite3
import smtplib
from email.message import EmailMessage
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, send_file, send_from_directory, jsonify
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
import io
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "billing.db"

SECRET_KEY_FILE = BASE_DIR / ".secret_key"


def load_secret_key():
    """Env var wins; otherwise reuse a generated key so sessions survive restarts."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    try:
        os.chmod(SECRET_KEY_FILE, 0o600)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)
DEBUG = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
UPLOAD_FOLDER = BASE_DIR / "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.pdf', '.txt', '.csv'}
PRODUCT_IMG_FOLDER = BASE_DIR / "static" / "product_images"
os.makedirs(PRODUCT_IMG_FOLDER, exist_ok=True)
ALLOWED_IMG_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}

TAX_DEFAULT = 18.0


def get_db():
    db = getattr(g, "db", None)
    if db is None:
        db = g.db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    with open(BASE_DIR / "schema.sql") as f:
        db.executescript(f.read())


def _cols(db, table):
    try:
        return [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def migrate_db():
    """Backwards-compatible migrations for old DBs."""
    db = get_db()
    tbls = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if 'products' in tbls:
        cols = _cols(db, 'products')
        for col, ddl in [
            ('description', "ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''"),
            ('category', "ALTER TABLE products ADD COLUMN category TEXT DEFAULT 'General'"),
            ('stock', "ALTER TABLE products ADD COLUMN stock INTEGER NOT NULL DEFAULT 50"),
            ('sku', "ALTER TABLE products ADD COLUMN sku TEXT DEFAULT ''"),
            ('image_url', "ALTER TABLE products ADD COLUMN image_url TEXT"),
        ]:
            if col not in cols:
                db.execute(ddl)
        db.commit()
    if 'invoices' in tbls:
        cols = _cols(db, 'invoices')
        for col, ddl in [
            ('subtotal', "ALTER TABLE invoices ADD COLUMN subtotal REAL DEFAULT 0"),
            ('discount', "ALTER TABLE invoices ADD COLUMN discount REAL DEFAULT 0"),
            ('tax', "ALTER TABLE invoices ADD COLUMN tax REAL DEFAULT 0"),
            ('status', "ALTER TABLE invoices ADD COLUMN status TEXT DEFAULT 'Unpaid'"),
        ]:
            if col not in cols:
                try:
                    db.execute(ddl)
                except Exception:
                    pass
        # backfill subtotal/status for legacy rows
        try:
            db.execute("UPDATE invoices SET subtotal = COALESCE(subtotal, total, 0) WHERE subtotal IS NULL OR subtotal = 0")
            db.execute("UPDATE invoices SET status = COALESCE(status, 'Unpaid') WHERE status IS NULL")
            db.commit()
        except Exception:
            pass
    if 'payments' not in tbls:
        db.execute("CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, amount REAL NOT NULL, method TEXT, note TEXT, created TEXT)")
        db.commit()
    if 'attachments' not in tbls:
        db.execute("CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, filename TEXT NOT NULL, filepath TEXT NOT NULL, uploaded_at TEXT)")
        db.commit()


@app.teardown_appcontext
def close_db(error):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


@app.before_request
def ensure_migrations():
    if DB_PATH.exists():
        try:
            migrate_db()
        except Exception:
            pass


def _cart_detail():
    db = get_db()
    cart = session.get("cart", {})
    items, subtotal = [], 0
    if cart:
        try:
            ids = tuple(int(i) for i in cart.keys())
        except ValueError:
            session["cart"] = {}
            return [], 0
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(f"SELECT * FROM products WHERE id IN ({placeholders})", ids).fetchall() if ids else []
        for r in rows:
            qty = cart.get(str(r["id"]), 0)
            if qty <= 0:
                continue
            st = r["price"] * qty
            subtotal += st
            items.append({"product": r, "qty": qty, "subtotal": st})
    return items, subtotal


def _invoice_status(inv_total, paid):
    if paid <= 0:
        return "Unpaid"
    if paid >= (inv_total or 0) - 0.01:
        return "Paid"
    return "Partially Paid"


@app.route("/healthz")
def healthz():
    db = get_db()
    try:
        products = db.execute("SELECT COUNT(*) FROM products").fetchone()[0] or 0
        invoices = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] or 0
    except Exception:
        products, invoices = 0, 0
    return jsonify(status="ok", products=products, invoices=invoices)


@app.route("/")
def index():
    db = get_db()
    q = (request.args.get("q") or "").strip()
    cat = (request.args.get("cat") or "").strip()
    query = "SELECT * FROM products WHERE 1=1"
    params: list = []
    if q:
        query += " AND (name LIKE ? OR description LIKE ? OR sku LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if cat:
        query += " AND category = ?"
        params.append(cat)
    query += " ORDER BY id DESC"
    products = db.execute(query, params).fetchall()
    cats = [r[0] for r in db.execute("SELECT DISTINCT category FROM products ORDER BY category").fetchall() if r[0]]

    cart = session.get("cart", {})
    cart_count = sum(cart.values()) if cart else 0

    # dashboard stats
    try:
        revenue = db.execute("SELECT COALESCE(SUM(total),0) FROM invoices").fetchone()[0] or 0
        inv_count = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] or 0
        prod_count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0] or 0
        low_stock = db.execute("SELECT COUNT(*) FROM products WHERE stock < 10").fetchone()[0] or 0
    except Exception:
        revenue, inv_count, prod_count, low_stock = 0, 0, len(products), 0
    stats = {"revenue": revenue, "invoices": inv_count, "products": prod_count, "low_stock": low_stock}
    return render_template("index.html", products=products, cart_count=cart_count,
                           categories=cats, active_cat=cat, search_q=q, stats=stats,
                           tax_default=TAX_DEFAULT)


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        return "Product not found", 404
    cart = session.get("cart", {})
    return render_template("product.html", product=p, cart_count=sum(cart.values()) if cart else 0)


@app.route("/cart")
def cart_view():
    items, subtotal = _cart_detail()
    tax_rate = float(request.args.get("tax", TAX_DEFAULT))
    tax_amt = round(subtotal * tax_rate / 100, 2)
    total = round(subtotal + tax_amt, 2)
    return render_template("cart.html", items=items, subtotal=subtotal,
                           tax_rate=tax_rate, tax_amt=tax_amt, total=total)


def _safe_back(default_endpoint="index"):
    """Redirect back to the referring page, but only on our own host (open-redirect guard)."""
    target = request.referrer
    if target:
        ref = urlparse(target)
        if ref.scheme in ("http", "https") and ref.netloc == request.host and ref.path.startswith("/"):
            return ref.path + (f"?{ref.query}" if ref.query else "")
    return url_for(default_endpoint)


@app.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    db = get_db()
    row = db.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()
    cart = session.get("cart", {})
    key = str(product_id)
    cur_qty = cart.get(key, 0)
    if row is not None and row["stock"] is not None and cur_qty + 1 > row["stock"]:
        flash(f"Only {row['stock']} in stock")
        return redirect(_safe_back())
    cart[key] = cur_qty + 1
    session["cart"] = cart
    flash("Added to cart")
    return redirect(_safe_back())


@app.route("/cart/update/<int:product_id>", methods=["POST"])
def update_cart(product_id):
    try:
        qty = int(request.form.get("qty", 1))
    except ValueError:
        qty = 1
    cart = session.get("cart", {})
    key = str(product_id)
    if qty <= 0:
        cart.pop(key, None)
    else:
        db = get_db()
        row = db.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is not None and row["stock"] is not None and qty > row["stock"]:
            flash(f"Only {row['stock']} in stock")
            qty = row["stock"]
        cart[key] = qty
    session["cart"] = cart
    return redirect(url_for("cart_view"))


@app.route("/cart/remove/<int:product_id>", methods=["POST"])
def remove_from_cart(product_id):
    cart = session.get("cart", {})
    key = str(product_id)
    if key in cart:
        del cart[key]
        session["cart"] = cart
    return redirect(url_for("cart_view"))


@app.route("/cart/clear", methods=["POST"])
def clear_cart():
    session["cart"] = {}
    flash("Cart cleared")
    return redirect(url_for("cart_view"))


@app.route("/checkout", methods=["POST"])
def checkout():
    db = get_db()
    customer = request.form.get("customer", "Walk-in") or "Walk-in"
    try:
        discount = float(request.form.get("discount") or 0)
    except ValueError:
        discount = 0
    try:
        tax_rate = float(request.form.get("tax_rate") or TAX_DEFAULT)
    except ValueError:
        tax_rate = TAX_DEFAULT
    cart = session.get("cart", {})
    if not cart:
        flash("Cart is empty")
        return redirect(url_for("index"))
    ids = tuple(int(i) for i in cart.keys())
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(f"SELECT * FROM products WHERE id IN ({placeholders})", ids).fetchall()
    subtotal = 0
    for r in rows:
        qty = cart[str(r["id"])]
        if r["stock"] is not None and qty > r["stock"]:
            flash(f"Insufficient stock for {r['name']}")
            return redirect(url_for("cart_view"))
        subtotal += r["price"] * qty
    discount = max(0, min(discount, subtotal))
    taxable = subtotal - discount
    tax_amt = round(taxable * tax_rate / 100, 2)
    total = round(taxable + tax_amt, 2)
    cur = db.execute(
        "INSERT INTO invoices (customer, subtotal, discount, tax, total, status, created) VALUES (?,?,?,?,?,?,?)",
        (customer, round(subtotal, 2), round(discount, 2), tax_amt, total, "Unpaid", datetime.now(timezone.utc)))
    invoice_id = cur.lastrowid
    for r in rows:
        pid = r["id"]
        qty = cart[str(pid)]
        db.execute("INSERT INTO invoice_items (invoice_id, product_id, quantity, price) VALUES (?,?,?,?)",
                   (invoice_id, pid, qty, r["price"]))
        db.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, pid))
    db.commit()
    session["cart"] = {}
    flash(f"Invoice #{invoice_id} created — Total ${total:.2f}")
    return redirect(url_for("view_invoice", invoice_id=invoice_id))


@app.route("/invoices")
def invoices():
    db = get_db()
    status_f = request.args.get("status", "")
    if status_f in ("Paid", "Partially Paid", "Unpaid"):
        rows = db.execute("SELECT * FROM invoices WHERE status = ? ORDER BY created DESC", (status_f,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM invoices ORDER BY created DESC").fetchall()
    # enrich with paid/outstanding without extra queries per row where possible
    enriched = []
    for inv in rows:
        paid = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id = ?", (inv["id"],)).fetchone()[0] or 0
        outstanding = (inv["total"] or 0) - paid
        status = _invoice_status(inv["total"], paid)
        if status != (inv["status"] or ""):
            try:
                db.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, inv["id"]))
                db.commit()
            except Exception:
                pass
        d = dict(inv)
        d["paid"] = paid
        d["outstanding"] = outstanding
        d["derived_status"] = status
        enriched.append(d)
    return render_template("invoices.html", invoices=enriched, active_status=status_f)


@app.route("/invoice/<int:invoice_id>")
def view_invoice(invoice_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv:
        return "Invoice not found", 404
    items = db.execute("SELECT ii.*, p.name, p.image_url, p.sku FROM invoice_items ii JOIN products p ON p.id = ii.product_id WHERE ii.invoice_id = ?", (invoice_id,)).fetchall()
    payments = db.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY created DESC", (invoice_id,)).fetchall()
    paid = sum(p['amount'] for p in payments) if payments else 0
    outstanding = (inv['total'] or 0) - paid
    attachments = db.execute("SELECT * FROM attachments WHERE invoice_id = ? ORDER BY uploaded_at DESC", (invoice_id,)).fetchall()
    status = _invoice_status(inv["total"], paid)
    return render_template("invoice.html", invoice=inv, items=items, payments=payments, paid=paid, outstanding=outstanding, attachments=attachments, status=status)


@app.route('/order/<int:invoice_id>')
def order_view(invoice_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv:
        return "Order not found", 404
    items = db.execute("SELECT ii.*, p.name, p.image_url, p.sku FROM invoice_items ii JOIN products p ON p.id = ii.product_id WHERE ii.invoice_id = ?", (invoice_id,)).fetchall()
    payments = db.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY created DESC", (invoice_id,)).fetchall()
    paid = sum(p['amount'] for p in payments) if payments else 0
    outstanding = (inv['total'] or 0) - paid
    attachments = db.execute("SELECT * FROM attachments WHERE invoice_id = ? ORDER BY uploaded_at DESC", (invoice_id,)).fetchall()
    status = _invoice_status(inv["total"], paid)
    return render_template('order.html', invoice=inv, items=items, payments=payments, paid=paid, outstanding=outstanding, attachments=attachments, status=status)


@app.route('/invoice/<int:invoice_id>/attachments', methods=['POST'])
def upload_attachment(invoice_id):
    db = get_db()
    if 'file' not in request.files:
        flash('No file part')
        return redirect(url_for('order_view', invoice_id=invoice_id))
    f = request.files['file']
    if f.filename == '':
        flash('No selected file')
        return redirect(url_for('order_view', invoice_id=invoice_id))
    filename = secure_filename(f.filename)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        flash('File type not allowed')
        return redirect(url_for('order_view', invoice_id=invoice_id))
    inv_dir = UPLOAD_FOLDER / str(invoice_id)
    os.makedirs(inv_dir, exist_ok=True)
    filepath = inv_dir / filename
    f.save(filepath)
    db.execute('INSERT INTO attachments (invoice_id, filename, filepath, uploaded_at) VALUES (?,?,?,?)', (invoice_id, filename, str(filepath), datetime.now(timezone.utc)))
    db.commit()
    flash('Attachment uploaded')
    return redirect(url_for('order_view', invoice_id=invoice_id))


@app.route('/invoice/<int:invoice_id>/attachment/<int:attachment_id>')
def serve_attachment(invoice_id, attachment_id):
    db = get_db()
    row = db.execute('SELECT * FROM attachments WHERE id = ? AND invoice_id = ?', (attachment_id, invoice_id)).fetchone()
    if not row:
        return 'Not found', 404
    path = Path(row['filepath'])
    if not path.exists():
        return 'File missing', 404
    return send_from_directory(path.parent, path.name, as_attachment=True)


@app.route('/invoice/<int:invoice_id>/send_email', methods=['POST'])
def send_email(invoice_id):
    db = get_db()
    to_addr = request.form.get('to')
    subject = request.form.get('subject') or f'Invoice #{invoice_id}'
    message = request.form.get('message') or ''
    if not to_addr:
        flash('Recipient required')
        return redirect(url_for('order_view', invoice_id=invoice_id))
    em = EmailMessage()
    em['Subject'] = subject
    em['From'] = os.environ.get('SMTP_FROM', 'no-reply@example.com')
    em['To'] = to_addr
    em.set_content(message)
    try:
        pdf_resp = invoice_pdf(invoice_id)
        data = pdf_resp.get_data()
        em.add_attachment(data, maintype='application', subtype='pdf', filename=f'invoice_{invoice_id}.pdf')
    except Exception:
        pass
    rows = db.execute('SELECT * FROM attachments WHERE invoice_id = ?', (invoice_id,)).fetchall()
    for r in rows:
        p = Path(r['filepath'])
        if p.exists():
            with open(p, 'rb') as fh:
                em.add_attachment(fh.read(), maintype='application', subtype='octet-stream', filename=r['filename'])
    smtp_host = os.environ.get('SMTP_HOST')
    smtp_port = int(os.environ.get('SMTP_PORT', '0')) if os.environ.get('SMTP_PORT') else None
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    try:
        if smtp_host and smtp_port:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                s.starttls()
                if smtp_user and smtp_pass:
                    s.login(smtp_user, smtp_pass)
                s.send_message(em)
            flash('Email sent')
        else:
            print('--- Email to', to_addr)
            print(em)
            flash('SMTP not configured — email printed to server console')
    except Exception as e:
        flash('Failed to send email: ' + str(e))
    return redirect(url_for('order_view', invoice_id=invoice_id))


@app.route('/invoice/<int:invoice_id>/payments', methods=['POST'])
def record_payment(invoice_id):
    db = get_db()
    try:
        amount = float(request.form.get('amount') or 0)
    except ValueError:
        amount = 0
    if amount <= 0:
        flash('Enter a valid amount')
        return redirect(url_for('order_view', invoice_id=invoice_id))
    method = request.form.get('method') or 'Manual'
    note = request.form.get('note') or ''
    db.execute('INSERT INTO payments (invoice_id, amount, method, note, created) VALUES (?,?,?,?,?)',
               (invoice_id, amount, method, note, datetime.now(timezone.utc)))
    inv = db.execute("SELECT total FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    paid = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchone()[0] or 0
    if inv:
        db.execute("UPDATE invoices SET status = ? WHERE id = ?", (_invoice_status(inv["total"], paid), invoice_id))
    db.commit()
    flash('Payment recorded')
    return redirect(url_for('order_view', invoice_id=invoice_id))


@app.route("/invoice/<int:invoice_id>/pdf")
def invoice_pdf(invoice_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv:
        return "Invoice not found", 404
    items = db.execute("SELECT ii.*, p.name FROM invoice_items ii JOIN products p ON p.id = ii.product_id WHERE ii.invoice_id = ?", (invoice_id,)).fetchall()
    paid = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchone()[0] or 0

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=60, bottomMargin=40)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph("MyShop — Premium Billing", styles['Title']))
    elems.append(Paragraph("123 Business Rd, City, Country • support@myshop.com", styles['Normal']))
    elems.append(Spacer(1, 12))
    elems.append(Paragraph(f"Invoice #{inv['id']} — {inv['status'] or ''}", styles['Heading2']))
    elems.append(Paragraph(f"Customer: {inv['customer']}", styles['Normal']))
    elems.append(Paragraph(f"Date: {inv['created']}", styles['Normal']))
    elems.append(Spacer(1, 12))

    data = [["Product", "Qty", "Price", "Subtotal"]]
    for it in items:
        subtotal = it['quantity'] * it['price']
        data.append([it['name'], str(it['quantity']), f"${it['price']:.2f}", f"${subtotal:.2f}"])
    subtotal_v = inv['subtotal'] if inv['subtotal'] else sum(it['quantity'] * it['price'] for it in items)
    data.append(["", "", "Subtotal:", f"${(subtotal_v or 0):.2f}"])
    data.append(["", "", "Discount:", f"-${(inv['discount'] or 0):.2f}"])
    data.append(["", "", "Tax:", f"${(inv['tax'] or 0):.2f}"])
    data.append(["", "", "Total:", f"${(inv['total'] or 0):.2f}"])
    data.append(["", "", "Paid:", f"${paid:.2f}"])
    data.append(["", "", "Due:", f"${(inv['total'] or 0) - paid:.2f}"])

    table = Table(data, colWidths=[240, 60, 80, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
    ]))
    elems.append(table)
    elems.append(Spacer(1, 12))
    elems.append(Paragraph("Thank you for shopping with MyShop!", styles['Normal']))
    doc.build(elems)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f"invoice_{invoice_id}.pdf")


@app.route('/admin/products', methods=['GET', 'POST'])
def admin_products():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description') or ''
        category = request.form.get('category') or 'General'
        try:
            price = float(request.form.get('price') or 0)
        except ValueError:
            price = 0
        try:
            stock = int(request.form.get('stock') or 50)
        except ValueError:
            stock = 50
        sku = request.form.get('sku') or ''
        image_url = request.form.get('image_url') or None
        img_file = request.files.get('image_file')
        if img_file and img_file.filename:
            filename = secure_filename(img_file.filename)
            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_IMG_EXT:
                flash('Image type not allowed (png/jpg/jpeg/webp/gif)')
                return redirect(url_for('admin_products'))
            filename = f"{secrets.token_hex(8)}{ext}"
            img_file.save(PRODUCT_IMG_FOLDER / filename)
            image_url = f"/static/product_images/{filename}"
        db.execute('INSERT INTO products (name, description, category, price, stock, sku, image_url) VALUES (?,?,?,?,?,?,?)',
                   (name, description, category, price, stock, sku, image_url))
        db.commit()
        flash('Product added')
        return redirect(url_for('admin_products'))
    rows = db.execute('SELECT * FROM products ORDER BY id DESC').fetchall()
    return render_template('admin_products.html', products=rows)


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


if __name__ == "__main__":
    if not DB_PATH.exists():
        with app.app_context():
            init_db()
    app.run(debug=DEBUG, use_reloader=DEBUG)
