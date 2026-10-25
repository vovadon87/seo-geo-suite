#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown статьи -> семантический HTML (только стандартная библиотека).

Поддерживается подмножество, которое использует навык: #/##/### заголовки,
абзацы, списки (- и 1.), таблицы, **жирный**, [текст](url), HTML-комментарии
(отбрасываются). Инлайн-стилей нет — типографику задаёт страница (Tilda T123).

CLI: python md_to_html.py <статья.md> <выход.html> [--full]
  --full — полная страница со стилями (для просмотра и печати в PDF).
"""
import html
import re
import sys

FULL_CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 11.5pt; line-height: 1.55; color: #1a1a1a; }
.meta { font-size: 9pt; color: #666; border-bottom: 1px solid #ddd; padding-bottom: 6px; margin-bottom: 16px; }
h1 { font-size: 22pt; line-height: 1.25; margin: 0 0 12px; }
h2 { font-size: 15pt; margin: 22px 0 6px; page-break-after: avoid; }
h3 { font-size: 13pt; margin: 18px 0 6px; page-break-after: avoid; }
p, li { margin: 0 0 9px; orphans: 3; widows: 3; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 10pt; page-break-inside: avoid; }
th, td { border: 1px solid #bbb; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #f0f0f0; }
a { color: #0b57d0; text-decoration: none; }
"""


def inline(text):
    """Экранирование + **bold** + [text](url)."""
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+|#[^)\s]+)\)",
               lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    return s


def _is_table_sep(line):
    return bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*", line))


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def convert(md):
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            head = _cells(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            t = ["<table>", "<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>", "<tbody>"]
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t += ["</tbody>", "</table>"]
            out.append("\n".join(t))
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>\n" + "\n".join(f"<li>{inline(x)}</li>" for x in items) + "\n</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>\n" + "\n".join(f"<li>{inline(x)}</li>" for x in items) + "\n</ol>")
            continue
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,3}\s|\s*[-*]\s|\s*\d+\.\s|\s*\|)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n\n".join(out) + "\n"


def full_page(body, title="", meta=""):
    meta_div = f'<div class="meta">{html.escape(meta)}</div>' if meta else ""
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<title>{html.escape(title)}</title><style>{FULL_CSS}</style></head>'
            f'<body>{meta_div}{body}</body></html>')


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]
    body = convert(open(src, encoding="utf-8-sig").read())
    if "--full" in sys.argv:
        body = full_page(body)
    open(dst, "w", encoding="utf-8").write(body)
    print(f"OK: {dst}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
