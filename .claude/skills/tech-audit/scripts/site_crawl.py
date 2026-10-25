#!/usr/bin/env python3
"""Технический обход сайта (любая ниша, любая CMS). Только чтение.

Использование:
  python site_crawl.py https://example.com [--max-pages 150] [--workers 4] [--delay 0.2]
                       [--out outdir] [--ignore-robots] [--check-images 40] [--pages /a,/b]

Что делает: варианты домена, robots.txt, sitemap(ы), обход внутренних ссылок (BFS, с учётом robots),
по каждой странице — статус, цепочка редиректов, title/description/H1/canonical/robots/hreflang,
JSON-LD, изображения, ссылки, сжатие, TTFB; затем правила → findings (P0–P3).
Результат: <out>/crawl.json, <out>/findings.csv и краткий отчёт в stdout."""
import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.robotparser
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fetch, normalize, parse_html, jsonld_items, types_of  # noqa: E402
from schema_check import check_items  # noqa: E402

ASSET_EXT = re.compile(r"\.(jpg|jpeg|png|gif|webp|avif|svg|ico|css|js|pdf|zip|rar|docx?|xlsx?|pptx?|mp4|mp3|woff2?|ttf|xml|json|txt)$", re.I)
SKIP_PARAMS = re.compile(r"(utm_|gclid|yclid|fbclid|_openstat)", re.I)


def host_of(u):
    return urllib.parse.urlsplit(u).netloc.lower()


def strip_www(h):
    return h[4:] if h.startswith("www.") else h


class Finding:
    def __init__(self, fid, pr, problem, urls, evidence, fix, recheck):
        self.fid, self.pr, self.problem = fid, pr, problem
        self.urls, self.evidence, self.fix, self.recheck = urls, evidence, fix, recheck

    def row(self):
        return {"id": self.fid, "priority": self.pr, "problem": self.problem, "count": len(self.urls),
                "examples": " | ".join(self.urls[:5]), "evidence": self.evidence, "fix": self.fix,
                "recheck": self.recheck}


# ------------------------------------------------------------------ домены / robots / sitemap
def check_domains(host):
    res, findings = {}, []
    for scheme in ("http", "https"):
        for h in (host, "www." + host):
            u = f"{scheme}://{h}/"
            r = fetch(u, max_hops=0)
            res[u] = {"status": r["status"], "location": r["headers"].get("location"), "error": r["error"]}
    ok = [u for u, v in res.items() if v["status"] == 200]
    if len(ok) > 1:
        findings.append(Finding("D1", "P0", "Несколько вариантов адреса главной отдают 200 без редиректа (дубли сайта)",
                                ok, "; ".join(f"{u} → 200" for u in ok),
                                "Выбрать один канонический вариант (протокол + www/без www), остальные — 301 на него",
                                "curl -I по четырём вариантам: три из четырёх дают 301"))
    https_alive = any(v["status"] in (200, 301, 302, 308) for u, v in res.items() if u.startswith("https://"))
    http200 = [u for u, v in res.items() if u.startswith("http://") and v["status"] == 200]
    if http200 and https_alive:
        findings.append(Finding("D2", "P1", "HTTP-версия отдаёт 200 вместо редиректа на HTTPS", http200,
                                "; ".join(f"{u} → 200" for u in http200), "Включить 301-редирект на HTTPS",
                                "curl -I http://… → 301 на https"))
    temp = [u for u, v in res.items() if v["status"] in (302, 303, 307)]
    if temp:
        findings.append(Finding("D3", "P2", "Редирект варианта домена временный (302/307) вместо постоянного", temp,
                                "; ".join(f"{u} → {res[u]['status']} {res[u]['location']}" for u in temp),
                                "Заменить на 301/308", "curl -I → 301/308"))
    return res, findings


