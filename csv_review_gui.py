#!/usr/bin/env python3
"""
csv_review_gui.py
Android-Studio-style side-by-side diff review for git-modified CSVs.

Layout:
  [File list] │ [Old version (read-only)] [New version (editable)] │ [PNG + highlights]

Diff colours match AS dark theme:
  - Red    = deleted line (left side only)
  - Green  = added line   (right side only)
  - Amber  = replaced     (left)   /   teal = replaced (right)
  - Grey   = placeholder row (other side)

Right panel is directly editable.
  Auto-saves 800 ms after last keystroke; Cmd/Ctrl+S saves immediately.
  Placeholder rows (deleted-only lines) block typing.

Click any line in the right panel → highlights matching name in PNG.
"""

import os
import sys
import json
import difflib
import threading
import subprocess
import unicodedata
import tkinter as tk
import tkinter.font as tkfont
from PIL import Image, ImageDraw, ImageTk

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_DIR     = os.path.dirname(os.path.abspath(__file__))
CSV_DIR      = "rossoschka_tafeln_textlist"
PNG_DIR      = "rossoschka_tafeln"
BOXES_SCRIPT = os.path.join(REPO_DIR, "get_word_boxes.swift")

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
BG       = '#1e1e1e'
BG_MID   = '#252526'
BG_HDR   = '#2d2d2d'
FG       = '#d4d4d4'
FG_DIM   = '#555555'
SEL_BG   = '#094771'

# Diff row backgrounds
C_EQUAL  = '#1e1e1e'   # unchanged
C_DEL    = '#3e1616'   # deleted (old side)
C_INS    = '#143014'   # inserted (new side)
C_ROLD   = '#3e2a10'   # replaced – old
C_RNEW   = '#0e2e20'   # replaced – new
C_HOLE   = '#242424'   # placeholder row

# Brighter foreground on highlighted rows
FG_ACTIVE  = '#e8e8e8'
FG_LINENUM = '#505050'
FG_LN_ACT  = '#909090'

FONT     = ('Menlo', 11)
AUTOSAVE = 800           # ms debounce

NAV_KEYS = frozenset({
    'Up', 'Down', 'Left', 'Right', 'Home', 'End', 'Prior', 'Next',
    'Control_L', 'Control_R', 'Meta_L', 'Meta_R',
    'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R',
    'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8',
})

MAG_SIZE = 220     # magnifier circle diameter in canvas pixels
MAG_ZOOM = 3.5     # zoom factor (source pixels → display pixels)
MAG_GAP  = 18      # gap between cursor and magnifier edge

HL_OUTLINE = '#ffd700'


# ---------------------------------------------------------------------------
# Git / file helpers
# ---------------------------------------------------------------------------

def _nfc(s: str) -> str:
    return unicodedata.normalize('NFC', s)


def get_modified_files() -> list[str]:
    files: set[str] = set()
    for extra in ([], ['HEAD']):
        r = subprocess.run(
            ['git', '-c', 'core.quotePath=false',
             'diff', '--name-only'] + extra + ['--', f'{CSV_DIR}/'],
            cwd=REPO_DIR, capture_output=True, text=True, encoding='utf-8')
        for line in r.stdout.splitlines():
            if line.strip().endswith('.csv'):
                files.add(line.strip())
    return sorted(files)


def get_old_lines(csv_rel: str) -> list[str]:
    r = subprocess.run(
        ['git', '-c', 'core.quotePath=false', 'show', f'HEAD:{csv_rel}'],
        cwd=REPO_DIR, capture_output=True, text=True, encoding='utf-8')
    return r.stdout.splitlines() if r.returncode == 0 else []


def get_new_lines(csv_rel: str) -> list[str]:
    path = os.path.join(REPO_DIR, csv_rel)
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().splitlines()
    except OSError:
        return []


def find_png(csv_rel: str) -> str | None:
    stem = _nfc(os.path.basename(csv_rel).replace('.csv', ''))
    png_dir = os.path.join(REPO_DIR, PNG_DIR)
    p = os.path.join(png_dir, stem + '.png')
    if os.path.exists(p):
        return p
    try:
        for f in os.listdir(png_dir):
            if f.lower().endswith('.png') and _nfc(f[:-4]).lower() == stem.lower():
                return os.path.join(png_dir, f)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Vision OCR (async)
