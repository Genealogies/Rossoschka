#!/usr/bin/env python3
"""
merge_tafeln.py — Merge two OCR sources into a single high-confidence dataset.

Sources
-------
  System A (new):  rossoschka_tafeln_text/  (484 files, 94% clean)
  System B (old):  rossoschka_text_vision/  (577 files, 91% clean)

Strategy
--------
  1. Parse both systems; filter noise from each.
  2. For each tafel, start with System A as primary.
  3. Add System B records that A missed (after noise filtering).
  4. For matching records (same lastname + born), prefer A — but pick the
     richer value for each field (prefer non-empty over empty).
  5. Tag every output record with its source confidence:
       "A"        — only in A (clean)
       "B"        — only in B (clean), A had no file or missed it
       "AB"       — both agree (highest confidence)
       "AB_diff"  — both have it, fields differ (manual review candidate)

Outputs
-------
  rossoschka_merged.csv             — combined, one row per person
  rossoschka_merge_report.txt       — summary + disagreement samples

Usage
-----
  python3 merge_tafeln.py
  python3 merge_tafeln.py --report-only   # print report without writing CSV
"""

import os, re, csv, sys, unicodedata
from collections import defaultdict

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DIR_A        = "rossoschka_tafeln_text"
DIR_B        = "rossoschka_text_vision"
OUT_CSV       = "rossoschka_merged.csv"
OUT_REPORT    = "rossoschka_merge_report.txt"
PER_FILE_DIR  = "rossoschka_tafeln_textlist"
FIELDNAMES    = ['tafel', 'firstname', 'lastname', 'born', 'died', 'source']
PF_FIELDNAMES = ['tafel', 'firstname', 'lastname', 'born', 'died']   # no source col in per-file

# ---------------------------------------------------------------------------
# Noise filter
# ---------------------------------------------------------------------------

# Valid lastname: 3+ uppercase letters (German alphabet), optional hyphen
# Also accepts U+0130 (İ) and other OCR-introduced Unicode variants
VALID_LAST = re.compile(r'^[A-ZÄÖÜÉSS\-\u0130\u00C9\u00C0\u00C8]{3,}$')

_SPECIAL_NAME_CHARS = str.maketrans({
    'ß': 'SS', 'ẞ': 'SS',
    'Ł': 'L', 'ł': 'L',
    'Đ': 'D', 'đ': 'D',
    # Eastern European / accented letters → ASCII base (Ä, Ö, Ü kept intact by NFKD guard)
    'Á': 'A', 'á': 'A', 'Â': 'A', 'â': 'A', 'Ã': 'A', 'ã': 'A',
    'À': 'A', 'à': 'A', 'Ą': 'A', 'ą': 'A', 'Å': 'A', 'å': 'A',
    'Ă': 'A', 'ă': 'A',
    'Ç': 'C', 'ç': 'C', 'Ć': 'C', 'ć': 'C', 'Č': 'C', 'č': 'C',
    'Ę': 'E', 'ę': 'E', 'É': 'E', 'é': 'E', 'Ê': 'E', 'ê': 'E',
    'È': 'E', 'è': 'E', 'Ě': 'E', 'ě': 'E',
    'Ğ': 'G', 'ğ': 'G',
    'Í': 'I', 'í': 'I', 'Î': 'I', 'î': 'I', 'Ì': 'I', 'ì': 'I',
    'İ': 'I', 'ı': 'I',
    'Ĺ': 'L', 'ĺ': 'L',
    'Ń': 'N', 'ń': 'N', 'Ñ': 'N', 'ñ': 'N', 'Ň': 'N', 'ň': 'N',
    'Ó': 'O', 'ó': 'O', 'Ô': 'O', 'ô': 'O', 'Ő': 'O', 'ő': 'O',
    'Ò': 'O', 'ò': 'O', 'Ø': 'O', 'ø': 'O',
    'Ř': 'R', 'ř': 'R',
    'Ś': 'S', 'ś': 'S', 'Ş': 'S', 'ş': 'S', 'Ș': 'S', 'ș': 'S',
    'Š': 'S', 'š': 'S',
    'Ț': 'T', 'ț': 'T', 'Ţ': 'T', 'ţ': 'T',
    'Ú': 'U', 'ú': 'U', 'Û': 'U', 'û': 'U', 'Ű': 'U', 'ű': 'U',
    'Ù': 'U', 'ù': 'U',
    'Ý': 'Y', 'ý': 'Y',
    'Ź': 'Z', 'ź': 'Z', 'Ż': 'Z', 'ż': 'Z', 'Ž': 'Z', 'ž': 'Z',
    '[': '', ']': '', '(': '', ')': '', '?': '', '!': '', '|': '',
    '\'': '', '\u2019': '',  # apostrophe variants: D'ELSA → DELSA, KEILACK' → KEILACK
})
# Valid born formats accepted (most to least complete):
#   DD.MM.YYYY  e.g. 22.09.1906
#   YYYY        e.g. 1942
#   MM.YYYY     e.g. 03.1943  (day unknown)
#   DD.MM.YY    e.g. 21.12.42 (2-digit year, will be normalised)
VALID_BORN = re.compile(
    r'^\d{2}\.\d{2}\.\d{4}$'      # DD.MM.YYYY
    r'|^(?:19|20)\d{2}$'           # YYYY
    r'|^\d{2}\.(?:19|20)\d{2}$'   # MM.YYYY
    r'|^\d{2}\.\d{2}\.\d{2}$'     # DD.MM.YY  (2-digit year)
)
# Valid died: same as born, or empty
VALID_DIED = re.compile(
    r'^\d{2}\.\d{2}\.\d{4}$'
    r'|^(?:19|20)\d{2}$'
    r'|^\d{2}\.(?:19|20)\d{2}$'
    r'|^\d{2}\.\d{2}\.\d{2}$'
    r'|^$'
)

