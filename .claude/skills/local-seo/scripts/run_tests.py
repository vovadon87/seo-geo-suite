#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты local-seo (nap_check) на локальном сайте с заведомыми расхождениями. Без сети.
Запуск: python run_tests.py"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PAGES = {
    "/": '<html><head><title>Главная</title></head><body><h1>Фирма</h1>'
         '<a href="tel:+78121234567">+7 (812) 123-45-67</a> <p>Звоните: 8 812 123 45 67</p></body></html>',
    "/contacts": '<html><head><title>Контакты</title>'
                 '<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"Фирма А",'
                 '"telephone":"+7 (495) 999-00-11","address":{"@type":"PostalAddress","streetAddress":"ул. Ленина, 5","addressLocality":"Москва"}}</script>'
                 '</head><body><h1>Контакты</h1><p>Телефон: +7 (495) 765-43-21</p><p>Адрес: ул. Садовая, д. 10</p>'
                 '<p>mail: info@example.com</p></body></html>',
    "/about": '<html><head><title>О нас</title></head><body><p>Мы на проспекте Мира, д. 3. Дата 2024-05-17.</p></body></html>',
    "/empty": '<html><head><title>Пусто</title></head><body><p>Нет контактов</p></body></html>',
}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        body = PAGES.get(p)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class NapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.out = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, os.path.join(HERE, "nap_check.py"), base, "--out", cls.out],
                           capture_output=True, text=True, timeout=60)
        cls.stdout, cls.stderr = r.stdout, r.stderr
        with open(os.path.join(cls.out, "nap.json"), encoding="utf-8") as fh:
            cls.rep = json.load(fh)
        cls.ids = {f["id"] for f in cls.rep["findings"]}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_clean_run(self):
        self.assertEqual(self.stderr.strip(), "", self.stderr)

    def test_phone_normalisation_same_number(self):
        # +7 (812) 123-45-67 и 8 812 123 45 67 — один номер в двух форматах
        self.assertIn("78121234567", self.rep["aggregate"]["phones"])
        self.assertEqual(len(self.rep["aggregate"]["phones"]["78121234567"]["forms"]), 2)
        self.assertIn("N3", self.ids)

    def test_multiple_phones_is_question_not_error(self):
        f = next(x for x in self.rep["findings"] if x["id"] == "N2")
        self.assertEqual(f["priority"], "P2")
        self.assertTrue(f["ask_owner"])

    def test_tel_link_missing(self):
        self.assertIn("N4", self.ids)  # телефон 495 без tel:

    def test_addresses_differ(self):
        self.assertIn("N5", self.ids)
        self.assertGreaterEqual(len(self.rep["aggregate"]["addresses"]), 2)

    def test_schema_phone_mismatch(self):
        self.assertIn("N7", self.ids)  # в разметке 495 999-00-11, на страницах других номеров нет

    def test_date_not_taken_for_phone(self):
        for n in self.rep["aggregate"]["phones"]:
            self.assertNotIn("20240517", n)

    def test_email_found(self):
        self.assertIn("info@example.com", self.rep["aggregate"]["emails"])

    def test_manual_checklist_present(self):
        self.assertGreaterEqual(len(self.rep["manual_checklist"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
