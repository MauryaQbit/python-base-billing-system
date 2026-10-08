"""Smoke tests for core routes. Uses an isolated temp DB — never touches billing.db.

Run:  python -m unittest discover -s tests -v
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

import app as app_module


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._real_db = app_module.DB_PATH
        cls._test_db = Path(cls._tmp.name) / "test_billing.db"
        app_module.DB_PATH = cls._test_db
        conn = sqlite3.connect(cls._test_db)
        with open(BASE_DIR / "schema.sql") as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO products (name, description, category, price, stock, sku, image_url)"
            " VALUES (?,?,?,?,?,?,?)",
            ("Test Widget", "A widget for tests", "Test", 10.00, 25, "TST-001", None),
        )
        conn.commit()
        conn.close()
        app_module.app.config["TESTING"] = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        app_module.DB_PATH = cls._real_db
        cls._tmp.cleanup()

    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")

    def test_home(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Test Widget", r.data.decode())

    def test_product_detail(self):
        r = self.client.get("/product/1")
        self.assertEqual(r.status_code, 200)

    def test_product_detail_404(self):
        r = self.client.get("/product/9999")
        self.assertEqual(r.status_code, 404)

    def test_cart_flow(self):
        self.assertEqual(self.client.get("/cart").status_code, 200)
        r = self.client.post("/cart/add/1")
        self.assertIn(r.status_code, (301, 302))
        r = self.client.post("/checkout", data={"customer": "Tester"})
        self.assertIn(r.status_code, (301, 302))
        invoice_url = r.headers["Location"]
        self.assertIn("/invoice/", invoice_url)
        self.assertEqual(self.client.get(invoice_url).status_code, 200)

    def test_invoices_page(self):
        self.assertEqual(self.client.get("/invoices").status_code, 200)

    def test_invoice_pdf(self):
        self.client.post("/cart/add/1")
        r = self.client.post("/checkout", data={"customer": "PDF Tester"})
        inv_id = r.headers["Location"].rstrip("/").split("/")[-1]
        r = self.client.get(f"/invoice/{inv_id}/pdf")
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/pdf", r.content_type)

    def test_admin_add_product(self):
        r = self.client.post("/admin/products",
                             data={"name": "Admin Widget", "price": "5.50"})
        self.assertIn(r.status_code, (301, 302))
        self.assertIn("Admin Widget", self.client.get("/").data.decode())


if __name__ == "__main__":
    unittest.main()
