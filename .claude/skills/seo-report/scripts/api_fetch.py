#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api_fetch.py: выгрузка запросов и страниц из Google Search Console и Яндекс Вебмастера по API в CSV,
которые читает period_compare.py. Только чтение (GET/POST запросов статистики). Только стандартная библиотека.

Google Search Console (токен OAuth с правом webmasters.readonly):
  GSC_ACCESS_TOKEN=... python api_fetch.py gsc --site sc-domain:example.com --from 2026-09-01 --to 2026-09-28 \
      --dim query --out gsc-queries-A.csv
  --dim page выгружает страницы. Срез: --country rus, --device MOBILE, --type web.

Яндекс Вебмастер (OAuth-токен с доступом к API Вебмастера):
  YWM_TOKEN=... python api_fetch.py ywm --host https://example.ru --from 2026-09-01 --to 2026-09-28 --out ywm-queries-A.csv
  --host можно не указывать, тогда скрипт покажет список ваших сайтов (host_id). Запросы только (популярные, до 3000);
  страницы Вебмастер этим методом не отдаёт.

Токены берутся только из переменных окружения GSC_ACCESS_TOKEN, YWM_TOKEN; в файлы и отчёты не записываются.
Важно: данные Яндекса и Google не смешивать. Состояние: формат запросов и ответов сверен с документацией (июнь 2026),
живые вызовы не проверялись (нет доступов); проверен на локальном имитаторе API."""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

GSC_BASE = os.environ.get("GSC_API_BASE", "https://www.googleapis.com/webmasters/v3")
YWM_BASE = os.environ.get("YWM_API_BASE", "https://api.webmaster.yandex.net/v4")
UA = "seo-geo-suite/api_fetch (read-only)"


class ApiError(Exception):
    pass


def _call(url, token, method="GET", body=None, scheme="Bearer", timeout=60):
    headers = {"Authorization": "%s %s" % (scheme, token), "User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            detail = ""
        hint = {401: "токен недействителен или истёк", 403: "нет доступа к ресурсу или не подтверждены права",
                404: "ресурс не найден (проверьте --site/--host)", 429: "лимит запросов, повторите позже"}.get(e.code, "")
        raise ApiError("HTTP %s %s. %s" % (e.code, hint, detail))
    except urllib.error.URLError as e:
        raise ApiError("сеть: %s" % e.reason)


# ----------------------------------------------------------------- Google Search Console
def gsc_rows(site, token, start, end, dim, country=None, device=None, qtype="web", page_size=25000, max_rows=100000):
    url = "%s/sites/%s/searchAnalytics/query" % (GSC_BASE, urllib.parse.quote(site, safe=""))
    filters = []
    if country:
        filters.append({"dimension": "country", "operator": "equals", "expression": country.lower()})
    if device:
        filters.append({"dimension": "device", "operator": "equals", "expression": device.upper()})
    rows, start_row = [], 0
    while len(rows) < max_rows:
        body = {"startDate": start, "endDate": end, "dimensions": [dim], "type": qtype,
                "rowLimit": page_size, "startRow": start_row, "dataState": "final"}
        if filters:
            body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
        resp = _call(url, token, "POST", body)
        batch = resp.get("rows", [])
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start_row += page_size
    return rows[:max_rows]


def gsc_to_table(rows, dim):
    head = ["Top queries" if dim == "query" else "Top pages", "Clicks", "Impressions", "CTR", "Position"]
    out = [head]
    for r in rows:
        out.append([r["keys"][0], int(round(r.get("clicks", 0))), int(round(r.get("impressions", 0))),
                    "%.2f%%" % (100 * r.get("ctr", 0)), "%.2f" % r.get("position", 0)])
    return out


# ----------------------------------------------------------------- Яндекс Вебмастер
def ywm_user_id(token):
    return _call(YWM_BASE + "/user", token, scheme="OAuth")["user_id"]


def ywm_hosts(token, user_id):
    return _call("%s/user/%s/hosts" % (YWM_BASE, user_id), token, scheme="OAuth").get("hosts", [])


def ywm_host_id(token, user_id, host):
    """Сопоставляет адрес сайта с host_id (формат вида https:example.ru:443)."""
    h = host.strip().rstrip("/").lower()
    h = h.split("://", 1)[-1]
    for item in ywm_hosts(token, user_id):
        hid = item["host_id"]
        parts = hid.split(":")
        if len(parts) >= 2 and parts[1].lower() in (h, h[4:] if h.startswith("www.") else "www." + h):
            return hid
    raise ApiError("сайт %s не найден среди ваших сайтов в Вебмастере" % host)


def ywm_rows(token, user_id, host_id, date_from, date_to, order_by="TOTAL_SHOWS", device="ALL", page_size=500, max_rows=3000):
    base = "%s/user/%s/hosts/%s/search-queries/popular" % (YWM_BASE, user_id, urllib.parse.quote(host_id, safe=""))
    rows, offset = [], 0
    while len(rows) < max_rows:
        q = [("order_by", order_by), ("device_type_indicator", device), ("date_from", date_from), ("date_to", date_to),
             ("offset", offset), ("limit", page_size)]
        for ind in ("TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION"):
            q.append(("query_indicator", ind))
        resp = _call(base + "?" + urllib.parse.urlencode(q), token, scheme="OAuth")
        batch = resp.get("queries", [])
        rows.extend(batch)
        total = resp.get("count") or 0
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = 0
        offset += page_size
        if len(batch) < page_size or (total and offset >= total):
            break
    return rows[:max_rows]


def ywm_to_table(rows):
    out = [["Запрос", "Показы", "Клики", "CTR", "Средняя позиция"]]
    for r in rows:
        ind = r.get("indicators", {})
        shows, clicks, pos = ind.get("TOTAL_SHOWS"), ind.get("TOTAL_CLICKS"), ind.get("AVG_SHOW_POSITION")
        ctr = (100.0 * clicks / shows) if shows and clicks is not None else None
        out.append([r.get("query_text", ""), "" if shows is None else int(round(shows)),
                    "" if clicks is None else int(round(clicks)), "" if ctr is None else "%.2f%%" % ctr,
                    "" if pos is None else "%.2f" % pos])
    return out


def write_csv(path, table):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f, delimiter=";").writerows(table)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gsc")
    g.add_argument("--site", required=True, help="свойство GSC: https://example.com/ или sc-domain:example.com")
    g.add_argument("--from", dest="d1", required=True)
    g.add_argument("--to", dest="d2", required=True)
    g.add_argument("--dim", choices=["query", "page"], default="query")
    g.add_argument("--country", help="код страны ISO alpha-3, например rus")
    g.add_argument("--device", choices=["DESKTOP", "MOBILE", "TABLET"])
    g.add_argument("--type", default="web", choices=["web", "discover", "googleNews", "news", "image", "video"])
    g.add_argument("--max-rows", type=int, default=100000)
    g.add_argument("--out", required=True)
    y = sub.add_parser("ywm")
    y.add_argument("--host", help="адрес сайта; без него — список сайтов")
    y.add_argument("--from", dest="d1")
    y.add_argument("--to", dest="d2")
    y.add_argument("--device", default="ALL", choices=["ALL", "DESKTOP", "MOBILE_AND_TABLET", "MOBILE", "TABLET"])
    y.add_argument("--order-by", default="TOTAL_SHOWS", choices=["TOTAL_SHOWS", "TOTAL_CLICKS"])
    y.add_argument("--out")
    a = ap.parse_args()
    try:
        if a.cmd == "gsc":
            token = os.environ.get("GSC_ACCESS_TOKEN")
            if not token:
                sys.exit("Не задан GSC_ACCESS_TOKEN (OAuth-токен с правом webmasters.readonly). Выгрузку можно сделать вручную в интерфейсе GSC.")
            rows = gsc_rows(a.site, token, a.d1, a.d2, a.dim, a.country, a.device, a.type, max_rows=a.max_rows)
            write_csv(a.out, gsc_to_table(rows, a.dim))
            print("GSC: %d строк → %s. API отдаёт верхние строки в пределах внутренних лимитов, не все данные." % (len(rows), a.out))
        else:
            token = os.environ.get("YWM_TOKEN")
            if not token:
                sys.exit("Не задан YWM_TOKEN (OAuth-токен для API Вебмастера). Выгрузку можно сделать вручную в интерфейсе Вебмастера.")
            uid = ywm_user_id(token)
            if not a.host:
                for h in ywm_hosts(token, uid):
                    print(h["host_id"], h.get("unicode_host_url", ""))
                return
            if not (a.d1 and a.d2 and a.out):
                sys.exit("Нужны --from, --to и --out.")
            hid = ywm_host_id(token, uid, a.host)
            rows = ywm_rows(token, uid, hid, a.d1, a.d2, a.order_by, a.device)
            write_csv(a.out, ywm_to_table(rows))
            print("Вебмастер: %d запросов → %s. Метод отдаёт до 3000 популярных запросов." % (len(rows), a.out))
    except ApiError as e:
        sys.exit("Ошибка API: %s" % e)


if __name__ == "__main__":
    main()