_2DIGIT_YEAR = re.compile(r'^(\d{2}\.\d{2}\.)(\d{2})$')

# Detects OCR concatenation like "AXMANN18.02.1904" or "BACHMANN.26.02.1910+02.1943"
_CONCAT_NAME_DATE = re.compile(
    r'^([A-ZÄÖÜSS]{2,})'            # name prefix (the actual lastname)
    r'[.\-\'\":/\_,+*]?'             # optional OCR separator
    r'(\d{1,2}[.:]\d{1,2}[.:]\d{2,4}'  # DD.MM.YYYY or DD.MM.YY
    r'|\d{2}\.(?:19|20)\d{2}'       # MM.YYYY
    r'|(?:19|20)\d{2})'             # YYYY
    r'(.*)'                          # remainder (may contain died date)
)
# Match a died date in the remainder — OCR often substitutes '4' for '+'
_DATE_IN_REMAINDER = re.compile(
    r'[+\-\.4÷](\d{1,2}[.:]\d{1,2}[.:]\d{2,4}'
    r'|\d{2}\.(?:19|20)\d{2}'
    r'|(?:19|20)\d{2})'
)
# born=YYYY+died  e.g. '1919+17.09.1942'
_YEAR_PLUS_DATE = re.compile(
    r'^((?:19|20)\d{2})[+\-÷,]'
    r'(\d{1,2}[.:]\d{1,2}[.:]\d{2,4}'
    r'|\d{2}\.(?:19|20)\d{2}'
    r'|(?:19|20)\d{2})$'
)
# born=DD.MM (no year) where the year is in died field
_DD_MM = re.compile(r'^(\d{2})\.(\d{2})$')

# OCR letter-for-digit substitutions within date strings.
# Each tuple: (pattern, replacement).
# Context rules used:
#   (?<=\d) or (?<=\.) — preceded by digit or dot separator
#   (?=[\d.]) or (?=\d) or $ — followed by digit, dot, or end-of-string
_OCR_IN_YEAR = [
    # T/t/I/l/ı/Г/J/j/f → 1  (very common: 04.1T.1919, 15.1t.1908, 19T5, 04.07.J914)
    (re.compile(r'(?<=\d)[TtIlıГLJjf](?=[\d.+\-÷]|$)'), '1'),
    (re.compile(r'(?<=\.)[TtIlıГfLJj](?=\d)'),           '1'),  # after sep: .T9 → .19; .J9 → .19
    (re.compile(r'^[TtIlıГLJj](?=[\d.])'),               '1'),  # leading: J914 → 1914
    (re.compile(r'(?<=\.)([JjTtIlıГfL])(?=\.)'),         '1'),
    # B → 8  (190B → 1908, 27.03.190B, 190B+... → 1908+...)
    (re.compile(r'(?<=\d)[B](?=[\d.+\-÷]|$)'),    '8'),
    (re.compile(r'(?<=\.)[B](?=\d)'),             '8'),
    (re.compile(r'(?<=\.)([B])(?=\.)'),           '8'),
    # Z/z → 2  (0Z.1943 → 02.1943, 190Z+... → 1902+...)
    (re.compile(r'(?<=\d)[Zz](?=[\d.+\-÷]|$)'),   '2'),
    (re.compile(r'(?<=\.)[Zz](?=\d)'),            '2'),
    # O/o → 0  (must come before F→1 so 19FO → 19F0 → 1910)
    (re.compile(r'(?<=\d)[Oo](?=[\d.]|$)'),       '0'),
    (re.compile(r'(?<=\.)[Oo](?=\d)'),            '0'),
    # D → 0  (191D → 1910, D8.12 → 08.12)
    (re.compile(r'(?<=\d)[D](?=[\d.]|$)'),        '0'),
    (re.compile(r'^[D](?=\d)'),                   '0'),  # leading D: D8.12 → 08.12
    # G → 6
    (re.compile(r'(?<=\d)[G](?=[\d.]|$)'),        '6'),
    # J/j → 1 already covered above; keeping explicit standalone for clarity
    (re.compile(r'(?<=\d)[Jj](?=[\d.]|$)'),       '1'),
    (re.compile(r'^[Jj](?=\d)'),                  '1'),
    # S/s → 5 (also at start followed by dot, e.g. S.1.1913 → 5.1.1913)
    (re.compile(r'(?<=\d)[Ss](?=[\d.+\-÷]|$)'),   '5'),
    (re.compile(r'(?<=\.)[Ss](?=\d)'),            '5'),
    (re.compile(r'^[Ss](?=[\d.])'),               '5'),  # S.1.1913 → 5.1.1913
    (re.compile(r'(?<=\.)([Ss])(?=\.)'),          '5'),
    # F/f → 1  (19FO → 1910 after O→0 above, 23.09.19F4 → 23.09.1914)
    (re.compile(r'(?<=\d)[Ff](?=[\d.+\-÷]|$)'),  '1'),
    (re.compile(r'(?<=\.)[Ff](?=\d)'),            '1'),
    # Two-char OCR pair FO → 10 when F precedes O and neither is adjacent to a digit
    (re.compile(r'(?<=\d)[Ff][Oo](?=[\d.]|$)'),   '10'),
    # İ/I + Z/z at start → 12  (İz.1.1911 → 12.1.1911)
    (re.compile(r'^[Iİil][Zz](?=[\d.])'),         '12'),
    # Cyrillic л/Л → 1  (9л1.1922 → 91.1922)
    (re.compile(r'(?<=\d)\u043b(?=[\d.]|$)'),     '1'),
    (re.compile(r'(?<=\.)\u043b(?=\d)'),          '1'),
    (re.compile(r'^\u043b(?=\d)'),                '1'),
    # ] [ ( ) → 1  (]921 → 1921, (912 → 1912 after leading strip)
    (re.compile(r'(?<=\.)[]\[()\]](?=\d)'),        '1'),  # after sep: .]921 → .1921
    (re.compile(r'(?<=\d)[]\[()\]](?=[\d.]|$)'),  '1'),
]


