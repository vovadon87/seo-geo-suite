#!/usr/bin/env python3
"""PageSpeed Insights API v5: лаборатория (Lighthouse) + поле (CrUX) для URL.
Использование:
  PSI_API_KEY=... python psi.py https://example.com/ [--strategy mobile|desktop|both] [--out dir]
Без ключа API работает, но с жёсткой квотой (возможен 429). Ключ создаёт владелец в Google Cloud.
Лаборатория запускается на серверах Google (не в вашем регионе) — это ориентир, не замер для аудитории."""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
LAB = {"first-contentful-paint": "FCP", "largest-contentful-paint": "LCP", "total-blocking-time": "TBT",
       "cumulative-layout-shift": "CLS", "speed-index": "SI", "interactive": "TTI", "server-response-time": "TTFB"}
FIELD = {"LARGEST_CONTENTFUL_PAINT_MS": "LCP", "INTERACTION_TO_NEXT_PAINT": "INP",
         "CUMULATIVE_LAYOUT_SHIFT_SCORE": "CLS", "FIRST_CONTENTFUL_PAINT_MS": "FCP",
         "EXPERIMENTAL_TIME_TO_FIRST_BYTE": "TTFB"}


def call(url, strategy, key=None, retries=3):
    q = [("url", url), ("strategy", strategy), ("category", "performance"), ("category", "seo"),
         ("category", "best-practices"), ("category", "accessibility")]
    if key:
        q.append(("key", key))
    full = API + "?" + urllib.parse.urlencode(q)
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers={"User-Agent": "seo-geo-audit/3.0"}), timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}"
            if e.code in (429, 500, 503):
                time.sleep(5 * (i + 1))
                continue
            break
        except Exception as e:
            last = str(e)
            time.sleep(3)
    raise RuntimeError(last)


def summarize(j):
    """Из ответа PSI делает компактную сводку. Лаборатория и поле — раздельно."""
    lh = j.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    out = {"url": j.get("id"), "final_url": lh.get("finalDisplayedUrl") or lh.get("finalUrl"),
           "fetch_time": lh.get("fetchTime"), "lighthouse": lh.get("lighthouseVersion"),
           "lab": {}, "scores": {}, "field_url": {}, "field_origin": {}, "opportunities": [], "warnings": lh.get("runWarnings", [])}
    for k, v in lh.get("categories", {}).items():
        if v.get("score") is not None:
            out["scores"][k] = round(v["score"] * 100)
    for aid, name in LAB.items():
        a = audits.get(aid)
        if a and a.get("numericValue") is not None:
            out["lab"][name] = round(a["numericValue"], 3 if name == "CLS" else 0)

    def field(block):
        res = {}
        for k, name in FIELD.items():
            m = (block or {}).get("metrics", {}).get(k)
            if m:
                res[name] = {"p75": m.get("percentile"), "category": m.get("category")}
        if res:
            res["_overall"] = (block or {}).get("overall_category")
        return res
    out["field_url"] = field(j.get("loadingExperience"))
    out["field_origin"] = field(j.get("originLoadingExperience"))
    out["field_is_origin_fallback"] = bool((j.get("loadingExperience") or {}).get("origin_fallback"))
    opps = []
    for aid, a in audits.items():
        d = a.get("details") or {}
        sav = d.get("overallSavingsMs") or 0
        sav_b = d.get("overallSavingsBytes") or 0
        if d.get("type") == "opportunity" and (sav > 0 or sav_b > 0) and (a.get("score") is None or a["score"] < 0.9):
            opps.append({"id": aid, "title": a.get("title"), "savings_ms": round(sav), "savings_kb": round(sav_b / 1024)})
    out["opportunities"] = sorted(opps, key=lambda x: (-x["savings_ms"], -x["savings_kb"]))[:10]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--strategy", default="both", choices=["mobile", "desktop", "both"])
    ap.add_argument("--out", default="audit-out")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    os.makedirs(a.out, exist_ok=True)
    key = os.environ.get("PSI_API_KEY")
    if not key:
        print("! PSI_API_KEY не задан — квота ограничена, возможен 429.", file=sys.stderr)
    res = {}
    for st in (["mobile", "desktop"] if a.strategy == "both" else [a.strategy]):
        try:
            raw = call(a.url, st, key)
        except Exception as e:
            print(f"[{st}] ОШИБКА PSI: {e}")
            res[st] = {"error": str(e)}
            continue
        s = summarize(raw)
        res[st] = s
        with open(os.path.join(a.out, f"psi-{st}-raw.json"), "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False)
        print(f"\n[{st}] баллы: {s['scores']}")
        print("  лаборатория:", s["lab"])
        print("  поле (URL):", {k: v for k, v in s["field_url"].items()} or "нет данных")
        print("  поле (origin):", {k: v for k, v in s["field_origin"].items()} or "нет данных")
        for o in s["opportunities"][:5]:
            print(f"  - {o['title']}: ~{o['savings_ms']} мс / {o['savings_kb']} КБ")
    with open(os.path.join(a.out, "psi.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(f"\nСохранено: {a.out}/psi.json")


if __name__ == "__main__":
    main()
