#!/usr/bin/env python3
"""Fill msgstr in locale/<lang>/LC_MESSAGES/django.po from a JSON file.

Usage: python scripts/apply_translations.py translations.json
JSON format: {"nl": {"<msgid>": "<msgstr>", ...}, "fr": {...}}
Requires polib (dev dependency): pip install polib
"""

import json
import re
import sys
from pathlib import Path

import polib

PLACEHOLDER = re.compile(r'%\([a-z_]+\)[sd]')


def apply(lang: str, table: dict[str, str], locale_dir: Path) -> None:
    path = locale_dir / lang / 'LC_MESSAGES' / 'django.po'
    po = polib.pofile(str(path), wrapwidth=79)
    remaining = dict(table)

    for entry in po:
        if entry.msgid not in remaining or entry.msgid_plural:
            continue
        msgstr = remaining.pop(entry.msgid)
        if sorted(PLACEHOLDER.findall(entry.msgid)) != sorted(
            PLACEHOLDER.findall(msgstr)
        ):
            print(f'[{lang}] PLACEHOLDER MISMATCH, skipped: {entry.msgid!r}')
            continue
        entry.msgstr = msgstr
        if 'fuzzy' in entry.flags:
            entry.flags.remove('fuzzy')

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
