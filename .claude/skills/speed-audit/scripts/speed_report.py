#!/usr/bin/env python3
"""Сборка отчёта по скорости из результатов psi.py / crux.py / lighthouse_run.py / page_weight.py.
Использование: python speed_report.py <папка с json> [--url https://...] [--region "Россия"]
Пишет speed-report.md в ту же папку. Лаборатория и поле — раздельно; вердикт «CWV пройдены» —
только по полевым данным (CrUX p75 по LCP, INP, CLS)."""
import argparse
import glob
import json
import os
import sys


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def fmt(k, v):
    if k == "CLS":
        return f"{v:.3f}"
    if k == "bytes":
        return f"{v / 1024:.0f} КБ"
    return f"{v:,.0f} мс".replace(",", " ") if k not in ("score",) else f"{v:.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--url", default="")
    ap.add_argument("--region", default="")
    a = ap.parse_args()
    d = a.dir
    psi, crux = load(os.path.join(d, "psi.json")), load(os.path.join(d, "crux.json"))
    lh = {os.path.basename(p): load(p) for p in glob.glob(os.path.join(d, "lighthouse-*.json"))}
    pw = {os.path.basename(p): load(p) for p in glob.glob(os.path.join(d, "page-weight-*.json"))}
    L = [f"# Отчёт по скорости {a.url}".strip(), ""]
    L.append("## Границы охвата")
    L.append("- Источники: " + ", ".join(n for n, v in (("PSI", psi), ("CrUX API", crux), ("Lighthouse (локально)", lh), ("разбор в браузере", pw)) if v) or "нет данных")
    L.append("- Лабораторные замеры сделаны из места запуска" + (f", аудитория: {a.region}" if a.region else "") +
             "; TTFB и скорость сети отличаются от тех, что видят пользователи. Выводы о пользовательском опыте — только по полевым данным.")
    L.append("")
    L.append("## Поле (реальные пользователи, CrUX, p75 за 28 дней)")
    verdict = None
    if crux:
        for ff, v in crux.items():
            if not v or "error" in (v or {}):
                L.append(f"- {ff}: нет данных / ошибка {(v or {}).get('error', '')}")
                continue
            ms = v["metrics"]
            row = ", ".join(f"{k}: {m['p75']:g} ({m['verdict']})" for k, m in ms.items())
            L.append(f"- {ff}: {row}")
            if v.get("core_web_vitals_passed") is not None:
                verdict = v["core_web_vitals_passed"] if verdict is None else (verdict and v["core_web_vitals_passed"])
    elif psi:
        for st, v in psi.items():
            if "error" in v:
                continue
            fu = v.get("field_url") or v.get("field_origin")
            src = "URL" if v.get("field_url") else "origin"
            if fu:
                L.append(f"- {st} ({src}{', замена: у URL нет своих данных' if v.get('field_is_origin_fallback') else ''}): " +
                         ", ".join(f"{k}: {m['p75']} ({m['category']})" for k, m in fu.items() if k != "_overall"))
            else:
                L.append(f"- {st}: полевых данных нет")
    else:
        L.append("- Полевых данных нет. Откройте Метрику → «Скорость» и GSC → «Основные интернет-показатели».")
    L.append("")
    if verdict is None:
        L.append("**Вердикт по Core Web Vitals:** не определён (недостаточно полевых данных по LCP/INP/CLS).")
    else:
        L.append(f"**Вердикт по Core Web Vitals (по полевым данным):** {'пройдены' if verdict else 'не пройдены'}.")
    L.append("")
    L.append("## Лаборатория")
    if psi:
        for st, v in psi.items():
            if "error" in v:
                L.append(f"- PSI {st}: ошибка {v['error']}")
                continue
            L.append(f"- PSI {st}: баллы {v['scores']}; {v['lab']}")
            for o in v["opportunities"][:5]:
                L.append(f"  - {o['title']}: ~{o['savings_ms']} мс / {o['savings_kb']} КБ")
    for n, v in lh.items():
        if v:
            L.append(f"- Lighthouse {v['preset']} ({v['runs']} прогон.): " + ", ".join(f"{k} {fmt(k, m['median'])}" + ("*" if m["unstable"] else "") for k, m in v["metrics"].items()))
            if any(m["unstable"] for m in v["metrics"].values()):
                L.append("  (* — разброс >15%, повторить)")
    for n, v in pw.items():
        if not v:
            continue
        s = v["summary"]
        L.append(f"- Разбор загрузки ({v['device']}{', throttle' if v.get('throttle') else ''}): {s['requests']} запросов, {s['total_kb']} КБ, "
                 f"сторонние {s['third_party_share_pct']}%, TTFB {s['ttfb_ms']} мс, LCP {s['lcp_ms']} мс, CLS {s['cls']}, long tasks {s['long_tasks_ms']} мс")
    L.append("")
    L.append("## Находки")
    L.append("| P | ID | Проблема | Доказательство | Исправление |")
    L.append("|---|---|---|---|---|")
    for n, v in pw.items():
        for f in (v or {}).get("findings", []):
            L.append(f"| {f['priority']} | {f['id']} ({v['device']}) | {f['problem']} | {f['evidence']} | {f['fix']} |")
    L.append("")
    L.append("## Не проверено")
    L.append("- Поведение на реальных устройствах и сетях аудитории; INP в лаборатории не измеряется.")
    L.append("- Влияние стороннего кода при реальных рекламных/аналитических настройках.")
    path = os.path.join(d, "speed-report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nСохранено: {path}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