def normalize_name_chars(s: str) -> str:
    """Normalize accented/special chars in names to uppercase German-compatible equivalents.
    Preserves Ä, Ö, Ü. Converts ß→SS, Ł→L, Á→A, Ç→C, etc. Strips bracket/noise chars."""
    return s.translate(_SPECIAL_NAME_CHARS).upper()


def normalize_date_field_chars(s: str) -> str:
    """Normalize accented/special chars in a date field WITHOUT forcing uppercase.
    Applies the same character translation as normalize_name_chars but preserves case,
    so lowercase OCR garbage in born/died is not accidentally treated as a name."""
    return s.translate(_SPECIAL_NAME_CHARS)


def sanitize_date_str(s: str) -> str:
    """
    Pre-clean an OCR date string before normalization:
      - Strip leading noise chars (•, :, ', [, ), ], `, etc.)
      - Normalize separators (; / _ °) → .
      - Fix comma as separator: 22,13921 → 22.13921 etc.
      - Replace OCR letter-for-digit substitutions within digit runs
      - Collapse spurious extra dot in year: 191.1 → 1911, 1.920 → 1920
    """
    if not s:
        return s
    # 1. Strip leading noise (including _ and . which OCR sometimes adds before names/dates)
    s = re.sub(r'^[•:\'\`\[\(\]\)\s\°฿/_\.]+', '', s)
    # 2. Normalize separators to '.'
    s = re.sub(r'[;°]', '.', s)
    s = re.sub(r'_', '.', s)
    # Comma used as separator between digits (not thousands): 22,13 → 22.13
    s = re.sub(r'(\d),(\d)', r'\1.\2', s)
    # Slash as date separator: 26.04/1915 → 26.04.1915
    s = re.sub(r'(\d)/(\d)', r'\1.\2', s)
    # Colon as date separator between digits: 09:11 → 09.11
    s = re.sub(r'(\d):(\d)', r'\1.\2', s)
    # x/X as date separator between digits: 20.05x1923 → 20.05.1923
    s = re.sub(r'(\d)[xX](\d)', r'\1.\2', s)
    # Stray colon at end of partial year before born/died separator: 191:+... → 191+...
    s = re.sub(r'(\d{3,4}):[+\-÷]', r'\1+', s)
    s = re.sub(r'[+\-÷]{2,}', '+', s)
    s = re.sub(r'(\d{4}),([+\-÷])', r'\1\2', s)
    # Strip OCR bracket noise embedded within uppercase name fragments: KO[SCHEN → KOSCHEN
    s = re.sub(r'(?<=[A-ZÄÖÜSS])[\[\]()?!](?=[A-ZÄÖÜSS])', '', s)
    # 3. OCR letter substitutions within date context
    for pattern, replacement in _OCR_IN_YEAR:
        s = pattern.sub(replacement, s)
    # 4. Spurious extra dot within a year in date strings.
    # Case A: 4-segment dates like DD.MM.19.12 → DD.MM.1912 or DD.MM.191.1 → DD.MM.1911
    def _fix_4seg_year(m: re.Match) -> str:
        d, mo, ya, yb = m.group(1), m.group(2), m.group(3), m.group(4)
        combined_year = ya + yb
        if len(combined_year) == 4 and 1880 <= int(combined_year) <= 2050:
            return f"{d}.{mo}.{combined_year}"
        return m.group(0)
    s = re.sub(r'\b(\d{1,2})\.(\d{1,2})\.(\d{1,3})\.(\d{1,3})\b', _fix_4seg_year, s)
    # Case B: standalone split year at end of string: 1.922 → 1922, 1.920 → 1920
    def _fix_standalone_year(m: re.Match) -> str:
        combined = m.group(1) + m.group(2)
        if len(combined) == 4 and 1880 <= int(combined) <= 2050:
            return combined
        return m.group(0)
    s = re.sub(r'(?<![.\d])(\d{1,3})\.(\d{1,3})(?![.\d])', _fix_standalone_year, s)
    return s.strip()


