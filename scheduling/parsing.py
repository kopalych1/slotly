DELIMITERS = ('\t', ';', ',')
NAME_FIELDS = ('first_name', 'last_name')


def parse_pasted_rows(text: str) -> list[dict[str, str]]:
    parsed = []
    for line in text.splitlines():
        if not line.strip():
            continue

        delimiter = next((d for d in DELIMITERS if d in line), None)
        cells = (
            [c.strip() for c in line.split(delimiter)] if delimiter else [line.strip()]
        )

        row = {'email': ''}
        names = []
        for cell in cells:
            if '@' in cell:
                row['email'] = cell
            else:
                names.append(cell)

        row.update(zip(NAME_FIELDS, names))
        parsed.append(row)
    return parsed
