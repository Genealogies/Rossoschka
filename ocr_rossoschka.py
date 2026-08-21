#!/usr/bin/env python3
"""
OCR text extraction from rossoschka_final/ images.

Calls tesseract on each PNG and saves the extracted text to rossoschka_text/
as a .txt file with the same base name. Uses German + English language models.
"""

import os
import sys
import subprocess

SRC_DIR  = "rossoschka_final"
DEST_DIR = "rossoschka_text"
LANGS    = "deu+eng"   # German primary, English fallback
TESSERACT = "/opt/homebrew/bin/tesseract"

def ocr_image(src_path, dest_txt_path):
    """Run tesseract on src_path, write text to dest_txt_path."""
    # Tesseract adds .txt automatically when given an output base
    base = dest_txt_path.removesuffix(".txt")
    result = subprocess.run(
        [TESSERACT, src_path, base, "-l", LANGS, "--psm", "6"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

def main():
    if not os.path.isdir(SRC_DIR):
        print(f"Source directory '{SRC_DIR}' not found.")
        sys.exit(1)

    os.makedirs(DEST_DIR, exist_ok=True)

    files = sorted(f for f in os.listdir(SRC_DIR) if f.lower().endswith(".png"))
    total = len(files)
    print(f"Extracting text from {total} images ({LANGS}) → ./{DEST_DIR}/\n")

    ok = skipped = errors = 0
    for i, name in enumerate(files, 1):
        src  = os.path.join(SRC_DIR, name)
        dest = os.path.join(DEST_DIR, os.path.splitext(name)[0] + ".txt")

        if os.path.exists(dest):
            skipped += 1
            continue

        try:
            ocr_image(src, dest)
            # Show a preview of first non-empty line
            with open(dest, encoding="utf-8", errors="replace") as f:
                lines = [l.strip() for l in f if l.strip()]
            preview = lines[0][:80] if lines else "(no text detected)"
            print(f"  [{i:>3}/{total}] {name}")
            print(f"          → {preview}")
            ok += 1
        except Exception as e:
            print(f"  [{i:>3}/{total}] ERR {name}: {e}")
            errors += 1

    print(f"\nDone. {ok} extracted, {skipped} skipped (already exist), {errors} errors.")
    print(f"Text files in ./{DEST_DIR}/")

if __name__ == "__main__":
    main()
