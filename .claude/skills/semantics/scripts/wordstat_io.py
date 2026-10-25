#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Чтение, валидация и дедупликация выгрузок Wordstat (CSV/XLSX).

Принципы:
- частотность никогда не выдумывается: значение либо целое число >= 0,
  либо один из допустимых маркеров отсутствия данных;
- исходные файлы не изменяются (только чтение); запись — в новый файл,
  существующий файл не перезаписывается без --force;
- каждый замер несёт источник, дату и регион.

CLI:
  python wordstat_io.py validate <файл> [--phrase-col N --freq-col N ...]
  python wordstat_io.py dedupe   <файл>
  python wordstat_io.py export   <файл_in> <файл_out.csv> [--force]
"""
import argparse
import csv
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

MISSING_MARKERS = {"", "нет данных", "не измерялось", "не измерялась", "n/a", "na", "-", "—"}

CANONICAL_COLUMNS = [
    "Исходная фраза", "Частотность без операторов", "Частотность в кавычках",
    "Частотность !словоформ", "Регион", "Период или дата замера", "Интент",
    "Кластер", "Целевой URL", "Коммерческая релевантность", "Приоритет",
    "Статус использования", "Источник числа",
]


def normalize_phrase(phrase):
    """Ключ дедупликации: нижний регистр, ё->е, без операторов, один пробел."""
    s = str(phrase).lower().replace("ё", "е")
    s = re.sub(r'["\[\]!+]', " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_frequency(value):
    """(ok, parsed): parsed = int | None (нет данных). ok=False — недопустимое значение."""
    if value is None:
        return True, None
    if isinstance(value, bool):
        return False, None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            return False, None
        iv = int(value)
        return (iv >= 0), (iv if iv >= 0 else None)
    s = str(value).strip().lower()
    if s in MISSING_MARKERS:
        return True, None
    s_num = s.replace(" ", "").replace(" ", "")
    if re.fullmatch(r"\d+", s_num):
        return True, int(s_num)
    return False, None


def read_rows(path):
    """Список списков значений (первая строка — заголовок). CSV: ; или , или tab; XLSX: первый лист либо все листы с колонкой фраз."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit("Для XLSX нужен openpyxl: pip install openpyxl")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        # многолистовые выгрузки: берём первый лист, в первой строке которого
        # распознаётся колонка фразы; иначе — первый лист
        chosen = wb.worksheets[0]
        for ws in wb.worksheets:
            first = next(ws.iter_rows(values_only=True, max_row=1), None)
            if first and detect_columns(list(first))["phrase"] is not None:
                chosen = ws
                break
        return [list(r) for r in chosen.iter_rows(values_only=True)]
    if ext in (".csv", ".txt", ".tsv"):
        with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            delim = ";"
            for cand in (";", "\t", ","):
                if cand in sample.splitlines()[0] if sample else False:
                    delim = cand
                    break
            reader = csv.reader(f, delimiter=delim)
            return [row for row in reader]
    raise SystemExit(f"Неподдерживаемое расширение: {ext}")


def detect_columns(header):
    """По заголовку найти индексы колонок фразы и частотностей (эвристика по подстрокам)."""
    idx = {"phrase": None, "freqs": []}
    for i, cell in enumerate(header):
        name = str(cell or "").lower()
        if idx["phrase"] is None and ("фраза" in name or "запрос" in name or "keyword" in name or "phrase" in name):
            idx["phrase"] = i
        if "частотн" in name or "показ" in name or "frequency" in name or "shows" in name:
            idx["freqs"].append(i)
    return idx