# ---------------------------------------------------------------------------

def fetch_word_boxes(png_path: str, callback) -> None:
    def _worker():
        try:
            r = subprocess.run(['swift', BOXES_SCRIPT, png_path],
                               capture_output=True, text=True, timeout=90)
            boxes = json.loads(r.stdout)
        except Exception:
            boxes = []
        callback(boxes)
    threading.Thread(target=_worker, daemon=True).start()


def search_boxes(csv_line: str, boxes: list[dict]) -> list[dict]:
    parts = csv_line.split(',')
    if len(parts) < 3:
        return []
    lastname  = parts[2].strip().upper()
    firstname = parts[1].strip().upper()
    born      = parts[3].strip() if len(parts) > 3 else ''
    died      = parts[4].strip() if len(parts) > 4 else ''

    import re
    def word_in(token: str, text: str) -> bool:
        if not token:
            return False
        return bool(re.search(r'(?<![A-ZÄÖÜ])' + re.escape(token) + r'(?![A-ZÄÖÜ])', text))

    # Step 1 — candidate boxes: lastname as whole word
    candidates = [b for b in boxes if word_in(lastname, b.get('text', '').upper())]
    if not candidates:
        return []
    if len(candidates) == 1:
        return candidates

    # Step 2 — score by date/firstname overlap to pick the specific person
    def score(box: dict) -> int:
        text = box.get('text', '').upper()
        s = 0
        # Birth year / death year (4-digit, most reliable)
        for date in (born, died):
            year = date[-4:] if len(date) >= 4 and date[-4:].isdigit() else ''
            if year:
                s += 3 if year in text else 0
            # Day.Month prefix (less reliable but adds confidence)
            prefix = date[:5]
            if len(prefix) == 5 and prefix.replace('.', '').isdigit():
                s += 1 if prefix in text else 0
        # First name (first token only, to cope with OCR variations)
        first_token = firstname.split()[0] if firstname else ''
        if first_token and word_in(first_token, text):
            s += 2
        return s

    scored = sorted(candidates, key=score, reverse=True)
    best   = score(scored[0])

    # Return only the top-scoring box(es); if nothing scored, return all candidates
    if best == 0:
        return candidates
    return [b for b in scored if score(b) == best]


# ---------------------------------------------------------------------------
# Diff alignment
# ---------------------------------------------------------------------------

def build_side_by_side(old: list[str], new: list[str]
                       ) -> list[tuple[str | None, str | None, str]]:
    """Align old and new lines.  Returns [(old_or_None, new_or_None, op), …]
    where op ∈ {'equal', 'delete', 'insert', 'replace'}.
    None = placeholder row on that side.
    """
    sm   = difflib.SequenceMatcher(None, old, new, autojunk=False)
    rows = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        oc, nc = old[i1:i2], new[j1:j2]
        if op == 'equal':
            for o, n in zip(oc, nc):
                rows.append((o, n, 'equal'))
        elif op == 'delete':
            for o in oc:
                rows.append((o, None, 'delete'))
        elif op == 'insert':
            for n in nc:
                rows.append((None, n, 'insert'))
        elif op == 'replace':
            for k in range(max(len(oc), len(nc))):
                o = oc[k] if k < len(oc) else None
                n = nc[k] if k < len(nc) else None
                rows.append((o, n, 'replace'))
    return rows


# ---------------------------------------------------------------------------
# Side-by-side diff panel
# ---------------------------------------------------------------------------

