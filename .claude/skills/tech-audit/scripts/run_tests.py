#!/usr/bin/env python3
"""Тесты tech-audit на локальном тестовом сайте (без сети).
Запуск: python run_tests.py
Поднимает сервер с заведомо «больными» страницами и проверяет, что краулер находит нужные проблемы
и не придумывает лишнего."""
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
    "/": ('<html lang="ru"><head><title>Главная</title><meta name="description" content="Описание главной">'
          '<meta name="viewport" content="width=device-width"><link rel="canonical" href="{base}/">'
          '<script src="/a.js"></script><script src="/b.js"></script><script src="/c.js"></script>'
          '<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Organization","name":"[Название]",'
          '"url":"https://ваш-домен/"}}</script>'
          '<script type="application/ld+json">{{"@type":"Product","name":"X","aggregateRating":{{"ratingValue":5}}}}</script>'
          '</head><body><h1>Главная</h1><a href="/a">A</a> <a href="/dup1">d1</a> <a href="/dup2">d2</a> '
          '<a href="/broken">битая</a> <a href="/redir">редирект</a> <a href="/chain1">цепочка</a> <a href="/private">p</a>'
          ' <a href="/deep1">глубже</a><img src="/img/ok.png" alt="ок" width="10" height="10">'
          '<img src="/img/missing.png"><p>' + "слово " * 200 + '</p></body></html>'),
    "/a": ('<html lang="ru"><head><title>Страница A</title><meta name="viewport" content="width=device-width">'
           '<link rel="canonical" href="{base}/other"></head><body><h1>A</h1><h1>Ещё H1</h1><p>коротко</p></body></html>'),
    "/dup1": '<html lang="ru"><head><title>Одинаковый</title><meta name="description" content="Одно">'
             '<meta name="viewport" content="x"><link rel="canonical" href="{base}/dup1"></head><body><h1>1</h1>' + "т " * 200 + '</body></html>',
    "/dup2": '<html lang="ru"><head><title>Одинаковый</title><meta name="description" content="Одно">'
             '<meta name="viewport" content="x"><link rel="canonical" href="{base}/dup2"></head><body><h1>2</h1>' + "т " * 200 + '</body></html>',
    "/private": '<html lang="ru"><head><title>Закрытая</title><meta name="robots" content="noindex"></head><body><h1>x</h1></body></html>',
    "/orphan": '<html lang="ru"><head><title>Сирота</title><meta name="description" content="d"><meta name="viewport" content="x">'
               '<link rel="canonical" href="{base}/orphan"></head><body><h1>o</h1>' + "т " * 200 + '</body></html>',
    "/chain2": None,
    "/deep1": '<html lang="ru"><head><title>D1</title></head><body><a href="/deep2">d</a></body></html>',
    "/deep2": '<html lang="ru"><head><title>D2</title></head><body><a href="/deep3">d</a></body></html>',
    "/deep3": '<html lang="ru"><head><title>D3</title></head><body><a href="/deep4">d</a></body></html>',
    "/deep4": '<html lang="ru"><head><title>D4</title></head><body><h1>конец</h1></body></html>',
    "/secret/x": '<html><head><title>секрет</title></head><body>закрыто robots</body></html>',
}


class H(http.server.BaseHTTPRequestHandler):
    base = ""

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/robots.txt":
            return self._send(200, f"User-agent: *\nDisallow: /secret/\nSitemap: {self.base}/sitemap.xml\n".encode(), "text/plain")
        if p == "/sitemap.xml":
            urls = ["/", "/a", "/private", "/orphan", "/redir"]
            xml = "<urlset>" + "".join(f"<url><loc>{self.base}{u}</loc></url>" for u in urls) + "</urlset>"
            return self._send(200, xml.encode(), "application/xml")
        if p == "/redir":
            return self._send(301, b"", extra={"Location": "/a"})
        if p == "/chain1":
            return self._send(301, b"", extra={"Location": "/chain2"})
        if p == "/chain2":
            return self._send(301, b"", extra={"Location": "/a"})
        if p == "/img/ok.png":
            return self._send(200, b"\x89PNG", "image/png")
        if p.startswith("/seo-geo-audit-404-"):
            return self._send(200, "<html><title>soft</title></html>".encode())  # мягкая 404 намеренно
        if p in PAGES and PAGES[p]:
            return self._send(200, PAGES[p].replace("{base}", self.base).replace("{{", "{").replace("}}", "}").encode())
        return self._send(404, b"not found")


class CrawlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        port = cls.srv.server_address[1]
        H.base = f"http://127.0.0.1:{port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.out = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, os.path.join(HERE, "site_crawl.py"), H.base, "--out", cls.out,
                            "--delay", "0", "--max-pages", "60"], capture_output=True, text=True, timeout=120)
        cls.stdout, cls.stderr = r.stdout, r.stderr
        with open(os.path.join(cls.out, "crawl.json"), encoding="utf-8") as f:
            cls.rep = json.load(f)
        cls.ids = {f["id"] for f in cls.rep["findings"]}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_runs_clean(self):
        self.assertEqual(self.stderr.strip(), "", self.stderr)

    def test_expected_findings(self):
        expected = {"P-4xx", "P-CHAIN", "P-RLINK", "P-SOFT404", "S3", "S5", "M2", "M4", "M3", "M6", "M7", "C1", "C4",
                    "T2", "I1", "I2", "I3", "L1" if False else "L2", "V3"}
        missing = expected - self.ids
        self.assertFalse(missing, f"не найдено: {missing}; есть: {sorted(self.ids)}")

    def test_robots_respected(self):
        self.assertTrue(any("/secret/x" in u for u in self.rep["blocked_by_robots"]) or
                        not any("/secret/x" in u for u in self.rep["pages"]))
        fetched = [u for u, d in self.rep["pages"].items() if d.get("status") == 200]
        self.assertFalse(any("/secret/" in u for u in fetched))

    def test_placeholder_and_risky_schema(self):
        msgs = " ".join(f["problem"] for f in self.rep["findings"] if f["id"] == "L2")
        self.assertIn("шаблонное значение", msgs)
        self.assertIn("AggregateRating", msgs)

    def test_depth(self):
        d = self.rep["pages"][H.base + "/deep4"]["depth"]
        self.assertEqual(d, 4)

    def test_no_false_p0_on_healthy_site(self):
        p0 = [f for f in self.rep["findings"] if f["priority"] == "P0"]
        self.assertEqual(p0, [], p0)

    def test_csv_written(self):
        self.assertTrue(os.path.exists(os.path.join(self.out, "findings.csv")))


class ParserTest(unittest.TestCase):
    def test_parse(self):
        from common import parse_html, normalize
        p = parse_html('<html lang="en"><head><title> T </title><meta name="Description" content="d"></head>'
                       '<body><h1>H</h1><a href="/x" rel="nofollow">x</a><script>var a=1</script></body></html>')
        self.assertEqual(p.title.strip(), "T")
        self.assertEqual(p.meta("description"), "d")
        self.assertEqual(p.html_lang, "en")
        self.assertNotIn("var a", p.text())
        self.assertEqual(normalize("HTTP://Ex.com:80/a#f"), "http://ex.com/a")


class RobotsTest(unittest.TestCase):
    def test_ai_matrix(self):
        from robots_check import analyse
        a = analyse("https://x.ru", "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
        m = {b["bot"]: b["home_allowed"] for b in a["bots"]}
        self.assertFalse(m["GPTBot"])
        self.assertTrue(m["ClaudeBot"])
        self.assertTrue(m["YandexBot"])
        self.assertFalse(a["blocked_all"])

    def test_blocked_all(self):
        from robots_check import analyse
        self.assertTrue(analyse("https://x.ru", "User-agent: *\nDisallow: /\n")["blocked_all"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
