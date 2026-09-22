#!/usr/bin/env bash
# rebuild_pipeline.sh
# Rebuilds all OCR-derived directories from rossoschka_tafeln/ (source PNGs).
#
# Steps:
#   1. Delete derived directories
#   2. Rename any unlabeled PNGs in rossoschka_tafeln/ (adds SURNAME-SURNAME suffix)
#   3. OCR with Apple Vision (System A) → rossoschka_tafeln_text/
#   4. OCR with Apple Vision (System B) → rossoschka_text_vision/
#   5. Merge both systems → rossoschka_tafeln_textlist_gefallen/ + rossoschka_merged.csv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

START_TIME=$(date +%s)

echo "=== Step 1: Deleting derived directories ==="
rm -rf rossoschka_tafeln_text_gefallen rossoschka_tafeln_textlist_gefallen rossoschka_text_vision_gefallen
echo "  Deleted: rossoschka_tafeln_text_gefallen, rossoschka_tafeln_textlist_gefallen, rossoschka_text_vision_gefallen"

echo ""
echo "=== Step 2: Renaming unlabeled PNGs in rossoschka_tafeln/ ==="
swift ocr_and_rename_tafelnt_gefallen.swift

echo ""
echo "=== Step 3: OCR (System A) rossoschka_tafeln/ → rossoschka_tafeln_text/ ==="
mkdir -p rossoschka_tafeln_text_gefallen
swift ocr_rossoschka_visiont_gefallen.swift

echo ""
echo "=== Step 4: OCR (System B) rossoschka_tafeln/ → rossoschka_text_vision/ ==="
mkdir -p rossoschka_text_vision_gefallen
swift ocr_missing_tafeln_gefallen.swift

echo ""
echo "=== Step 5: Merge → rossoschka_tafeln_textlist_gefallen/ + rossoschka_merged.csv ==="
python3 merge_tafeln.py --per-file

echo ""
echo "=== Done ==="
echo "  rossoschka_tafeln_text_gefallen/   : $(ls rossoschka_tafeln_text_gefallen | wc -l | tr -d ' ') files"
echo "  rossoschka_text_vision_gefallen/   : $(ls rossoschka_text_vision_gefallen | wc -l | tr -d ' ') files"
echo "  rossoschka_tafeln_textlist_gefallen/: $(ls rossoschka_tafeln_textlist_gefallen | wc -l | tr -d ' ') files"
echo "  rossoschka_merged_gefallen.csv     : $(tail -n +2 rossoschka_merged_gefallen.csv | wc -l | tr -d ' ') records"

ELAPSED=$(( $(date +%s) - START_TIME ))
printf "  Elapsed                   : %d min %02d sec\n" $(( ELAPSED / 60 )) $(( ELAPSED % 60 ))
