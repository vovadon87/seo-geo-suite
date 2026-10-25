#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты api_fetch.py на локальном имитаторе GSC и Вебмастера (структура ответов по документации). Запуск: python run_api_tests.py"""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import api_fetch as af  # noqa: E402
import period_compare as pc  # noqa: E402

LOG = []


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        LOG.append(("POST", self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != "Bearer good":
            return self._json(401, {"error": {"message": "bad token"}})
        total = 5  # всего строк у «сайта»
        start, lim = body["startRow"], body["rowLimit"]
        rows = [{"keys": ["запрос %d" % i], "clicks": 10.0 - i, "impressions": 100.0 + i, "ctr": (10.0 - i) / (100.0 + i),
                 "position": 3.5 + i} for i in range(total)][start:start + lim]
        self._json(200, {"rows": rows, "responseAggregationType": "auto"})

    def do_GET(self):
        LOG.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.headers.get("Authorization") != "OAuth good":
            return self._json(403, {"error_code": "INVALID_USER_ID"})
        p = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(p.query)
        if p.path == "/v4/user":
            return self._json(200, {"user_id": 123})
        if p.path == "/v4/user/123/hosts":
            return self._json(200, {"hosts": [{"host_id": "https:example.ru:443", "unicode_host_url": "https://example.ru"}]})
        if p.path.endswith("/search-queries/popular"):
            off, lim = int(q["offset"][0]), int(q["limit"][0])
            allq = [{"query_id": str(i), "query_text": "яндекс запрос %d" % i,
                     "indicators": {"TOTAL_SHOWS": 200.0 - i, "TOTAL_CLICKS": 20.0 - i, "AVG_SHOW_POSITION": 5.0 + i}} for i in range(7)]
            allq.append({"query_id": "x", "query_text": "без кликов", "indicators": {"TOTAL_SHOWS": 50.0}})
            return self._json(200, {"queries": allq[off:off + lim], "date_from": q["date_from"][0], "date_to": q["date_to"][0], "count": len(allq)})
        self._json(404, {})


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        af.GSC_BASE = base + "/webmasters/v3"
        af.YWM_BASE = base + "/v4"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_gsc_request_shape_and_paging(self):
        LOG.clear()
        rows = af.gsc_rows("sc-domain:example.com", "good", "2026-09-01", "2026-09-28", "query", country="rus", page_size=2)
        self.assertEqual(len(rows), 5)
        posts = [l for l in LOG if l[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertIn("sc-domain%3Aexample.com", posts[0][1])
        b = posts[0][3]
        self.assertEqual(b["dimensions"], ["query"])
        self.assertEqual(b["dataState"], "final")
        self.assertEqual(b["dimensionFilterGroups"][0]["filters"][0]["expression"], "rus")
        self.assertEqual([p[3]["startRow"] for p in posts], [0, 2, 4])

    def test_gsc_table_format_readable_by_period_compare(self):
        rows = af.gsc_rows("https://example.com/", "good", "2026-09-01", "2026-09-28", "query")
        t = af.gsc_to_table(rows, "query")
        self.assertEqual(t[0][0], "Top queries")
        path = os.path.join(tempfile.mkdtemp(), "g.csv")
        af.write_csv(path, t)
        data = pc.load(path)
        self.assertEqual(pc.totals(data)["clicks"], sum(int(round(r["clicks"])) for r in rows))

    def test_bad_token_gives_hint(self):
        with self.assertRaises(af.ApiError) as c:
            af.gsc_rows("sc-domain:example.com", "bad", "2026-09-01", "2026-09-28", "query")
        self.assertIn("токен", str(c.exception))

    def test_ywm_host_lookup_and_paging(self):
        uid = af.ywm_user_id("good")
        self.assertEqual(uid, 123)
        hid = af.ywm_host_id("good", uid, "https://www.example.ru/")
        self.assertEqual(hid, "https:example.ru:443")
        rows = af.ywm_rows("good", uid, hid, "2026-09-01", "2026-09-28", page_size=3)
        self.assertEqual(len(rows), 8)

    def test_ywm_table_and_missing_indicators(self):
        rows = af.ywm_rows("good", 123, "https:example.ru:443", "2026-09-01", "2026-09-28")
        t = af.ywm_to_table(rows)
        self.assertEqual(t[0], ["Запрос", "Показы", "Клики", "CTR", "Средняя позиция"])
        last = t[-1]
        self.assertEqual(last[0], "без кликов")
        self.assertEqual(last[2], "")      # нет данных ≠ 0
        path = os.path.join(tempfile.mkdtemp(), "y.csv")
        af.write_csv(path, t)
        data = pc.load(path)
        self.assertTrue(data)

    def test_ywm_unknown_host(self):
        with self.assertRaises(af.ApiError):
            af.ywm_host_id("good", 123, "other.ru")

    def test_ywm_bad_token(self):
        with self.assertRaises(af.ApiError):
            af.ywm_user_id("nope")

    def test_cli_no_token_message(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k not in ("GSC_ACCESS_TOKEN", "YWM_TOKEN")}
        r = subprocess.run([sys.executable, os.path.join(HERE, "api_fetch.py"), "gsc", "--site", "x", "--from", "2026-09-01",
                            "--to", "2026-09-02", "--out", "o.csv"], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("GSC_ACCESS_TOKEN", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