def normalise_date(d: str) -> str:
    """Sanitize OCR noise then expand 2-digit year: '21.12.42' → '21.12.1942'.
    Also zero-pads single-digit day or month: '6.10.1914' → '06.10.1914'."""
    d = sanitize_date_str(d)
    m = _2DIGIT_YEAR.match(d)
    if m:
        yy = int(m.group(2))
        century = '19' if yy >= 0 else '20'   # all records are 1900s
        d = f"{m.group(1)}{century}{m.group(2)}"
    # Zero-pad single-digit day or month in DD.MM.YYYY
    d = re.sub(r'^(\d)\.(\d{2})\.(\d{4})$', r'0\1.\2.\3', d)
    d = re.sub(r'^(\d{2})\.(\d)\.(\d{4})$', r'\1.0\2.\3', d)
    d = re.sub(r'^(\d)\.(\d)\.(\d{4})$',    r'0\1.0\2.\3', d)
    return d


_DIED_DATE_YEAR = re.compile(r'^(\d{2}\.\d{2}\.)(\d{4})$')

def _correct_date_year(d: str, valid_lo: int, valid_hi: int, fallback: int) -> str:
    """Fix a single OCR digit error in the year of a DD.MM.YYYY date string.

    Tries replacements of each digit in order of OCR likelihood and returns
    the first candidate whose year falls in [valid_lo, valid_hi].
    Falls back to fallback year if no single-digit fix works.
    """
    m = _DIED_DATE_YEAR.match(d)
    if not m:
        return d
    year_str = m.group(2)
    y = int(year_str)
    if valid_lo <= y <= valid_hi:
        return d
    candidates = [
        int('1' + year_str[1:]),               # first digit → 1  (4922 → 1922, 7942 → 1942)
        int(year_str[0] + '9' + year_str[2:]), # second digit → 9 (1042 → 1942)
        int(year_str[:2] + '4' + year_str[3]), # third digit → 4  (1912 → 1942)
    ]
    for last in '0123456789':                  # last digit off (1941 → 1942)
        candidates.append(int(year_str[:3] + last))
    for c in candidates:
        if valid_lo <= c <= valid_hi:
            return m.group(1) + str(c)
    return m.group(1) + str(fallback)


def correct_born_year(d: str) -> str:
    """Fix OCR single-digit errors in a birth-date year.
    Soldiers buried at Rossoschka were born roughly 1870–1935."""
    return _correct_date_year(d, valid_lo=1870, valid_hi=1935, fallback=1900)


def correct_died_year(d: str) -> str:
    """Fix OCR single-digit errors in a death-date year.
    Deaths at Rossoschka are 1942–1999."""
    return _correct_date_year(d, valid_lo=1942, valid_hi=1999, fallback=1942)


def rescue_record(r: dict) -> dict | None:
    """
    Recover a record where OCR concatenated the lastname with the born date,
    e.g. born='AXMANN18.02.1904'  →  lastname='AXMANN', born='18.02.1904'
         born='BACHMANN.26.02.1910+02.1943'  →  lastname='BACHMANN', born='26.02.1910', died='02.1943'
    Returns a corrected record, or None if the pattern doesn't match.
    """
    born = r.get('born', '')
    m = _CONCAT_NAME_DATE.match(born)
    if not m:
        return None
    actual_lastname = m.group(1)
    actual_born     = normalise_date(m.group(2))
    remainder       = m.group(3)
    actual_died = r.get('died', '')
    if not actual_died:
        dm = _DATE_IN_REMAINDER.search(remainder)
        if dm:
            actual_died = normalise_date(dm.group(1))
    return {
        'tafel':     r['tafel'],
        'firstname': r['lastname'],   # misidentified lastname was actually firstname
        'lastname':  actual_lastname,
        'born':      actual_born,
        'died':      normalise_date(actual_died),
    }


def rescue_year_plus_died(r: dict) -> dict | None:
    """
    Recover a record where born contains 'YYYY+died', e.g. '1919+17.09.1942'.
    Returns a corrected record, or None if the pattern doesn't match.
    """
    born = r.get('born', '')
    m = _YEAR_PLUS_DATE.match(born)
    if not m:
        return None
    return {**r, 'born': m.group(1), 'died': normalise_date(m.group(2))}


