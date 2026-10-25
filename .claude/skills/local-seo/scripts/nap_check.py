#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка согласованности NAP (название, адрес, телефон) на сайте. Только чтение.

Использование:
  python nap_check.py https://example.com [--pages /contacts,/about] [--max-pages 12] [--out dir]

Что делает: берёт главную, типовые страницы контактов и первые страницы из sitemap; собирает телефоны
(tel: и в тексте), e-mail, адресоподобные строки и данные JSON-LD (Organization/LocalBusiness);
сравнивает страницы между собой и с разметкой. Не знает, какие адреса и телефоны настоящие: расхождения
выводятся как вопросы владельцу. Карточки на картах и в справочниках проверяются вручную (чек-лист в отчёте).
Использует функции из tech-audit/scripts/common.py (рядом в репозитории или SEO_TECH_AUDIT_SCRIPTS)."""
import argparse
import json
import os
import re
import sys
import urllib.parse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (os.environ.get("SEO_TECH_AUDIT_SCRIPTS"), os.path.join(HERE, "..", "..", "tech-audit", "scripts"),
             os.path.join(HERE, "..", "tech-audit", "scripts"), HERE):
    if cand and os.path.exists(os.path.join(cand, "common.py")):
        sys.path.insert(0, cand)
        break
try:
    from common import fetch, normalize, parse_html, jsonld_items, types_of  # noqa: E402
except ImportError:
    raise SystemExit("Не найден tech-audit/scripts/common.py. Положите модули рядом "
                     "(.claude/skills/tech-audit) или задайте SEO_TECH_AUDIT_SCRIPTS.")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CONTACT_PATHS = ["/contacts", "/contact", "/kontakty", "/kontakti", "/about", "/o-kompanii", "/contacts/", "/contact-us"]
PHONE_RE = re.compile(r"(?<![\d.])(?:\+?\d{1,3}[\s\-]?)?(?:\(\d{3,5}\)|\d{3,5})[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}(?![\d])")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
STREET_RE = re.compile(r"(?:ул\.|улица|пр-т|пр\.|проспект|пер\.|переулок|шоссе|ш\.|наб\.|набережная|б-р|бульвар|пл\.|площадь)"
                       r"\s*[А-ЯЁа-яё0-9\-\. ]{2,40}?,?\s*(?:д\.?|дом)?\s*\d+[а-яА-Я]?(?:[/\-]\d+[а-яА-Я]?)?", re.I)
LD_BUSINESS = {"organization", "localbusiness", "store", "professionalservice", "homeandconstructioncontractor",
               "automotivebusiness", "foodestablishment", "medicalbusiness", "dentist", "legalservice", "realestateagent"}


def norm_phone(raw):
    d = re.sub(r"\D", "", raw)
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return d


def phone_ok(raw):
    d = re.sub(r"\D", "", raw)
    if not 10 <= len(d) <= 13:
        return False
    if re.fullmatch(r"(19|20)\d{2}[\s.\-]\d{2}[\s.\-]\d{2}", raw.strip()):
        return False
    return ("+" in raw or "(" in raw or "-" in raw or " " in raw.strip())


def page_data(url):
    r = fetch(url)
    if r["status"] != 200 or "html" not in r["headers"].get("content-type", "").lower():
        return None
    p = parse_html(r["body"])
    text = p.text()
    tel_links = {norm_phone(a["href"][4:]) for a in p.anchors if a["href"].lower().startswith("tel:")}
    mail_links = {a["href"][7:].split("?")[0].lower() for a in p.anchors if a["href"].lower().startswith("mailto:")}
    visible = {}
    for m in PHONE_RE.finditer(text):
        raw = m.group(0).strip()
        if phone_ok(raw):
            visible.setdefault(norm_phone(raw), set()).add(raw)
    emails = {e.lower().strip(".") for e in EMAIL_RE.findall(text)} | mail_links
    addresses = {re.sub(r"\s+", " ", m.group(0)).strip(" ,.").lower() for m in STREET_RE.finditer(text)}
    items, bad = jsonld_items(p.jsonld_raw)
    ld = []
    for it in items:
        ts = {t.lower() for t in types_of(it)}
        if ts & LD_BUSINESS:
            addr = it.get("address")
            if isinstance(addr, dict):
                addr = ", ".join(str(addr.get(k, "")) for k in ("streetAddress", "addressLocality") if addr.get(k))
            ld.append({"types": sorted(types_of(it)), "name": it.get("name"), "telephone": it.get("telephone"),
                       "email": it.get("email"), "address": addr, "url": it.get("url"), "sameAs": it.get("sameAs"),
                       "opening": it.get("openingHours") or it.get("openingHoursSpecification")})
    return {"url": url, "title": (p.title or "").strip(), "tel_links": sorted(tel_links),
            "visible_phones": {k: sorted(v) for k, v in visible.items()}, "emails": sorted(emails),
            "addresses": sorted(addresses), "ld": ld, "ld_invalid": bad}


def analyse(pages):
    F = []

    def add(pr, fid, msg, ev, ask):
        F.append({"priority": pr, "id": fid, "problem": msg, "evidence": ev, "ask_owner": ask})
    phones = defaultdict(lambda: {"pages": set(), "forms": set()})
    for pg in pages:
        for n, forms in pg["visible_phones"].items():
            phones[n]["pages"].add(pg["url"])
            phones[n]["forms"].update(forms)
    addrs = defaultdict(set)
    for pg in pages:
        for a in pg["addresses"]:
            addrs[a].add(pg["url"])
    all_emails = {e for pg in pages for e in pg["emails"]}
    if not phones and not all_emails:
        add("P1", "N1", "На проверенных страницах не найдено ни телефона, ни e-mail", f"{len(pages)} стр.",
            "Где на сайте указаны контакты? Есть ли страница контактов?")
    if len(phones) > 1:
        add("P2", "N2", "На сайте несколько разных телефонов",
            "; ".join(f"{'/'.join(sorted(v['forms']))} — {len(v['pages'])} стр." for v in phones.values()),
            "Какой телефон основной и для чего нужны остальные (регион, отдел, мессенджер)?")
    for n, v in phones.items():
        if len(v["forms"]) > 1:
            add("P3", "N3", "Один номер записан в разных форматах", f"{n}: " + " | ".join(sorted(v["forms"])),
                "Принять единый формат записи номера")
    for pg in pages:
        notlinked = [n for n in pg["visible_phones"] if n not in pg["tel_links"]]
        if notlinked:
            add("P2", "N4", "Телефон в тексте без ссылки tel: (на мобильных не нажимается)", f"{pg['url']}: {', '.join(notlinked)}",
                "Сделать номера кликабельными")
    if len(addrs) > 1:
        add("P2", "N5", "На страницах разные адреса (или разные написания одного)",
            "; ".join(f"«{a}» — {len(u)} стр." for a, u in addrs.items()),
            "Сколько точек у компании и какова роль каждой (офис, производство, выдача, склад)? Единое написание адреса.")
    ld_all = [(pg["url"], x) for pg in pages for x in pg["ld"]]
    if not ld_all:
        add("P2", "N6", "Нет разметки Organization/LocalBusiness на проверенных страницах", f"{len(pages)} стр.",
            "Добавить разметку с реальными названием, адресом и телефоном (только то, что видно на сайте)")
    else:
        ld_phones = {norm_phone(x["telephone"]) for _, x in ld_all if x.get("telephone")}
        visible_all = set(phones)
        if ld_phones and visible_all and not (ld_phones & visible_all):
            add("P1", "N7", "Телефон в разметке не совпадает ни с одним телефоном на страницах",
                f"в разметке: {sorted(ld_phones)}; на страницах: {sorted(visible_all)}", "Какой номер верный?")
        for _, x in ld_all:
            t = str(x.get("telephone") or "")
            if re.search(r"\[|ваш|example|xxx", t + str(x.get("name") or "") + str(x.get("address") or ""), re.I):
                add("P1", "N8", "В разметке шаблонные значения", json.dumps(x, ensure_ascii=False)[:200], "Заполнить реальными данными")
                break
        names = {str(x.get("name")).strip() for _, x in ld_all if x.get("name")}
        if len(names) > 1:
            add("P2", "N9", "Название организации в разметке различается", "; ".join(sorted(names)),
                "Принять единое название (бренд и юрлицо — разные поля)")
        ld_addr = {str(x.get("address")).strip().lower() for _, x in ld_all if x.get("address")}
        if ld_addr and addrs and not any(any(a.split(",")[0] in la for la in ld_addr) for a in addrs):
            add("P2", "N10", "Адрес в разметке не найден среди адресов на страницах",
                f"в разметке: {sorted(ld_addr)}; на страницах: {sorted(addrs)}", "Сверить написание адреса")
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    F.sort(key=lambda f: (order[f["priority"]], f["id"]))
    return F, {"phones": {k: {"forms": sorted(v["forms"]), "pages": sorted(v["pages"])} for k, v in phones.items()},
               "addresses": {k: sorted(v) for k, v in addrs.items()}, "emails": sorted(all_emails)}


def discover(base, extra, max_pages):
    urls = [base + "/"]
    urls += [base + p for p in CONTACT_PATHS]
    for p in extra:
        urls.append(urllib.parse.urljoin(base + "/", p))
    r = fetch(base + "/sitemap.xml")
    if r["status"] == 200:
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r["body"].decode("utf-8", "ignore"))
        urls += [l for l in locs if not l.endswith(".xml")][:max_pages]
    seen, out = set(), []
    for u in urls:
        n = normalize(u)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


CHECKLIST = [
    "Карточка в Яндекс Бизнес: название, адрес, телефон, категории, часы, ссылка на сайт совпадают с сайтом",
    "2ГИС и Google Business Profile (если есть): те же данные",
    "Отзывы на картах реальные; в разметку Review не копировать без основания",
    "Каждая точка (офис, производство, выдача) названа так, как её роль подтверждена владельцем",
    "Региональные подстановки по UTM/геолокации не меняют версию страницы для роботов",
    "Юрлицо, ИНН, режим работы в футере и на странице контактов совпадают",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--pages", default="")
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument("--out", default="audit-out")
    a = ap.parse_args()
    base = a.url.rstrip("/")
    if "://" not in base:
        base = "https://" + base
    pages = []
    for u in discover(base, [p for p in a.pages.split(",") if p], a.max_pages)[: a.max_pages + len(CONTACT_PATHS) + 1]:
        d = page_data(u)
        if d:
            pages.append(d)
    findings, agg = analyse(pages)
    os.makedirs(a.out, exist_ok=True)
    rep = {"base": base, "pages_checked": [p["url"] for p in pages], "aggregate": agg, "findings": findings,
           "manual_checklist": CHECKLIST}
    with open(os.path.join(a.out, "nap.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    print(f"Проверено страниц: {len(pages)}")
    print("Телефоны:", {k: v["forms"] for k, v in agg["phones"].items()} or "не найдены")
    print("Адреса:", list(agg["addresses"]) or "не найдены")
    print("E-mail:", agg["emails"] or "не найдены")
    for f in findings:
        print(f"[{f['priority']}] {f['id']} {f['problem']}\n      {f['evidence']}\n      вопрос владельцу: {f['ask_owner']}")
    print("\nНе проверено скриптом (вручную):")
    for c in CHECKLIST:
        print(" -", c)
    print(f"\nСохранено: {a.out}/nap.json")


if __name__ == "__main__":
    main()
