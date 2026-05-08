# python-base-billing-system
This is a minimal Python-based billing system with a simple, attractive web UI built with Flask and Bootstrap.

Quick start
1. Create a virtual environment and install dependencies:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

2. Seed the database and run the app:

```bash
python seed.py
python app.py
```

Open http://127.0.0.1:5000 in your browser.

If you'd like enhancements (PDF invoices, user auth, nicer styles), tell me which features to prioritize.
 
Email and attachments
- To enable sending emails from the app, set these environment variables before starting the server:

```powershell
setx SMTP_HOST "smtp.example.com"
setx SMTP_PORT "587"
setx SMTP_USER "your-smtp-user"
setx SMTP_PASS "your-smtp-password"
setx SMTP_FROM "your@company.com"
```

If SMTP is not configured the app will print the email to the server console instead of sending.

Attachments uploaded from the order page are stored in `uploads/<invoice_id>/` and served by the app.