def load_sitemaps(base, robots_text, depth=0):
    urls_seen, out, problems = set(), [], []
    cands = []
    for line in (robots_text or "").splitlines():
        if line.lower().startswith("sitemap:"):
            cands.append(line.split(":", 1)[1].strip())
    if not cands:
        cands = [base + "/sitemap.xml"]
    queue = list(cands)
    while queue and len(urls_seen) < 20:
        sm = queue.pop(0)
        if sm in urls_seen:
            continue
        urls_seen.add(sm)
        r = fetch(sm, max_bytes=8_000_000)
        if r["status"] != 200:
            problems.append((sm, r["status"]))
            continue
        body = r["body"]
        if sm.endswith(".gz") or body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except Exception:
                pass
        txt = body.decode("utf-8", "ignore")
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", txt)
        if "<sitemapindex" in txt:
            queue.extend(locs)
        else:
            out.extend(locs)
    return out, list(urls_seen), problems


# ------------------------------------------------------------------ страница
def analyse_page(url, r, site_hosts):
    d = {"url": url, "status": r["status"], "final_url": r["url"], "chain": r["chain"],
         "ttfb": round(r.get("first_elapsed") or 0, 3), "bytes_html": len(r["body"]),
         "content_encoding": r["headers"].get("content-encoding"),
         "content_type": r["headers"].get("content-type", ""), "x_robots": r["headers"].get("x-robots-tag"),
         "cache_control": r["headers"].get("cache-control")}
    if r["status"] != 200 or "html" not in d["content_type"].lower():
        return d, []
    p = parse_html(r["body"])
    robots_meta = (p.meta("robots") or "").lower()
    canon = [l.get("href", "") for l in p.link_rel("canonical")]
    hreflang = [(l.get("hreflang", ""), l.get("href", "")) for l in p.link_rel("alternate") if l.get("hreflang")]
    items, bad = jsonld_items(p.jsonld_raw)
    imgs = p.imgs
    internal, external = [], []
    for a in p.anchors:
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "whatsapp:", "tg:")):
            continue
        absu = normalize(urllib.parse.urljoin(r["url"], href))
        sp = urllib.parse.urlsplit(absu)
        if sp.scheme not in ("http", "https"):
            continue
        (internal if strip_www(sp.netloc) in site_hosts else external).append(
            {"url": absu, "nofollow": "nofollow" in a["rel"].lower(), "text": re.sub(r"\s+", " ", a["text"]).strip()[:80]})
    h1 = [re.sub(r"\s+", " ", t).strip() for lv, t in p.headings if lv == 1]
    d.update({
        "title": re.sub(r"\s+", " ", p.title or "").strip() or None,
        "description": (p.meta("description") or "").strip() or None,
        "meta_robots": robots_meta or None,
        "viewport": p.meta("viewport"),
        "canonical": canon, "hreflang": hreflang, "lang": p.html_lang,
        "h1": h1, "h2_count": sum(1 for lv, _ in p.headings if lv == 2),
        "og_title": p.meta("og:title"), "og_image": p.meta("og:image"),
        "jsonld_types": sorted({t for it in items for t in types_of(it)}), "jsonld_invalid": bad,
        "jsonld_issues": check_items(items),
        "words": len(p.text().split()),
        "images": len(imgs),
        "images_no_alt": sum(1 for i in imgs if "alt" not in i),
        "images_no_size": sum(1 for i in imgs if not (i.get("width") and i.get("height"))),
        "images_lazy": sum(1 for i in imgs if i.get("loading", "").lower() == "lazy"),
        "img_srcs": [i.get("src") or i.get("data-src") or i.get("data-original") for i in imgs][:200],
        "internal_links": internal, "external_links_count": len(external),
        "external_links": [e["url"] for e in external][:100],
        "scripts_blocking_head": sum(1 for s in p.scripts if s["src"] and s["in_head"] and not s["async"]
                                     and not s["defer"] and s["type"] != "module"),
        "http_resources_on_https": [x for x in ((i.get("src") or "") for i in imgs) if x.startswith("http://")][:10]
        + [s["src"] for s in p.scripts if s["src"].startswith("http://")][:10],
    })
    return d, internal


