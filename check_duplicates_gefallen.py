#!/usr/bin/env python3
"""Check how often each first+last name combination appears in rossoschka_merged.csv.

Focuses on records marked as source='manual', but shows all matching records
(manual + AB) so you can spot duplicates before they are added.

Usage:
    python3 check_duplicates.py                  # report only manual records
    python3 check_duplicates.py --all            # report every name that appears >1x
    python3 check_duplicates.py WEBER            # look up a specific lastname
    python3 check_duplicates.py HELMUT WEBER     # look up firstname + lastname
"""

import csv
import sys
from collections import defaultdict

MERGED_CSV = "rossoschka_merged.csv"


def load(path: str) -> list[dict]:
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def name_key(r: dict) -> tuple[str, str]:
    return (r['firstname'].strip().upper(), r['lastname'].strip().upper())


def fmt(r: dict) -> str:
    return (f"  {r['tafel']:20s}  {r['firstname']:25s}  {r['lastname']:20s}"
            f"  born={r['born'] or '?':12s}  died={r['died'] or '?':12s}"
            f"  [{r['source']}]")


def report(records: list[dict], label: str) -> None:
    if not records:
        print(f"  (no matches for {label})")
        return
    for r in records:
        print(fmt(r))


def main() -> None:
    rows = load(MERGED_CSV)

    # Build index: (firstname, lastname) → list of records
    index: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        index[name_key(r)].append(r)

    args = sys.argv[1:]

    # ------------------------------------------------------------------ #
    # Mode 1: look up a specific name on the command line
    # ------------------------------------------------------------------ #
    if args and args[0] != '--all':
        if len(args) == 1:
            # lastname only
            ln = args[0].upper()
            matches = {k: v for k, v in index.items() if k[1] == ln}
        else:
            fn = args[0].upper()
            ln = args[1].upper()
            matches = {(fn, ln): index.get((fn, ln), [])}

        for (fn, ln), recs in sorted(matches.items()):
            print(f"\n{fn} {ln}  ({len(recs)} record(s))")
            report(recs, f"{fn} {ln}")
        return

    # ------------------------------------------------------------------ #
    # Mode 2: report duplicates (--all) or duplicates among manual records
    # ------------------------------------------------------------------ #
    report_all = '--all' in args

    manual_keys = {name_key(r) for r in rows if r.get('source') == 'manual'}

    if report_all:
        # Every name that appears more than once
        candidate_keys = {k for k, v in index.items() if len(v) > 1}
        title = "Names appearing more than once"
    else:
        # Only manual records — show all rows for that name
        candidate_keys = manual_keys
        title = "Manual records and their duplicates in the full dataset"

    if not candidate_keys:
        print("No duplicates / manual records found.")
        return

    print(f"\n{title}\n{'=' * len(title)}")
    duplicates_found = 0
    for key in sorted(candidate_keys):
        fn, ln = key
        recs = index[key]
        count = len(recs)
        flag = "  *** DUPLICATE ***" if count > 1 else ""
        print(f"\n{fn} {ln}  ({count} record(s)){flag}")
        report(recs, f"{fn} {ln}")
        if count > 1:
            duplicates_found += 1

    print(f"\n{'─' * 60}")
    if report_all:
        print(f"  {duplicates_found} name(s) with duplicates out of "
              f"{len({name_key(r) for r in rows})} unique names")
    else:
        print(f"  {len(candidate_keys)} manual record(s), "
              f"{duplicates_found} with potential duplicate(s)")


if __name__ == '__main__':
    main()