def rescue_dd_mm_no_year(r: dict) -> dict | None:
    """
    Recover a record where the parser split DD.MM.YYYY into born='DD.MM' / died='YYYY'.
    Only applies when died looks like a birth year (before 1941).
    e.g. born='20.01', died='1924'  →  born='20.01.1924', died=''
    """
    born = r.get('born', '')
    died = r.get('died', '')
    m_born = _DD_MM.match(born)
    m_died = re.match(r'^((?:18|19)\d{2})$', died)
    if not m_born or not m_died:
        return None
    year = int(m_died.group(1))
    if year >= 1941:   # plausible death year — don't reassign
        return None
    return {**r, 'born': f"{born}.{died}", 'died': ''}


def rescue_date_plus_died(r: dict) -> dict | None:
    """
    Recover when born contains 'DD.MM.YYYY+DD.MM.YYYY' (birth+death concatenated).
    e.g. born='26.04.1915+08.01.1942' → born='26.04.1915', died='08.01.1942'
    """
    born = r.get('born', '')
    m = re.match(
        r'^(\d{1,2}\.\d{1,2}\.\d{2,4})'  # birth date
        r'[+\-÷]'                          # separator
        r'(\d{1,2}[.:]\d{1,2}[.:]\d{2,4}'  # death date variants
        r'|\d{2}\.(?:19|20)\d{2}'
        r'|(?:19|20)\d{2})$',
        born
    )
    if not m:
        return None
    result = dict(r)
    result['born'] = normalise_date(m.group(1))
    if not result.get('died'):
        result['died'] = normalise_date(m.group(2))
    return result


# Matches born field that contains only a name (no date) — OCR placed surname in wrong col.
# e.g. born='KEIL:' or born='KRISTEN:' or born='ÄKEL'
_NAME_ONLY_IN_BORN = re.compile(r'^([A-ZÄÖÜSS]{3,})[.:\-,+!*?•]?$')


def rescue_name_only_in_born(r: dict) -> dict | None:
    """
    Recover a record where OCR placed the actual lastname in the born field
    with no accompanying date, e.g. born='KEIL:' or born='ÄKEL'.
    The old lastname is promoted to firstname.
    """
    born = r.get('born', '')
    m = _NAME_ONLY_IN_BORN.match(born)
    if not m:
        return None
    actual_lastname = m.group(1)
    return {
        'tafel':     r['tafel'],
        'firstname': r.get('lastname', ''),
        'lastname':  actual_lastname,
        'born':      '',
        'died':      r.get('died', ''),
    }


def is_clean(r: dict) -> bool:
    """
    Return True if record passes noise filter.
    Lastname must be a valid German uppercase surname (3+ chars).
    Born/died are accepted if:
      - empty, OR
      - match a known date format, OR
      - contain only digits and date separators (truncated OCR date fragment).
    Records with letters/symbols in born/died (e.g. 'Ü', 'Pt', '@') are dropped.
    """
    if not VALID_LAST.match(r['lastname']):
        return False
    for field in ('born', 'died'):
        val = r.get(field, '')
        if not val:
            continue
        if VALID_BORN.match(val) if field == 'born' else VALID_DIED.match(val):
            continue
        # Accept pure digit/separator fragments (OCR-truncated date)
        if re.match(r'^[\d.\-:/]{1,10}$', val):
            continue
        return False
    return True


# ---------------------------------------------------------------------------
# Parser (reuse from parse_tafeln.py)
# ---------------------------------------------------------------------------

# Import parse_text and tafel_label from sibling module
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "parse_tafeln",
    os.path.join(os.path.dirname(__file__), "parse_tafeln.py"),
)
_mod = importlib.util.load_from_spec = None
try:
    from parse_tafeln import parse_text, tafel_label
except ImportError:
    print("ERROR: parse_tafeln.py not found in the same directory.")
    sys.exit(1)


_FNAME_RANGE  = re.compile(r'^((?:19|20)\d{2}-(?:19|20)\d{2})-0*(\d+)')
_FNAME_SINGLE = re.compile(r'^((?:19|20)\d{2})-0*(\d+)')


def label_from_filename(fname: str) -> str:
    """Derive a clean tafel label from the filename stem (most reliable source)."""
    stem = fname.removesuffix('.txt')
    m = _FNAME_RANGE.match(stem)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):03d}"
    m = _FNAME_SINGLE.match(stem)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):03d}"
    return stem


def read_clean_records(path: str, fname: str) -> list[dict]:
    """Parse a text file and return only clean records, with filename-based tafel label."""
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    label   = label_from_filename(fname)
    records = parse_text(text)
    result  = []
    for r in records:
        r['tafel']    = label
        r['born']     = correct_born_year(
                            normalise_date(normalize_date_field_chars(r.get('born') or ''))
                        )
        r['died']     = correct_died_year(
                            normalise_date(normalize_date_field_chars(r.get('died') or ''))
                        )
        r['lastname'] = normalize_name_chars(r.get('lastname', ''))
        # Try all rescues first (they improve data quality even when is_clean would pass)
        for rescue_fn in (rescue_record, rescue_year_plus_died, rescue_dd_mm_no_year, rescue_date_plus_died, rescue_name_only_in_born):
            improved = rescue_fn(r)
            if improved and is_clean(improved):
                r = improved
                break
        if is_clean(r):
            result.append(r)
    return result


