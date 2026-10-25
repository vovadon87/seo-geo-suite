# Входной бриф: {{сайт}} — {{дата}}

Обязателен только `url`. Остальное заполняет пользователь или `auto` (решение принимает модуль и фиксирует ниже).

```yaml
url:                       # обязательный
mode: auto                 # on-page | blog | rewrite | cluster | refresh | audit | auto
site_profile: auto         # sites/<имя>/ ; auto = по домену; нет профиля → onboard
region: auto               # регионы замеров и текста из profile.md
wordstat_source: auto      # путь к выгрузке | auto (файлы → инструменты → «предварительно»)
existing_semantics: auto
audience: auto             # из profile.md (B2B/B2C, подкласс)
primary_goal: qualified_leads
content_length: auto       # по интенту
competitor_analysis: true
yandex_epos: true
geo_optimization: true
cannibalization_check: true
technical_fact_check: true
visual_compatibility: true
publication: manual_approval   # публикует только владелец
```

## Решения модуля (заполняется)
- Выбранный режим и почему:
- Источник частотности и статус (подтверждена / предварительная):
- Регионы замеров:
- Режим тематики: обычный / строгий (здоровье, финансы, право …):
- Ограничения запуска (нет сети / нет выгрузки / капча / нет профиля):
