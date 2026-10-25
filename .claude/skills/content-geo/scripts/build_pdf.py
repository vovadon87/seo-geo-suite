#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Статья (.md или .html) -> PDF через headless Chrome/Edge (только стандартная библиотека).

Использование:
  python build_pdf.py <статья.md|.html> <выход.pdf> [--meta "строка над статьёй"]

Нужен установленный Chrome или Edge; без них скрипт сообщает об этом и завершается с кодом 2
(ничего не устанавливается). Исходный файл не изменяется; временный HTML удаляется.
"""
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import md_to_html as m2h

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_browser():
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    return None


def build(src, dst, meta=""):
    browser = find_browser()
    if not browser:
        print("Не найден Chrome/Edge: PDF не создан. Откройте HTML в браузере и сохраните как PDF.")
        return 2
    text = open(src, encoding="utf-8-sig").read()
    body = text if src.lower().endswith((".html", ".htm")) else m2h.convert(text)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", body, flags=re.S)
    title = re.sub(r"<[^>]+>", "", m.group(1)) if m else os.path.basename(src)
    page = m2h.full_page(body, title=title, meta=meta)
    fd, tmp = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(page)
        out = os.path.abspath(dst)
        r = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={out}", "file:///" + tmp.replace("\\", "/")],
                           capture_output=True, text=True, timeout=120)
        ok = os.path.exists(out) and os.path.getsize(out) > 1000
        print(("OK: " + out) if ok else ("FAIL: " + r.stderr[-300:]))
        return 0 if ok else 1
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    meta = sys.argv[sys.argv.index("--meta") + 1] if "--meta" in sys.argv else ""
    sys.exit(build(sys.argv[1], sys.argv[2], meta))
