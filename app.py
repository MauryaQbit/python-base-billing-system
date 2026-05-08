import os
import sqlite3
import smtplib
from email.message import EmailMessage
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, send_file, send_from_directory
from pathlib import Path
from datetime import datetime
import io
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "billing.db"

app = Flask(__name__)
app.secret_key = "replace-with-a-secure-random-key"
UPLOAD_FOLDER = BASE_DIR / "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.pdf', '.txt', '.csv'}


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


def migrate_db():
    """Apply small migrations such as adding `image_url` column if missing."""
    db = get_db()
    # check if image_url exists
    cols = [r[1] for r in db.execute("PRAGMA table_info(products)").fetchall()]
    if 'image_url' not in cols:
        db.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
        db.commit()
    # ensure payments table exists
    tbls = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
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


@app.route("/")
def index():
    db = get_db()
    products = db.execute("SELECT * FROM products").fetchall()
    cart = session.get("cart", {})
    cart_count = sum(cart.values())
    return render_template("index.html", products=products, cart_count=cart_count)


@app.route("/cart")
def cart_view():
    db = get_db()
    cart = session.get("cart", {})
    items = []
    total = 0
    if cart:
        ids = tuple(int(i) for i in cart.keys())
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(f"SELECT * FROM products WHERE id IN ({placeholders})", ids).fetchall()
        for r in rows:
            qty = cart[str(r["id"])]
            subtotal = r["price"] * qty
            total += subtotal
            items.append({"product": r, "qty": qty, "subtotal": subtotal})
    return render_template("cart.html", items=items, total=total)


@app.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    cart = session.get("cart", {})
    key = str(product_id)
    cart[key] = cart.get(key, 0) + 1
    session["cart"] = cart
    flash("Added to cart")
    return redirect(url_for("index"))


@app.route("/cart/remove/<int:product_id>")
def remove_from_cart(product_id):
    cart = session.get("cart", {})
    key = str(product_id)
    if key in cart:
        del cart[key]
        session["cart"] = cart
    return redirect(url_for("cart_view"))


@app.route("/checkout", methods=["POST"])
def checkout():
    db = get_db()
    customer = request.form.get("customer", "Walk-in")
    cart = session.get("cart", {})
    if not cart:
        flash("Cart is empty")
        return redirect(url_for("index"))
    total = 0
    ids = tuple(int(i) for i in cart.keys())
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(f"SELECT * FROM products WHERE id IN ({placeholders})", ids).fetchall()
    for r in rows:
        qty = cart[str(r["id"])]
        total += r["price"] * qty
    cur = db.execute("INSERT INTO invoices (customer, total, created) VALUES (?,?,?)", (customer, total, datetime.utcnow()))
    invoice_id = cur.lastrowid
    for r in rows:
        pid = r["id"]
        qty = cart[str(pid)]
        price = r["price"]
        db.execute("INSERT INTO invoice_items (invoice_id, product_id, quantity, price) VALUES (?,?,?,?)", (invoice_id, pid, qty, price))
    db.commit()
    session["cart"] = {}
    flash("Invoice created")
    return redirect(url_for("view_invoice", invoice_id=invoice_id))


@app.route("/invoices")
def invoices():
    db = get_db()
    rows = db.execute("SELECT * FROM invoices ORDER BY created DESC").fetchall()
    return render_template("invoices.html", invoices=rows)


