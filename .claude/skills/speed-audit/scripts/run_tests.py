#!/usr/bin/env python3
"""Тесты speed-audit. Без внешней сети.
- psi/crux/lighthouse_run: разбор на СИНТЕТИЧЕСКИХ ответах (по структуре документации API; живые вызовы требуют ключ).
- page_weight: сквозной тест в Chromium на локальном «тяжёлом» сайте (нужен Playwright + Chromium).
- lighthouse_run: сквозной тест, если найден Lighthouse (иначе пропускается).
Запуск: python run_tests.py"""
import http.server
import json
import os
import struct
import sys
import threading
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def make_png(w, h):
    raw = b"".join(b"\x00" + os.urandom(w * 3) for _ in range(h))  # несжимаемый шум

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 0)) + chunk(b"IEND", b"")


BIG_PNG = make_png(700, 500)
BIG_JS = ("/*" + "x" * 700_000 + "*/ window.__big=1;").encode()
HTML = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Тяжёлая страница</title>
<script src="/a.js"></script><script src="/b.js"></script><script src="/c.js"></script>
<script src="http://localhost:{{port}}/third.js"></script>
<link rel="stylesheet" href="/s.css">
</head><body><h1>Тест</h1>
<img id="hero" src="/hero.png" loading="lazy" style="width:300px">
{"".join(f'<img src="/hero.png?{i}" style="width:100px">' for i in range(4))}
<div style="height:3000px"></div>
{"".join(f'<img src="/hero.png?b{i}" style="width:100px">' for i in range(4))}
</body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _s(self, body, ctype, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        port = self.server.server_address[1]
        if p == "/":
            return self._s(HTML.replace("{port}", str(port)).encode(), "text/html; charset=utf-8")
        if p in ("/a.js", "/b.js", "/c.js", "/third.js"):
            return self._s(BIG_JS if p == "/a.js" else b"window.x=1;", "application/javascript")
        if p == "/s.css":
            return self._s(b"body{margin:0}", "text/css")
        if p == "/hero.png":
            return self._s(BIG_PNG, "image/png")
        self.send_response(404)
        self.end_headers()


def load_budget():
    with open(os.path.join(HERE, "budget.json"), encoding="utf-8") as fh:
        return json.load(fh)


class SyntheticParsers(unittest.TestCase):
    def test_third_party_share(self):
        import page_weight as pw
        reqs = [{"url": "https://site.ru/", "status": 200, "mime": "text/html", "type": "Document", "size": 20000, "headers": {"content-encoding": "br"}},
                {"url": "https://cdn.tracker.com/t.js", "status": 200, "mime": "application/javascript", "type": "Script", "size": 90000,
                 "headers": {"content-encoding": "br", "cache-control": "max-age=3600"}}]
        dom = {"imgs": [], "headScripts": [], "blockingCss": [], "dom_nodes": 100, "ttfb": 100, "dcl": 1, "load": 1, "fonts_count": 0}
        s, f = pw.analyse(reqs, dom, {"lcp": None, "cls": 0, "longtasks": []}, "www.site.ru", load_budget())
        self.assertIn("W7", {x["id"] for x in f})
        self.assertGreater(s["third_party_share_pct"], 70)

    def test_psi_summarize(self):
        import psi
        j = {"id": "https://x.ru/", "loadingExperience": {"overall_category": "AVERAGE", "origin_fallback": True, "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 3100, "category": "AVERAGE"},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5, "category": "FAST"}}},
            "lighthouseResult": {"lighthouseVersion": "12", "categories": {"performance": {"score": 0.41}},
                                 "audits": {"largest-contentful-paint": {"numericValue": 4200.4},
                                            "cumulative-layout-shift": {"numericValue": 0.1234},
                                            "uses-optimized-images": {"title": "Эффективно кодируйте изображения", "score": 0.3,
                                                                      "details": {"type": "opportunity", "overallSavingsMs": 800, "overallSavingsBytes": 204800}}}}}
        s = psi.summarize(j)
        self.assertEqual(s["scores"]["performance"], 41)
        self.assertEqual(s["lab"]["LCP"], 4200)
        self.assertEqual(s["lab"]["CLS"], 0.123)
        self.assertEqual(s["field_url"]["LCP"]["p75"], 3100)
        self.assertTrue(s["field_is_origin_fallback"])
        self.assertEqual(s["opportunities"][0]["savings_kb"], 200)

    def test_crux_summarize(self):
        import crux
        rec = {"record": {"key": {"url": "https://x.ru/"}, "metrics": {
            "largest_contentful_paint": {"percentiles": {"p75": 2300}, "histogram": [{"density": 0.8}, {"density": 0.1}, {"density": 0.1}]},
            "interaction_to_next_paint": {"percentiles": {"p75": 180}},
            "cumulative_layout_shift": {"percentiles": {"p75": "0.05"}}}}}
        s = crux.summarize(rec)
        self.assertTrue(s["core_web_vitals_passed"])
        rec["record"]["metrics"]["interaction_to_next_paint"]["percentiles"]["p75"] = 350
        self.assertFalse(crux.summarize(rec)["core_web_vitals_passed"])
        self.assertIsNone(crux.summarize(None))
        del rec["record"]["metrics"]["cumulative_layout_shift"]
        self.assertIsNone(crux.summarize(rec)["core_web_vitals_passed"])  # данных не хватает — вывода нет

    def test_lighthouse_aggregate(self):
        import lighthouse_run as lr
        rows = [{"LCP": 2000, "CLS": 0.01, "score": 80}, {"LCP": 2100, "CLS": 0.01, "score": 79}, {"LCP": 3500, "CLS": 0.01, "score": 60}]
        a = lr.aggregate(rows)
        self.assertEqual(a["LCP"]["median"], 2100)
        self.assertTrue(a["LCP"]["unstable"])
        self.assertFalse(a["CLS"]["unstable"])


class BrowserE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("нет Playwright")
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_page_weight(self):
        import page_weight as pw
        budget = load_budget()
        reqs, dom, metrics, meta = pw.measure(f"http://127.0.0.1:{self.port}/", "mobile", False, wait_ms=800)
        summary, findings = pw.analyse(reqs, dom, metrics, "127.0.0.1", budget)
        ids = {f["id"] for f in findings}
        self.assertEqual(meta["status"], 200)
        self.assertGreater(summary["by_type"]["js"]["kb"], 600)
        for must in ("W3", "W8", "W10", "W16"):
            self.assertIn(must, ids, f"нет {must}; есть {sorted(ids)}")
        self.assertIn("W9", ids)  # статика без Cache-Control
        self.assertGreaterEqual(summary["third_party_share_pct"], 0)

    def test_budget_not_triggered_on_light_page(self):
        import page_weight as pw
        budget = load_budget()
        reqs = [{"url": "https://a.ru/", "status": 200, "mime": "text/html", "type": "Document", "size": 8000, "headers": {"content-encoding": "br"}}]
        dom = {"imgs": [], "headScripts": [], "blockingCss": [], "dom_nodes": 200, "ttfb": 120, "dcl": 300, "load": 500, "fonts_count": 1}
        s, f = pw.analyse(reqs, dom, {"lcp": {"time": 1200, "tag": "h1"}, "cls": 0.0, "longtasks": []}, "a.ru", budget)
        self.assertEqual(f, [], f)


class LighthouseE2E(unittest.TestCase):
    def test_runs_if_available(self):
        import lighthouse_run as lr
        cmd = lr.find_lighthouse()
        if not cmd or cmd[0] == "npx":
            self.skipTest("Lighthouse не установлен локально")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            import tempfile
            out = os.path.join(tempfile.mkdtemp(), "lh.json")
            rep = lr.run_once(cmd, f"http://127.0.0.1:{srv.server_address[1]}/", "mobile", out,
                              "--headless=new --no-sandbox --disable-gpu", "performance")
            row, _ = lr.extract(rep)
            self.assertIsNotNone(row["score"])
            self.assertIn("LCP", row)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
