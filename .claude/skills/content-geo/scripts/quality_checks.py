#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Механические проверки текста статьи (Markdown) против переспама.

Выход — сигналы для ручного решения, не вердикт (см. references/anti-spam.md).
Использование: python quality_checks.py <статья.md> [--json]
"""
import argparse
import json
import re
import sys
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TEMPLATE_PHRASES = [
    "в современном мире", "на сегодняшний день", "широкий спектр",
    "важную роль играет", "играет важную роль", "индивидуальный подход",
    "высокое качество по доступной цене", "наша компания предлагает",
    "стоит отметить", "не секрет, что", "давайте разберемся",
    "давайте разберёмся", "ключевым аспектом", "подводя итог",
    "в данной статье", "как известно", "ни для кого не секрет",
]

STOPWORDS = set("""и в во не на но а то что это как его ее её их для от до из у о об при с со по за над под же бы ли или чтобы если когда где тоже также можно нужно есть был была были быть этот эта эти тот та те вы мы он она они оно вам нам ваш наш который которая которые чем том все всё ещё еще уже только очень может мог""".split())


def stem(token):
    """Грубая нормализация: срезать типовые окончания (для сигналов, не морфология)."""
    t = token.lower().replace("ё", "е")
    for suf in ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
                "ая", "яя", "ой", "ей", "ом", "ем", "ах", "ях", "ов", "ев",
                "ы", "и", "а", "я", "о", "е", "у", "ю", "ь"):
        if len(t) - len(suf) >= 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokenize(text):
    return [w for w in re.findall(r"[а-яёa-z]+", text.lower().replace("ё", "е")) if len(w) >= 3 and w not in STOPWORDS]


def analyze(text):
    report = {}
    body = re.sub(r"```.*?```", " ", text, flags=re.S)
    headings = re.findall(r"^#{1,4}\s+(.+)$", body, flags=re.M)
    prose = re.sub(r"^#{1,4}\s+.+$", " ", body, flags=re.M)
    prose = re.sub(r"\|[^\n]*\|", " ", prose)  # таблицы не считаем в повторы
    tokens = tokenize(prose)
    stems = [stem(t) for t in tokens]
    total = max(len(stems), 1)

    # 1. Доминирующие леммы
    top = Counter(stems).most_common(10)
    report["top_lemmas"] = [
        {"lemma": l, "count": c, "share_pct": round(100.0 * c / total, 1)} for l, c in top
    ]
    report["flag_lemma_overuse"] = [x for x in report["top_lemmas"] if x["share_pct"] >= 3.5 and x["count"] >= 8]

    # 2. Повторяющиеся биграммы/триграммы
    ngrams = Counter()
    for n in (2, 3):
        for i in range(len(stems) - n + 1):
            ngrams[" ".join(stems[i : i + n])] += 1
    report["repeated_ngrams"] = [
        {"ngram": g, "count": c} for g, c in ngrams.most_common(30) if c >= 4
    ][:15]

    # 3. Заголовки: повтор одного слова в нескольких H2/H3
    htokens = Counter()
    for h in headings:
        for t in set(stem(x) for x in tokenize(h)):
            htokens[t] += 1
    report["heading_repeats"] = [
        {"lemma": l, "headings": c} for l, c in htokens.most_common() if c >= 3
    ]

    # 4. Шаблонные обороты
    low = body.lower().replace("ё", "е")
    report["template_phrases"] = [p for p in TEMPLATE_PHRASES if p.replace("ё", "е") in low]

    # 5. Похожие абзацы (Жаккар по множеству лемм >= 0.6)
    paras = [p.strip() for p in re.split(r"\n\s*\n", prose) if len(p.strip()) >= 120]
    sets = [set(stem(t) for t in tokenize(p)) for p in paras]
    similar = []
    for i in range(len(paras)):
        for j in range(i + 1, len(paras)):
            if not sets[i] or not sets[j]:
                continue
            jac = len(sets[i] & sets[j]) / len(sets[i] | sets[j])
            if jac >= 0.6:
                similar.append({"para_a": paras[i][:70], "para_b": paras[j][:70], "jaccard": round(jac, 2)})
    report["similar_paragraphs"] = similar

    # 6. Географические перечисления (3+ РАЗНЫХ города в одном предложении);
    # варианты одного города ("санкт-петербург"/"петербург"/"спб") считаются одним
    city_groups = {
        "москва": ["москв"], "спб": ["санкт-петербург", "петербург", "спб"],
        "казань": ["казан"], "новосибирск": ["новосибирск"], "екатеринбург": ["екатеринбург"],
        "нижний": ["нижн"], "самара": ["самар"], "краснодар": ["краснодар"],
        "ростов": ["ростов"], "уфа": ["уфе", "уфа", "уфу"], "пермь": ["перм"], "воронеж": ["воронеж"],
    }
    geo_flags = []
    for sent in re.split(r"[.!?]\s", prose):
        s = sent.lower()
        hits = {city for city, pats in city_groups.items() if any(p in s for p in pats)}
        if len(hits) >= 3:
            geo_flags.append(sent.strip()[:100])
    report["geo_enumerations"] = geo_flags

    report["words_total"] = total
    report["headings_total"] = len(headings)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    with open(args.path, encoding="utf-8-sig") as f:
        text = f.read()
    report = analyze(text)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    print(f"Слов (без заголовков/таблиц): {report['words_total']}; заголовков: {report['headings_total']}")
    def section(title, items, fmt):
        print(f"\n== {title}: {len(items)}")
        for it in items:
            print("  -", fmt(it))
    section("Переупотребление лемм (>=3.5%)", report["flag_lemma_overuse"],
            lambda x: f"{x['lemma']} x{x['count']} ({x['share_pct']}%)")
    section("Повторяющиеся n-граммы (>=4)", report["repeated_ngrams"],
            lambda x: f"{x['ngram']} x{x['count']}")
    section("Слово в 3+ заголовках", report["heading_repeats"],
            lambda x: f"{x['lemma']} в {x['headings']} заголовках")
    section("Шаблонные обороты", report["template_phrases"], lambda x: x)
    section("Похожие абзацы (Жаккар>=0.6)", report["similar_paragraphs"],
            lambda x: f"{x['jaccard']}: «{x['para_a']}…» ~ «{x['para_b']}…»")
    section("Гео-перечисления", report["geo_enumerations"], lambda x: x)
    print("\nИтог: это сигналы для редактора, не вердикт (anti-spam.md).")


if __name__ == "__main__":
    main()
