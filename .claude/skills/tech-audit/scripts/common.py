#!/usr/bin/env python3
"""Общие функции tech-audit: загрузка URL с цепочкой редиректов и разбор HTML.
Только стандартная библиотека. Ничего не изменяет на сайте (только GET/HEAD)."""
import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; seo-geo-audit/3.0; +read-only)"
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _decode(body, enc):
    enc = (enc or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(body)
        if "deflate" in enc:
            try:
                return zlib.decompress(body)
            except zlib.error:
                return zlib.decompress(body, -zlib.MAX_WBITS)
    except Exception:
        pass
    return body


def fetch_once(url, method="GET", timeout=30, max_bytes=3_000_000, headers=None):
    """Один запрос без следования редиректам."""
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method=method)
    t0 = time.time()
    try:
        r = _opener.open(req, timeout=timeout)
        status, hdrs, fp = r.status, r.headers, r
    except urllib.error.HTTPError as e:
        status, hdrs, fp = e.code, e.headers, e
    except Exception as e:  # сеть, TLS, таймаут
        return {"status": None, "headers": {}, "body": b"", "error": str(e), "elapsed": time.time() - t0, "raw_len": 0}
    elapsed = time.time() - t0  # время до получения заголовков ≈ TTFB
    try:
        raw = fp.read(max_bytes) if method != "HEAD" else b""
    except Exception:
        raw = b""
    headers_l = {k.lower(): v for k, v in hdrs.items()}
    body = _decode(raw, headers_l.get("content-encoding"))
    return {"status": status, "headers": headers_l, "body": body, "error": None,
            "elapsed": elapsed, "raw_len": len(raw)}


def fetch(url, method="GET", timeout=30, max_bytes=3_000_000, max_hops=10, headers=None):
    """Загрузка с ручным следованием редиректам. Возвращает цепочку."""
    chain = []
    cur = url
    seen = set()
    first_elapsed = None
    for _ in range(max_hops + 1):
        r = fetch_once(cur, method, timeout, max_bytes, headers)
        if first_elapsed is None:
            first_elapsed = r["elapsed"]
        chain.append({"url": cur, "status": r["status"], "location": r["headers"].get("location")})
        loc = r["headers"].get("location")
        if r["status"] in (301, 302, 303, 307, 308) and loc:
            nxt = urllib.parse.urljoin(cur, loc)
            if nxt in seen:
                r["error"] = "redirect loop"
                break
            seen.add(cur)
            cur = nxt
            continue
        break
    r["url"] = cur
    r["chain"] = chain
    r["ttfb"] = r["elapsed"]
    r["first_elapsed"] = first_elapsed
    return r


def normalize(url):
    """Приведение URL к виду для дедупликации: без фрагмента, хост в нижнем регистре."""
    p = urllib.parse.urlsplit(url)
    netloc = p.netloc.lower()
    if netloc.endswith(":80") and p.scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and p.scheme == "https":
        netloc = netloc[:-4]
    path = p.path or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), netloc, path, p.query, ""))


class PageParser(HTMLParser):
    """Собирает SEO-значимые элементы страницы."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self._in_title = False
        self.metas = []          # (key, value)
        self.links = []          # dict(rel, href, hreflang, ...)
        self.anchors = []        # dict(href, rel, text)
        self.headings = []       # (level, text)
        self.imgs = []
        self.scripts = []        # dict(src, async, defer, type, in_head)
        self.jsonld_raw = []
        self.html_lang = None
        self.iframes = 0
        self.forms = 0
        self._head = False
        self._skip = 0
        self._cur_a = None
        self._cur_h = None
        self._ld = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "html":
            self.html_lang = a.get("lang")
        elif tag == "head":
            self._head = True
        elif tag == "body":
            self._head = False
        elif tag == "title" and self.title is None:
            self._in_title = True
            self.title = ""
        elif tag == "meta":
            key = a.get("name") or a.get("property") or a.get("http-equiv")
            if key:
                self.metas.append((key.lower(), a.get("content", "")))
        elif tag == "link":
            self.links.append(a)
        elif tag == "a":
            self._cur_a = {"href": a.get("href", ""), "rel": a.get("rel", ""), "text": ""}
            self.anchors.append(self._cur_a)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._cur_h = [int(tag[1]), ""]
            self.headings.append(self._cur_h)
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = []
            else:
                self.scripts.append({"src": a.get("src", ""), "async": "async" in a, "defer": "defer" in a,
                                     "type": a.get("type", ""), "in_head": self._head})
            self._skip += 1
        elif tag == "style" or tag == "noscript":
            self._skip += 1
        elif tag == "iframe":
            self.iframes += 1
        elif tag == "form":
            self.forms += 1

    def handle_endtag(self, tag):
        if tag == "head":
            self._head = False
        elif tag == "title":
            self._in_title = False
        elif tag == "a":
            self._cur_a = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._cur_h = None
        elif tag == "script":
            if self._ld is not None:
                self.jsonld_raw.append("".join(self._ld))
                self._ld = None
            self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._in_title and self.title is not None:
            self.title += data
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._skip:
            return
        if self._cur_a is not None:
            self._cur_a["text"] += data
        if self._cur_h is not None:
            self._cur_h[1] += data
        self._text.append(data)

    # удобные аксессоры
    def meta(self, key):
        key = key.lower()
        for k, v in self.metas:
            if k == key:
                return v
        return None

    def link_rel(self, rel):
        return [l for l in self.links if rel in (l.get("rel", "").lower().split())]

    def text(self):
        return re.sub(r"\s+", " ", " ".join(self._text)).strip()


def parse_html(body):
    text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else body
    p = PageParser()
    try:
        p.feed(text)
        p.close()
    except Exception:
        pass
    return p


def jsonld_items(raws):
    """Возвращает (список объектов, число невалидных блоков). @graph раскрывается."""
    items, bad = [], 0
    for raw in raws:
        try:
            data = json.loads(raw.strip())
        except Exception:
            bad += 1
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "@graph" in o and isinstance(o["@graph"], list):
                    stack.extend(o["@graph"])
                items.append(o)
    return items, bad


def types_of(obj):
    t = obj.get("@type")
    if isinstance(t, list):
        return [str(x) for x in t]
    return [str(t)] if t else []
