#!/usr/bin/env python3
"""
parse_tafeln.py — Parse OCR text files from rossoschka_text_vision/ into
structured CSV records (one person per row).

Columns: tafel, firstname, lastname, born, died

Usage:
    # Parse a single file, print to stdout
    python3 parse_tafeln.py rossoschka_text_vision/24643-02503-001-friedhof-rossoschka.txt

    # Parse all files, write to rossoschka_parsed.csv
    python3 parse_tafeln.py --all

    # Parse all files, write to a custom output file
    python3 parse_tafeln.py --all --out my_output.csv
"""

import os
import re
import csv
import sys

TEXT_DIR    = "rossoschka_text_vision"
TAFELN_DIR  = "rossoschka_tafeln"
OUTPUT_CSV  = "rossoschka_parsed.csv"
PER_FILE_DIR = "rossoschka_tafeln_textlist"
FIELDNAMES  = ['tafel', 'firstname', 'lastname', 'born', 'died']

# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------

# Matches dates like:  22.09.1906  04:04.1916  .15.05.1923  1902  28.07.1942
# Also bare years: 1902, 1943 etc.
DATE_PAT = re.compile(
    r'\.?(\d{1,2})[.:,\-](\d{1,2})[.:,\-](\d{4})'   # DD.MM.YYYY with OCR noise
    r'|\.?(\d{1,2})[.:,\-](\d{2})\b'                  # DD.MM (2-digit year, rare)
    r'|\b((?:19|20)\d{2})\b'                           # bare year
)


def clean_date(raw: str) -> str:
    """Normalise an OCR date string to DD.MM.YYYY or YYYY."""
    raw = raw.strip(' .+*,-')
    # Standard: single separator char between day, month, year (4-digit)
    m = re.match(r'(\d{1,2})[.:,\-](\d{1,2})[.:,\-](\d{4})', raw)
    if m:
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}"
    # Relaxed: allow 1-2 separator chars (e.g. '25,08.:1910' → '25.08.1910')
    m = re.match(r'(\d{1,2})[.:,\-]{1,2}(\d{1,2})[.:,\-]{1,2}(\d{4})', raw)
    if m:
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}"
    # 2-digit year short form: 21.12.42 → 21.12.1942
    # All dates on this memorial are 1900-1945 era, so YY → 19YY is always correct.
    m = re.match(r'(\d{1,2})[.:,\-](\d{1,2})[.:,\-](\d{2})$', raw)
    if m:
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.19{m.group(3)}"
    m = re.match(r'((?:19|20)\d{2})$', raw)
    if m:
        return m.group(1)
    # OCR sometimes inserts a comma inside a digit pair, e.g. '3,0.07.1912' for '30.07.1912'.
    # Only collapse when the digit after the comma is NOT followed by a letter (which would
    # mean the comma is a legitimate separator, not noise inside a number).
    collapsed = re.sub(r'(\d),(\d)(?!\w)', r'\1\2', raw)
    if collapsed != raw:
        return clean_date(collapsed)
    return raw.strip()


# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

# A "date token" is anything that looks like a date
DATE_TOKEN = re.compile(
    r'\.?\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{4}'
    r'|(?:^|(?<=\s))(?:19|20)\d{2}(?=\s|$)',
    re.MULTILINE
)

# Prefixes that are part of a name but not surnames
NAME_PREFIXES = {'DR', 'PROF', 'JR', 'SR', 'H', 'J', 'K', 'W', 'F', 'E', 'G'}

# Single-letter tokens that mark section labels (e.g. "A", "B") — skip as names
SECTION_LABEL = re.compile(r'^[A-ZÄÖÜ]$')


_NAME_NORM = str.maketrans({'İ': 'I', 'É': 'E', 'À': 'A', 'È': 'E', 'Ü': 'Ü'})