# ---------------------------------------------------------------------------
# Load both systems
# ---------------------------------------------------------------------------

def load_system(directory: str) -> dict[str, list[dict]]:
    """Return {filename_stem: [clean_records]} for all .txt files in directory."""
    result = {}
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith('.txt'):
            continue
        path    = os.path.join(directory, fname)
        records = read_clean_records(path, fname)
        result[fname] = records
    return result


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def record_key(r: dict) -> tuple:
    """Stable identity key for a person record."""
    return (r['lastname'].upper(), r['born'])


def merge_fields(ra: dict, rb: dict) -> tuple[dict, bool]:
    """
    Merge two records with the same key.
    Prefer non-empty values; prefer A for ties.
    Returns (merged_record, had_conflict).
    """
    conflict = False
    merged   = dict(ra)  # start with A

    for field in ('firstname', 'died'):
        va = (ra.get(field) or '').strip()
        vb = (rb.get(field) or '').strip()
        if not va and vb:
            merged[field] = vb       # A missing, B has it → take B
        elif va and vb and va != vb:
            conflict = True          # both have it but disagree → keep A
        # else: both same or only A has it → keep A (already set)

    return merged, conflict


def merge_two_lists(
    recs_a: list[dict],
    recs_b: list[dict],
) -> tuple[list[dict], dict]:
    """
    Merge records from A (primary) and B (supplement).
    Returns (merged_list, stats_dict).
    """
    map_a = {record_key(r): r for r in recs_a}
    map_b = {record_key(r): r for r in recs_b}

    merged   = []
    stats    = defaultdict(int)

    # Keys present in A
    for key, ra in map_a.items():
        if key in map_b:
            merged_rec, conflict = merge_fields(ra, map_b[key])
            merged_rec['source'] = 'AB_diff' if conflict else 'AB'
            stats['AB_diff' if conflict else 'AB'] += 1
        else:
            merged_rec           = dict(ra)
            merged_rec['source'] = 'A'
            stats['A'] += 1
        merged.append(merged_rec)

    # Keys only in B (A missed them)
    for key, rb in map_b.items():
        if key not in map_a:
            rec           = dict(rb)
            rec['source'] = 'B'
            stats['B'] += 1
            merged.append(rec)

    return merged, dict(stats)


# ---------------------------------------------------------------------------
# Per-file output
# ---------------------------------------------------------------------------

def _write_per_file(per_file_data: dict[str, list[dict]]):
    """
    Write one CSV per tafel into rossoschka_tafeln_textlist/, overwriting
    any existing files so they reflect the merged (A+B) data.
    Records are sorted by lastname then born for consistent ordering.
    """
    os.makedirs(PER_FILE_DIR, exist_ok=True)
    written = 0
    for fname, records in per_file_data.items():
        stem     = fname.removesuffix('.txt')
        out_path = os.path.join(PER_FILE_DIR, stem + '.csv')
        # Strip internal 'source' column — per-file CSVs are consumer-facing
        rows = [{k: r[k] for k in PF_FIELDNAMES} for r in records]
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=PF_FIELDNAMES, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        written += 1
    print(f"Wrote {written} per-file CSVs → {PER_FILE_DIR}/")


# ---------------------------------------------------------------------------
# Post-processing: umlaut normalisation
# ---------------------------------------------------------------------------

def _strip_umlauts(s: str) -> str:
    return s.replace('Ä','A').replace('Ö','O').replace('Ü','U')


def normalize_name_umlauts(records: list[dict]) -> list[dict]:
    """Resolve U/Ü, A/Ä, O/Ö confusion introduced by OCR.

    Strategy: for each group of name variants that differ only in umlauts,
    prefer the umlaut form — UNLESS the plain form appears more than 10×
    as often (strong evidence the umlaut was falsely added by OCR).

    Applied to both firstname and lastname fields.
    """
    from collections import Counter, defaultdict

    fn_counts: Counter = Counter()
    ln_counts: Counter = Counter()
    for r in records:
        if r.get('firstname'): fn_counts[r['firstname']] += 1
        if r.get('lastname'):  ln_counts[r['lastname']]  += 1

    def _build_canon(counts: Counter) -> dict[str, str]:
        groups: dict[str, dict] = defaultdict(dict)
        for name, cnt in counts.items():
            groups[_strip_umlauts(name)][name] = cnt
        corrections: dict[str, str] = {}
        for variants in groups.values():
            if len(variants) == 1:
                continue
            umlaut_forms = {v: c for v, c in variants.items()
                            if any(ch in v for ch in 'ÄÖÜ')}
            plain_forms  = {v: c for v, c in variants.items() if v not in umlaut_forms}
            if not umlaut_forms:
                continue
            best_umlaut = max(umlaut_forms, key=lambda v: umlaut_forms[v])
            u_count     = umlaut_forms[best_umlaut]
            if plain_forms:
                best_plain = max(plain_forms, key=lambda v: plain_forms[v])
                n_count    = plain_forms[best_plain]
                canonical  = best_plain if n_count > 10 * u_count else best_umlaut
            else:
                canonical = best_umlaut
            for v in variants:
                if v != canonical:
                    corrections[v] = canonical
        return corrections

    fn_canon = _build_canon(fn_counts)
    ln_canon = _build_canon(ln_counts)

    result = []
    for r in records:
        r = dict(r)
        if r.get('firstname') in fn_canon:
            r['firstname'] = fn_canon[r['firstname']]
        if r.get('lastname') in ln_canon:
            r['lastname'] = ln_canon[r['lastname']]
        result.append(r)
    return result