def validate(path, verbose=True):
    """Проверка типов: фраза непустая, частотности корректны. Возвращает (ошибки, строки-данные, idx)."""
    rows = read_rows(path)
    if not rows:
        return ["файл пуст"], [], None
    header, data = rows[0], rows[1:]
    idx = detect_columns(header)
    errors = []
    if idx["phrase"] is None:
        errors.append("не найдена колонка фразы (ожидается подстрока 'фраза'/'запрос')")
        return errors, data, idx
    for n, row in enumerate(data, start=2):
        if idx["phrase"] >= len(row) or not str(row[idx["phrase"]] or "").strip():
            if any(str(c or "").strip() for c in row):
                errors.append(f"строка {n}: пустая фраза")
            continue
        for fi in idx["freqs"]:
            if fi < len(row):
                ok, _ = parse_frequency(row[fi])
                if not ok:
                    errors.append(f"строка {n}: недопустимая частотность {row[fi]!r} (число или маркер 'нет данных')")
    if verbose:
        print(f"Файл: {path}")
        print(f"Строк данных: {len(data)}; колонка фразы: {idx['phrase']}; колонок частотности: {len(idx['freqs'])}")
        if errors:
            print(f"ОШИБКИ ({len(errors)}):")
            for e in errors[:50]:
                print("  -", e)
        else:
            print("Ошибок типов не найдено.")
    return errors, data, idx


def find_duplicates(path, verbose=True):
    """Дубликаты по нормализованной фразе."""
    _, data, idx = validate(path, verbose=False)
    if idx is None or idx["phrase"] is None:
        raise SystemExit("не найдена колонка фразы")
    seen, dups = {}, []
    for n, row in enumerate(data, start=2):
        if idx["phrase"] >= len(row):
            continue
        phrase = str(row[idx["phrase"]] or "").strip()
        if not phrase:
            continue
        key = normalize_phrase(phrase)
        if key in seen:
            dups.append((seen[key][0], seen[key][1], n, phrase))
        else:
            seen[key] = (n, phrase)
    if verbose:
        if dups:
            print(f"Дубликатов по нормализованной форме: {len(dups)}")
            for first_n, first_p, n, p in dups[:50]:
                print(f"  строка {n} «{p}» = строка {first_n} «{first_p}»")
        else:
            print("Дубликатов не найдено.")
    return dups


def export(src, dst, force=False):
    """Экспорт в канонический CSV (см. CANONICAL_COLUMNS). Недостающие поля — пустые, не выдуманные."""
    if os.path.abspath(src) == os.path.abspath(dst):
        raise SystemExit("Запись в исходный файл запрещена: укажите другой путь")
    if os.path.exists(dst) and not force:
        raise SystemExit(f"Файл существует: {dst}. Перезапись только с --force")
    errors, data, idx = validate(src, verbose=False)
    fatal = [e for e in errors if "не найдена колонка" in e]
    if fatal:
        raise SystemExit("; ".join(fatal))
    rows_out = []
    header = read_rows(src)[0]
    for row in data:
        if idx["phrase"] >= len(row):
            continue
        phrase = str(row[idx["phrase"]] or "").strip()
        if not phrase:
            continue
        out = {c: "" for c in CANONICAL_COLUMNS}
        out["Исходная фраза"] = phrase
        freq_names = [str(header[i] or "") for i in idx["freqs"]]
        for i, name in zip(idx["freqs"], freq_names):
            ok, val = parse_frequency(row[i] if i < len(row) else None)
            cell = "нет данных" if (ok and val is None and str(row[i] if i < len(row) else "").strip() != "") else ("" if val is None else str(val))
            low = name.lower()
            if "кавыч" in low or '"' in name:
                out["Частотность в кавычках"] = cell
            elif "!" in name or "словоформ" in low:
                out["Частотность !словоформ"] = cell
            else:
                out["Частотность без операторов"] = cell
        rows_out.append(out)
    with io.open(dst, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CANONICAL_COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows_out)
    print(f"Экспортировано строк: {len(rows_out)} -> {dst}")
    if errors:
        print(f"Предупреждений валидации: {len(errors)} (см. validate)")
    return len(rows_out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("validate"); p1.add_argument("path")
    p2 = sub.add_parser("dedupe"); p2.add_argument("path")
    p3 = sub.add_parser("export"); p3.add_argument("src"); p3.add_argument("dst"); p3.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.cmd == "validate":
        errors, _, _ = validate(args.path)
        sys.exit(1 if errors else 0)
    elif args.cmd == "dedupe":
        dups = find_duplicates(args.path)
        sys.exit(1 if dups else 0)
    elif args.cmd == "export":
        export(args.src, args.dst, force=args.force)


if __name__ == "__main__":
    main()
