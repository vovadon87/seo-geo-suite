#!/usr/bin/env python3
"""Разбор загрузки страницы в браузере (Playwright/Chromium): вес по типам, запросы, сторонние домены,
сжатие, кэш, форматы и размеры картинок, блокирующие ресурсы, LCP-элемент, CLS, long tasks.
Использование:
  python page_weight.py https://example.com/ [--device mobile|desktop] [--throttle] [--budget budget.json] [--out dir]
Требуется: pip install playwright; Chromium (playwright install chromium, либо уже установленный).
--throttle включает эмуляцию «медленного 4G» и замедление CPU ×4 (грубое приближение, не замена Lighthouse).
Это лабораторный замер из места запуска."""
import argparse
import json
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
INIT_JS = """
window.__m = {lcp:null, cls:0, longtasks:[], shifts:[]};
try {
 new PerformanceObserver(l=>{for(const e of l.getEntries()){
   window.__m.lcp={time:e.startTime,size:e.size,url:e.url||null,
     tag:e.element?e.element.tagName.toLowerCase():null,
     id:e.element?e.element.id:null,
     cls:e.element?String(e.element.className).slice(0,80):null};}}).observe({type:'largest-contentful-paint',buffered:true});
 new PerformanceObserver(l=>{for(const e of l.getEntries()){ if(!e.hadRecentInput){window.__m.cls+=e.value;
   window.__m.shifts.push({v:e.value,t:e.startTime,src:(e.sources||[]).slice(0,2).map(s=>s.node&&s.node.nodeName)});}}}).observe({type:'layout-shift',buffered:true});
 new PerformanceObserver(l=>{for(const e of l.getEntries()){window.__m.longtasks.push(e.duration);}}).observe({type:'longtask',buffered:true});
} catch(e) {}
"""
DOM_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const imgs = [...document.images].map(i => ({src: i.currentSrc || i.src, natW: i.naturalWidth, natH: i.naturalHeight,
      w: i.clientWidth, h: i.clientHeight, lazy: i.loading === 'lazy', hasSize: !!(i.getAttribute('width') && i.getAttribute('height')),
      aboveFold: i.getBoundingClientRect().top < window.innerHeight}));
  const headScripts = [...document.head.querySelectorAll('script[src]')].filter(s => !s.async && !s.defer && s.type !== 'module').map(s => s.src);
  const blockingCss = [...document.head.querySelectorAll('link[rel=stylesheet]')].filter(l => !l.media || l.media === 'all' || l.media === 'screen').map(l => l.href);
  const preloads = [...document.querySelectorAll('link[rel=preload]')].map(l => ({as: l.as, href: l.href}));
  const fonts = (document.fonts && document.fonts.size) || 0;
  return {ttfb: nav.responseStart, dcl: nav.domContentLoadedEventEnd, load: nav.loadEventEnd,
          dom_nodes: document.getElementsByTagName('*').length, imgs, headScripts, blockingCss, preloads, fonts_count: fonts,
          viewport: {w: window.innerWidth, h: window.innerHeight}};
}
"""
TEXT_TYPES = ("text/", "application/javascript", "application/json", "application/xml", "image/svg+xml", "font/ttf", "application/x-javascript")


def reg_domain(host):
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def classify(mime, rtype):
    m = (mime or "").lower()
    if rtype == "Document":
        return "html"
    if "javascript" in m or rtype == "Script":
        return "js"
    if "css" in m or rtype == "Stylesheet":
        return "css"
    if m.startswith("image/") or rtype == "Image":
        return "image"
    if "font" in m or rtype == "Font":
        return "font"
    if m.startswith("video/") or m.startswith("audio/") or rtype == "Media":
        return "media"
    return "other"


def analyse(reqs, dom, metrics, page_host, budget):
    """reqs: список dict(url,status,mime,type,size,headers). Возвращает (summary, findings)."""
    by = {}
    for r in reqs:
        c = classify(r["mime"], r["type"])
        b = by.setdefault(c, {"count": 0, "kb": 0.0})
        b["count"] += 1
        b["kb"] += r["size"] / 1024
    for b in by.values():
        b["kb"] = round(b["kb"], 1)
    total_kb = round(sum(r["size"] for r in reqs) / 1024, 1)
    first_party = reg_domain(page_host)
    third = {}
    for r in reqs:
        h = urllib.parse.urlsplit(r["url"]).hostname or ""
        if h and reg_domain(h) != first_party:
            t = third.setdefault(reg_domain(h), {"count": 0, "kb": 0.0})
            t["count"] += 1
            t["kb"] += r["size"] / 1024
    for t in third.values():
        t["kb"] = round(t["kb"], 1)
    third_kb = sum(t["kb"] for t in third.values())
    third_share = round(100 * third_kb / total_kb, 1) if total_kb else 0
    F = []

    def add(pr, fid, problem, evidence, fix):
        F.append({"priority": pr, "id": fid, "problem": problem, "evidence": evidence, "fix": fix})

    if total_kb > budget["total_transfer_kb"]:
        add("P2", "W1", "Большой общий вес страницы", f"{total_kb} КБ при бюджете {budget['total_transfer_kb']}",
            "Сжать изображения, убрать неиспользуемый JS/CSS, отложить блоки ниже первого экрана")
    if len(reqs) > budget["total_requests"]:
        add("P3", "W2", "Много запросов", f"{len(reqs)} при бюджете {budget['total_requests']}", "Объединить/отложить ресурсы, убрать лишние виджеты")
    for cls_, key, fid in (("js", "js_transfer_kb", "W3"), ("css", "css_transfer_kb", "W4"),
                           ("image", "image_transfer_kb", "W5"), ("font", "font_transfer_kb", "W6")):
        v = by.get(cls_, {}).get("kb", 0)
        if v > budget[key]:
            add("P2" if cls_ in ("js", "image") else "P3", fid, f"Много {cls_}-данных", f"{v} КБ при бюджете {budget[key]}",
                {"js": "Удалить неиспользуемые скрипты, разделить бандл, defer", "css": "Убрать неиспользуемые стили, критический CSS",
                 "image": "WebP/AVIF, адаптивные размеры, lazy-load", "font": "Оставить 1–2 начертания, woff2, font-display: swap"}[cls_])
    if third_share > budget["third_party_share_pct"]:
        top = sorted(third.items(), key=lambda kv: -kv[1]["kb"])[:3]
        add("P2", "W7", "Большая доля сторонних ресурсов", f"{third_share}% веса; топ: " + ", ".join(f"{d} {v['kb']} КБ" for d, v in top),
            "Отложить/убрать неиспользуемые виджеты, аналитику загружать после взаимодействия там, где допустимо")
    # сжатие
    nocomp = [r["url"] for r in reqs if r["status"] == 200 and r["size"] > 2048
              and any((r["mime"] or "").startswith(t) for t in TEXT_TYPES) and not r["headers"].get("content-encoding")
              and r["type"] != "Font"]
    if nocomp:
        add("P2", "W8", "Текстовые ресурсы без сжатия", f"{len(nocomp)} файл(ов), напр. {nocomp[0]}", "Включить brotli/gzip на сервере/CDN")
    # кэш для статики
    nocache = []
    for r in reqs:
        if classify(r["mime"], r["type"]) in ("js", "css", "image", "font") and urllib.parse.urlsplit(r["url"]).hostname \
                and reg_domain(urllib.parse.urlsplit(r["url"]).hostname) == first_party:
            cc = r["headers"].get("cache-control", "")
            if not cc or "no-store" in cc or "max-age=0" in cc or "no-cache" in cc:
                nocache.append(r["url"])
    if nocache:
        add("P3", "W9", "Статика без долгого кэширования", f"{len(nocache)} файл(ов), напр. {nocache[0]}",
            "Cache-Control: public, max-age=31536000, immutable для версионируемых файлов")
    # изображения
    big = sorted([r for r in reqs if classify(r["mime"], r["type"]) == "image" and r["size"] / 1024 > budget["single_image_kb"]],
                 key=lambda r: -r["size"])
    if big:
        add("P2", "W10", "Тяжёлые изображения", "; ".join(f"{r['url'].split('/')[-1][:40]} {r['size'] // 1024} КБ" for r in big[:3]),
            "Сжать, подобрать размер под отображение, WebP/AVIF")
    legacy = [r for r in reqs if (r["mime"] or "") in ("image/jpeg", "image/png") and r["size"] > 50 * 1024]
    if legacy:
        add("P3", "W11", "Крупные JPEG/PNG вместо WebP/AVIF", f"{len(legacy)} файл(ов)", "Конвертировать в WebP/AVIF, оставить фолбэк при необходимости")
    over = [i for i in dom["imgs"] if i["w"] and i["natW"] and i["natW"] > 2 * i["w"] * 1.0 and i["natW"] > 600]
    if over:
        add("P3", "W12", "Изображения заметно больше области показа", f"{len(over)} шт., напр. {over[0]['src'].split('/')[-1][:40]} "
            f"{over[0]['natW']}px в блок {over[0]['w']}px", "srcset/sizes или отдача нужного размера")
    nolazy = [i for i in dom["imgs"] if not i["aboveFold"] and not i["lazy"]]
    if len(nolazy) >= 3:
        add("P3", "W13", "Изображения ниже первого экрана без loading=lazy", f"{len(nolazy)} шт.", "Добавить loading=\"lazy\" (кроме LCP-картинки)")
    lcp = metrics.get("lcp")
    if lcp and lcp.get("tag") == "img":
        im = next((i for i in dom["imgs"] if lcp.get("url") and i["src"] == lcp["url"]), None)
        if im and im["lazy"]:
            add("P1", "W14", "LCP-изображение загружается лениво (loading=lazy)", lcp["url"], "Убрать lazy у LCP-картинки, добавить fetchpriority=high и preload")
    nosize = [i for i in dom["imgs"] if not i["hasSize"]]
    if len(nosize) >= 3 and metrics.get("cls", 0) > 0.05:
        add("P2", "W15", "Изображения без width/height при заметном сдвиге макета", f"{len(nosize)} шт.; CLS {metrics['cls']:.3f}", "Задать размеры или aspect-ratio")
    # блокирующие ресурсы
    if len(dom["headScripts"]) > budget["blocking_head_scripts"]:
        add("P2", "W16", "Блокирующие скрипты в <head>", f"{len(dom['headScripts'])} шт., напр. {dom['headScripts'][0]}", "defer/async, перенос вниз, удаление лишнего")
    if len(dom["blockingCss"]) > 3:
        add("P3", "W17", "Много блокирующих CSS-файлов", f"{len(dom['blockingCss'])} шт.", "Объединить, критический CSS, остальное отложить")
    # метрики
    if dom.get("ttfb") and dom["ttfb"] > budget["ttfb_ms"]:
        add("P2", "W18", "Медленный ответ сервера (TTFB из места запуска)", f"{dom['ttfb']:.0f} мс", "Кэш страниц, CDN, оптимизация бэкенда; сверить с полевым TTFB")
    if lcp and lcp["time"] > budget["lcp_ms"]:
        add("P2", "W19", "LCP выше ориентира (лаборатория)", f"{lcp['time']:.0f} мс; элемент {lcp.get('tag')} {lcp.get('url') or ''}"[:200],
            "Сократить путь до LCP-элемента: TTFB, блокирующие ресурсы, размер и приоритет LCP-картинки")
    if metrics.get("cls", 0) > budget["cls"]:
        add("P2", "W20", "Сдвиги макета выше ориентира", f"CLS {metrics['cls']:.3f}", "Зарезервировать место под картинки/баннеры/шрифты")
    lt = sum(metrics.get("longtasks", []))
    if lt > budget["long_tasks_total_ms"]:
        add("P2", "W21", "Долгие задачи основного потока", f"сумма {lt:.0f} мс ({len(metrics['longtasks'])} шт.)", "Разбить/отложить скрипты, убрать тяжёлые виджеты")
    if dom["dom_nodes"] > budget["dom_nodes"]:
        add("P3", "W22", "Большой DOM", f"{dom['dom_nodes']} элементов", "Упростить разметку, виртуализировать длинные списки")
    summary = {"requests": len(reqs), "total_kb": total_kb, "by_type": by, "third_party_share_pct": third_share,
               "third_party": dict(sorted(third.items(), key=lambda kv: -kv[1]["kb"])[:10]),
               "ttfb_ms": round(dom.get("ttfb") or 0), "dcl_ms": round(dom.get("dcl") or 0), "load_ms": round(dom.get("load") or 0),
               "lcp_ms": round(lcp["time"]) if lcp else None, "lcp_element": lcp, "cls": round(metrics.get("cls", 0), 4),
               "long_tasks_ms": round(lt), "dom_nodes": dom["dom_nodes"], "fonts": dom.get("fonts_count")}
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    F.sort(key=lambda f: (order[f["priority"]], f["id"]))
    return summary, F


def measure(url, device, throttle, wait_ms=2500):
    from playwright.sync_api import sync_playwright
    reqs, meta = {}, {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        if device == "mobile":
            ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                                user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36")
        else:
            ctx = b.new_context(viewport={"width": 1366, "height": 768})
        page = ctx.new_page()
        page.add_init_script(INIT_JS)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        if throttle:
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150, "downloadThroughput": 1.6 * 1024 * 1024 / 8,
                                                         "uploadThroughput": 750 * 1024 / 8})
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})

        def on_resp(e):
            r = e["response"]
            reqs[e["requestId"]] = {"url": r["url"], "status": r["status"], "mime": r.get("mimeType"), "type": e.get("type"),
                                    "headers": {k.lower(): v for k, v in (r.get("headers") or {}).items()}, "size": 0}

        def on_fin(e):
            if e["requestId"] in reqs:
                reqs[e["requestId"]]["size"] = int(e.get("encodedDataLength") or 0)
        cdp.on("Network.responseReceived", on_resp)
        cdp.on("Network.loadingFinished", on_fin)
        resp = page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(wait_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        dom = page.evaluate(DOM_JS)
        metrics = page.evaluate("() => window.__m || {lcp:null,cls:0,longtasks:[]}")
        meta = {"final_url": page.url, "status": resp.status if resp else None}
        b.close()
    return [r for r in reqs.values() if not r["url"].startswith("data:")], dom, metrics, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--device", default="mobile", choices=["mobile", "desktop"])
    ap.add_argument("--throttle", action="store_true")
    ap.add_argument("--budget", default=os.path.join(HERE, "budget.json"))
    ap.add_argument("--out", default="audit-out")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    budget = json.load(open(a.budget, encoding="utf-8"))
    try:
        reqs, dom, metrics, meta = measure(a.url, a.device, a.throttle)
    except ImportError:
        print("Нужен Playwright: pip install playwright && playwright install chromium")
        sys.exit(2)
    host = urllib.parse.urlsplit(meta["final_url"]).hostname or ""
    summary, findings = analyse(reqs, dom, metrics, host, budget)
    os.makedirs(a.out, exist_ok=True)
    res = {"url": a.url, "device": a.device, "throttle": a.throttle, "meta": meta, "summary": summary, "findings": findings,
           "note": "Лабораторный замер из места запуска; не заменяет полевые данные."}
    with open(os.path.join(a.out, f"page-weight-{a.device}.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    s = summary
    print(f"{a.url} [{a.device}{', throttle' if a.throttle else ''}] статус {meta['status']}")
    print(f"запросов {s['requests']}, вес {s['total_kb']} КБ, сторонние {s['third_party_share_pct']}%")
    print("по типам:", {k: f"{v['count']} шт / {v['kb']} КБ" for k, v in s["by_type"].items()})
    print(f"TTFB {s['ttfb_ms']} мс, LCP {s['lcp_ms']} мс ({(s['lcp_element'] or {}).get('tag')}), CLS {s['cls']}, long tasks {s['long_tasks_ms']} мс")
    for f in findings:
        print(f"[{f['priority']}] {f['id']} {f['problem']} — {f['evidence']}\n      исправить: {f['fix']}")
    print(f"\nСохранено: {a.out}/page-weight-{a.device}.json")


if __name__ == "__main__":
    main()