# ------------------------------------------------------------------ правила
def build_findings(pages, base, sitemap_urls, sitemap_problems, robots, domains_f, images_bad, soft404):
    F = list(domains_f)
    https_site = base.startswith("https://")
    ok = {u: d for u, d in pages.items() if d["status"] == 200 and "title" in d and len(d["chain"]) <= 1}
    indexable = {u: d for u, d in ok.items() if "noindex" not in (d.get("meta_robots") or "")
                 and "noindex" not in (d.get("x_robots") or "").lower()}

    def urls(pred, src=None):
        return [u for u, d in (src or ok).items() if pred(d)]

    # robots
    if robots is None:
        F.append(Finding("R2", "P3", "robots.txt отсутствует", [base + "/robots.txt"], "статус ≠ 200",
                         "Добавить robots.txt: закрыть служебные пути, указать Sitemap", "GET /robots.txt → 200"))
    else:
        if robots["blocked_all"]:
            F.append(Finding("R1", "P0", "robots.txt закрывает сайт для всех роботов", [base + "/robots.txt"],
                             "Disallow: / для User-agent: *", "Убрать общий запрет", "robots_check.py: «закрыт целиком» = False"))
        if robots["asset_blocked"]:
            F.append(Finding("R3", "P1", "robots.txt закрывает CSS/JS/картинки", [base + "/robots.txt"],
                             ", ".join(robots["asset_blocked"]), "Открыть ресурсы, нужные для отображения", "robots_check.py"))
        if not robots["sitemaps"]:
            F.append(Finding("R4", "P2", "В robots.txt нет строки Sitemap", [base + "/robots.txt"], "нет директивы",
                             "Добавить `Sitemap: <полный URL>`", "robots_check.py"))
    # sitemap
    if not sitemap_urls:
        F.append(Finding("S1", "P1", "sitemap.xml не найден или пуст", [base + "/sitemap.xml"], f"{sitemap_problems}",
                         "Создать sitemap и указать в robots.txt", "GET sitemap → 200, <loc> > 0"))
    else:
        bad = [u for u in sitemap_urls if normalize(u) in pages and pages[normalize(u)]["status"] != 200]
        if bad:
            F.append(Finding("S2", "P1", "В sitemap есть URL, которые не отдают 200 (редирект/ошибка)", bad,
                             "; ".join(f"{u} → {pages[normalize(u)]['status']}" for u in bad[:3]),
                             "Оставить в sitemap только канонические URL со статусом 200", "повторный обход"))
        noidx = [u for u in sitemap_urls if normalize(u) in ok and normalize(u) not in indexable]
        if noidx:
            F.append(Finding("S3", "P1", "В sitemap есть страницы с noindex", noidx, "meta robots / X-Robots-Tag: noindex",
                             "Убрать из sitemap или снять noindex", "повторный обход"))
        sm_norm = {normalize(u) for u in sitemap_urls}
        missing = [u for u in indexable if u not in sm_norm and not urllib.parse.urlsplit(u).query]
        if missing and len(missing) > 2:
            F.append(Finding("S4", "P2", "Индексируемые страницы, найденные по ссылкам, отсутствуют в sitemap", missing,
                             f"{len(missing)} страниц", "Добавить в sitemap или закрыть, если страницы служебные", "повторный обход"))
        inbound = Counter()
        for d in pages.values():
            for l in d.get("internal_links", []):
                inbound[l["url"]] += 1
        orphans = [u for u in sm_norm if u in ok and inbound[u] == 0 and u.rstrip("/") != base.rstrip("/")]
        if orphans:
            F.append(Finding("S5", "P2", "Страницы из sitemap без внутренних ссылок (сироты)", orphans, f"{len(orphans)} страниц",
                             "Добавить ссылки из релевантных страниц и меню", "повторный обход: входящих ≥1"))
    # статусы
    srv = [u for u, d in pages.items() if d["status"] and d["status"] >= 500]
    if srv:
        F.append(Finding("P-5xx", "P0", "Страницы отвечают 5xx", srv, "; ".join(f"{u} → {pages[u]['status']}" for u in srv[:3]),
                         "Исправить ошибки сервера", "повторный запрос → 200"))
    broken = [u for u, d in pages.items() if d["status"] and 400 <= d["status"] < 500]
    if broken:
        F.append(Finding("P-4xx", "P1", "Внутренние ссылки ведут на страницы с ошибкой 4xx", broken,
                         "; ".join(f"{u} → {pages[u]['status']}" for u in broken[:3]),
                         "Исправить ссылки или вернуть страницы", "повторный обход: битых 0"))
    err = [u for u, d in pages.items() if d["status"] is None]
    if err:
        F.append(Finding("P-ERR", "P1", "Страницы недоступны (сеть/таймаут/TLS)", err, "нет ответа", "Проверить хостинг и сертификат", "повторный запрос"))
    long_chain = [u for u, d in pages.items() if len(d["chain"]) > 2]
    if long_chain:
        F.append(Finding("P-CHAIN", "P2", "Цепочки редиректов длиннее одного перехода", long_chain,
                         "; ".join(f"{len(pages[u]['chain'])-1} перехода: {u}" for u in long_chain[:3]),
                         "Ссылаться и редиректить сразу на конечный URL", "один переход"))
    redir_links = Counter()
    for d in pages.values():
        for l in d.get("internal_links", []):
            tgt = pages.get(l["url"])
            if tgt and len(tgt["chain"]) > 1:
                redir_links[l["url"]] += 1
    if redir_links:
        F.append(Finding("P-RLINK", "P3", "Внутренние ссылки ведут через редирект", list(redir_links),
                         f"{sum(redir_links.values())} ссылок на {len(redir_links)} URL", "Обновить ссылки на конечные адреса", "повторный обход"))
    if soft404:
        F.append(Finding("P-SOFT404", "P1", "Несуществующий адрес отдаёт 200 вместо 404", [soft404], f"{soft404} → 200",
                         "Настроить корректный статус 404 для несуществующих страниц", "случайный URL → 404"))
    # метаданные
    no_title = urls(lambda d: not d["title"], indexable)
    if no_title:
        F.append(Finding("M1", "P1", "Нет title", no_title, f"{len(no_title)} стр.", "Задать уникальный title", "crawl: title не пуст"))
    for field, fid, pr, name in (("title", "M2", "P2", "title"), ("description", "M4", "P2", "description")):
        groups = defaultdict(list)
        for u, d in indexable.items():
            if d.get(field):
                groups[d[field].strip().lower()].append(u)
        dup = [u for g in groups.values() if len(g) > 1 for u in g]
        if dup:
            F.append(Finding(fid, pr, f"Дубли {name} у разных страниц", dup, f"{len(dup)} страниц делят одинаковые значения",
                             f"Сделать {name} уникальными под намерение страницы", "crawl: дублей 0"))
    no_desc = urls(lambda d: not d["description"], indexable)
    if no_desc:
        F.append(Finding("M3", "P2", "Нет meta description", no_desc, f"{len(no_desc)} стр.", "Добавить уникальное описание с ценностью для клика", "crawl"))
    long_t = urls(lambda d: d["title"] and len(d["title"]) > 70, indexable)
    if long_t:
        F.append(Finding("M5", "P3", "Очень длинный title (ориентир, не требование поисковиков)", long_t,
                         f"{len(long_t)} стр. > 70 символов", "Сократить, ключевое — в начало", "crawl"))
    no_h1 = urls(lambda d: not d["h1"], indexable)
    if no_h1:
        F.append(Finding("M6", "P2", "Нет H1", no_h1, f"{len(no_h1)} стр.", "Добавить один H1 по теме страницы", "crawl"))
    multi_h1 = urls(lambda d: len(d["h1"]) > 1, indexable)
    if multi_h1:
        F.append(Finding("M7", "P3", "Несколько H1", multi_h1, f"{len(multi_h1)} стр.", "Оставить один H1", "crawl"))
    # canonical / индексация
    no_can = urls(lambda d: not d["canonical"], indexable)
    if no_can:
        F.append(Finding("C1", "P2", "Нет canonical", no_can, f"{len(no_can)} стр.", "Добавить self-canonical на канонический URL", "crawl"))
    if https_site:
        http_can = urls(lambda d: d["canonical"] and d["canonical"][0].startswith("http://"), indexable)
        if http_can:
            F.append(Finding("C2", "P1", "canonical указывает на http:// на сайте с https", http_can, "canonical с http", "Исправить на https", "crawl"))
    multi_can = urls(lambda d: len(d["canonical"]) > 1, indexable)
    if multi_can:
        F.append(Finding("C3", "P1", "Несколько canonical на странице", multi_can, "более одного <link rel=canonical>", "Оставить один", "crawl"))
    other_can = []
    for u, d in indexable.items():
        if d["canonical"]:
            c = normalize(urllib.parse.urljoin(u, d["canonical"][0]))
            if c != u and c.rstrip("/") != u.rstrip("/"):
                other_can.append(u)
    if other_can:
        F.append(Finding("C4", "P2", "Canonical указывает на другой URL (проверить, что это намеренно)", other_can,
                         f"{len(other_can)} стр.", "Проверить: дубли → ок; уникальные страницы → поставить self-canonical", "crawl"))
    noindex_linked = urls(lambda d: "noindex" in (d.get("meta_robots") or ""), ok)
    if noindex_linked:
        F.append(Finding("C5", "P3", "Страницы с noindex (проверить, что они служебные)", noindex_linked,
                         f"{len(noindex_linked)} стр.", "Должны быть noindex только служебные страницы", "crawl"))
    # язык / мобильная
    no_vp = urls(lambda d: not d["viewport"], indexable)
    if no_vp:
        F.append(Finding("X1", "P1", "Нет meta viewport (мобильная версия)", no_vp, f"{len(no_vp)} стр.",
                         "Добавить <meta name=viewport content='width=device-width, initial-scale=1'>", "crawl"))
    no_lang = urls(lambda d: not d["lang"], indexable)
    if no_lang:
        F.append(Finding("X2", "P3", "Не задан lang у <html>", no_lang, f"{len(no_lang)} стр.", "Указать язык страницы", "crawl"))
    bad_hl = []
    for u, d in indexable.items():
        if d["hreflang"] and not any(normalize(urllib.parse.urljoin(u, h)) == u for _, h in d["hreflang"]):
            bad_hl.append(u)
    if bad_hl:
        F.append(Finding("X3", "P2", "hreflang без ссылки страницы на саму себя", bad_hl, f"{len(bad_hl)} стр.",
                         "Добавить self-hreflang и обратные ссылки", "crawl"))
    # контент / структура
    thin = urls(lambda d: d["words"] < 150, indexable)
    if thin:
        F.append(Finding("T1", "P3", "Мало текста (<150 слов) на индексируемых страницах", thin, f"{len(thin)} стр.",
                         "Проверить, закрывает ли страница намерение; при нехватке — дополнить по существу", "crawl"))
    deep = [u for u, d in indexable.items() if d.get("depth", 0) > 3]
    if deep:
        F.append(Finding("T2", "P2", "Страницы глубже 3 кликов от главной", deep, f"{len(deep)} стр.",
                         "Добавить ссылки из разделов и меню", "crawl: depth ≤ 3"))
    # изображения
    noalt = urls(lambda d: d["images_no_alt"] > 0, indexable)
    if noalt:
        tot = sum(ok[u]["images_no_alt"] for u in noalt)
        F.append(Finding("I1", "P3", "Изображения без атрибута alt", noalt, f"{tot} изображений",
                         "Описательный alt у содержательных картинок, alt='' у декоративных", "crawl"))
    nosize = urls(lambda d: d["images_no_size"] > 0, indexable)
    if nosize:
        F.append(Finding("I2", "P3", "Изображения без width/height (риск сдвигов макета, CLS)", nosize,
                         f"{sum(ok[u]['images_no_size'] for u in nosize)} изображений", "Задать размеры или aspect-ratio", "speed-audit: CLS"))
    if images_bad:
        F.append(Finding("I3", "P1", "Битые изображения (4xx/5xx)", [u for u, _ in images_bad],
                         "; ".join(f"{u} → {s}" for u, s in images_bad[:3]), "Исправить пути или заменить файлы", "повторный обход"))
    # безопасность контента
    if https_site:
        mixed = urls(lambda d: d["http_resources_on_https"], ok)
        if mixed:
            F.append(Finding("H1", "P1", "Смешанный контент: http-ресурсы на https-странице", mixed, "src=http://…",
                             "Перевести ресурсы на https", "crawl"))
    # скорость ответа и сжатие
    slow = urls(lambda d: d["ttfb"] > 1.5, ok)
    if slow:
        F.append(Finding("V1", "P2", "Медленный ответ сервера (>1.5 с до первого байта, замер из места запуска)", slow,
                         "; ".join(f"{u}: {ok[u]['ttfb']} с" for u in slow[:3]),
                         "Кэш страниц, CDN, оптимизация бэкенда. Подтвердить замером speed-audit и полевыми данными", "speed-audit"))
    nocomp = urls(lambda d: d["bytes_html"] > 15000 and not d["content_encoding"], ok)
    if nocomp:
        F.append(Finding("V2", "P2", "HTML отдаётся без сжатия (gzip/br)", nocomp, "нет Content-Encoding",
                         "Включить brotli/gzip на сервере или CDN", "curl -H 'Accept-Encoding: gzip' -I"))
    blocking = urls(lambda d: d["scripts_blocking_head"] >= 3, ok)
    if blocking:
        F.append(Finding("V3", "P3", "Много блокирующих скриптов в <head> (≥3 без async/defer)", blocking,
                         f"{len(blocking)} стр.", "defer/async или перенос вниз; подробности — speed-audit", "speed-audit"))
    # JSON-LD
    badld = urls(lambda d: d["jsonld_invalid"] > 0, ok)
    if badld:
        F.append(Finding("L1", "P2", "Невалидный JSON-LD", badld, f"{len(badld)} стр.", "Исправить синтаксис JSON", "schema_check.py"))
    ld_issues = defaultdict(list)
    for u, d in ok.items():
        for i in d.get("jsonld_issues", []):
            ld_issues[(i["level"], i["msg"])].append(u)
    for (lvl, msg), us in ld_issues.items():
        F.append(Finding("L2", lvl if lvl in ("P1", "P2") else "P3", f"Разметка: {msg}", us, f"{len(us)} стр.",
                         "Дополнить/исправить разметку так, чтобы она соответствовала видимому тексту", "schema_check.py + валидатор"))
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    F.sort(key=lambda f: (order[f.pr], f.fid))
    return F


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--out", default="audit-out")
    ap.add_argument("--ignore-robots", action="store_true")
    ap.add_argument("--check-images", type=int, default=40)
    ap.add_argument("--pages", help="список путей через запятую (вместо обхода)")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    os.makedirs(a.out, exist_ok=True)
    start = normalize(a.url if "://" in a.url else "https://" + a.url)
    host = strip_www(host_of(start))

    print(f"== Домены ({host})")
    domains, dom_f = check_domains(host)
    for u, v in domains.items():
        print(f"  {u:32} {v['status']} {v['location'] or ''}")

    first = fetch(start)
    base = urllib.parse.urlunsplit(urllib.parse.urlsplit(first["url"])[:2] + ("", "", ""))
    site_hosts = {host}
    rb = fetch(base + "/robots.txt")
    robots_text = rb["body"].decode("utf-8", "ignore") if rb["status"] == 200 else None
    from robots_check import analyse
    robots = analyse(base, robots_text) if robots_text is not None else None
    rp = urllib.robotparser.RobotFileParser()
    if robots_text:
        rp.parse(robots_text.splitlines())

    sm_urls, sm_files, sm_problems = load_sitemaps(base, robots_text)
    print(f"== robots.txt: {'есть' if robots else 'нет'}; sitemap: {len(sm_urls)} URL в {len(sm_files)} файл(ах)")

    pages, depth, queue = {}, {}, []
    seeds = [normalize(urllib.parse.urljoin(base + "/", p)) for p in a.pages.split(",")] if a.pages else [normalize(first["url"])]
    for s in seeds:
        depth[s] = 0
        queue.append(s)
    if not a.pages:
        for u in sm_urls[: a.max_pages]:
            n = normalize(u)
            if n not in depth:
                depth[n] = 1  # из sitemap, глубина по ссылкам уточнится
                queue.append(n)
    blocked = []
    t0 = time.time()
    while queue and len(pages) < a.max_pages:
        batch, queue = queue[: a.workers * 4], queue[a.workers * 4:]
        batch = [u for u in batch if u not in pages]
        todo = []
        for u in batch:
            if not a.ignore_robots and robots_text and not rp.can_fetch("*", u):
                blocked.append(u)
                pages[u] = {"url": u, "status": None, "blocked_by_robots": True, "chain": [], "title": None}
                continue
            todo.append(u)

        def work(u):
            time.sleep(a.delay)
            r = fetch(u)
            return u, r
        with ThreadPoolExecutor(a.workers) as ex:
            results = list(ex.map(work, todo))
        for u, r in results:
            d, internal = analyse_page(u, r, site_hosts)
            d["depth"] = depth.get(u, 0)
            pages[u] = d
            fin = normalize(r["url"])
            if fin != u and fin not in pages and strip_www(host_of(fin)) in site_hosts:
                pass
            for l in internal:
                t = l["url"]
                if ASSET_EXT.search(urllib.parse.urlsplit(t).path):
                    continue
                if SKIP_PARAMS.search(urllib.parse.urlsplit(t).query):
                    continue
                if t not in depth:
                    depth[t] = d["depth"] + 1
                    queue.append(t)
                elif t in pages and pages[t].get("depth", 0) > d["depth"] + 1:
                    pages[t]["depth"] = d["depth"] + 1
        queue.sort(key=lambda x: depth.get(x, 9))
    pages = {u: d for u, d in pages.items() if not d.get("blocked_by_robots")} | \
            {u: d for u, d in pages.items() if d.get("blocked_by_robots")}

    # soft 404
    s404 = fetch(base + "/seo-geo-audit-404-" + str(int(time.time())))
    soft404 = s404["url"] if s404["status"] == 200 else None

    # проверка части изображений (HEAD)
    images_bad, checked = [], set()
    cands = []
    for u, d in pages.items():
        for s in d.get("img_srcs", []) or []:
            if s and not s.startswith("data:"):
                absu = normalize(urllib.parse.urljoin(u, s))
                if absu not in checked and absu.startswith("http"):
                    checked.add(absu)
                    cands.append(absu)
    for img in cands[: a.check_images]:
        r = fetch(img, method="HEAD", max_hops=3)
        if r["status"] and r["status"] >= 400:
            images_bad.append((img, r["status"]))

    findings = build_findings({u: d for u, d in pages.items() if not d.get("blocked_by_robots")}, base, sm_urls,
                              sm_problems, robots, dom_f, images_bad, soft404)
    rep = {"started": start, "base": base, "date": time.strftime("%Y-%m-%d"), "elapsed_s": round(time.time() - t0, 1),
           "domains": domains, "robots": robots, "sitemap_urls": sm_urls, "sitemap_files": sm_files,
           "blocked_by_robots": blocked, "pages": pages, "findings": [f.row() for f in findings]}
    with open(os.path.join(a.out, "crawl.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "findings.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "priority", "problem", "count", "examples", "evidence", "fix", "recheck"])
        w.writeheader()
        for f in findings:
            w.writerow(f.row())

    ok_n = sum(1 for d in pages.values() if d["status"] == 200)
    print(f"\n== Обход: {len(pages)} URL ({ok_n} со статусом 200), {len(blocked)} закрыто robots, {rep['elapsed_s']} с")
    print("   Охват ограничен --max-pages; это не весь сайт, если страниц больше." if len(pages) >= a.max_pages else "")
    print("\n== Находки (по приоритету)")
    for f in findings:
        print(f"[{f.pr}] {f.fid:9} {f.problem}  — {len(f.urls)} URL")
        print(f"        пример: {f.urls[0] if f.urls else '-'}")
        print(f"        исправить: {f.fix}")
    if not findings:
        print("  (находок нет — это не гарантия: проверьте охват и недоступные источники)")
    print(f"\nСохранено: {a.out}/crawl.json, {a.out}/findings.csv")
    print("НЕ проверено этим скриптом: индексация в поисковиках (Вебмастер/GSC), полевые CWV, рендеринг JS, серверные логи.")


if __name__ == "__main__":
    main()