# ---------------------------------------------------------------------------
# Post-processing: explicit name corrections
# ---------------------------------------------------------------------------

NAME_CORRECTIONS_FILE = "name_corrections.csv"


def apply_name_corrections(records: list[dict]) -> list[dict]:
    """Apply explicit bulk name corrections from name_corrections.csv.

    Used for OCR failures where the correct umlaut form *never* appears in
    the dataset (so frequency-based normalization cannot detect the error).

    File format (CSV with header): field,wrong,correct
      field   — 'lastname' or 'firstname'
      wrong   — the OCR-produced string to replace
      correct — the correct replacement string

    Add new rows to name_corrections.csv whenever you discover a systematic
    OCR miss that cannot be auto-detected.
    """
    import pathlib

    corrections_path = pathlib.Path(NAME_CORRECTIONS_FILE)
    if not corrections_path.exists():
        return records

    ln_corr: dict[str, str] = {}
    fn_corr: dict[str, str] = {}
    with corrections_path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            field   = (row.get('field')   or '').strip().lower()
            wrong   = (row.get('wrong')   or '').strip()
            correct = (row.get('correct') or '').strip()
            if not wrong or not correct:
                continue
            if field == 'lastname':
                ln_corr[wrong] = correct
            elif field == 'firstname':
                fn_corr[wrong] = correct

    total = 0
    result = []
    for r in records:
        r = dict(r)
        if r.get('lastname')  in ln_corr:
            r['lastname']  = ln_corr[r['lastname']]
            total += 1
        if r.get('firstname') in fn_corr:
            r['firstname'] = fn_corr[r['firstname']]
            total += 1
        result.append(r)
    if total:
        print(f"  Applied {total} explicit name correction(s) from {NAME_CORRECTIONS_FILE}")
    return result


# ---------------------------------------------------------------------------
# Post-processing: manual corrections
# ---------------------------------------------------------------------------

MANUAL_CORRECTIONS_FILE = "manual_corrections.csv"
MANUAL_FIELDNAMES       = ['action', 'tafel', 'firstname', 'lastname', 'born', 'died']


