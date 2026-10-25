#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты render_check.py на локальном сайте: SPA-страница (контент и мета только после JS) и статичная страница.
Нужны playwright и Chromium (CHROME_PATH); без них тесты пропускаются. Запуск: python run_render_tests.py"""
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

try:
    import playwright  # noqa: F401
    HAVE_PW = True
except ImportError:
    HAVE_PW = False

LINKS = "".join('<a href="/p%d">p%d</a> ' % (i, i) for i in range(8))
SPA_JS = """
document.title='Заголовок из JS';
var m=document.createElement('meta');m.name='description';m.content='Описание из JS';document.head.appendChild(m);
var c=document.createElement('link');c.rel='canonical';c.href='/spa-final';document.head.appendChild(c);
var s=document.createElement('script');s.type='application/ld+json';s.text='{"@context":"https://schema.org","@type":"Organization","name":"X"}';document.head.appendChild(s);
document.getElementById('app').innerHTML='<h1>Каталог</h1><p>%s</p>%s';
""" % ("слово " * 200, LINKS)

PAGES = {
    "/": '<html lang="ru"><head><title>Главная</title></head><body><a href="/spa">spa</a> <a href="/static">static</a> <a href="/noidx">noidx</a></body></html>',
    "/spa": '<html lang="ru"><head><meta charset="utf-8"></head><body><div id="app"></div><script src="/app.js"></script></body></html>',
    "/static": ('<html lang="ru"><head><title>Статика</title><meta name="description" content="Описание">'
                '<link rel="canonical" href="{base}/static"></head><body><h1>Статика</h1><p>' + "текст " * 120 + '</p></body></html>'),
    "/noidx": ('<html lang="ru"><head><title>N</title><meta name="robots" content="noindex"></head><body><h1>N</h1><p>' + "т " * 60 +
               '</p><script>document.querySelector("meta[name=robots]").content="index,follow";</script></body></html>'),
}


class H(http.server.BaseHTTPRequestHandler):
    base = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            body, ctype = b"User-agent: *\nAllow: /\n", "text/plain"
        elif path == "/app.js":
            body, ctype = SPA_JS.encode(), "application/javascript"
        elif path in PAGES:
            body, ctype = PAGES[path].replace("{base}", self.base).encode(), "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(HAVE_PW, "нет playwright")
class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        H.base = "http://127.0.0.1:%d" % srv.server_address[1]
        cls.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        out = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, os.path.join(HERE, "render_check.py"), H.base, "--pages", "/spa,/static,/noidx",
                            "--ignore-robots", "--wait", "3000", "--out", out], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.data = json.load(open(os.path.join(out, "render-check.json"), encoding="utf-8"))
        cls.report = open(os.path.join(out, "render-report.md"), encoding="utf-8").read()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def codes(self, path):
        return {f["code"] for f in self.data["findings"] if f["url"].endswith(path)}

    def test_spa_content_links_meta(self):
        c = self.codes("/spa")
        self.assertTrue({"J1", "J2", "J3", "J5", "J6"} <= c, c)

    def test_static_page_clean(self):
        self.assertEqual(self.codes("/static"), set())

    def test_noindex_removed_by_js_is_p0(self):
        f = [x for x in self.data["findings"] if x["code"] == "J4"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["priority"], "P0")

    def test_report_has_limits(self):
        self.assertIn("Не проверено", self.report)
        self.assertIn("Границы", self.report)

    def test_page_table_counts(self):
        spa = [p for p in self.data["pages"] if p["url"].endswith("/spa")][0]
        self.assertLess(spa["raw_words"], 5)
        self.assertGreater(spa["rendered_words"], 150)


class Unit(unittest.TestCase):
    def test_compare_no_diff(self):
        import render_check as rc
        s = {"title": "a", "description": "d", "robots": None, "canonical": None, "h1": ["x"], "words": 300, "types": [], "anchors": []}
        self.assertEqual(rc.compare("u", s, dict(s), set(), set()), [])

    def test_compare_title_differs(self):
        import render_check as rc
        a = {"title": "a", "description": "d", "robots": None, "canonical": None, "h1": ["x"], "words": 300, "types": [], "anchors": []}
        b = dict(a, title="b")
        self.assertEqual([f["code"] for f in rc.compare("u", a, b, set(), set())], ["J7"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
