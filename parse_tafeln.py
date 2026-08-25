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
import unicodedata

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
    # Spurious leading digit (OCR misread of '+' or engraving dot as a digit):
    # '421.12.1942' → '21.12.1942'.  Three digits before first separator = always noise.
    m = re.match(r'\d(\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{4})$', raw)
    if m and len(m.group(0)) > len(m.group(1)):   # only strip when the leading digit was extra
        return clean_date(m.group(1))
    # Fused day+month before year separator: 'DDM.YYYY' → 'DD.M.YYYY'
    # e.g. '141.1917' = '14.1.1917' (day 14, month 1, year 1917)
    m = re.match(r'(\d{2})(\d)[.:,\-]((?:19|20)\d{2})$', raw)
    if m:
        return clean_date(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
    # Fused month+year after day separator: 'DD.MMYYYY' → 'DD.MM.YYYY'
    # e.g. '20.071911' = '20.07.1911' (day 20, month 07, year 1911)
    m = re.match(r'(\d{1,2})[.:,\-](\d{2})(\d{4})$', raw)
    if m:
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}"
    # Fused single-digit month+year: 'DD.MYYYY' → 'DD.MM.YYYY'
    # e.g. '10.91942' = '10.09.1942' (day 10, month 9, year 1942)
    m = re.match(r'(\d{1,2})[.:,\-](\d)((?:19|20)\d{2})$', raw)
    if m:
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}"
    # OCR year-dot noise: 'MM.Y.YYY' → 'MM.YYYY' (e.g. '02.1.943' → '02.1943')
    m = re.match(r'(\d{1,2})[.:,\-](\d)\.(\d{3})$', raw)
    if m:
        return clean_date(f"{m.group(1)}.{m.group(2)}{m.group(3)}")
    # Partial date: MM.YYYY (month + year only, no day engraved on plate)
    m = re.match(r'(\d{1,2})[.:,\-]((?:19|20)\d{2})$', raw)
    if m:
        month_val = int(m.group(1))
        if month_val > 12 and len(m.group(1)) == 2:
            # Fused day+month with dot dropped: '61.1908' = '6.1.1908'
            return clean_date(f"{m.group(1)[0]}.{m.group(1)[1]}.{m.group(2)}")
        return f"{month_val:02d}.{m.group(2)}"
    m = re.match(r'((?:19|20)\d{2})$', raw)
    if m:
        return m.group(1)
    # OCR year with embedded separator: 'DD.MM.YY-D' → 'DD.MM.YY0D' for correction later
    # e.g. '05.12.19-2' → '05.12.1902' → correct_died_year fixes 1902 → 1942
    m = re.match(r'(\d{1,2})[.:,\-](\d{1,2})[.:,\-](\d{2})[.:,\-](\d)$', raw)
    if m:
        return clean_date(f"{m.group(1)}.{m.group(2)}.{m.group(3)}0{m.group(4)}")
    # Fused DDMM.YYYY (4 digits before separator): '0904.1909' → '09.04.1909'
    m = re.match(r'(\d{2})(\d{2})[.:,\-]((?:19|20)\d{2})$', raw)
    if m:
        return clean_date(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
    # Fully fused DDMMYYYY (no separators): '29011922' → '29.01.1922'
    m = re.match(r'(\d{2})(\d{2})((?:19|20)\d{2})$', raw)
    if m:
        return clean_date(f"{m.group(1)}.{m.group(2)}.{m.group(3)}")
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


_NAME_NORM = str.maketrans({'İ': 'I', 'É': 'E', 'À': 'A', 'È': 'E'})


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

    # Repair line-wrap: a line that starts with a death-date marker (+) is a
    # continuation of the last person on the previous line — the plate engraver
    # ran out of space and put the death date at the start of the next row.
    # Move that token to the end of the previous line before flattening.
    _DEATH_LINE_START = re.compile(
        r'^([+\-÷]\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{2,4}'  # +/-/÷ DD.MM.YYYY / DD.MM.YY
        r'|[+\-÷]\d{1,2}[.:,\-]\d{2}\d{4}'               # +/-/÷ DD.MMYYYY (fused month+year)
        r'|[+\-÷]\d{1,2}\.(?:19|20)\d{2}'                 # +/-/÷ MM.YYYY
        r'|[+\-÷](?:19|20)\d{2})'                          # +/-/÷ YYYY bare year
    )
    raw_lines = text.splitlines()
    repaired_lines: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        # Normalize common OCR misreads of '+' at line start so _DEATH_LINE_START fires:
        #  '€7.1.1943'       → '+7.1.1943'   (Euro sign = OCR for +)
        #  '+ 28.12 1942'    → '+28.12.1942' (space after prefix + space-in-date)
        stripped = re.sub(r'^€(\d)', r'+\1', stripped)
        stripped = re.sub(r'^([+\-÷])\s+(\d)', r'\1\2', stripped)
        stripped = re.sub(r'^([+\-÷]\d{1,2}\.\d{1,2})\s+(\d{4})\b', r'\1.\2', stripped)
        m = _DEATH_LINE_START.match(stripped)
        if m and repaired_lines:
            # Attach death date to the previous line
            repaired_lines[-1] = repaired_lines[-1] + ' ' + m.group(1)
            # Keep the rest of this line (next entries after the separator)
            remainder = stripped[m.end():].lstrip(' -–').strip()
            if remainder:
                repaired_lines.append(remainder)
        else:
            repaired_lines.append(stripped)

    # Flatten into single space-separated string
    flat = ' '.join(' '.join(repaired_lines).split())

    # Replace Cyrillic lookalike characters with their Latin equivalents.
    # OCR engines sometimes confuse Latin capital letters with visually identical
    # Cyrillic ones (e.g. BÖHME engraved → ВОНМЕ OCR'd). All Cyrillic here is noise.
    flat = flat.translate(str.maketrans(
        'АВЕКМНОРСТХавекмнорстх',
        'ABEKMHOPCTXabekmnopctx'
    ))

    # Normalize OCR-artifact accented letters that are not German umlauts.
    # e.g. 'Í' (U+00CD, acute I) → 'I', while Ä/Ö/Ü are preserved.
    # Strategy: NFD-decompose, drop all combining marks except diaeresis (U+0308),
    # then NFC-recompose to restore proper Ä/Ö/Ü.
    flat = unicodedata.normalize('NFC', ''.join(
        c for c in unicodedata.normalize('NFD', flat)
        if unicodedata.category(c) != 'Mn' or c == '\u0308'
    ))

    # Fix OCR letter→digit confusion inside year positions of date strings.
    # Pattern: after DD.MM. the year's first digit is sometimes a letter.
    #   T / I / l → 1  (vertical strokes misread as digits)
    #   O          → 0  (round letter misread as zero)
    flat = re.sub(r'(\d{1,2}[.:,\-]\d{1,2}[.:,\-])[Tl](\d{3})', r'\g<1>1\2', flat)
    flat = re.sub(r'(\d{1,2}[.:,\-]\d{1,2}[.:,\-])I(\d{3})',    r'\g<1>1\2', flat)
    flat = re.sub(r'(\d{1,2}[.:,\-]\d{1,2}[.:,\-])O(\d{3})',    r'\g<1>0\2', flat)
    # T/I in the DAY position: 'T0:08.1942' → '10:08.1942'
    flat = re.sub(r'(?<![A-ZÄÖÜ])T(\d[.:,\-]\d{1,2}[.:,\-]\d{4})', r'1\1', flat)
    flat = re.sub(r'(?<![A-ZÄÖÜ])I(\d[.:,\-]\d{1,2}[.:,\-]\d{4})', r'1\1', flat)

    # '$' is misread by OCR for '5' in some fonts; fix before other date processing.
    # e.g. '$.3.1911' → '5.3.1911'  (KAHRS plate: $.3.1911 = 5.3.1911)
    flat = re.sub(r'\$([.:,\-\d])', r'5\1', flat)

    # 'FO' is an OCR misread for '10' when appearing in a date position
    # (F=1, O=0 in some optical fonts). Only fire when not preceded by an uppercase letter
    # (i.e., not inside a name token) and followed by a date separator.
    # e.g. '08. FO.1910' → '08. 10.1910' → then the space-repair rule gives '08.10.1910'
    flat = re.sub(r'(?<![A-ZÄÖÜ])FO([.:,\-])', r'10\1', flat)

    # Replace OCR misreads of '+' as death-date prefix:
    #   '€' (Euro sign) and '=' before a date digit → '+'
    flat = re.sub(r'€(\d)', r'+\1', flat)
    flat = re.sub(r'=(\d{1,2}[.:,\-])', r'+\1', flat)

    # Fix OCR letter-for-digit in the MONTH position of DD.MM.YYYY
    # (must run before the name.date split below so the dot isn't stripped first).
    # Pattern: DD-sep-partial_digit_LETTER-sep-YYYY
    #   '22.0M.1906' → '22.04.1906'  (M looks like 4)
    #   '22.0T.1922' → '22.01.1922'  (T looks like 1)
    #   '04.1T.1919' → '04.11.1919'  (T as second month digit)
    #   '19.1J.1922' → '19.11.1922'  (J looks like 1)
    flat = re.sub(r'(\d{1,2}[.:,\-]\d)M([.:,\-]\d{4})', r'\g<1>4\2', flat)
    flat = re.sub(r'(\d{1,2}[.:,\-]\d)T([.:,\-]\d{4})', r'\g<1>1\2', flat)
    flat = re.sub(r'(\d{1,2}[.:,\-]\d)J([.:,\-]\d{4})', r'\g<1>1\2', flat)
    # Fix OCR letter-for-digit as full single-digit MONTH (before next separator + year):
    #   '17.J.1915' → '17.1.1915'  (J looks like 1 = January)
    #   '23.Z.1922' → '23.7.1922'  (Z looks like 7 = July)
    flat = re.sub(r'(\d{1,2}[.:,\-])J([.:,\-](?:19|20)\d{2})', r'\g<1>1\2', flat)
    flat = re.sub(r'(\d{1,2}[.:,\-])Z([.:,\-](?:19|20)\d{2})', r'\g<1>7\2', flat)
    # Fix OCR letter-for-digit in the DAY or first-part of date (before first separator):
    #   '0Z.1943' → '02.1943'  (Z looks like 2 as second day/month digit)
    #   '0S.03.1919' → '05.03.1919'  (S looks like 5)
    #   '2G.8.1905' → '26.8.1905'  (G looks like 6 as second day digit)
    #   'OS:08,1914' → '05:08,1914'  (O looks like 0, S looks like 5 — must run before S→5)
    flat = re.sub(r'(?<![A-ZÄÖÜ\d])O([S\d][.:,\-])', r'0\1', flat)
    flat = re.sub(r'(\d)Z([.:,\-])', r'\g<1>2\2', flat)
    flat = re.sub(r'(\d)S([.:,\-])', r'\g<1>5\2', flat)
    flat = re.sub(r'(\d)G([.:,\-])', r'\g<1>6\2', flat)

    # Split name token directly fused with a year: 'MULLER1898' → 'MULLER 1898'
    flat = re.sub(r'([A-ZÄÖÜ]{3,})((?:18|19|20)\d{2})(?=[\s+\-÷]|$)', r'\1 \2', flat)

    # Split name token fused with start of date digits: 'RAABE15.09.1' → 'RAABE 15.09.1'
    # Must run AFTER name+year split so MULLER1898 doesn't also match here.
    flat = re.sub(r'([A-ZÄÖÜ]{3,})(\d{1,2})([.:,\-]\d)', r'\1 \2\3', flat)

    # Split name token connected to date digits via a hyphen (OCR noise for blank space):
    # e.g. 'TAUSCH-0904.1909' → 'TAUSCH 0904.1909'
    # Only fires when the char after the hyphen is a digit (preserves NAME-NAME hyphenation).
    flat = re.sub(r'([A-ZÄÖÜ]{3,})-(\d)', r'\1 \2', flat)

    # Rejoin a year split across a space after the leading '1': '15.09.1 23' → '15.09.1923'
    flat = re.sub(r'(\d{1,2}\.\d{1,2}\.1)\s+(\d{2})(?=\s|$)', r'\g<1>9\2', flat)

    # Fix T/I OCR as second digit of day: '1T.08.1913' → '11.08.1913'
    # Must run BEFORE the LETTER.digit split below, otherwise '1T.08.1913' → '1T 08.1913'.
    flat = re.sub(r'(\d)T([.:,\-]\d{1,2}[.:,\-]\d{4})', r'\g<1>1\2', flat)
    flat = re.sub(r'(\d)I([.:,\-]\d{1,2}[.:,\-]\d{4})', r'\g<1>1\2', flat)
    # Fix T/I OCR as single-character month: '9.T.1914' → '9.1.1914'
    flat = re.sub(r'(\d{1,2}[.:,\-])T([.:,\-](?:19|20)\d{2})', r'\g<1>1\2', flat)
    flat = re.sub(r'(\d{1,2}[.:,\-])I([.:,\-](?:19|20)\d{2})', r'\g<1>1\2', flat)

    # Split NAME.DATE fusions where a letter is immediately followed by '.digit':
    # e.g. 'BISHOP.12.06.1920' → 'BISHOP 12.06.1920'
    flat = re.sub(r'([A-ZÄÖÜ])\.(\d)', r'\1 \2', flat)

    # Repair OCR split-date: NAME directly fused with day digits, month+year in next token.
    # e.g. 'GROHMANN15 03.1921' → 'GROHMANN 15.03.1921'
    flat = re.sub(r'([A-ZÄÖÜSS]{2,})(\d{1,2})\s+(\d{1,2}[.:]\d{4})', r'\1 \2.\3', flat)

    # Repair date split by a space after the day-dot: '07. 4.1909' → '07.4.1909'
    # Caused by OCR inserting a space after the dot (dirt/scratch on the plate).
    # The (?<!\d) lookbehind prevents this from fusing two separate adjacent dates,
    # e.g. '1914. 15.06.1942' must NOT become '1914.15.06.1942'.
    flat = re.sub(r'(?<!\d)(\d{1,2})\.\s+(\d{1,2}[.:,\-]\d{2,4})', r'\1.\2', flat)

    # Repair 'DD.MM. YYYY' → 'DD.MM.YYYY' (space before 4-digit year after second dot)
    flat = re.sub(r'(\d{1,2}\.\d{1,2})\.\s+(\d{4})\b', r'\1.\2', flat)

    # Repair 'DD.MM YYYY' → 'DD.MM.YYYY' (space replaces final dot)
    flat = re.sub(r'(\d{1,2}\.\d{1,2})\s+(\d{4})(?!\d)', r'\1.\2', flat)

    # Repair date with missing day-dot: '21 12,1920' → '21.12,1920' (space instead of dot)
    # Matches: 1-2 digit day, space, 1-2 digit month + separator + 4-digit year.
    flat = re.sub(r'(?<!\d)(\d{1,2})\s+(\d{1,2}[.,\-]\d{4})(?!\d)', r'\1.\2', flat)

    # Repair OCR trailing '1' misread as 'I' in all-uppercase name tokens: 'NAWAROTZK1' → 'NAWAROTZKI'
    flat = re.sub(r'([A-ZÄÖÜ]{2,})1(?=\s|$)', r'\1I', flat)

    # Split digit_NAME fusions: '1942_ADOLF' → '1942 ADOLF' (underscore between date and next name)
    flat = re.sub(r'(\d)_([A-ZÄÖÜ])', r'\1 \2', flat)

    # Repair fused NAME•DATE: OCR bullet between a name and a date (e.g. 'MANNSEE•23.02.1914')
    flat = re.sub(r'([A-ZÄÖÜ])•(\d)', r'\1 \2', flat)

    # Split into tokens preserving them
    raw_tokens = re.split(r'\s+', flat)

    # Annotate each token
    tokens = []
    for t in raw_tokens:
        t = t.strip(',;:|\\()[]{}•_')   # • is OCR bullet noise; _ is OCR noise between tokens
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
        tok = tok.lstrip('.+*-÷').rstrip('.,*-÷')
        return bool(re.match(
            r'\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{4}'   # DD.MM.YYYY
            r'|\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{2}$'  # DD.MM.YY
            r'|(?:19|20)\d{2}$'                       # bare YYYY
            r'|\d{1,2}[.:,\-](?:19|20)\d{2}$'        # MM.YYYY partial (any separator)
            r'|\d{1,2}[.:,\-]\d{2}\d{4}$'            # DD.MMYYYY (fused month+year)
            r'|\d{1,2}[.:,\-]\d(?:19|20)\d{2}$'      # DD.MYYYY (single-digit month fused)
            r'|\d{2}\d[.:,\-](?:19|20)\d{2}$'        # DDM.YYYY (fused day+month)
            r'|\d{1,2}\.\d{1}\.\d{3}$'               # MM.Y.YYY OCR noise
            r'|\d{3}[.:,\-]\d{1,2}[.:,\-]\d{4}'     # 3-digit prefix = spurious leading char
            r'|\d{1,2}[.:,\-]\d{1,2}[.:,\-]\d{2}[.:,\-]\d$'  # DD.MM.YY-D (dash in year)
            r'|\d{4}[.:,\-](?:19|20)\d{2}$'           # DDMM.YYYY (fused, no inner dot)
            r'|\d{4}(?:19|20)\d{2}$',                  # DDMMYYYY (fully fused, no separators)
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
        while i < n and not looks_like_date(tokens[i].lstrip('.+*-÷')):
            word = tokens[i].strip('.+*,')
            # Normalize OCR mixed-case name tokens: 'jOHANNES' → 'JOHANNES'
            if word and not any(c.isdigit() for c in word):
                word = word.upper()
            if word in ('DR', 'DR.'):
                # Check whether the next token is MED/JUR → DR.MED. / DR.JUR. compound title
                next_word = tokens[i + 1].strip('.+*,:') if i + 1 < n else ''
                if next_word in ('MED', 'MED.'):
                    names.append('DR.MED.')
                    i += 1   # consume MED; outer i+=1 moves past it
                elif next_word in ('JUR', 'JUR.'):
                    names.append('DR.JUR.')
                    i += 1   # consume JUR; outer i+=1 moves past it
                else:
                    names.append('DR.')   # plain DR. title
            elif is_name_token(word):
                names.append(word)
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
        has_death_prefix = raw_tok.lstrip('.').startswith(('+', '÷'))
        date_raw  = raw_tok.lstrip('.+*-÷').strip('.,*-÷')
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
            # Edge case: OCR/line-wrap puts born date AFTER the death date (next line).
            # If the next token is a plain date (no death prefix), treat it as born.
            if i < n and looks_like_date(tokens[i].lstrip('.+*-÷')):
                if not tokens[i].lstrip('.').startswith(('+', '÷')):
                    born = clean_date(tokens[i].lstrip('.+*-÷').strip('.,*-÷'))
                    i += 1
        else:
            born = first_date
            # Skip any separator tokens (+, -, ., *) between born and died
            while i < n and re.match(r'^[+\-\.\*]+$', tokens[i]):
                i += 1

            # Next date-like token is the death date
            died = inline_died
            if not died and i < n and looks_like_date(tokens[i].lstrip('.+*-÷')):
                died = clean_date(tokens[i].lstrip('.+*-÷').strip('.,'))
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
