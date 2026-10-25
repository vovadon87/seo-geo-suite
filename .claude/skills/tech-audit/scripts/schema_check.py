#!/usr/bin/env python3
"""Проверка JSON-LD: обязательные/рекомендуемые поля, шаблонные значения.
Использование: python schema_check.py <url>   (или как модуль: check_items(items))
Это проверка полноты, а не гарантия расширенного сниппета."""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fetch, parse_html, jsonld_items, types_of  # noqa: E402

# тип -> (обязательные, рекомендуемые)
RULES = {
    "Organization": (["name"], ["url", "logo", "contactPoint", "sameAs"]),
    "LocalBusiness": (["name", "address"], ["telephone", "openingHours", "geo", "url", "image"]),
    "WebSite": (["name", "url"], ["potentialAction"]),
    "WebPage": ([], ["name", "description"]),
    "Product": (["name"], ["image", "description", "offers", "brand"]),
    "Offer": (["price", "priceCurrency"], ["availability", "url"]),
    "Service": (["name"], ["provider", "areaServed", "description"]),
    "FAQPage": (["mainEntity"], []),
    "Question": (["name", "acceptedAnswer"], []),
    "BreadcrumbList": (["itemListElement"], []),
    "ListItem": (["position"], ["name", "item"]),
    "Article": (["headline"], ["datePublished", "dateModified", "author", "image", "publisher"]),
    "BlogPosting": (["headline"], ["datePublished", "dateModified", "author", "image", "publisher"]),
    "NewsArticle": (["headline"], ["datePublished", "dateModified", "author", "image", "publisher"]),
    "Person": (["name"], ["url"]),
    "PostalAddress": ([], ["streetAddress", "addressLocality", "addressCountry"]),
}
PLACEHOLDER = re.compile(r"(\[[^\]]*\]|ваш[- ]домен|example\.(com|ru)|your[-_ ]?(site|domain|company)|lorem ipsum|\{\{.*?\}\}|xxx+)", re.I)
RISKY = {"AggregateRating": "рейтинг без реальных отзывов на странице", "Review": "отзыв должен быть виден на странице и быть реальным"}


def _walk_values(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _walk_values(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk_values(v, f"{path}[{i}]")
    else:
        yield path, o


def _all_dicts(o):
    """Сам объект и все вложенные словари (Offer внутри Product и т.п.)."""
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _all_dicts(v)
    elif isinstance(o, list):
        for v in o:
            yield from _all_dicts(v)


def check_items(items):
    """-> список находок {level: P1/P2/P3, msg}"""
    out = []
    flat = []
    for it in items:
        flat.extend(d for d in _all_dicts(it) if d.get("@type"))
    for obj in flat:
        for t in types_of(obj):
            if t in RISKY:
                out.append(("P1", f"{t}: {RISKY[t]} — проверить, что данные реальные и видны на странице"))
            req, rec = RULES.get(t, ([], []))
            for f in req:
                if f not in obj or obj[f] in ("", None, [], {}):
                    out.append(("P2", f"{t}: нет обязательного поля «{f}»"))
            miss = [f for f in rec if f not in obj or obj[f] in ("", None, [], {})]
            if miss:
                out.append(("P3", f"{t}: не заполнены рекомендуемые поля: {', '.join(miss)}"))
    key_risk = {"aggregaterating": "AggregateRating", "review": "Review"}
    for it in items:
        for d in _all_dicts(it):
            for k in d:
                t = key_risk.get(str(k).lower())
                if t and not any(isinstance(x, dict) and t in types_of(x) for x in _all_dicts(d.get(k))):
                    out.append(("P1", f"{t}: {RISKY[t]} — проверить, что данные реальные и видны на странице"))
    for it in items:
        for path, val in _walk_values(it):
            if isinstance(val, str) and PLACEHOLDER.search(val):
                out.append(("P2", f"шаблонное значение в «{path}»: {val[:60]}"))
    # убрать дубли
    seen, res = set(), []
    for lvl, m in out:
        if m not in seen:
            seen.add(m)
            res.append({"level": lvl, "msg": m})
    return res


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    r = fetch(sys.argv[1])
    p = parse_html(r["body"])
    items, bad = jsonld_items(p.jsonld_raw)
    print(f"JSON-LD блоков: {len(p.jsonld_raw)}, невалидных: {bad}, объектов: {len(items)}")
    for it in items:
        print(" -", ", ".join(types_of(it)) or "(без @type)")
    for f in check_items(items):
        print(f"[{f['level']}] {f['msg']}")
    print("Дополнительно проверить во внешних валидаторах: Rich Results Test, validator.schema.org, Яндекс Вебмастер.")


if __name__ == "__main__":
    main()
