#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_check.py: сравнение «сырого» HTML и страницы после выполнения JavaScript (Chromium).

Зачем: site_crawl.py не исполняет JS. На сайтах, где контент, ссылки, title, canonical или разметка
появляются только после JS, обычный обход видит пустую страницу. Скрипт показывает расхождения.

Запуск:
  python render_check.py https://site.ru --max-pages 10 --out audit-out
  python render_check.py https://site.ru --pages /,/catalog,/contacts

Только чтение (GET). Нужны: pip install playwright и Chromium. Путь к браузеру: CHROME_PATH
(иначе используется браузер Playwright по умолчанию).

Границы: рендеринг идёт из места запуска, без авторизации и без региональных подстановок. Googlebot, Яндекс
и ИИ-боты исполняют JS по-разному и с задержкой; скрипт показывает, что видит Chromium, а не что проиндексировано.
Коды находок: J1–J8."""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.robotparser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402


def strip_www(h):
    return h.lower()[4:] if h.lower().startswith("www.") else h.lower()


def snapshot(body):
    """Компактный слепок страницы из HTML."""
    p = common.parse_html(body)
    canon = [l.get("href", "") for l in p.link_rel("canonical")]
    items, _ = common.jsonld_items(p.jsonld_raw)
    h1 = [re.sub(r"\s+", " ", t).strip() for lv, t in p.headings if lv == 1]
    links = set()
    return {
        "title": re.sub(r"\s+", " ", p.title or "").strip() or None,
        "description": (p.meta("description") or "").strip() or None,
        "robots": (p.meta("robots") or "").lower() or None,
        "canonical": canon[0] if canon else None,
        "h1": [x for x in h1 if x],
        "words": len(p.text().split()),
        "types": sorted({t for it in items for t in common.types_of(it)}),
        "anchors": [a["href"].strip() for a in p.anchors],
        "_links": links,
    }


def internal_links(snap, base_url, site_host):
    out = set()
    for href in snap["anchors"]:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "tg:", "whatsapp:")):
            continue
        absu = common.normalize(urllib.parse.urljoin(base_url, href))
        sp = urllib.parse.urlsplit(absu)
        if sp.scheme in ("http", "https") and strip_www(sp.netloc) == site_host:
            out.add(absu)
    return out


def compare(url, raw, rend, raw_links, rend_links):
    """Возвращает список находок по паре слепков."""
    F = []

    def add(code, p, title, evidence, fix, conf="высокая"):
        F.append({"code": code, "priority": p, "url": url, "title": title, "evidence": evidence,
                  "fix": fix, "confidence": conf})

    # J1: контент только после JS
    if raw["words"] < 50 and rend["words"] >= max(150, raw["words"] * 3):
        add("J1", "P1", "Основной текст появляется только после JavaScript",
            "слов в сыром HTML: %d, после рендеринга: %d" % (raw["words"], rend["words"]),
            "Отдавать основной контент в HTML (серверный рендеринг или предварительная генерация); проверить, что поисковики "
            "и ИИ-боты видят его без JS. Проверка: просмотр кода страницы и «страница как её видит робот» в Вебмастере/GSC.")
    # J2: ссылки только после JS
    only = rend_links - raw_links
    if len(only) >= 5 and len(only) >= 0.5 * max(1, len(rend_links)):
        add("J2", "P1", "Внутренние ссылки видны только после JavaScript",
            "в сыром HTML %d внутренних ссылок, после рендеринга %d (только после JS: %d, например %s)"
            % (len(raw_links), len(rend_links), len(only), ", ".join(sorted(only)[:3])),
            "Делать навигацию и ссылки на разделы обычными `<a href>` в исходном HTML; страницы, до которых бот доходит только "
            "через JS, могут находиться позже или не находиться.")
    # J3: title/description/h1 отсутствуют в сыром, появляются после JS
    miss = []
    for key, label in (("title", "title"), ("description", "meta description")):
        if not raw[key] and rend[key]:
            miss.append(label)
    if not raw["h1"] and rend["h1"]:
        miss.append("h1")
    if miss:
        add("J3", "P1", "Элементы появляются только после JavaScript: " + ", ".join(miss),
            "в сыром HTML пусто, после рендеринга заполнено",
            "Задавать title, description и h1 на сервере. Яндекс и другие системы могут не дождаться JS.")
    # J7: значения различаются
    diff = []
    for key, label in (("title", "title"), ("description", "meta description")):
        if raw[key] and rend[key] and raw[key] != rend[key]:
            diff.append("%s: «%s» → «%s»" % (label, raw[key][:60], rend[key][:60]))
    if raw["h1"] and rend["h1"] and raw["h1"] != rend["h1"]:
        diff.append("h1: «%s» → «%s»" % (raw["h1"][0][:60], rend["h1"][0][:60]))
    if diff:
        add("J7", "P2", "JavaScript меняет заголовок или описание", "; ".join(diff),
            "Привести значения в исходном HTML к итоговым, чтобы разные системы видели одно и то же. "
            "Если замена намеренная (подстановка региона), проверить, какую версию видит робот.", "средняя")
    # J4/J5: robots и canonical
    rr, dr = raw["robots"] or "", rend["robots"] or ""
    if "noindex" in rr and "noindex" not in dr:
        add("J4", "P0", "noindex в исходном HTML снимается JavaScript",
            "meta robots в сыром HTML: «%s», после рендеринга: «%s»" % (rr, dr or "нет"),
            "Не полагаться на JS: часть систем может не исполнять скрипт и увидит noindex. Решить, должна ли страница индексироваться, "
            "и задать директиву в исходном HTML (проверить, не staging ли это).")
    elif "noindex" in dr and "noindex" not in rr:
        add("J4", "P1", "noindex добавляется JavaScript",
            "meta robots в сыром HTML: «%s», после рендеринга: «%s»" % (rr or "нет", dr),
            "Проверить намеренность. Если страница должна индексироваться, убрать директиву из скрипта.", "средняя")
    if raw["canonical"] != rend["canonical"] and (raw["canonical"] or rend["canonical"]):
        add("J5", "P1", "canonical отличается после JavaScript",
            "сырой HTML: %s, после рендеринга: %s" % (raw["canonical"] or "нет", rend["canonical"] or "нет"),
            "Задавать canonical один раз в исходном HTML; разные значения до и после JS дают системам разные сигналы.")
    # J6: разметка только после JS
    ld_only = sorted(set(rend["types"]) - set(raw["types"]))
    if ld_only:
        add("J6", "P2", "Структурированные данные появляются только после JavaScript",
            "типы только после рендеринга: " + ", ".join(ld_only),
            "Google обрабатывает JSON-LD после рендеринга, но другие системы и ИИ-боты могут не исполнять JS; "
            "по возможности отдавать JSON-LD в исходном HTML.", "средняя")
    return F


def render_pages(urls, wait_ms, user_agent=None):
    """Рендерит список URL, возвращает {url: (html, final_url, error)}."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Нужен playwright: pip install playwright (и Chromium). Без него рендеринг не выполняется.")
    res = {}
    with sync_playwright() as pw:
        kw = {}
        exe = os.environ.get("CHROME_PATH")
        if exe and os.path.exists(exe):
            kw["executable_path"] = exe
        browser = pw.chromium.launch(args=["--no-sandbox"], **kw)
        ctx = browser.new_context(user_agent=user_agent or common.UA, locale="ru-RU")
        for u in urls:
            page = ctx.new_page()
            try:
                page.goto(u, wait_until="load", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=wait_ms)
                except Exception:
                    pass
                res[u] = (page.content(), page.url, None)
            except Exception as e:
                res[u] = (None, None, str(e)[:200])
            finally:
                page.close()
        browser.close()
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--pages", help="пути через запятую (вместо обхода)")
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--wait", type=int, default=8000, help="ожидание затишья сети после загрузки, мс")
    ap.add_argument("--out", default="audit-out")
    ap.add_argument("--ignore-robots", action="store_true")
    a = ap.parse_args()

    base = a.url if "://" in a.url else "https://" + a.url
    host = strip_www(urllib.parse.urlsplit(base).netloc)
    rp = urllib.robotparser.RobotFileParser()
    try:
        rr = common.fetch(base.rstrip("/") + "/robots.txt")
        rp.parse(rr["body"].decode("utf-8", "ignore").splitlines() if rr["status"] == 200 else [])
    except Exception:
        rp.parse([])

    def allowed(u):
        return a.ignore_robots or rp.can_fetch(common.UA, u)

    if a.pages:
        queue = [common.normalize(urllib.parse.urljoin(base, p.strip())) for p in a.pages.split(",") if p.strip()]
    else:
        queue = [common.normalize(base)]
    seen, results, findings, skipped = set(), [], [], []
    while queue and len(seen) < a.max_pages:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        if not allowed(u):
            skipped.append(u)
            continue
        raw = common.fetch(u)
        if raw["status"] != 200 or "html" not in raw["headers"].get("content-type", "").lower():
            results.append({"url": u, "status": raw["status"], "note": "не HTML 200: сравнение пропущено"})
            continue
        html, final, err = render_pages([u], a.wait)[u]
        if err:
            results.append({"url": u, "status": 200, "note": "рендеринг не удался: " + err})
            findings.append({"code": "J8", "priority": "P2", "url": u, "title": "Страница не отрисовалась в Chromium",
                             "evidence": err, "fix": "Проверить ошибки скриптов и доступность ресурсов; повторить запуск.",
                             "confidence": "средняя"})
            continue
        s_raw, s_rend = snapshot(raw["body"]), snapshot(html.encode("utf-8"))
        l_raw = internal_links(s_raw, raw["url"], host)
        l_rend = internal_links(s_rend, final or raw["url"], host)
        findings.extend(compare(u, s_raw, s_rend, l_raw, l_rend))
        results.append({"url": u, "status": 200, "raw_words": s_raw["words"], "rendered_words": s_rend["words"],
                        "raw_links": len(l_raw), "rendered_links": len(l_rend)})
        if not a.pages:
            for link in sorted(l_rend | l_raw):
                if link not in seen and link not in queue:
                    queue.append(link)

    os.makedirs(a.out, exist_ok=True)
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    findings.sort(key=lambda f: (order[f["priority"]], f["code"]))
    with open(os.path.join(a.out, "render-check.json"), "w", encoding="utf-8") as fh:
        json.dump({"pages": results, "findings": findings, "skipped_by_robots": skipped}, fh, ensure_ascii=False, indent=2)
    md = ["# Сравнение HTML до и после JavaScript", "",
          "## Границы",
          "- Проверено страниц: %d (лимит %d). Рендеринг в Chromium из места запуска, без авторизации и региональных подстановок." % (len(results), a.max_pages),
          "- Не проверено: что реально проиндексировали Яндекс и Google, как рендерят страницы их роботы и ИИ-боты. Проверка: «страница как её видит робот» в Вебмастере и GSC.",
          "- Пропущено по robots.txt: %d." % len(skipped), "",
          "## Страницы", "", "| URL | слов в HTML | слов после JS | ссылок в HTML | ссылок после JS |", "|---|---|---|---|---|"]
    for r in results:
        if "raw_words" in r:
            md.append("| %s | %d | %d | %d | %d |" % (r["url"], r["raw_words"], r["rendered_words"], r["raw_links"], r["rendered_links"]))
        else:
            md.append("| %s | — | — | — | — (%s) |" % (r["url"], r.get("note", "")))
    md += ["", "## Находки", ""]
    if not findings:
        md.append("Расхождений между HTML до и после JS не найдено на проверенных страницах. Это не доказывает, что всё видно роботам.")
    for f in findings:
        md += ["### %s %s: %s" % (f["priority"], f["code"], f["title"]), "- URL: " + f["url"],
               "- Доказательство: " + f["evidence"], "- Исправление: " + f["fix"], "- Уверенность: " + f["confidence"],
               "- Повторная проверка: тот же запуск после правки.", ""]
    with open(os.path.join(a.out, "render-report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    print("Страниц: %d, находок: %d. Отчёт: %s" % (len(results), len(findings), os.path.join(a.out, "render-report.md")))


if __name__ == "__main__":
    main()
