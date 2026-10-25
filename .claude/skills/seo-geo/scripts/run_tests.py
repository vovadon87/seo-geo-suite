#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты merge_findings.py. Запуск: python run_tests.py"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import merge_findings as mf  # noqa: E402


def make():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "tech"))
    os.makedirs(os.path.join(d, "local"))
    os.makedirs(os.path.join(d, "content"))
    with open(os.path.join(d, "tech", "findings.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "priority", "problem", "count", "examples", "evidence", "fix", "recheck"])
        w.writeheader()
        w.writerow({"id": "M1", "priority": "P1", "problem": "Нет title", "count": 2, "examples": "https://s.ru/a | https://s.ru/b",
                    "evidence": "пусто", "fix": "задать", "recheck": "обход"})
        w.writerow({"id": "R1", "priority": "P0", "problem": "Сайт закрыт", "count": 1, "examples": "https://s.ru/", "evidence": "Disallow: /",
                    "fix": "открыть", "recheck": "robots"})
    json.dump({"findings": [{"priority": "P1", "id": "M1", "problem": "Нет title", "url": "https://s.ru/c", "evidence": "x"}]},
              open(os.path.join(d, "content", "findings.json"), "w", encoding="utf-8"))
    json.dump({"findings": [{"priority": "P2", "id": "N3", "problem": "Разные телефоны", "evidence": "2 номера", "ask_owner": "Какой основной?"}]},
              open(os.path.join(d, "local", "nap.json"), "w", encoding="utf-8"))
    json.dump({"findings": [{"code": "J1", "priority": "P1", "url": "https://s.ru/spa", "title": "Текст только после JS",
                             "evidence": "5 → 300 слов", "fix": "SSR", "confidence": "высокая"}]},
              open(os.path.join(d, "tech", "render-check.json"), "w", encoding="utf-8"))
    return d


class Merge(unittest.TestCase):
    def setUp(self):
        self.items, self.sources = mf.read(make())
        self.res = mf.merge(self.items)

    def test_reads_all_formats(self):
        self.assertEqual(len(self.sources), 4)
        self.assertEqual(len(self.items), 5)

    def test_dedup_merges_urls_and_modules(self):
        m1 = [f for f in self.res if f["id"] == "M1"]
        self.assertEqual(len(m1), 1)
        self.assertEqual(len(m1[0]["urls"]), 3)
        self.assertEqual(sorted(m1[0]["modules"]), ["content", "tech"])

    def test_sorted_by_priority(self):
        self.assertEqual(self.res[0]["priority"], "P0")
        self.assertEqual([f["priority"] for f in self.res], sorted(f["priority"] for f in self.res))

    def test_module_from_folder(self):
        n3 = [f for f in self.res if f["id"] == "N3"][0]
        self.assertEqual(n3["modules"], ["local"])

    def test_render_code_and_title_aliases(self):
        j1 = [f for f in self.res if f["id"] == "J1"][0]
        self.assertEqual(j1["problem"], "Текст только после JS")
        self.assertEqual(j1["urls"], ["https://s.ru/spa"])

    def test_cli_outputs(self):
        d = make()
        r = subprocess.run([sys.executable, os.path.join(HERE, "merge_findings.py"), d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        md = open(os.path.join(d, "merged-findings.md"), encoding="utf-8").read()
        self.assertIn("Вопросы владельцу", md)
        self.assertIn("Какой основной?", md)
        self.assertTrue(os.path.exists(os.path.join(d, "merged-findings.csv")))

    def test_empty_dir(self):
        d = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, os.path.join(HERE, "merge_findings.py"), d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Источники не найдены", open(os.path.join(d, "merged-findings.md"), encoding="utf-8").read())

    def test_broken_json_skipped(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "findings.json"), "w").write("{не json")
        items, sources = mf.read(d)
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
