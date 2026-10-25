#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Черновая кластеризация поисковых фраз для любой ниши (русский язык).

Это ЧЕРНОВИК для проверки человеком: кластеры строятся по общим словам, а намерение (интент)
определяется по формулировке фразы. Настоящий интент устанавливает только проверка выдачи
(references/serp-intent.md). Частотность не выдумывается: нет значения — остаётся пустым.

Использование:
  python cluster_phrases.py <выгрузка.csv|xlsx> --out semantic-out
        [--freq-col N] [--minus minus.txt] [--brand "бренд1,бренд2"]
        [--relevant "слово1,слово2"] [--threshold 0.6] [--weights weights.json]

Результат: <out>/clusters.csv, <out>/phrases.csv и сводка в stdout. Исходный файл не изменяется."""
import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wordstat_io as w  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STOP = set("""и в во не на но а то что это как для от до из у о об при с со по за над под же бы ли или чтобы если
когда где тоже также можно нужно есть быть этот эта эти тот та те вы мы он она они оно вам нам ваш наш который
чем том все всё еще ещё уже только очень без про при через между""".split())

TX = ["купить", "заказать", "заказ", "цена", "цены", "стоимость", "прайс", "недорого", "дешево", "дёшево", "оптом",
      "расчет", "расчёт", "рассчитать", "под ключ", "услуга", "услуги", "изготовление", "доставка", "в наличии"]
COMPARE = ["лучший", "лучшие", "рейтинг", "сравнение", "сравнить", "отзывы", "отзыв", "что лучше", "или", "альтернатива", "топ"]
INFO = ["как", "что такое", "почему", "зачем", "чем отличается", "инструкция", "своими руками", "виды", "способы",
        "пример", "примеры", "образец", "шаблон", "можно ли", "сколько", "какой", "какие", "какая", "для чего", "правила", "требования"]
NAV = ["официальный сайт", "контакты", "адрес", "телефон", "личный кабинет", "вход", "отзывы о компании"]
GENERIC_NOISE = ["скачать", "торрент", "реферат", "курсовая", "вакансия", "вакансии", "бесплатно скачать"]


SYN = {"стоимост": "цен", "прайс": "цен", "расценк": "цен", "тариф": "цен"}  # синонимы «цены» → одна основа
PRICE_FORMS = {"цена", "цены", "цену", "цене", "ценой", "цен", "ценам", "ценах"}  # короткие слова не режутся стеммером


def stem(tok):
    t = tok.lower().replace("ё", "е")
    if not re.search(r"[а-я]", t):
        return t[:-1] if t.endswith("s") and len(t) > 3 else t
    if t in PRICE_FORMS:
        return "цен"
    return SYN.get(_stem_ru(t), _stem_ru(t))


def _stem_ru(t):
    for suf in ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ая", "яя", "ой", "ей", "ом", "ем",
                "ах", "ях", "ов", "ев", "ий", "ый", "ые", "ие", "ы", "и", "а", "я", "о", "е", "у", "ю", "ь"):
        if len(t) - len(suf) >= 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokens(phrase):
    return [t for t in re.findall(r"[a-zа-яё0-9]+", phrase.lower().replace("ё", "е")) if t not in STOP]


def has_marker(norm, markers):
    toks = set(stem(t) for t in re.findall(r"[a-zа-яё0-9]+", norm))
    for m in markers:
        ms = m.replace("ё", "е")
        if " " in ms:
            if ms in norm:
                return True
        elif stem(ms) in toks or ms in toks:
            return True
    return False


def intent_of(norm, brands):
    if brands and any(b and b in norm for b in brands):
        return "navigational"
    if has_marker(norm, NAV):
        return "navigational"
    tx, cmp_, info = has_marker(norm, TX), has_marker(norm, COMPARE), has_marker(norm, INFO)
    if tx and info:
        return "info_commercial"
    if tx:
        return "transactional"
    if cmp_:
        return "commercial_investigation"
    if info:
        return "informational"
    return "unknown"


def compatible(a, b):
    return a == b or a == "unknown" or b == "unknown"


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


DEFAULT_WEIGHTS = {"relevance": 25, "demand": 20, "commercial": 20}


def demand_points(maxf, wmax):
    if maxf is None:
        return 0
    steps = [(1000, 1.0), (100, 0.75), (10, 0.5)]
    for lim, k in steps:
        if maxf > lim:
            return round(wmax * k)
    return round(wmax * 0.25)


def commercial_points(intent, wmax):
    return {"transactional": wmax, "info_commercial": round(wmax * 0.75), "commercial_investigation": round(wmax * 0.75),
            "informational": round(wmax * 0.25), "navigational": 0, "unknown": round(wmax * 0.25)}.get(intent, 0)


def run(path, out_dir, freq_col=None, minus=None, brands=None, relevant=None, threshold=0.6, weights=None):
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    errors, data, idx = w.validate(path, verbose=False)
    if idx is None or idx["phrase"] is None:
        raise SystemExit("Не найдена колонка фразы (ожидается «фраза»/«запрос»).")
    fcol = freq_col if freq_col is not None else (idx["freqs"][0] if idx["freqs"] else None)
    minus_stems = [m.strip().lower() for m in (minus or []) if m.strip()]
    # 1. чтение и дедупликация по нормализованной форме (берём максимальную частотность)
    items = {}
    for row in data:
        if idx["phrase"] >= len(row):
            continue
        phrase = str(row[idx["phrase"]] or "").strip()
        if not phrase:
            continue
        norm = w.normalize_phrase(phrase)
        freq = None
        if fcol is not None and fcol < len(row):
            ok, freq = w.parse_frequency(row[fcol])
            if not ok:
                freq = None
        cur = items.get(norm)
        if cur is None or (freq is not None and (cur["freq"] is None or freq > cur["freq"])):
            items[norm] = {"phrase": phrase, "norm": norm, "freq": freq}
    arr = list(items.values())
    # 2. признаки
    for it in arr:
        it["toks"] = frozenset(stem(t) for t in tokens(it["norm"]))
        it["intent"] = intent_of(it["norm"], brands or [])
        reason = None
        if has_marker(it["norm"], minus_stems) if minus_stems else False:
            reason = "минус-слово пользователя"
        elif has_marker(it["norm"], GENERIC_NOISE):
            reason = "типовой нецелевой маркер"
        it["noise"] = reason
    # 3. кластеризация (Jaccard по основам; разные известные интенты не объединяем)
    n = len(arr)
    dsu = DSU(n)
    df = Counter()
    for it in arr:
        for t in it["toks"]:
            df[t] += 1
    common = {t for t, c in df.items() if n >= 20 and c > 0.3 * n}  # слова-«шапки» не порождают пары кандидатов
    inv = defaultdict(list)
    for i, it in enumerate(arr):
        for t in it["toks"]:
            if t not in common:
                inv[t].append(i)
    seen_pairs = set()
    for t, lst in inv.items():
        for ai in range(len(lst)):
            for bi in range(ai + 1, len(lst)):
                i, j = lst[ai], lst[bi]
                if (i, j) in seen_pairs:
                    continue
                seen_pairs.add((i, j))
                A, B = arr[i], arr[j]
                if not compatible(A["intent"], B["intent"]):
                    continue
                if (A["noise"] is None) != (B["noise"] is None):
                    continue
                inter = len(A["toks"] & B["toks"])
                union = len(A["toks"] | B["toks"])
                if union and inter / union >= threshold:
                    dsu.union(i, j)
    groups = defaultdict(list)
    for i in range(n):
        groups[dsu.find(i)].append(i)
    # 4. кластеры
    rel = [stem(r.strip().lower()) for r in (relevant or []) if r.strip()]
    clusters = []
    for gi, members in enumerate(sorted(groups.values(), key=lambda m: -max((arr[i]["freq"] or 0) for i in m)), start=1):
        ph = [arr[i] for i in members]
        main = sorted(ph, key=lambda x: (-(x["freq"] if x["freq"] is not None else -1), len(x["phrase"])))[0]
        ints = Counter(p["intent"] for p in ph if p["intent"] != "unknown")
        intent = ints.most_common(1)[0][0] if ints else "unknown"
        freqs = [p["freq"] for p in ph if p["freq"] is not None]
        noise_share = round(sum(1 for p in ph if p["noise"]) / len(ph), 2)
        if rel:
            hit = sum(1 for p in ph if any(r in p["toks"] for r in rel)) / len(ph)
            rel_pts = round(weights["relevance"] * (1 if hit >= 0.8 else 0.5 if hit >= 0.3 else 0))
        else:
            rel_pts = None
        maxf = max(freqs) if freqs else None
        auto = (rel_pts or 0) + demand_points(maxf, weights["demand"]) + commercial_points(intent, weights["commercial"])
        clusters.append({"id": gi, "label": main["phrase"], "intent": intent, "phrases": len(ph),
                         "max_freq": maxf, "sum_freq": sum(freqs) if freqs else None,
                         "noise_share": noise_share, "relevance_pts": rel_pts, "auto_score": auto, "members": ph})
        for p in ph:
            p["cluster"] = gi
            p["is_main"] = p is main
    os.makedirs(out_dir, exist_ok=True)
    cl_path = os.path.join(out_dir, "clusters.csv")
    ph_path = os.path.join(out_dir, "phrases.csv")
    maxscore = weights["demand"] + weights["commercial"] + (weights["relevance"] if rel else 0)
    with io.open(cl_path, "w", encoding="utf-8-sig", newline="") as f:
        cw = csv.writer(f, delimiter=";")
        cw.writerow(["Кластер", "Главная фраза", "Интент (по формулировке, проверить по выдаче)", "Фраз", "Макс. частотность",
                     "Сумма частотностей (пересекаются!)", "Доля шума", "Релевантность продукту (баллы)",
                     f"Авто-балл (из {maxscore})", "Охват страницей (0/5/10) — заполнить", "Каннибализация (0/5/10) — заполнить",
                     "Потенциал заявки (0–15) — заполнить", "Целевой URL", "Решение"])
        for c in clusters:
            cw.writerow([c["id"], c["label"], c["intent"], c["phrases"], "" if c["max_freq"] is None else c["max_freq"],
                         "" if c["sum_freq"] is None else c["sum_freq"], c["noise_share"],
                         "не задано" if c["relevance_pts"] is None else c["relevance_pts"], c["auto_score"], "", "", "", "", ""])
    with io.open(ph_path, "w", encoding="utf-8-sig", newline="") as f:
        pw = csv.writer(f, delimiter=";")
        pw.writerow(["Фраза", "Нормализованная", "Частотность", "Кластер", "Главная в кластере", "Интент", "Нецелевая (причина)"])
        for p in sorted(arr, key=lambda x: (x["cluster"], -(x["freq"] or 0))):
            pw.writerow([p["phrase"], p["norm"], "" if p["freq"] is None else p["freq"], p["cluster"],
                         "да" if p["is_main"] else "", p["intent"], p["noise"] or ""])
    return clusters, arr, errors, (cl_path, ph_path), maxscore


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--out", default="semantic-out")
    ap.add_argument("--freq-col", type=int)
    ap.add_argument("--minus", help="файл с минус-словами (по одному на строку)")
    ap.add_argument("--brand", help="бренды/названия через запятую (навигационные запросы)")
    ap.add_argument("--relevant", help="слова, показывающие релевантность продукту компании, через запятую")
    ap.add_argument("--threshold", type=float, default=0.6)
    ap.add_argument("--weights", help="JSON с весами {relevance, demand, commercial}")
    a = ap.parse_args()
    minus = open(a.minus, encoding="utf-8").read().splitlines() if a.minus else []
    weights = json.load(open(a.weights, encoding="utf-8")) if a.weights else None
    clusters, arr, errors, paths, maxscore = run(
        a.path, a.out, a.freq_col, minus, [b.strip().lower() for b in (a.brand or "").split(",")],
        [r for r in (a.relevant or "").split(",")], a.threshold, weights)
    nof = sum(1 for p in arr if p["freq"] is None)
    print(f"Фраз после дедупликации: {len(arr)}; кластеров: {len(clusters)}; без частотности: {nof}")
    if errors:
        print(f"Предупреждений валидации исходника: {len(errors)}")
    print("\nТоп кластеров по авто-баллу (часть факторов заполняется человеком):")
    for c in sorted(clusters, key=lambda x: -x["auto_score"])[:15]:
        print(f"  #{c['id']:<3} {c['auto_score']:>3}/{maxscore}  {c['intent']:<24} {c['phrases']:>3} фраз  «{c['label']}»")
    noisy = [c for c in clusters if c["noise_share"] >= 0.5]
    if noisy:
        print(f"\nКластеров с долей нецелевых ≥50%: {len(noisy)} — проверить вручную, кандидаты в минус-слова.")
    print("\nНапоминание: интент определён по формулировке; суммы частотностей пересекаются; "
          "авто-балл — для сортировки, не для обещаний. Далее: проверка выдачи и решение по каждому кластеру.")
    print(f"Сохранено: {paths[0]}, {paths[1]}")


if __name__ == "__main__":
    main()
