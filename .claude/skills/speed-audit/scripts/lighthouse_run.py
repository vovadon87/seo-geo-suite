#!/usr/bin/env python3
"""Локальный Lighthouse: несколько прогонов, медиана, оценка разброса.
Использование:
  python lighthouse_run.py https://example.com/ [--runs 3] [--preset mobile|desktop] [--out dir]
Требуется: Node.js и Lighthouse (npm i -g lighthouse  или локально), Chrome/Chromium.
Переменные: LIGHTHOUSE_BIN (путь к lighthouse), CHROME_PATH (путь к Chrome).
Это лабораторный замер из места запуска. Для вывода «Core Web Vitals пройдены» нужны полевые данные (crux.py/psi.py)."""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

METRICS = {"first-contentful-paint": "FCP", "largest-contentful-paint": "LCP", "total-blocking-time": "TBT",
           "cumulative-layout-shift": "CLS", "speed-index": "SI", "server-response-time": "TTFB",
           "total-byte-weight": "bytes"}


def find_lighthouse():
    if os.environ.get("LIGHTHOUSE_BIN"):
        return [os.environ["LIGHTHOUSE_BIN"]]
    p = shutil.which("lighthouse")
    if p:
        return [p]
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.getcwd(), here, os.path.join(here, "..", ".."), os.path.expanduser("~")):
        c = os.path.join(d, "node_modules", ".bin", "lighthouse")
        if os.path.exists(c):
            return [c]
    if shutil.which("npx"):
        return ["npx", "--yes", "lighthouse@12"]
    return None


def run_once(cmd, url, preset, out_path, chrome_flags, categories):
    args = cmd + [url, "--output=json", f"--output-path={out_path}", "--quiet",
                  f"--chrome-flags={chrome_flags}", f"--only-categories={categories}"]
    if preset == "desktop":
        args.append("--preset=desktop")
    r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if not os.path.exists(out_path):
        raise RuntimeError(f"Lighthouse не создал отчёт: {r.stderr[-400:] or r.stdout[-400:]}")
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh)


def extract(rep):
    a = rep["audits"]
    row = {}
    for aid, name in METRICS.items():
        if aid in a and a[aid].get("numericValue") is not None:
            row[name] = a[aid]["numericValue"]
    perf = rep["categories"].get("performance", {}).get("score")
    row["score"] = None if perf is None else round(perf * 100)
    row["_lcp_element"] = ((a.get("largest-contentful-paint-element", {}).get("details") or {}).get("items") or [{}])[0] \
        .get("items", [{}])[0].get("node", {}).get("snippet") if a.get("largest-contentful-paint-element") else None
    return row, rep.get("runWarnings", [])


def aggregate(rows):
    agg = {}
    keys = [k for k in rows[0] if not k.startswith("_") and rows[0][k] is not None]
    for k in keys:
        vals = [r[k] for r in rows if r.get(k) is not None]
        med = statistics.median(vals)
        spread = (max(vals) - min(vals)) / med if med else 0
        agg[k] = {"median": round(med, 3 if k == "CLS" else 0), "min": round(min(vals), 3 if k == "CLS" else 0),
                  "max": round(max(vals), 3 if k == "CLS" else 0), "unstable": spread > 0.15 and len(vals) > 1}
    return agg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--preset", default="mobile", choices=["mobile", "desktop"])
    ap.add_argument("--out", default="audit-out")
    ap.add_argument("--categories", default="performance")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cmd = find_lighthouse()
    if not cmd:
        print("Lighthouse не найден. Установите: npm i -g lighthouse (нужен Node.js), либо используйте psi.py.")
        sys.exit(2)
    flags = "--headless=new --no-sandbox --disable-gpu"
    os.makedirs(a.out, exist_ok=True)
    rows, warns = [], set()
    for i in range(a.runs):
        tmp = os.path.join(tempfile.mkdtemp(), f"lh{i}.json")
        try:
            rep = run_once(cmd, a.url, a.preset, tmp, flags, a.categories)
        except Exception as e:
            print(f"прогон {i + 1}: ошибка — {e}")
            continue
        row, w = extract(rep)
        rows.append(row)
        warns.update(w)
        print(f"прогон {i + 1}: score={row['score']} LCP={row.get('LCP', 0):.0f} TBT={row.get('TBT', 0):.0f} CLS={row.get('CLS', 0):.3f}")
    if not rows:
        print("Ни один прогон не удался.")
        sys.exit(1)
    agg = aggregate(rows)
    res = {"url": a.url, "preset": a.preset, "runs": len(rows), "metrics": agg, "warnings": sorted(warns),
           "note": "Лабораторные данные из места запуска; не заменяют CrUX/Метрику."}
    with open(os.path.join(a.out, f"lighthouse-{a.preset}.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("\nМедианы:", {k: v["median"] for k, v in agg.items()})
    uns = [k for k, v in agg.items() if v["unstable"]]
    if uns:
        print("Нестабильные метрики (разброс >15%):", uns, "— увеличьте --runs или повторите в спокойной сети.")
    for w in res["warnings"]:
        print("Предупреждение Lighthouse:", w)


if __name__ == "__main__":
    main()
