# seo-geo-suite

Набор навыков для SEO и GEO (видимость в поиске и в ответах ИИ) любого сайта и любой ниши. Вариант 2 из обсуждения: роутер + независимые модули, нишевых знаний в навыках нет, профиль сайта создаётся при подключении (onboard).

## Состав (фазы 1 и 2)

```
seo-geo-suite/
├── _shared/rules.md            правила: доказательства, P0–P3, формат находки, границы охвата
├── seo-geo/                    роутер + onboard (исследование ниши, профиль сайта)
│   ├── SKILL.md
│   ├── references/onboard.md
│   └── templates/{site-context.md, profile.md}
├── tech-audit/                 технический аудит
│   ├── SKILL.md
│   ├── references/checks.md    критерии, ИИ-боты, таблица «где вносить правки» по CMS
│   ├── templates/tech-audit-report.md
│   └── scripts/                site_crawl.py, render_check.py, robots_check.py, schema_check.py, common.py, run_tests.py, run_render_tests.py
├── speed-audit/                скорость и Core Web Vitals
│   ├── SKILL.md
│   ├── references/metrics-and-fixes.md
│   └── scripts/                psi.py, crux.py, lighthouse_run.py, page_weight.py, speed_report.py, budget.json, run_tests.py
├── semantics/                  Wordstat, кластеризация, интент, карта страниц
│   ├── SKILL.md, references/{wordstat-research,clustering,serp-intent}.md
│   ├── templates/{semantic-map.md, semantic-core.csv}
│   └── scripts/                wordstat_io.py, cluster_phrases.py, run_tests.py
├── content-geo/                страницы, статьи, GEO, проверка фактов, ворота качества
│   ├── SKILL.md, references/ (8 файлов), templates/ (5 файлов)
│   └── scripts/                quality_checks.py, md_to_html.py, build_pdf.py, run_tests.py
├── local-seo/                  регионы, карточки, NAP
│   ├── SKILL.md, references/local.md
│   └── scripts/                nap_check.py, run_tests.py (использует tech-audit/scripts/common.py)
├── seo-report/                 сравнение периодов
│   ├── SKILL.md, references/measurement.md, templates/period-report.md
│   └── scripts/                period_compare.py, run_tests.py
├── agents/seo-geo-writer.md    агент-автор (skills: seo-geo, content-geo, semantics)
└── evals/scenarios.md          сценарии проверки
```

## Что проверено
- `tech-audit/scripts/run_tests.py`: 10 тестов на локальном сайте с заведомыми проблемами (дубли, битые ссылки, цепочки редиректов, noindex в sitemap, сироты, глубина, мягкая 404, шаблонная разметка, robots).
- `tech-audit/scripts/run_render_tests.py`: 7 тестов `render_check.py` (SPA с контентом, ссылками, мета и разметкой только после JS; статичная страница без находок; noindex, снятый JS).
- `speed-audit/scripts/run_tests.py`: 7 тестов; `page_weight.py` и `lighthouse_run.py` прошли сквозной запуск в Chromium и Lighthouse 12 на локальном «тяжёлом» сайте.
- Контрольный прогон `site_crawl.py` на публичном сайте (gzip, редиректы, разбор HTML без кавычек в атрибутах).
- Фаза 2: `semantics` 13 тестов, `content-geo` 13, `local-seo` 9, `seo-report` 13 (синтетические данные; кластеризатор проверен на нише «ремонт квартир»). Кластеры и интент — черновик для проверки человеком, частотность Wordstat скрипты не выдумывают.
- **Не проверено вживую:** вызовы PSI и CrUX API (нужен ключ Google; разбор ответов проверен на синтетических данных по документации). Первый реальный запуск с ключом — контрольная точка.

## Установка

**Как навыки аккаунта** (чтобы вызывались сами): сохранить карточки SKILL.md всех семи навыков (`seo-geo`, `tech-audit`, `speed-audit`, `semantics`, `content-geo`, `local-seo`, `seo-report`). Карточка сохраняет только текст SKILL.md.

**Скрипты и справочники** живут в репозитории. Положить папки в `.claude/skills/<имя>/` репозитория `etiketka-claude` (или в `~/.claude/skills/<имя>/` на основной машине), а `_shared/` и `evals/` — рядом. Тогда SKILL.md находит `scripts/` рядом с собой. В облачной сессии на репозитории они подтянутся автоматически.

Зависимости: Python 3.9+ (скрипты tech-audit — стандартная библиотека); для `page_weight.py` — `pip install playwright` и Chromium; для `lighthouse_run.py` — Node.js и `npm i -g lighthouse`.

Ключи (по желанию, у владельца): `PSI_API_KEY`, `CRUX_API_KEY` — Google Cloud Console, включить PageSpeed Insights API и Chrome UX Report API. В репозиторий не коммитить.

## Миграция с audit-seo-geo
- Все режимы `audit-seo-geo` покрыты модулями набора: `audit` → tech-audit, `onboard` → seo-geo, `semantics` → semantics, `content`/`geo`/`page` → content-geo, `local` → local-seo, `report` → seo-report. После проверки набора старый навык можно убрать из роутинга; сам репозиторий не трогать до решения владельца.
- Старые `crawl.py` и `check_page.py` заменены универсальным `site_crawl.py` (без нишевых привязок).
- Профиль `sites/etiketka/` переносится как есть; формат совместим.
- Агент `seo-geo-writer` обобщает `etiketka-seo-geo-writer`; нишевый агент и `etiketka-seo-geo-articles` не затронуты.
- Скрипты `wordstat_io.py`, `quality_checks.py`, `md_to_html.py`, `build_pdf.py` скопированы из репозитория без изменений.

## Дальше (по желанию)
Подключение Вебмастера и GSC по API, мониторинг по расписанию (только с разрешения владельца), агенты-аудиторы для параллельного запуска, прогон на реальных сайтах разных ниш.

## Ограничения
- `site_crawl.py` не исполняет JavaScript (для этого есть `render_check.py`: 10 страниц по умолчанию, из места запуска), не знает статус индексации и не читает логи; охват ограничен `--max-pages`.
- Лабораторная скорость измеряется из места запуска; для аудитории в другом регионе опираться на поле (CrUX, Метрика).
- Список ИИ-ботов в `robots_check.py` нужно периодически сверять с документацией владельцев ботов.
