#!/usr/bin/env python3
"""robots.txt: общая доступность, служебные пути, Sitemap, матрица ботов (поиск / ИИ).
Использование: python robots_check.py https://example.com
Список ботов меняется — перед выводами сверяйтесь с документацией владельцев ботов."""
import sys
import os
import urllib.parse
import urllib.robotparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import fetch  # noqa: E402

# (бот, владелец, назначение)
BOTS = [
    ("Googlebot", "Google", "поиск"),
    ("Google-Extended", "Google", "управляющий токен: обучение Gemini (не отдельный краулер)"),
    ("Bingbot", "Microsoft", "поиск"),
    ("YandexBot", "Яндекс", "поиск"),
    ("YandexAdditional", "Яндекс", "дополнительные сервисы Яндекса (уточнить по документации)"),
    ("GPTBot", "OpenAI", "обучение"),
    ("OAI-SearchBot", "OpenAI", "поиск ChatGPT"),
    ("ChatGPT-User", "OpenAI", "загрузка по запросу пользователя"),
    ("ClaudeBot", "Anthropic", "обучение"),
    ("Claude-SearchBot", "Anthropic", "поиск Claude"),
    ("Claude-User", "Anthropic", "загрузка по запросу пользователя"),
    ("PerplexityBot", "Perplexity", "поиск"),
    ("Perplexity-User", "Perplexity", "загрузка по запросу пользователя"),
    ("Applebot", "Apple", "поиск/Siri"),
    ("Applebot-Extended", "Apple", "управляющий токен: обучение"),
    ("CCBot", "Common Crawl", "открытый архив, используется для обучения"),
    ("Bytespider", "ByteDance", "обучение"),
    ("Meta-ExternalAgent", "Meta", "обучение/индексация"),
]
CMS_PATHS = ["/wp-admin/", "/admin", "/bitrix/", "/search", "/cart", "/checkout", "/thanks", "/tmp/"]


def analyse(base, text):
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    res = {"sitemaps": [], "blocked_all": False, "bots": [], "asset_blocked": [], "notes": []}
    for line in text.splitlines():
        if line.lower().startswith("sitemap:"):
            res["sitemaps"].append(line.split(":", 1)[1].strip())
    res["blocked_all"] = not rp.can_fetch("*", base + "/")
    for name, owner, purpose in BOTS:
        res["bots"].append({"bot": name, "owner": owner, "purpose": purpose,
                            "home_allowed": rp.can_fetch(name, base + "/")})
    for p in ["/assets/app.css", "/static/main.js", "/images/a.jpg", "/wp-content/themes/x/style.css"]:
        if not rp.can_fetch("Googlebot", base + p):
            res["asset_blocked"].append(p)
    if "clean-param" not in text.lower():
        res["notes"].append("нет Clean-param (актуально для Яндекса при utm/метках в URL)")
    if not res["sitemaps"]:
        res["notes"].append("нет строки Sitemap:")
    return res


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    base = sys.argv[1].rstrip("/")
    r = fetch(base + "/robots.txt")
    if r["status"] != 200:
        print(f"robots.txt: статус {r['status']} (файла нет или недоступен)")
        return
    text = r["body"].decode("utf-8", "ignore")
    a = analyse(base, text)
    print("Сайт закрыт целиком для *:", a["blocked_all"])
    print("Sitemap:", a["sitemaps"] or "нет")
    print("Закрыты ли типовые CSS/JS/картинки для Googlebot:", a["asset_blocked"] or "нет")
    print("\nБоты (доступ к главной):")
    for b in a["bots"]:
        print(f"  {'разрешён' if b['home_allowed'] else 'ЗАКРЫТ  '}  {b['bot']:20} {b['owner']:13} {b['purpose']}")
    for n in a["notes"]:
        print("Заметка:", n)


if __name__ == "__main__":
    main()
