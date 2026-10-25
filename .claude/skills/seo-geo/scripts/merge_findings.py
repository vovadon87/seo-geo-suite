#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""merge_findings.py: сводит находки аудиторов (tech, speed, content, local и др.) в один список.

Читает рекурсивно из папки (по умолчанию audit-out):
  findings.json   общий формат: список или {"findings": [...]}; поля: id, priority, problem, urls|examples, evidence, fix, recheck,
                  confidence, ask_owner, module
  findings.csv    вывод tech-audit/site_crawl.py
  render-check.json  вывод tech-audit/render_check.py
  nap.json        вывод local-seo/nap_check.py
Модуль определяется по имени подпапки (tech, speed, content, local), если не указан в находке.

Пишет в --out: merged-findings.csv и merged-findings.md (по приоритетам, вопросы владельцу, охват источников).
Одинаковые находки (тот же id и проблема) объединяются, URL складываются; модули перечисляются.
Ничего не придумывает: поля, которых нет в источнике, остаются пустыми.

Запуск: python merge_findings.py audit-out --out audit-out"""
import argparse
import csv
import glob
import json
import os
import sys

ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def module_of(path, root):
    rel = os.path.relpath(os.path.dirname(path), root)
    first = rel.split(os.sep)[0]
    return "" if first in (".", "") else first


def norm(f, module, source):
    urls = f.get("urls")
    if urls is None:
        ex = f.get("examples") or f.get("url") or ""
        urls = [u.strip() for u in str(ex).split("|") if u.strip()] if isinstance(ex, str) else list(ex)
    if isinstance(urls, str):
        urls = [urls]
    return {"module": f.get("module") or module, "id": str(f.get("id") or f.get("code") or ""),
            "priority": str(f.get("priority") or f.get("p") or "P3").upper(),
            "problem": f.get("problem") or f.get("title") or "", "urls": list(urls),
            "evidence": f.get("evidence") or "", "fix": f.get("fix") or "", "recheck": f.get("recheck") or "",
            "confidence": f.get("confidence") or "", "ask_owner": f.get("ask_owner") or "", "source": source}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read(root):
    out, sources = [], []
    for path in sorted(glob.glob(os.path.join(root, "**", "*"), recursive=True)):
        base = os.path.basename(path)
        mod = module_of(path, root)
        try:
            if base == "findings.json":
                data = _load(path)
                items = data.get("findings", []) if isinstance(data, dict) else data
            elif base == "findings.csv":
                with open(path, encoding="utf-8-sig", newline="") as fh:
                    items = list(csv.DictReader(fh))
                mod = mod or "tech"
            elif base in ("render-check.json", "nap.json"):
                data = _load(path)
                items = data.get("findings", [])
                mod = mod or ("tech" if base.startswith("render") else "local")
            else:
                continue
        except (OSError, ValueError) as e:
            sys.stderr.write("пропущен %s: %s\n" % (path, e))
            continue
        sources.append((os.path.relpath(path, root), len(items)))
        for f in items:
            out.append(norm(f, mod, os.path.relpath(path, root)))
    return out, sources


def merge(items):
    merged = {}
    for f in items:
        key = (f["id"], f["problem"].strip().lower())
        m = merged.get(key)
        if not m:
            merged[key] = dict(f, modules=[f["module"]] if f["module"] else [], sources=[f["source"]])
            continue
        for u in f["urls"]:
            if u not in m["urls"]:
                m["urls"].append(u)
        if f["module"] and f["module"] not in m["modules"]:
            m["modules"].append(f["module"])
        m["sources"].append(f["source"])
        if ORDER.get(f["priority"], 9) < ORDER.get(m["priority"], 9):
            m["priority"] = f["priority"]
        for k in ("evidence", "fix", "recheck", "ask_owner", "confidence"):
            if not m[k] and f[k]:
                m[k] = f[k]
    res = list(merged.values())
    res.sort(key=lambda x: (ORDER.get(x["priority"], 9), x["id"]))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default="audit-out")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or a.root
    items, sources = read(a.root)
    res = merge(items)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "merged-findings.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["priority", "id", "modules", "problem", "url_count", "examples", "evidence", "fix", "recheck", "confidence"])
        for f in res:
            w.writerow([f["priority"], f["id"], "+".join(f["modules"]), f["problem"], len(f["urls"]), " | ".join(f["urls"][:5]),
                        f["evidence"], f["fix"], f["recheck"], f["confidence"]])
    md = ["# Сводные находки", "", "## Источники", ""]
    if not sources:
        md.append("Источники не найдены: в папке нет findings.json, findings.csv, render-check.json, nap.json.")
    for s, n in sources:
        md.append("- `%s`: %d находок" % (s, n))
    cnt = {p: sum(1 for f in res if f["priority"] == p) for p in ORDER}
    md += ["", "Итого после объединения: " + ", ".join("%s — %d" % (p, c) for p, c in cnt.items()) + ".", ""]
    for p in ORDER:
        grp = [f for f in res if f["priority"] == p]
        if not grp:
            continue
        md += ["## " + p, ""]
        for f in grp:
            md.append("### %s: %s" % (f["id"] or "—", f["problem"]))
            md.append("- Модули: %s; URL: %d (%s)" % (", ".join(f["modules"]) or "—", len(f["urls"]), "; ".join(f["urls"][:3]) or "нет"))
            if f["evidence"]:
                md.append("- Доказательство: " + f["evidence"])
            if f["fix"]:
                md.append("- Исправление: " + f["fix"])
            if f["recheck"]:
                md.append("- Проверка: " + f["recheck"])
            if f["confidence"]:
                md.append("- Уверенность: " + f["confidence"])
            md.append("")
    asks = [(f["id"], f["ask_owner"]) for f in res if f["ask_owner"]]
    md += ["## Вопросы владельцу", ""]
    md += ["- %s: %s" % a_ for a_ in asks] if asks else ["Нет вопросов в находках."]
    md += ["", "Охват и ограничения каждого аудитора — в его собственном отчёте; сводка не заменяет их."]
    with open(os.path.join(out, "merged-findings.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    print("Находок до объединения: %d, после: %d. %s" % (len(items), len(res), os.path.join(out, "merged-findings.md")))


if __name__ == "__main__":
    main()
