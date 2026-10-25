#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты content-geo: анти-спам сигналы и конвертер Markdown → HTML.
Тексты произвольной тематики (проверка независимости от ниши). Запуск: python run_tests.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import md_to_html as h  # noqa: E402
import quality_checks as q  # noqa: E402

SPAM = ("## Ремонт квартир\n\nВ современном мире ремонт квартир это ремонт квартир недорого. "
        "Наша компания предлагает ремонт квартир под ключ. Ремонт квартир в Москве, Санкт-Петербурге, Казани, Новосибирске.\n\n"
        "## Ремонт квартир цена\n\n## Ремонт квартир срочно\n")
CLEAN = ("## Какую стяжку выбрать для тёплого пола\n\nЕсли под стяжкой проходит водяной контур, "
         "важна теплопроводность и отсутствие усадочных трещин. Цементно-песчаная смесь подойдёт для большинства квартир.\n\n"
         "## Как подготовить основание\n\nУдалите пыль и масляные пятна, прогрунтуйте поверхность в один слой "
         "и дождитесь полного высыхания грунтовки перед заливкой.\n")


class AntiSpam(unittest.TestCase):
    def test_template_phrases(self):
        self.assertIn("в современном мире", q.analyze(SPAM)["template_phrases"])

    def test_repeated_ngrams(self):
        self.assertTrue(any(x["count"] >= 4 for x in q.analyze(SPAM)["repeated_ngrams"]))

    def test_heading_repeat(self):
        self.assertTrue(any(x["headings"] >= 3 for x in q.analyze(SPAM)["heading_repeats"]))

    def test_geo_enumeration(self):
        self.assertGreaterEqual(len(q.analyze(SPAM)["geo_enumerations"]), 1)

    def test_clean_text_no_false_positives(self):
        r = q.analyze(CLEAN)
        self.assertFalse(r["template_phrases"] or r["flag_lemma_overuse"] or r["geo_enumerations"])

    def test_similar_paragraphs(self):
        a = "Цементная стяжка выравнивает основание и подходит для большинства помещений без повышенной нагрузки, если слой грунтовки высох."
        b = a.replace("большинства", "многих")
        text = "## А\n\n" + a + "\n\n" + b + "\n"
        self.assertGreaterEqual(len(q.analyze(text)["similar_paragraphs"]), 1)


class MdToHtml(unittest.TestCase):
    SAMPLE = ("<!-- служебный комментарий -->\n# Заголовок\n\nАбзац с **жирным** и [ссылкой](https://example.com/a) & знаком <.\n\n"
              "## Раздел\n\n- пункт один\n- пункт два\n\n1. первый\n2. второй\n\n"
              "| А | Б |\n|---|---|\n| 1 | **2** |\n")

    def setUp(self):
        self.out = h.convert(self.SAMPLE)

    def test_comments_dropped(self):
        self.assertNotIn("служебный", self.out)

    def test_headings(self):
        self.assertIn("<h1>Заголовок</h1>", self.out)
        self.assertIn("<h2>Раздел</h2>", self.out)

    def test_inline(self):
        self.assertIn("<strong>жирным</strong>", self.out)
        self.assertIn('<a href="https://example.com/a">ссылкой</a>', self.out)

    def test_escape(self):
        self.assertIn("&amp;", self.out)
        self.assertIn("&lt;", self.out)

    def test_lists_and_table(self):
        for frag in ("<ul>", "<li>пункт два</li>", "<ol>", "<li>второй</li>", "<table>", "<th>А</th>", "<td><strong>2</strong></td>"):
            self.assertIn(frag, self.out)

    def test_no_inline_styles(self):
        self.assertNotIn("style=", self.out)

    def test_javascript_link_blocked(self):
        self.assertNotIn('<a href="javascript', h.convert("[x](javascript:alert(1))"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
