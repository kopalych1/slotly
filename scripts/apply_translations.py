#!/usr/bin/env python3
"""Fill msgstr in locale/<lang>/LC_MESSAGES/django.po from a JSON file.

Usage: python scripts/apply_translations.py translations.json
JSON format: {"nl": {"<msgid>": "<msgstr>", ...}, "fr": {...}}
For plural entries the value is a list with one string per plural form of the
language (nl: 2, fr: 3 in Django's CLDR rules); the key is the singular msgid.
Requires polib (dev dependency): pip install polib
"""

import json
import re
import sys
from pathlib import Path

import polib

PLACEHOLDER = re.compile(r'%\([a-z_]+\)[sd]')


def placeholders(text: str) -> list[str]:
    return sorted(PLACEHOLDER.findall(text))


def plural_count(po: polib.POFile) -> int:
    match = re.search(r'nplurals\s*=\s*(\d+)', po.metadata.get('Plural-Forms', ''))
    return int(match.group(1)) if match else 2


def apply(lang: str, table: dict[str, str | list[str]], locale_dir: Path) -> None:
    path = locale_dir / lang / 'LC_MESSAGES' / 'django.po'
    po = polib.pofile(str(path), wrapwidth=79)
    nplurals = plural_count(po)
    remaining = dict(table)

    for entry in po:
        if entry.msgid not in remaining:
            continue
        value = remaining.pop(entry.msgid)

        if entry.msgid_plural:
            if not isinstance(value, list) or len(value) != nplurals:
                print(f'[{lang}] needs a list of {nplurals} forms, skipped: {entry.msgid!r}')
                continue
            expected = set(placeholders(entry.msgid_plural))
            if any(set(placeholders(form)) != expected for form in value):
                print(f'[{lang}] PLACEHOLDER MISMATCH, skipped: {entry.msgid!r}')
                continue
            entry.msgstr_plural = {i: form for i, form in enumerate(value)}
        else:
            if not isinstance(value, str):
                print(f'[{lang}] expected a string, skipped: {entry.msgid!r}')
                continue
            if placeholders(entry.msgid) != placeholders(value):
                print(f'[{lang}] PLACEHOLDER MISMATCH, skipped: {entry.msgid!r}')
                continue
            entry.msgstr = value

        if 'fuzzy' in entry.flags:
            entry.flags.remove('fuzzy')
        entry.previous_msgid = None
        entry.previous_msgid_plural = None
        entry.previous_msgctxt = None

    for msgid in remaining:
        print(f'[{lang}] not found in .po: {msgid!r}')

    po.save(str(path))
    left = len(po.untranslated_entries()) + len(po.fuzzy_entries())
    print(f'[{lang}] saved, still untranslated or fuzzy: {left}')


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    for lang, table in data.items():
        apply(lang, table, Path('locale'))


if __name__ == '__main__':
    main()