@app.route("/invoice/<int:invoice_id>")
def view_invoice(invoice_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    items = db.execute("SELECT ii.*, p.name FROM invoice_items ii JOIN products p ON p.id = ii.product_id WHERE ii.invoice_id = ?", (invoice_id,)).fetchall()
    payments = db.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY created DESC", (invoice_id,)).fetchall()
    paid = sum(p['amount'] for p in payments) if payments else 0
    outstanding = (inv['total'] or 0) - paid
    attachments = db.execute("SELECT * FROM attachments WHERE invoice_id = ? ORDER BY uploaded_at DESC", (invoice_id,)).fetchall()
    return render_template("invoice.html", invoice=inv, items=items, payments=payments, paid=paid, outstanding=outstanding, attachments=attachments)


@app.route('/order/<int:invoice_id>')
def order_view(invoice_id):
    # richer order detail page (admin-like)
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not inv:
        return "Order not found", 404
    items = db.execute("SELECT ii.*, p.name, p.image_url FROM invoice_items ii JOIN products p ON p.id = ii.product_id WHERE ii.invoice_id = ?", (invoice_id,)).fetchall()
    payments = db.execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY created DESC", (invoice_id,)).fetchall()
    paid = sum(p['amount'] for p in payments) if payments else 0
    outstanding = (inv['total'] or 0) - paid
    attachments = db.execute("SELECT * FROM attachments WHERE invoice_id = ? ORDER BY uploaded_at DESC", (invoice_id,)).fetchall()
    return render_template('order.html', invoice=inv, items=items, payments=payments, paid=paid, outstanding=outstanding, attachments=attachments)


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
    db.execute('INSERT INTO attachments (invoice_id, filename, filepath, uploaded_at) VALUES (?,?,?,?)', (invoice_id, filename, str(filepath), datetime.utcnow()))
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
    # prepare email
    em = EmailMessage()
    em['Subject'] = subject
    em['From'] = os.environ.get('SMTP_FROM', 'no-reply@example.com')
    em['To'] = to_addr
    em.set_content(message)
    # attach invoice PDF
    try:
        pdf_resp = invoice_pdf(invoice_id)
        # invoice_pdf returns a Flask response with BytesIO
        data = pdf_resp.get_data()
        em.add_attachment(data, maintype='application', subtype='pdf', filename=f'invoice_{invoice_id}.pdf')
    except Exception:
        pass
    # attach other attachments
    rows = db.execute('SELECT * FROM attachments WHERE invoice_id = ?', (invoice_id,)).fetchall()
    for r in rows:
        p = Path(r['filepath'])
        if p.exists():
            with open(p, 'rb') as fh:
                em.add_attachment(fh.read(), maintype='application', subtype='octet-stream', filename=r['filename'])
    # send via SMTP if configured, otherwise print to console
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
            # fallback: write to console
            print('--- Email to', to_addr)
            print(em)
            flash('SMTP not configured — email printed to server console')
    except Exception as e:
        flash('Failed to send email: ' + str(e))
    return redirect(url_for('order_view', invoice_id=invoice_id))


@app.route('/invoice/<int:invoice_id>/payments', methods=['POST'])
def record_payment(invoice_id):
    db = get_db()
    amount = float(request.form.get('amount') or 0)
    method = request.form.get('method') or 'Manual'
    note = request.form.get('note') or ''
    created = datetime.utcnow()
    db.execute('INSERT INTO payments (invoice_id, amount, method, note, created) VALUES (?,?,?,?,?)', (invoice_id, amount, method, note, created))
    db.commit()
    flash('Payment recorded')
    return redirect(url_for('order_view', invoice_id=invoice_id))


@app.route("/invoice/<int:invoice_id>/pdf")
def invoice_pdf(invoice_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    items = db.execute("SELECT ii.*, p.name FROM invoice_items ii JOIN products p ON p.id = ii.product_id WHERE ii.invoice_id = ?", (invoice_id,)).fetchall()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=60, bottomMargin=40)
    styles = getSampleStyleSheet()
    elems = []
    # Company header
    elems.append(Paragraph("MyShop — Company Name", styles['Title']))
    elems.append(Paragraph("123 Business Rd, City, Country", styles['Normal']))
    elems.append(Spacer(1,12))
    elems.append(Paragraph(f"Invoice #{inv['id']}", styles['Heading2']))
    elems.append(Paragraph(f"Customer: {inv['customer']}", styles['Normal']))
    elems.append(Paragraph(f"Date: {inv['created']}", styles['Normal']))
    elems.append(Spacer(1, 12))

    data = [["Product", "Qty", "Price", "Subtotal"]]
    total = 0
    for it in items:
        subtotal = it['quantity'] * it['price']
        total += subtotal
        data.append([it['name'], str(it['quantity']), f"${it['price']:.2f}", f"${subtotal:.2f}"])
    data.append(["", "", "Total:", f"${total:.2f}"])

    table = Table(data, colWidths=[240, 60, 80, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ALIGN', (1,1), (-1,-1), 'RIGHT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
    ]))

    elems.append(table)
    doc.build(elems)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f"invoice_{invoice_id}.pdf")


@app.route('/admin/products', methods=['GET', 'POST'])
def admin_products():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name')
        price = float(request.form.get('price') or 0)
        image_url = request.form.get('image_url') or None
        db.execute('INSERT INTO products (name, price, image_url) VALUES (?,?,?)', (name, price, image_url))
        db.commit()
        flash('Product added')
        return redirect(url_for('admin_products'))
    rows = db.execute('SELECT * FROM products ORDER BY id DESC').fetchall()
    return render_template('admin_products.html', products=rows)


if __name__ == "__main__":
    if not DB_PATH.exists():
        with app.app_context():
            init_db()
    app.run(debug=True)
