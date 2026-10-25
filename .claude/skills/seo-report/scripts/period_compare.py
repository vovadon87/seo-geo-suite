#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сравнение двух периодов по выгрузкам Яндекс Вебмастера или Google Search Console (запросы или страницы).

Использование:
  python period_compare.py <период_А.csv|xlsx> <период_Б.csv|xlsx> --days-a 28 --days-b 28
         [--brand "бренд1,бренд2"] [--key-col N] [--min-impressions 20] [--out dir]
А — «до», Б — «после». Файлы только читаются. Не смешивайте Яндекс и Google в одном сравнении:
у них разные определения показов и позиций. Причинность скрипт не устанавливает: он показывает,
что изменилось, а не почему."""
import argparse
import csv
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

KEY_NAMES = ["запрос", "query", "queries", "страница", "page", "url", "top queries", "top pages"]
IMPR_NAMES = ["показы", "impressions"]
CLICK_NAMES = ["клики", "clicks"]
POS_NAMES = ["позиция", "position"]
CTR_NAMES = ["ctr"]


def read_rows(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit("Для XLSX нужен openpyxl: pip install openpyxl (или сохраните выгрузку в CSV)")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        first = sample.splitlines()[0] if sample else ""
        delim = next((d for d in (";", "\t", ",") if d in first), ";")
        return [r for r in csv.reader(f, delimiter=delim)]


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "").replace("%", "")
    if s in ("", "-", "—", "нетданных", "n/a"):
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def find_col(header, names, exclude=()):
    for i, h in enumerate(header):
        low = str(h or "").lower()
        if any(n in low for n in names) and not any(e in low for e in exclude):
            return i
    return None


def load(path, key_col=None):
    rows = read_rows(path)
    if not rows:
        raise SystemExit(f"{path}: файл пуст")
    header, data = rows[0], rows[1:]
    k = key_col if key_col is not None else find_col(header, KEY_NAMES)
    im = find_col(header, IMPR_NAMES)
    cl = find_col(header, CLICK_NAMES)
    ps = find_col(header, POS_NAMES)
    if k is None or im is None or cl is None:
        raise SystemExit(f"{path}: не найдены колонки ключа/показов/кликов (заголовок: {header})")
    agg = {}
    for r in data:
        if k >= len(r) or not str(r[k] or "").strip():
            continue
        key = str(r[k]).strip().lower()
        impr = num(r[im]) if im < len(r) else None
        clicks = num(r[cl]) if cl < len(r) else None
        pos = num(r[ps]) if ps is not None and ps < len(r) else None
        a = agg.setdefault(key, {"impr": 0.0, "clicks": 0.0, "pos_w": 0.0, "pos_n": 0.0})
        a["impr"] += impr or 0
        a["clicks"] += clicks or 0
        if pos is not None and impr:
            a["pos_w"] += pos * impr
            a["pos_n"] += impr
    for a in agg.values():
        a["pos"] = a["pos_w"] / a["pos_n"] if a["pos_n"] else None
    return agg


def totals(agg):
    impr = sum(a["impr"] for a in agg.values())
    clicks = sum(a["clicks"] for a in agg.values())
    pw = sum(a["pos_w"] for a in agg.values())
    pn = sum(a["pos_n"] for a in agg.values())
    return {"impr": impr, "clicks": clicks, "ctr": (clicks / impr * 100) if impr else None, "pos": (pw / pn) if pn else None,
            "keys": len(agg)}


def split_brand(agg, brands):
    br, nb = {}, {}
    for k, v in agg.items():
        (br if brands and any(b and b in k for b in brands) else nb)[k] = v
    return br, nb


def pct(a, b):
    if a in (None, 0) or b is None:
        return None
    return (b - a) / a * 100


def f(v, nd=0, suffix=""):
    return "—" if v is None else f"{v:,.{nd}f}".replace(",", " ") + suffix


def compare(A, B, days_a=None, days_b=None, brands=None, min_impr=20, top=15):
    out = {"warnings": [], "rows": []}
    if not days_a or not days_b:
        out["warnings"].append("Длина периодов не указана (--days-a/--days-b): сравнение абсолютных значений может быть некорректным.")
    elif days_a != days_b:
        out["warnings"].append(f"Периоды разной длины ({days_a} и {days_b} дн.): сравниваются значения в день.")
    ka = (days_a or 1) if days_a and days_b and days_a != days_b else 1
    kb = (days_b or 1) if days_a and days_b and days_a != days_b else 1
    out["norm"] = (ka, kb)
    for label, a, b in (("Все", A, B),) + tuple(zip(("Брендовые", "Небрендовые"), split_brand(A, brands), split_brand(B, brands))
                                                  ) if brands else (("Все", A, B),):
        ta, tb = totals(a), totals(b)
        out["rows"].append({"segment": label, "A": ta, "B": tb,
                            "impr_pct": pct(ta["impr"] / ka, tb["impr"] / kb), "clicks_pct": pct(ta["clicks"] / ka, tb["clicks"] / kb)})
    keys = set(A) | set(B)
    diffs = []
    for key in keys:
        a, b = A.get(key), B.get(key)
        ca, cb = (a["clicks"] / ka if a else 0), (b["clicks"] / kb if b else 0)
        ia, ib = (a["impr"] / ka if a else 0), (b["impr"] / kb if b else 0)
        diffs.append({"key": key, "clicks_a": ca, "clicks_b": cb, "d_clicks": cb - ca, "impr_a": ia, "impr_b": ib,
                      "d_impr": ib - ia, "pos_a": a["pos"] if a else None, "pos_b": b["pos"] if b else None,
                      "status": "новый" if not a else ("пропал" if not b else "")})
    out["diffs"] = diffs
    out["gainers"] = sorted([d for d in diffs if d["d_clicks"] > 0], key=lambda d: -d["d_clicks"])[:top]
    out["losers"] = sorted([d for d in diffs if d["d_clicks"] < 0], key=lambda d: d["d_clicks"])[:top]
    out["new"] = sorted([d for d in diffs if d["status"] == "новый"], key=lambda d: -d["impr_b"])[:top]
    out["lost"] = sorted([d for d in diffs if d["status"] == "пропал"], key=lambda d: -d["impr_a"])[:top]
    out["no_clicks"] = sorted([d for d in diffs if d["impr_b"] * kb >= min_impr and d["clicks_b"] == 0], key=lambda d: -d["impr_b"])[:top]
    moved = [d for d in diffs if d["pos_a"] and d["pos_b"] and d["impr_b"] * kb >= min_impr]
    out["pos_better"] = sorted([d for d in moved if d["pos_b"] - d["pos_a"] <= -3], key=lambda d: d["pos_b"] - d["pos_a"])[:top]
    out["pos_worse"] = sorted([d for d in moved if d["pos_b"] - d["pos_a"] >= 3], key=lambda d: -(d["pos_b"] - d["pos_a"]))[:top]
    return out


def report(res, days_a, days_b, source_a, source_b):
    L = ["# Сравнение периодов", "", f"- Период А (до): `{os.path.basename(source_a)}`, {days_a or '?'} дн.",
         f"- Период Б (после): `{os.path.basename(source_b)}`, {days_b or '?'} дн.", ""]
    for w in res["warnings"]:
        L.append(f"> ВНИМАНИЕ: {w}")
    L += ["", "## Итоги", "| Сегмент | Показы А → Б | Δ показов | Клики А → Б | Δ кликов | CTR А → Б, % | Позиция А → Б |", "|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        a, b = r["A"], r["B"]
        L.append(f"| {r['segment']} | {f(a['impr'])} → {f(b['impr'])} | {f(r['impr_pct'], 1, '%')} | {f(a['clicks'])} → {f(b['clicks'])} | "
                 f"{f(r['clicks_pct'], 1, '%')} | {f(a['ctr'], 2)} → {f(b['ctr'], 2)} | {f(a['pos'], 1)} → {f(b['pos'], 1)} |")
    L.append("")
    L.append("Дельты в процентах считаются по значениям в день, если периоды разной длины. Позиция: меньше — лучше.")

    def table(title, rows, cols):
        L.extend(["", f"## {title}"])
        if not rows:
            L.append("— нет —")
            return
        L.append("| " + " | ".join(c[0] for c in cols) + " |")
        L.append("|" + "---|" * len(cols))
        for d in rows:
            L.append("| " + " | ".join(c[1](d) for c in cols) + " |")
    key = ("Ключ", lambda d: d["key"][:80])
    table("Рост кликов (топ)", res["gainers"], [key, ("Клики А", lambda d: f(d["clicks_a"], 1)), ("Клики Б", lambda d: f(d["clicks_b"], 1)),
                                                  ("Δ", lambda d: f(d["d_clicks"], 1)), ("Статус", lambda d: d["status"])])
    table("Падение кликов (топ)", res["losers"], [key, ("Клики А", lambda d: f(d["clicks_a"], 1)), ("Клики Б", lambda d: f(d["clicks_b"], 1)),
                                                    ("Δ", lambda d: f(d["d_clicks"], 1)), ("Статус", lambda d: d["status"])])
    table("Новые в периоде Б (по показам)", res["new"], [key, ("Показы Б", lambda d: f(d["impr_b"], 1)), ("Клики Б", lambda d: f(d["clicks_b"], 1))])
    table("Пропали в периоде Б (по показам А)", res["lost"], [key, ("Показы А", lambda d: f(d["impr_a"], 1)), ("Клики А", lambda d: f(d["clicks_a"], 1))])
    table("Показы без кликов в Б (кандидаты на доработку сниппета/страницы)", res["no_clicks"], [key, ("Показы Б", lambda d: f(d["impr_b"], 1)), ("Позиция Б", lambda d: f(d["pos_b"], 1))])
    table("Позиция улучшилась на 3+", res["pos_better"], [key, ("А", lambda d: f(d["pos_a"], 1)), ("Б", lambda d: f(d["pos_b"], 1)), ("Показы Б", lambda d: f(d["impr_b"], 1))])
    table("Позиция ухудшилась на 3+", res["pos_worse"], [key, ("А", lambda d: f(d["pos_a"], 1)), ("Б", lambda d: f(d["pos_b"], 1)), ("Показы Б", lambda d: f(d["impr_b"], 1))])
    L += ["", "## Как читать", "- Это изменения в данных, а не доказательство причины: учтите сезонность, рекламу, изменения сайта и алгоритмов.",
          "- Показы и позиции в Вебмастере и в GSC считаются по-разному: не сравнивать их между собой.",
          "- Падение «в ноль» у отдельных ключей может быть сдвигом порога отчёта, а не потерей позиции: проверить выдачу.",
          "- Выводы о связи изменений сайта и метрик делать осторожно; без Метрики и CRM нельзя говорить о заявках."]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--days-a", type=int)
    ap.add_argument("--days-b", type=int)
    ap.add_argument("--brand", default="")
    ap.add_argument("--key-col", type=int)
    ap.add_argument("--min-impressions", type=int, default=20)
    ap.add_argument("--out", default="audit-out")
    args = ap.parse_args()
    A, B = load(args.a, args.key_col), load(args.b, args.key_col)
    brands = [x.strip().lower() for x in args.brand.split(",") if x.strip()]
    res = compare(A, B, args.days_a, args.days_b, brands, args.min_impressions)
    md = report(res, args.days_a, args.days_b, args.a, args.b)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "period-report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    with io.open(os.path.join(args.out, "period-diff.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Ключ", "Клики А", "Клики Б", "Δ кликов", "Показы А", "Показы Б", "Δ показов", "Позиция А", "Позиция Б", "Статус"])
        for d in sorted(res["diffs"], key=lambda d: d["d_clicks"]):
            w.writerow([d["key"], round(d["clicks_a"], 2), round(d["clicks_b"], 2), round(d["d_clicks"], 2), round(d["impr_a"], 2),
                        round(d["impr_b"], 2), round(d["d_impr"], 2), "" if d["pos_a"] is None else round(d["pos_a"], 1),
                        "" if d["pos_b"] is None else round(d["pos_b"], 1), d["status"]])
    print(md)
    print(f"Сохранено: {args.out}/period-report.md, {args.out}/period-diff.csv")


if __name__ == "__main__":
    main()
