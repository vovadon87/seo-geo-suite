#!/usr/bin/env python3
"""Chrome UX Report API: полевые данные (реальные пользователи Chrome, p75 за 28 дней).
Использование:
  CRUX_API_KEY=... python crux.py https://example.com/ [--origin] [--form-factor PHONE|DESKTOP|TABLET] [--out dir]
Ключ — тот же тип, что для PSI (Google Cloud, включить Chrome UX Report API).
Нет данных ≠ плохо: у малопосещаемых страниц/сайтов CrUX может не быть записи."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
METRICS = ["largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift",
           "first_contentful_paint", "experimental_time_to_first_byte"]
# пороги Google: (хорошо ≤, плохо >)
THRESH = {"largest_contentful_paint": (2500, 4000), "interaction_to_next_paint": (200, 500),
          "cumulative_layout_shift": (0.1, 0.25), "first_contentful_paint": (1800, 3000),
          "experimental_time_to_first_byte": (800, 1800)}


def query(target, origin=False, form_factor=None, key=None):
    body = {"metrics": METRICS}
    body["origin" if origin else "url"] = target
    if form_factor:
        body["formFactor"] = form_factor
    req = urllib.request.Request(f"{ENDPOINT}?key={key}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # нет данных для этой цели
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")


def rate(metric, p75):
    good, poor = THRESH[metric]
    return "хорошо" if p75 <= good else ("плохо" if p75 > poor else "требует улучшения")


def summarize(rec):
    if not rec:
        return None
    r = rec.get("record", {})
    out = {"key": r.get("key"), "period": r.get("collectionPeriod"), "metrics": {}}
    for m, v in r.get("metrics", {}).items():
        p = v.get("percentiles", {}).get("p75")
        if p is None:
            continue
        p = float(p)
        hist = [round(b.get("density", 0) * 100, 1) for b in v.get("histogram", [])]
        out["metrics"][m] = {"p75": p, "verdict": rate(m, p) if m in THRESH else None, "distribution_pct_good_ni_poor": hist}
    core = [out["metrics"].get(m) for m in ("largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift")]
    if all(core):
        out["core_web_vitals_passed"] = all(c["verdict"] == "хорошо" for c in core)
    else:
        out["core_web_vitals_passed"] = None  # данных не хватает для вывода
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--origin", action="store_true", help="запрашивать по origin, а не по URL")
    ap.add_argument("--form-factor")
    ap.add_argument("--out", default="audit-out")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    key = os.environ.get("CRUX_API_KEY") or os.environ.get("PSI_API_KEY")
    if not key:
        print("Нужен CRUX_API_KEY (или PSI_API_KEY с включённым Chrome UX Report API). Без ключа полевые данные "
              "берите из блока loadingExperience в psi.py и из Метрики («Скорость»).")
        sys.exit(2)
    os.makedirs(a.out, exist_ok=True)
    res = {}
    for ff in ([a.form_factor] if a.form_factor else ["PHONE", "DESKTOP"]):
        try:
            res[ff] = summarize(query(a.target, a.origin, ff, key))
        except Exception as e:
            res[ff] = {"error": str(e)}
        print(f"\n[{ff}]", json.dumps(res[ff], ensure_ascii=False, indent=1) if res[ff] else "нет данных CrUX для этой цели")
    with open(os.path.join(a.out, "crux.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
