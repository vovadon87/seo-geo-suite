#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты semantics: чтение выгрузок (wordstat_io) и кластеризация (cluster_phrases).
Тестовая ниша намеренно произвольная (ремонт квартир), чтобы проверить независимость от ниши.
Запуск: python run_tests.py"""
import csv
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cluster_phrases as cp  # noqa: E402
import wordstat_io as w  # noqa: E402

ROWS = [
    ("Фраза", "Частотность"),
    ("ремонт квартир цена", "1200"),
    ("стоимость ремонта квартир", "800"),
    ("ремонт квартир под ключ цена", "500"),
    ("заказать ремонт квартиры", "300"),
    ("как сделать ремонт в квартире", "2500"),
    ("как сделать ремонт квартиры самостоятельно", "400"),
    ("ремонт квартир скачать бесплатно", "90"),
    ("ремонт квартир отзывы", "150"),
    ("лучшая компания по ремонту квартир", "60"),
    ("Ремонт  квартир цена!", "нет данных"),   # дубль по норме
    ("ремонт офисов цена", ""),
    ("ремонтпро официальный сайт", "40"),
]


def write_csv(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig", newline="")
    csv.writer(f, delimiter=";").writerows(rows)
    f.close()
    return f.name


class ClusterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = write_csv(ROWS)
        cls.out = tempfile.mkdtemp()
        cls.clusters, cls.arr, cls.errors, cls.paths, cls.maxscore = cp.run(
            cls.src, cls.out, brands=["ремонтпро"], relevant=["ремонт"], minus=["самостоятельно"])
        cls.by_phrase = {p["norm"]: p for p in cls.arr}

    def test_dedupe_keeps_known_frequency(self):
        p = self.by_phrase["ремонт квартир цена"]
        self.assertEqual(p["freq"], 1200)  # «нет данных» не затирает число
        self.assertEqual(len(self.arr), len(ROWS) - 2)

    def test_missing_frequency_stays_missing(self):
        self.assertIsNone(self.by_phrase["ремонт офисов цена"]["freq"])

    def test_intents(self):
        self.assertEqual(self.by_phrase["ремонт квартир цена"]["intent"], "transactional")
        self.assertEqual(self.by_phrase["как сделать ремонт в квартире"]["intent"], "informational")
        self.assertEqual(self.by_phrase["ремонтпро официальный сайт"]["intent"], "navigational")
        self.assertEqual(self.by_phrase["ремонт квартир отзывы"]["intent"], "commercial_investigation")

    def test_commercial_and_info_not_merged(self):
        a = self.by_phrase["ремонт квартир цена"]["cluster"]
        b = self.by_phrase["как сделать ремонт в квартире"]["cluster"]
        self.assertNotEqual(a, b)

    def test_price_synonyms_and_object_difference(self):
        a = self.by_phrase["ремонт квартир цена"]["cluster"]
        self.assertEqual(self.by_phrase["стоимость ремонта квартир"]["cluster"], a)  # цена = стоимость
        self.assertNotEqual(self.by_phrase["ремонт офисов цена"]["cluster"], a)      # другой объект — другой кластер

    def test_price_phrases_clustered(self):
        a = self.by_phrase["ремонт квартир цена"]["cluster"]
        b = self.by_phrase["ремонт квартир под ключ цена"]["cluster"]
        self.assertEqual(a, b)

    def test_noise_flagged_not_deleted(self):
        self.assertIn("типовой", self.by_phrase["ремонт квартир скачать бесплатно"]["noise"])
        self.assertIn("минус", self.by_phrase["как сделать ремонт квартиры самостоятельно"]["noise"])
        self.assertIn("ремонт квартир скачать бесплатно", self.by_phrase)

    def test_outputs_written(self):
        for p in self.paths:
            self.assertTrue(os.path.exists(p))
        with open(self.paths[0], encoding="utf-8-sig") as f:
            head = f.readline()
        self.assertIn("заполнить", head)  # факторы, которые решает человек, не подставляются автоматически

    def test_auto_score_uses_only_computable_factors(self):
        self.assertEqual(self.maxscore, 65)
        top = max(self.clusters, key=lambda c: c["auto_score"])
        self.assertLessEqual(top["auto_score"], 65)

    def test_source_not_modified(self):
        before = open(self.src, "rb").read()
        cp.run(self.src, tempfile.mkdtemp())
        self.assertEqual(before, open(self.src, "rb").read())


class WordstatIoTest(unittest.TestCase):
    def test_parse_frequency(self):
        self.assertEqual(w.parse_frequency("1 200"), (True, 1200))
        self.assertEqual(w.parse_frequency("нет данных"), (True, None))
        self.assertFalse(w.parse_frequency("около 200")[0])
        self.assertFalse(w.parse_frequency(-5)[0])

    def test_validate_and_dedupe(self):
        p = write_csv(ROWS)
        errs, data, idx = w.validate(p, verbose=False)
        self.assertEqual(errs, [])
        self.assertEqual(len(w.find_duplicates(p, verbose=False)), 1)

    def test_export_refuses_overwrite(self):
        p = write_csv(ROWS)
        out = os.path.join(tempfile.mkdtemp(), "o.csv")
        w.export(p, out)
        with self.assertRaises(SystemExit):
            w.export(p, out)
        with self.assertRaises(SystemExit):
            w.export(p, p, force=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