def apply_manual_corrections(
    all_records: list[dict],
    per_file_data: dict[str, list[dict]],
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Apply manual additions/updates/deletions from manual_corrections.csv.

    Each row in manual_corrections.csv has:
      action    — 'add' | 'update' | 'delete'
      tafel     — tafel label (e.g. 1998-002)
      firstname — first name (may be empty)
      lastname  — last name (required)
      born      — birth date in DD.MM.YYYY (may be empty)
      died      — death date in DD.MM.YYYY (may be empty)

    'add'    inserts the record if no existing record matches (lastname+born).
    'update' replaces non-empty correction fields on the matching record.
    'delete' removes the matching record.
    """
    if not os.path.exists(MANUAL_CORRECTIONS_FILE):
        return all_records, per_file_data

    corrections = []
    with open(MANUAL_CORRECTIONS_FILE, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            corrections.append(row)

    if not corrections:
        return all_records, per_file_data

    def _key(tafel: str, lastname: str, born: str) -> tuple:
        return (tafel.strip(), lastname.strip().upper(), born.strip())

    # Build lookup for existing records
    existing_keys = {_key(r['tafel'], r['lastname'], r['born']) for r in all_records}

    adds    = 0
    updates = 0
    deletes = 0

    for c in corrections:
        action = c.get('action', '').strip().lower()
        key    = _key(c.get('tafel',''), c.get('lastname',''), c.get('born',''))

        if action == 'add':
            if key not in existing_keys:
                new_rec = {
                    'tafel':     c['tafel'].strip(),
                    'firstname': c.get('firstname','').strip(),
                    'lastname':  c['lastname'].strip(),
                    'born':      c.get('born','').strip(),
                    'died':      correct_died_year(c.get('died','').strip()),
                    'source':    'manual',
                }
                all_records.append(new_rec)
                existing_keys.add(key)
                # Add to per_file_data under any matching fname key
                for fname, recs in per_file_data.items():
                    if label_from_filename(fname) == c['tafel'].strip():
                        recs.append(new_rec)
                        break
                adds += 1

        elif action == 'update':
            for r in all_records:
                if _key(r['tafel'], r['lastname'], r['born']) == key:
                    for field in ('firstname', 'died'):
                        val = c.get(field, '').strip()
                        if val:
                            r[field] = correct_died_year(val) if field == 'died' else val
                    updates += 1
                    break

        elif action == 'delete':
            all_records = [r for r in all_records
                           if _key(r['tafel'], r['lastname'], r['born']) != key]
            for fname in per_file_data:
                per_file_data[fname] = [r for r in per_file_data[fname]
                                        if _key(r['tafel'], r['lastname'], r['born']) != key]
            deletes += 1

    print(f"Manual corrections applied: {adds} added, {updates} updated, {deletes} deleted")
    return all_records, per_file_data




def main():
    report_only = '--report-only' in sys.argv
    per_file    = '--per-file'    in sys.argv

    print("Loading System A (rossoschka_tafeln_text/)…")
    sys_a = load_system(DIR_A)
    print(f"  {len(sys_a)} files, {sum(len(v) for v in sys_a.values())} clean records")

    print("Loading System B (rossoschka_text_vision/)…")
    sys_b = load_system(DIR_B)
    print(f"  {len(sys_b)} files, {sum(len(v) for v in sys_b.values())} clean records\n")

    all_files = sorted(set(sys_a) | set(sys_b))

    all_records    = []
    per_file_data  = {}   # fname → merged records (without source col)
    total_stats    = defaultdict(int)
    disagreements  = []

    for fname in all_files:
        recs_a = sys_a.get(fname, [])
        recs_b = sys_b.get(fname, [])

        merged, stats = merge_two_lists(recs_a, recs_b)
        all_records.extend(merged)
        per_file_data[fname] = merged

        for k, v in stats.items():
            total_stats[k] += v

        if len(disagreements) < 50:
            diffs = [r for r in merged if r['source'] == 'AB_diff']
            disagreements.extend(diffs[:5])

    # -------------------------------------------------------------------
    # Post-processing
    # -------------------------------------------------------------------
    print("Post-processing: normalising name umlauts…")
    all_records = normalize_name_umlauts(all_records)
    print("Post-processing: applying explicit name corrections…")
    all_records = apply_name_corrections(all_records)
    # Propagate umlaut/name fixes back into per_file_data
    per_file_lookup: dict[tuple, dict] = {
        (r['tafel'], r['lastname'], r['born']): r for r in all_records
    }
    for fname in per_file_data:
        per_file_data[fname] = [
            per_file_lookup.get((r['tafel'], r['lastname'], r['born']), r)
            for r in per_file_data[fname]
        ]

    all_records, per_file_data = apply_manual_corrections(all_records, per_file_data)

    # -------------------------------------------------------------------
    # Write merged CSV
    # -------------------------------------------------------------------
    if not report_only:
        with open(OUT_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, lineterminator='\n')
            writer.writeheader()
            writer.writerows(all_records)
        print(f"Wrote {len(all_records):,} merged records → {OUT_CSV}")

    # -------------------------------------------------------------------
    # Write per-file CSVs into rossoschka_tafeln_textlist/
    # -------------------------------------------------------------------
    if per_file and not report_only:
        _write_per_file(per_file_data)

    # -------------------------------------------------------------------
    # Build report
    # -------------------------------------------------------------------
    total = sum(total_stats.values())
    lines = [
        "=" * 60,
        "rossoschka_tafeln merge report",
        "=" * 60,
        "",
        f"System A (new): {len(sys_a)} files",
        f"System B (old): {len(sys_b)} files",
        f"Union of files: {len(all_files)}",
        "",
        "Record sources in merged output:",
        f"  AB      (both agree)         : {total_stats.get('AB', 0):>6,}  ({100*total_stats.get('AB',0)//max(total,1)}%)",
        f"  AB_diff (both, fields differ): {total_stats.get('AB_diff', 0):>6,}  ({100*total_stats.get('AB_diff',0)//max(total,1)}%)  ← review",
        f"  A only  (B missed / no file) : {total_stats.get('A', 0):>6,}  ({100*total_stats.get('A',0)//max(total,1)}%)",
        f"  B only  (A missed / no file) : {total_stats.get('B', 0):>6,}  ({100*total_stats.get('B',0)//max(total,1)}%)",
        f"  Total                        : {total:>6,}",
        "",
        "Quality estimate:",
        f"  High confidence (AB)         : {100*total_stats.get('AB',0)//max(total,1)}%",
        f"  Needs review (AB_diff)       : {total_stats.get('AB_diff', 0):,} records",
        "",
    ]

    if disagreements:
        lines += [
            "Sample AB_diff records (A value kept, B value shown for comparison):",
            "-" * 60,
        ]
        for r in disagreements[:20]:
            lines.append(
                f"  {r['tafel']:<20}  {r['lastname']:<20} born={r['born']}"
                f"  died={r['died']}  fn={r['firstname']}"
            )

    report_text = '\n'.join(lines) + '\n'

    with open(OUT_REPORT, 'w', encoding='utf-8') as f:
        f.write(report_text)

    print(report_text)
    print(f"Report written → {OUT_REPORT}")


if __name__ == '__main__':
    main()
