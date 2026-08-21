#!/usr/bin/env bash
# rebuild_pipeline.sh
# Rebuilds all OCR-derived directories from rossoschka_tafeln/ (source PNGs).
#
# Steps:
#   1. Delete derived directories
#   2. Rename any unlabeled PNGs in rossoschka_tafeln/ (adds SURNAME-SURNAME suffix)
#   3. OCR with Apple Vision (System A) → rossoschka_tafeln_text/
#   4. OCR with Apple Vision (System B) → rossoschka_text_vision/
#   5. Merge both systems → rossoschka_tafeln_textlist/ + rossoschka_merged.csv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Step 1: Deleting derived directories ==="
rm -rf rossoschka_tafeln_text rossoschka_tafeln_textlist rossoschka_text_vision
echo "  Deleted: rossoschka_tafeln_text, rossoschka_tafeln_textlist, rossoschka_text_vision"

echo ""
echo "=== Step 2: Renaming unlabeled PNGs in rossoschka_tafeln/ ==="
swift ocr_and_rename_tafeln.swift

echo ""
echo "=== Step 3: OCR (System A) rossoschka_tafeln/ → rossoschka_tafeln_text/ ==="
mkdir -p rossoschka_tafeln_text
swift ocr_rossoschka_vision.swift

echo ""
echo "=== Step 4: OCR (System B) rossoschka_tafeln/ → rossoschka_text_vision/ ==="
mkdir -p rossoschka_text_vision
swift ocr_missing_tafeln.swift

echo ""
echo "=== Step 5: Merge → rossoschka_tafeln_textlist/ + rossoschka_merged.csv ==="
python3 merge_tafeln.py --per-file

echo ""
echo "=== Done ==="
echo "  rossoschka_tafeln_text/   : $(ls rossoschka_tafeln_text | wc -l | tr -d ' ') files"
echo "  rossoschka_text_vision/   : $(ls rossoschka_text_vision | wc -l | tr -d ' ') files"
echo "  rossoschka_tafeln_textlist/: $(ls rossoschka_tafeln_textlist | wc -l | tr -d ' ') files"
echo "  rossoschka_merged.csv     : $(tail -n +2 rossoschka_merged.csv | wc -l | tr -d ' ') records"