def is_name_token(tok: str) -> bool:
    """Return True if tok looks like a name word (all uppercase, 2+ chars, no digits)."""
    tok = tok.strip('.')
    if not tok:
        return False
    if re.search(r'\d', tok):
        return False
    # Normalise OCR-introduced Unicode variants (e.g. İ → I) before char-class check
    tok_norm = tok.translate(_NAME_NORM)
    # Accept abbreviated double names like "FRIED.ARTUR" by ignoring internal dots
    tok_no_dots = tok_norm.replace('.', '')
    if not re.match(r'^[A-ZÄÖÜÉSS\-]{2,}$', tok_no_dots):
        return False
    return True


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_text(text: str) -> list[dict]:
    """
    Parse the raw OCR text of one tafel into a list of person dicts:
      {firstname, lastname, born, died}

    Strategy:
    - Flatten all text to a single token stream (words).
    - Find all date-like tokens.
    - Between consecutive date tokens, the preceding name-words form the
      [firstnames…] [lastname] group; the following token after a "+" is the
      death date.
    """

    # Flatten into single space-separated string, remove newlines
    flat = ' '.join(text.split())

    # Repair OCR split-date: NAME directly fused with day digits, month+year in next token.
    # e.g. 'GROHMANN15 03.1921' → 'GROHMANN 15.03.1921'
    flat = re.sub(r'([A-ZÄÖÜSS]{2,})(\d{1,2})\s+(\d{1,2}[.:]\d{4})', r'\1 \2.\3', flat)

    # Split into tokens preserving them
    raw_tokens = re.split(r'\s+', flat)

    # Annotate each token
    tokens = []
    for t in raw_tokens:
        t = t.strip(',;|\\()[]{}')
        if not t:
            continue
        # OCR sometimes fuses two name words with a comma or dot: "RICHARD,ARNDT", "ANTON.CUMA"
        # Split on internal commas/dots only when the token contains no digits
        # (digits mean the separator is inside a date string, handled elsewhere).
        if any(sep in t for sep in (',', '.')) and not any(c.isdigit() for c in t):
            for part in re.split(r'[,.]', t):
                part = part.strip()
                if part:
                    tokens.append(part)
        else:
            tokens.append(t)

    records = []
    i = 0
    n = len(tokens)

    def looks_like_date(tok):
        tok = tok.lstrip('.+*')
        return bool(re.match(
            r'\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{4}'
            r'|\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{2}$'
            r'|(?:19|20)\d{2}$',
            tok
        ))

    while i < n:
        tok = tokens[i]

        # Skip tafel header tokens (e.g. "1998-1", "2006-2009-347", "A", "B")
        if re.match(r'^\d{4}[-–]\d', tok) or SECTION_LABEL.match(tok):
            i += 1
            continue

        # Collect name words until we hit a date
        names = []
        while i < n and not looks_like_date(tokens[i].lstrip('.+*')):
            word = tokens[i].strip('.+*,')
            if is_name_token(word):
                names.append(word)
            elif word in ('DR', 'PROF'):
                pass  # skip titles
            elif len(word) == 1 and word.isalpha() and word.isupper() and names:
                # Single uppercase letter after names = OCR split one word in two
                # e.g. 'ALO S DZIENDZIEL' where 'ALOIS' was read as 'ALO S'
                names[-1] += word   # rejoin: 'ALO' + 'S' → 'ALOS'
            elif names:
                # Non-name, non-date word after names = probably OCR noise; stop
                break
            i += 1

        if not names or i >= n:
            i += 1
            continue

        # Now tokens[i] is the first date token after the name.
        # A leading '+' means the plate shows *only* a death date (no birth date).
        raw_tok   = tokens[i]
        has_death_prefix = raw_tok.lstrip('.').startswith('+')
        date_raw  = raw_tok.lstrip('.+*').strip(',')
        first_date = clean_date(date_raw)
        i += 1

        # Handle DATE+DATE concatenated in a single token (no whitespace), e.g.
        # '18.07.1916+11.10.1942' → born='18.07.1916', inline_died='11.10.1942'
        inline_died = ''
        plus_idx = date_raw.find('+')
        if plus_idx > 0:
            suffix = date_raw[plus_idx + 1:]
            if looks_like_date(suffix):
                inline_died = clean_date(suffix)
                first_date  = clean_date(date_raw[:plus_idx])

        if has_death_prefix:
            # Plate records only the death date for this soldier (no birth date engraved)
            born = ''
            died = first_date
        else:
            born = first_date
            # Skip any separator tokens (+, -, .) between born and died
            while i < n and re.match(r'^[+\-\.]+$', tokens[i]):
                i += 1

            # Next date-like token is the death date
            died = inline_died
            if not died and i < n and looks_like_date(tokens[i].lstrip('.+*')):
                died = clean_date(tokens[i].lstrip('.+*').strip('.,'))
                i += 1

        # Split names: last word = surname, rest = firstname(s)
        if len(names) == 1:
            firstname = ''
            lastname  = names[0]
        else:
            lastname  = names[-1]
            firstname = ' '.join(names[:-1])

        if born or died:
            records.append({
                'firstname': firstname,
                'lastname':  lastname,
                'born':      born,
                'died':      died,
            })

    return records


# ---------------------------------------------------------------------------
# Tafel label extraction (from first line)
# ---------------------------------------------------------------------------

_SEP   = r'[\s\-:./]*'   # zero or more separators (handles "2010-2013446")
_YEAR  = r'(?:19|20)\d{2}'
PAT_RANGE  = re.compile(r'^\s*(' + _YEAR + r')' + _SEP + r'(' + _YEAR + r')' + _SEP + r'(\d{1,3})')
PAT_SINGLE = re.compile(r'^\s*(' + _YEAR + r')' + _SEP + r'(\d{1,3})')


