#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты seo-report (period_compare) на синтетических выгрузках в форматах Вебмастера и GSC.
Запуск: python run_tests.py"""
import csv
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import period_compare as pc  # noqa: E402


def write(rows, delim=";"):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig", newline="")
    csv.writer(f, delimiter=delim).writerows(rows)
    f.close()
    return f.name


# Формат «Яндекс Вебмастер»: русские заголовки, числа с пробелами и запятой
A_RU = write([
    ["Запрос", "Показы", "Клики", "CTR", "Средняя позиция"],
    ["ремонт квартир", "1 000", "50", "5%", "6,0"],
    ["ремонт квартир цена", "400", "20", "5%", "8,0"],
    ["фирма ремонт", "200", "40", "20%", "1,5"],
    ["потерянный запрос", "300", "10", "3,3%", "12,0"],
    ["ремонт офисов", "100", "0", "0%", "20,0"],
])
B_RU = write([
    ["Запрос", "Показы", "Клики", "CTR", "Средняя позиция"],
    ["ремонт квартир", "1 500", "90", "6%", "3,0"],
    ["ремонт квартир цена", "400", "10", "2,5%", "12,0"],
    ["фирма ремонт", "220", "45", "20%", "1,4"],
    ["новый запрос", "250", "15", "6%", "9,0"],
    ["ремонт офисов", "100", "0", "0%", "19,0"],
])
# Формат GSC: английские заголовки, запятая-разделитель, точка в десятичных
A_EN = write([["Top queries", "Clicks", "Impressions", "CTR", "Position"], ["a", "10", "100", "10%", "5.0"], ["b", "5", "50", "10%", "7.0"]], ",")
B_EN = write([["Top queries", "Clicks", "Impressions", "CTR", "Position"], ["a", "30", "200", "15%", "3.0"], ["c", "2", "40", "5%", "9.0"]], ",")


class Compare(unittest.TestCase):
    def setUp(self):
        self.A, self.B = pc.load(A_RU), pc.load(B_RU)
        self.res = pc.compare(self.A, self.B, 28, 28, ["фирма"], min_impr=20)

    def test_number_parsing(self):
        self.assertEqual(pc.num("1 000"), 1000.0)
        self.assertEqual(pc.num("6,5"), 6.5)
        self.assertEqual(pc.num("12,5%"), 12.5)
        self.assertIsNone(pc.num("—"))
        self.assertIsNone(pc.num(""))

    def test_totals(self):
        t = pc.totals(self.A)
        self.assertEqual(t["impr"], 2000)
        self.assertEqual(t["clicks"], 120)

    def test_new_and_lost(self):
        self.assertEqual([d["key"] for d in self.res["new"]], ["новый запрос"])
        self.assertEqual([d["key"] for d in self.res["lost"]], ["потерянный запрос"])

    def test_gainers_losers(self):
        self.assertEqual(self.res["gainers"][0]["key"], "ремонт квартир")
        self.assertEqual({d["key"] for d in self.res["losers"][:2]}, {"потерянный запрос", "ремонт квартир цена"})

    def test_brand_split(self):
        segs = {r["segment"]: r for r in self.res["rows"]}
        self.assertEqual(set(segs), {"Все", "Брендовые", "Небрендовые"})
        self.assertEqual(segs["Брендовые"]["A"]["clicks"], 40)
        self.assertEqual(segs["Брендовые"]["B"]["clicks"], 45)

    def test_position_movements(self):
        better = [d["key"] for d in self.res["pos_better"]]
        worse = [d["key"] for d in self.res["pos_worse"]]
        self.assertIn("ремонт квартир", better)       # 6 → 3
        self.assertIn("ремонт квартир цена", worse)    # 8 → 12

    def test_impressions_without_clicks(self):
        self.assertEqual([d["key"] for d in self.res["no_clicks"]], ["ремонт офисов"])

    def test_missing_days_warns(self):
        r = pc.compare(self.A, self.B, None, None)
        self.assertTrue(any("не указана" in w for w in r["warnings"]))

    def test_unequal_periods_normalised_per_day(self):
        r = pc.compare(self.A, self.B, 14, 28)
        row = r["rows"][0]
        # клики/день: А 120/14 = 8.57, Б 160/28 = 5.71 → падение, хотя абсолютные клики выросли
        self.assertLess(row["clicks_pct"], 0)
        self.assertTrue(any("разной длины" in w for w in r["warnings"]))

    def test_gsc_english_headers(self):
        A, B = pc.load(A_EN), pc.load(B_EN)
        r = pc.compare(A, B, 28, 28)
        self.assertEqual(r["rows"][0]["A"]["clicks"], 15)
        self.assertEqual(r["rows"][0]["B"]["impr"], 240)
        self.assertEqual([d["key"] for d in r["new"]], ["c"])

    def test_report_text(self):
        md = pc.report(self.res, 28, 28, "a.csv", "b.csv")
        self.assertIn("Как читать", md)
        self.assertIn("не доказательство причины", md)

    def test_cli_runs_and_does_not_modify_sources(self):
        import subprocess
        before = open(A_RU, "rb").read()
        out = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, os.path.join(HERE, "period_compare.py"), A_RU, B_RU, "--days-a", "28", "--days-b", "28",
                            "--brand", "фирма", "--out", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(out, "period-report.md")))
        self.assertEqual(before, open(A_RU, "rb").read())

    def test_missing_columns_error(self):
        bad = write([["x", "y"], ["1", "2"]])
        with self.assertRaises(SystemExit):
            pc.load(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