class DiffPanel(tk.Frame):
    """Four synchronized Text widgets: line-numbers + content for old and new."""

    def __init__(self, parent, on_line_click, on_status) -> None:
        super().__init__(parent, bg=BG)
        self.on_line_click = on_line_click   # callback(csv_line: str)
        self.on_status     = on_status       # callback(msg: str)

        self._csv_path: str | None = None
        self._rows: list = []
        self._new_real: list[bool] = []   # True = real line, False = placeholder
        self._save_timer = None
        self._syncing    = False

        self._build()

    # ── Construction ────────────────────────────────────────────────────

    def _build(self) -> None:
        # ---- Header bar ----
        hdr = tk.Frame(self, bg=BG_HDR, height=26)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        self._lbl_old = tk.Label(hdr, text='Previous version', bg=BG_HDR,
                                  fg='#888888', font=('Helvetica', 10),
                                  anchor='w', padx=10)
        self._lbl_old.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Frame(hdr, bg='#555555', width=2).pack(side=tk.LEFT, fill=tk.Y)
        self._lbl_new = tk.Label(hdr, text='Current version', bg=BG_HDR,
                                  fg='#cccccc', font=('Helvetica', 10, 'bold'),
                                  anchor='w', padx=10)
        self._lbl_new.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---- Content grid ----
        cf = tk.Frame(self, bg=BG)
        cf.pack(fill=tk.BOTH, expand=True)
        cf.grid_columnconfigure(1, weight=1)   # old content
        cf.grid_columnconfigure(4, weight=1)   # new content
        cf.grid_rowconfigure(0, weight=1)

        kw_num = dict(width=4, bg='#1a1a1a', fg=FG_LINENUM, font=FONT,
                      state='disabled', wrap='none', relief='flat', bd=0,
                      highlightthickness=0, selectbackground='#1a1a1a',
                      takefocus=False)
        kw_txt = dict(bg=C_EQUAL, fg=FG, font=FONT, wrap='none',
                      relief='flat', bd=0, highlightthickness=0,
                      selectbackground=SEL_BG)

        # _ln_old stays state='normal' so window_create (inline buttons) works;
        # key events are blocked below instead.
        self._ln_old  = tk.Text(cf, **{**kw_num, 'state': 'normal'})
        self._ln_old.grid(row=0, column=0, sticky='nsew')
        self._tx_old  = tk.Text(cf, **kw_txt, cursor='arrow')
        self._tx_old.grid(row=0, column=1, sticky='nsew')

        tk.Frame(cf, bg='#444444', width=2).grid(row=0, column=2, sticky='ns')

        self._ln_new  = tk.Text(cf, **kw_num);  self._ln_new.grid( row=0, column=3, sticky='nsew')
        self._tx_new  = tk.Text(cf, **kw_txt, insertbackground='white', undo=True)
        self._tx_new.grid(row=0, column=4, sticky='nsew')

        self._sb = tk.Scrollbar(cf, orient='vertical', command=self._do_scroll)
        self._sb.grid(row=0, column=5, sticky='ns')

        self._all = [self._ln_old, self._tx_old, self._ln_new, self._tx_new]
        self._tx_new.config(yscrollcommand=self._on_new_yscroll)

        for w in self._all:
            w.bind('<MouseWheel>', self._on_wheel)

        # ---- Define colour tags ----
        tag_map = [
            ('equal',  C_EQUAL,  FG),
            ('delete', C_DEL,    FG_ACTIVE),
            ('insert', C_INS,    FG_ACTIVE),
            ('rold',   C_ROLD,   FG_ACTIVE),
            ('rnew',   C_RNEW,   FG_ACTIVE),
            ('hole',   C_HOLE,   C_HOLE),
        ]
        for w in self._all:
            for name, bg, fg in tag_map:
                w.tag_configure(name, background=bg, foreground=fg)
            w.tag_configure('lnum',     foreground=FG_LINENUM)
            w.tag_configure('lnum_act', foreground=FG_LN_ACT)
        # Extra tags for editable panel
        self._tx_new.tag_configure('sel_row', background='#1c3f6e')
        self._tx_new.tag_configure('hole',    background=C_HOLE, foreground=C_HOLE)

        # ---- Edit bindings ----
        self._tx_new.bind('<Key>',            self._on_key)
        self._tx_new.bind('<ButtonRelease-1>', self._on_click)
        # Block typing in old/linenum panels (they stay state='normal' for embedded buttons)
        for w in (self._ln_old, self._tx_old):
            w.bind('<Key>', lambda e: 'break')
        for seq in ('<Control-s>', '<Command-s>'):
            self._tx_new.bind(seq, lambda e: (self._save(), 'break')[1])

        # ---- Status bar ----
        sf = tk.Frame(self, bg='#1a1a1a', height=22)
        sf.pack(fill=tk.X)
        sf.pack_propagate(False)
        self._sv = tk.StringVar()
        tk.Label(sf, textvariable=self._sv, bg='#1a1a1a', fg='#888888',
                  font=('Helvetica', 10), anchor='w', padx=10
                  ).pack(side=tk.LEFT, fill=tk.Y)

        self._csv_rel: str | None = None

    # ── Scroll sync ──────────────────────────────────────────────────────

    def _do_scroll(self, *args) -> None:
        if self._syncing:
            return
        self._syncing = True
        for w in self._all:
            w.yview(*args)
        self._syncing = False

    def _on_new_yscroll(self, first: str, last: str) -> None:
        self._sb.set(first, last)
        if self._syncing:
            return
        self._syncing = True
        for w in (self._ln_old, self._tx_old, self._ln_new):
            w.yview_moveto(float(first))
        self._syncing = False

    def _on_wheel(self, event) -> str:
        if self._syncing:
            return 'break'
        delta = -1 if event.delta > 0 else 1
        self._syncing = True
        for w in self._all:
            w.yview_scroll(delta, 'units')
        self._syncing = False
        return 'break'

    # ── Editing ──────────────────────────────────────────────────────────

    def _row_idx(self, text_pos: str) -> int:
        return int(text_pos.split('.')[0]) - 1

    def _col_of(self, text_pos: str) -> int:
        return int(text_pos.split('.')[1])

    def _on_key(self, event) -> str | None:
        if event.keysym in NAV_KEYS:
            return None   # allow
        pos     = self._tx_new.index('insert')
        row     = self._row_idx(pos)
        col     = self._col_of(pos)
        n_rows  = len(self._new_real)

        # Block edits on placeholder row
        if row < n_rows and not self._new_real[row]:
            return 'break'

        # Block BackSpace that would merge into placeholder above
        if event.keysym == 'BackSpace' and col == 0 and row > 0:
            if not self._new_real[row - 1]:
                return 'break'

        # Block Delete that would merge into placeholder below
        if event.keysym == 'Delete':
            end_of_line = self._tx_new.index(f'{row + 1}.end')
            if pos == end_of_line and row + 1 < n_rows and not self._new_real[row + 1]:
                return 'break'

        # Schedule auto-save
        if self._save_timer:
            self._tx_new.after_cancel(self._save_timer)
        self._save_timer = self._tx_new.after(AUTOSAVE, self._save)
        self._sv.set('● Unsaved changes   (Cmd+S to save now)')
        return None

    def _on_click(self, event) -> None:
        idx  = self._tx_new.index(f'@{event.x},{event.y}')
        row  = self._row_idx(idx)
        line = self._tx_new.get(f'{row + 1}.0', f'{row + 1}.end').strip()
        if not line:
            return
        # Highlight selected row in new panel
        self._tx_new.tag_remove('sel_row', '1.0', 'end')
        self._tx_new.tag_add('sel_row', f'{row + 1}.0', f'{row + 2}.0')
        self._tx_new.tag_raise('sel_row')
        self.on_line_click(line)

    # ── Save ─────────────────────────────────────────────────────────────

    def _save(self) -> None:
        if not self._csv_path:
            return
        self._save_timer = None
        raw   = self._tx_new.get('1.0', 'end-1c').split('\n')
        lines = [l for l, real in zip(raw, self._new_real) if real]
        try:
            with open(self._csv_path, 'w', encoding='utf-8', newline='') as f:
                f.write('\n'.join(lines) + '\n')
            self._sv.set('✓ Saved')
            self.on_status('saved')
        except Exception as exc:
            self._sv.set(f'✗ Save failed: {exc}')

    def _find_apply_blocks(self) -> dict[int, list[tuple[int, str]]]:
        """Return {display_row: [(row_idx, old_txt), …]} for each consecutive
        block that has at least one row where old has content to apply
        (delete rows where new=None, or replace rows where contents differ).
        """
        blocks: dict[int, list] = {}
        i, n = 0, len(self._rows)
        while i < n:
            _, _, op = self._rows[i]
            if op != 'equal':
                start = i
                applicable = []
                while i < n and self._rows[i][2] != 'equal':
                    o, nw, _ = self._rows[i]
                    if o is not None:   # delete or replace — old has something to apply
                        applicable.append((i, o))
                    i += 1
                if applicable:
                    blocks[start] = applicable
            else:
                i += 1
        return blocks

    def _apply_block(self, applicable_rows: list[tuple[int, str]]) -> None:
        """Apply old content to the new panel for delete and replace rows."""
        for row_idx, old_txt in applicable_rows:
            start = f'{row_idx + 1}.0'
            self._tx_new.delete(start, f'{row_idx + 1}.end')
            self._tx_new.insert(start, old_txt)
            # Remove old diff tag (hole for delete, rnew for replace) → equal
            for tag in ('hole', 'rnew', 'insert'):
                self._tx_new.tag_remove(tag, f'{row_idx + 1}.0', f'{row_idx + 2}.0')
            self._tx_new.tag_add('equal', f'{row_idx + 1}.0', f'{row_idx + 2}.0')
            self._new_real[row_idx] = True
            self._rows[row_idx] = (self._rows[row_idx][0], old_txt, 'equal')
        self._save()
        if self._csv_rel:
            self.load(self._csv_rel)

    def load(self, csv_rel: str) -> None:
        # Flush pending save for previous file
        if self._save_timer:
            self._tx_new.after_cancel(self._save_timer)
            self._save_timer = None
            self._save()

        self._csv_rel  = csv_rel
        self._csv_path = os.path.join(REPO_DIR, csv_rel)

        old = get_old_lines(csv_rel)
        new = get_new_lines(csv_rel)
        rows = build_side_by_side(old, new)
        self._rows    = rows
        self._new_real = [n is not None for _, n, _ in rows]

        # Commit hash for header
        r = subprocess.run(['git', 'log', '--oneline', '-1', 'HEAD'],
                           cwd=REPO_DIR, capture_output=True, text=True)
        commit = (r.stdout.split()[0] if r.stdout.split() else 'HEAD')
        stem   = os.path.basename(csv_rel)
        self._lbl_old.config(text=f'  {commit}  {stem}')
        self._lbl_new.config(text=f'  Current version   {stem}')

        n_add = sum(1 for _, n, op in rows if op in ('insert', 'replace') and n is not None)
        n_del = sum(1 for o, _, op in rows if op in ('delete', 'replace') and o is not None)
        self._sv.set(f'{len(rows)} rows   +{n_add} added / −{n_del} removed   Cmd+S to save')

        # ---- Fill all four text widgets ----
        for w in self._all:
            w.config(state='normal')
            w.delete('1.0', 'end')

        # Pre-compute which display rows are the start of a delete block
        block_starts = self._find_apply_blocks()

        ln_old = ln_new = 0

        for disp_row, (old_txt, new_txt, op) in enumerate(rows):
            if   op == 'equal':   ot, nt = 'equal',  'equal'
            elif op == 'delete':  ot, nt = 'delete', 'hole'
            elif op == 'insert':  ot, nt = 'hole',   'insert'
            else:                 ot, nt = 'rold',   'rnew'   # replace

            # Old side
            if old_txt is not None:
                ln_old += 1
                if disp_row in block_starts:
                    # Embed a small '›' button at the start of a delete block
                    deleted = block_starts[disp_row]
                    btn = tk.Button(
                        self._ln_old,
                        text='›',
                        font=('Menlo', 10, 'bold'),
                        bg='#0e3a1a', fg='#7ec87e',
                        activebackground='#1a6e3e', activeforeground='#ccffcc',
                        relief='flat', padx=1, pady=0, bd=0, cursor='hand2',
                        command=lambda dr=deleted: self._apply_block(dr))
                    self._ln_old.window_create('end', window=btn)
                    self._ln_old.insert('end', '\n', (ot,))
                else:
                    self._ln_old.insert('end', f'{ln_old:>4}\n', ('lnum_act', ot))
                self._tx_old.insert('end', old_txt + '\n', ot)
            else:
                self._ln_old.insert('end', '    \n', ('lnum', 'hole'))
                self._tx_old.insert('end', '\n', 'hole')

            # New side
            if new_txt is not None:
                ln_new += 1
                self._ln_new.insert('end', f'{ln_new:>4}\n', ('lnum_act', nt))
                self._tx_new.insert('end', new_txt + '\n', nt)
            else:
                self._ln_new.insert('end', '    \n', ('lnum', 'hole'))
                self._tx_new.insert('end', '\n', ('hole',))

        # Lock only _tx_old (no typing) and _ln_new; _ln_old stays normal for buttons
        self._tx_old.config(state='disabled')
        self._ln_new.config(state='disabled')

        # Scroll to first changed line
        for i, (_, _, op) in enumerate(rows):
            if op != 'equal':
                target = f'{i + 1}.0'
                for w in self._all:
                    w.see(target)
                break


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class ReviewApp(tk.Tk):

    def __init__(self, modified_files: list[str]) -> None:
        super().__init__()
        self.title('CSV Review — Modified Tafeln')
        self.geometry('1600x960')
        self.minsize(1000, 600)

        self.modified_files = modified_files
        self._photo: ImageTk.PhotoImage | None = None
        self._current_img:  Image.Image | None = None
        self._scaled_img:   Image.Image | None = None   # display-scaled, for magnifier
        self._mag_photo:    ImageTk.PhotoImage | None = None
        self._current_png:  str | None         = None
        self._img_offset    = (0, 0)
        self._img_disp_size = (0, 0)
        self._diffs: dict = {}
        self._boxes: dict = {}
        self._pending_highlight: str | None = None
        # highlight rects in image-local display pixels: [(x1,y1,x2,y2), ...]
        self._hl_rects: list[tuple[float, float, float, float]] = []

        self._build_ui()
        self._populate_list()
        if modified_files:
            self.after(100, lambda: self._select_index(0))

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.configure(bg=BG)
        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL,
                               sashwidth=5, sashrelief=tk.FLAT, bg='#3a3a3a')
        pane.pack(fill=tk.BOTH, expand=True)

        # ---- Left: file list ----
        left = tk.Frame(pane, bg=BG_MID, width=240)
        pane.add(left, minsize=180)
        hdr = tk.Frame(left, bg=BG_MID)
        hdr.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(hdr, text='Modified Files', bg=BG_MID, fg='#cccccc',
                  font=('Helvetica', 13, 'bold')).pack(side=tk.LEFT)
        self._count = tk.Label(hdr, bg=BG_MID, fg='#888888', font=('Helvetica', 11))
        self._count.pack(side=tk.RIGHT)
        lf = tk.Frame(left, bg=BG_MID)
        lf.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        sc = tk.Scrollbar(lf)
        self._lb = tk.Listbox(lf, font=('Menlo', 11), bg='#1e1e1e', fg=FG,
                               selectbackground=SEL_BG, selectforeground='white',
                               activestyle='none', bd=0, highlightthickness=0,
                               yscrollcommand=sc.set)
        sc.config(command=self._lb.yview)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        self._lb.pack(fill=tk.BOTH, expand=True)
        self._lb.bind('<<ListboxSelect>>', self._on_list_select)
        self.bind('<Up>',   lambda _: self._nav(-1))
        self.bind('<Down>', lambda _: self._nav(+1))

        # ---- Centre: diff panel ----
        self._diff = DiffPanel(pane,
                               on_line_click=self._on_line_click,
                               on_status=self._on_save_status)
        pane.add(self._diff, minsize=600)

        # ---- Right: PNG panel ----
        right = tk.Frame(pane, bg=BG)
        pane.add(right, minsize=300)
        sb2 = tk.Frame(right, bg='#007acc', height=26)
        sb2.pack(fill=tk.X)
        sb2.pack_propagate(False)
        self._status = tk.StringVar(value='Select a file')
        tk.Label(sb2, textvariable=self._status, bg='#007acc', fg='white',
                  font=('Helvetica', 11), anchor='w', padx=10
                  ).pack(fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(right, bg='#2b2b2b', highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind('<Configure>',  lambda _: self._on_resize())
        self._canvas.bind('<Motion>',     self._on_mag_motion)
        self._canvas.bind('<Leave>',      lambda _: self._canvas.delete('magnifier'))

    # ── List ─────────────────────────────────────────────────────────────

    def _populate_list(self) -> None:
        self._count.config(text=f'{len(self.modified_files)} files')
        for f in self.modified_files:
            stem = os.path.basename(f).replace('.csv', '')
            self._lb.insert(tk.END, stem if len(stem) <= 34 else stem[:31] + '…')

    def _nav(self, delta: int) -> None:
        sel = self._lb.curselection()
        if not sel:
            self._select_index(0); return
        self._select_index(max(0, min(self._lb.size() - 1, sel[0] + delta)))

    def _on_list_select(self, _=None) -> None:
        sel = self._lb.curselection()
        if sel:
            self._select_index(sel[0])

    def _select_index(self, idx: int) -> None:
        self._lb.selection_clear(0, tk.END)
        self._lb.selection_set(idx)
        self._lb.see(idx)
        self._load_file(idx)

    # ── File loading ──────────────────────────────────────────────────────

    def _load_file(self, idx: int) -> None:
        csv_rel  = self.modified_files[idx]
        stem     = os.path.basename(csv_rel).replace('.csv', '')
        png_path = find_png(csv_rel)

        self._current_png       = png_path
        self._pending_highlight = None
        self._hl_rects          = []

        # Colour list item
        old = get_old_lines(csv_rel)
        new = get_new_lines(csv_rel)
        net = len(new) - len(old)
        self._lb.itemconfig(idx,
            fg='#6db96d' if net > 0 else '#d46a6a' if net < 0 else FG)

        self._status.set(
            stem + ('    ⚠ PNG not found' if not png_path else ''))

        # Load diff panel
        self._diff.load(csv_rel)

        # Load PNG
        self._canvas.delete('all')
        if png_path:
            try:
                self._current_img = Image.open(png_path)
                self._render_png()
                if png_path not in self._boxes:
                    self._status.set(self._status.get() + '   ⏳')
                    fetch_word_boxes(
                        png_path,
                        lambda b, p=png_path: self.after(0, self._on_boxes_ready, p, b))
            except Exception as exc:
                self._status.set(f'PNG error: {exc}')
        else:
            cw, ch = self._canvas.winfo_width(), self._canvas.winfo_height()
            self._canvas.create_text(cw // 2, ch // 2,
                text=f'PNG not found:\n{stem}',
                fill='#aaaaaa', font=('Helvetica', 14), justify='center')

    # ── PNG rendering ─────────────────────────────────────────────────────

    def _render_png(self) -> None:
        if not self._current_img:
            return
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        img   = self._current_img
        ratio = min(cw / img.width, ch / img.height)
        nw    = int(img.width  * ratio)
        nh    = int(img.height * ratio)
        scaled = img.resize((nw, nh), Image.LANCZOS)
        self._scaled_img = scaled
        self._photo = ImageTk.PhotoImage(scaled)
        xo = (cw - nw) // 2
        yo = (ch - nh) // 2
        self._img_offset    = (xo, yo)
        self._img_disp_size = (nw, nh)
        self._canvas.delete('base_img')
        self._canvas.create_image(xo, yo, anchor='nw',
                                   image=self._photo, tags='base_img')
        self._canvas.tag_lower('base_img')

    def _on_mag_motion(self, event) -> None:
        """Draw a circular magnifier loupe that follows the cursor."""
        if not self._scaled_img:
            return
        mx, my = event.x, event.y
        xo, yo = self._img_offset
        nw, nh = self._img_disp_size
        # Only show when the cursor is over the actual image area
        if not (xo <= mx <= xo + nw and yo <= my <= yo + nh):
            self._canvas.delete('magnifier')
            return

        # Crop region in display-image coordinates
        ix   = mx - xo
        iy   = my - yo
        half = MAG_SIZE / (2 * MAG_ZOOM)
        x1   = max(0,  int(ix - half))
        y1   = max(0,  int(iy - half))
        x2   = min(nw, int(ix + half))
        y2   = min(nh, int(iy + half))

        crop   = self._scaled_img.crop((x1, y1, x2, y2))
        zoomed = crop.resize((MAG_SIZE, MAG_SIZE), Image.BILINEAR)

        # Zoom factor for mapping highlight rects into the magnifier space
        # The crop covers (x2-x1) × (y2-y1) display pixels → MAG_SIZE output pixels
        crop_w = max(x2 - x1, 1)
        crop_h = max(y2 - y1, 1)
        scale_x = MAG_SIZE / crop_w
        scale_y = MAG_SIZE / crop_h

        # Circular mask — pixels outside the circle become transparent
        mask = Image.new('L', (MAG_SIZE, MAG_SIZE), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, MAG_SIZE - 1, MAG_SIZE - 1], fill=255)
        out = zoomed.convert('RGBA')
        out.putalpha(mask)

        # Paint highlight borders inside the loupe
        if self._hl_rects:
            d = ImageDraw.Draw(out)
            for (rx1, ry1, rx2, ry2) in self._hl_rects:
                # Translate from image-local to crop-local, then scale to loupe
                lx1 = (rx1 - x1) * scale_x
                ly1 = (ry1 - y1) * scale_y
                lx2 = (rx2 - x1) * scale_x
                ly2 = (ry2 - y1) * scale_y
                # Only draw if at least partially inside the loupe
                if lx2 > 0 and ly2 > 0 and lx1 < MAG_SIZE and ly1 < MAG_SIZE:
                    d.rectangle([lx1, ly1, lx2, ly2],
                                 outline=(255, 215, 0, 230), width=2)

        # Crosshair at centre of the loupe
        cx, cy = MAG_SIZE // 2, MAG_SIZE // 2
        ch_len, ch_gap = 14, 5
        d = ImageDraw.Draw(out)
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            d.line(
                [(cx + dx * ch_gap, cy + dy * ch_gap),
                 (cx + dx * (ch_gap + ch_len), cy + dy * (ch_gap + ch_len))],
                fill=(255, 220, 0, 200), width=1)

        # Place loupe: upper-right of cursor; flip if near canvas edge
        cw_px = self._canvas.winfo_width()
        ch_px = self._canvas.winfo_height()
        px = mx + MAG_GAP
        py = my - MAG_SIZE - MAG_GAP
        if px + MAG_SIZE > cw_px:
            px = mx - MAG_SIZE - MAG_GAP
        if py < 0:
            py = my + MAG_GAP

        self._mag_photo = ImageTk.PhotoImage(out)
        self._canvas.delete('magnifier')
        self._canvas.create_image(px, py, anchor='nw',
                                   image=self._mag_photo, tags='magnifier')
        # Border ring
        self._canvas.create_oval(
            px - 2, py - 2, px + MAG_SIZE + 1, py + MAG_SIZE + 1,
            outline='#dddddd', width=2, tags='magnifier')
        self._canvas.tag_raise('magnifier')

    def _on_resize(self) -> None:
        if self._current_img:
            self._render_png()

    # ── Highlights ────────────────────────────────────────────────────────

    def _on_line_click(self, csv_line: str) -> None:
        if self._current_png and self._current_png in self._boxes:
            self._draw_highlights(csv_line)
        else:
            self._pending_highlight = csv_line

    def _on_boxes_ready(self, png_path: str, boxes: list) -> None:
        self._boxes[png_path] = boxes
        s = self._status.get().replace('   ⏳', '')
        self._status.set(s)
        if png_path == self._current_png and self._pending_highlight:
            self._draw_highlights(self._pending_highlight)
            self._pending_highlight = None

    def _draw_highlights(self, csv_line: str) -> None:
        self._canvas.delete('highlight')
        self._hl_rects = []
        png = self._current_png
        if not png or png not in self._boxes:
            return
        matches = search_boxes(csv_line, self._boxes[png])
        if not matches:
            return
        xo, yo = self._img_offset
        nw, nh = self._img_disp_size
        for box in matches:
            px = xo + box['x'] * nw
            py = yo + box['y'] * nh
            pw = box['w'] * nw
            ph = box['h'] * nh
            self._canvas.create_rectangle(
                px, py, px + pw, py + ph,
                outline=HL_OUTLINE, width=2, fill='', tags='highlight')
            # image-local coords (relative to image top-left) for magnifier
            self._hl_rects.append((px - xo, py - yo, px - xo + pw, py - yo + ph))
        self._canvas.tag_raise('highlight')

    # ── Misc ──────────────────────────────────────────────────────────────

    def _on_save_status(self, _status: str) -> None:
        pass   # diff panel handles its own status label


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    modified = get_modified_files()
    if not modified:
        print('No modified CSV files found in rossoschka_tafeln_textlist/')
        sys.exit(0)
    print(f'{len(modified)} modified CSV files.')
    ReviewApp(modified).mainloop()


if __name__ == '__main__':
    main()