def tafel_label(text: str) -> str:
    first = re.sub(r'\b(\d{2})\.(\d{2})\b', r'\1\2', text.split('\n')[0].strip())
    m = PAT_RANGE.match(first)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{int(m.group(3)):03d}"
    m = PAT_SINGLE.match(first)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):03d}"
    return os.path.basename(text[:20]).replace('\n', '')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_file(path: str) -> list[dict]:
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    label   = tafel_label(text)
    records = parse_text(text)
    for r in records:
        r['tafel'] = label
    return records


def main():
    args = sys.argv[1:]
    do_all    = '--all' in args
    do_per    = '--per-file' in args
    out_arg   = args[args.index('--out') + 1] if '--out' in args else OUTPUT_CSV
    pos_args  = [a for a in args if not a.startswith('-') and a != out_arg]

    # --per-file: build a year-num → tafeln-PNG-stem map, then write one CSV each
    if do_per:
        _build_per_file_output()

    if do_all:
        files = sorted(
            os.path.join(TEXT_DIR, f)
            for f in os.listdir(TEXT_DIR)
            if f.endswith('.txt')
        )
        all_records = []
        for path in files:
            all_records.extend(process_file(path))
        with open(out_arg, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(all_records)
        print(f"Wrote {len(all_records):,} records from {len(files)} files → {out_arg}")

    if not do_all and not do_per:
        if not pos_args:
            print(__doc__)
            sys.exit(0)
        # Single file(s): pretty-print to stdout
        writer = csv.DictWriter(sys.stdout, fieldnames=FIELDNAMES, delimiter='\t')
        writer.writeheader()
        for path in pos_args:
            writer.writerows(process_file(path))


# ---------------------------------------------------------------------------
# Per-file output
# ---------------------------------------------------------------------------

# Tafeln filename pattern: YEAR[-YEAR]-NUM[-SURNAMES].png
FNAME_RANGE  = re.compile(r'^((?:19|20)\d{2}-(?:19|20)\d{2})-(\d+)')
FNAME_SINGLE = re.compile(r'^((?:19|20)\d{2})-(\d+)')


def _fname_key(stem: str):
    """Extract (year_str, num) from a tafeln PNG stem."""
    m = FNAME_RANGE.match(stem)
    if m:
        return (m.group(1), int(m.group(2)))
    m = FNAME_SINGLE.match(stem)
    if m:
        return (m.group(1), int(m.group(2)))
    return None


def _build_per_file_output():
    """Write one CSV per tafeln PNG into rossoschka_tafeln_textlist/."""
    os.makedirs(PER_FILE_DIR, exist_ok=True)

    # Index 1: original files (24xxx-...) by year-num key from their content
    # Index 2: new-stem files (1998-001-...) by year-num from filename (direct match)
    orig_index: dict[tuple, str] = {}
    for fname in os.listdir(TEXT_DIR):
        if not fname.endswith('.txt') or not re.match(r'^\d{5}-', fname):
            continue
        path = os.path.join(TEXT_DIR, fname)
        key  = _txt_key(path)
        if key:
            orig_index.setdefault(key, path)

    tafeln_files = sorted(f for f in os.listdir(TAFELN_DIR) if f.lower().endswith('.png'))
    written = skipped = 0

    for png_name in tafeln_files:
        stem     = os.path.splitext(png_name)[0]
        out_path = os.path.join(PER_FILE_DIR, stem + '.csv')

        if os.path.exists(out_path):
            skipped += 1
            continue

        # 1) Primary: original file matched by year-num key
        key      = _fname_key(stem)
        txt_path = orig_index.get(key) if key else None

        # 1b) Try year-range alias: "2001" → also check "2000-2001" (forum used both notations)
        if txt_path is None and key and '-' not in str(key[0]):
            year = int(key[0])
            alias = (f"{year-1}-{year}", key[1])
            txt_path = orig_index.get(alias)

        # 2) Fallback: new-stem file with same stem (93 unique files)
        if txt_path is None:
            direct = os.path.join(TEXT_DIR, stem + '.txt')
            txt_path = direct if os.path.exists(direct) else None

        if txt_path is None:
            print(f"  SKIP (no text file): {png_name}")
            skipped += 1
            continue

        records = process_file(txt_path)
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(records)
        written += 1

    print(f"Per-file: wrote {written} CSVs, skipped {skipped} → ./{PER_FILE_DIR}/")


def _txt_key(path: str):
    """Return (year_str, num) from first lines of a text file."""
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = [f.readline() for _ in range(3)]
    except OSError:
        return None
    for raw in lines:
        line = re.sub(r'\b(\d{2})\.(\d{2})\b', r'\1\2', raw.strip())
        m = PAT_RANGE.match(line)
        if m:
            num = int(m.group(3))
            if num < 1000:
                return (f"{m.group(1)}-{m.group(2)}", num)
        m = PAT_SINGLE.match(line)
        if m:
            num = int(m.group(2))
            if num < 1000:
                return (m.group(1), num)
    return None


if __name__ == '__main__':
    main()
