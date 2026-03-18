

# https://chatgpt.com/c/6946786c-1bd0-8324-96db-afad7946b069

import os
import io
import zipfile
import tempfile
import shutil
from pathlib import Path
from urllib.request import urlopen

# repo zip (toàn bộ)
GITHUB_ZIP_URL = "https://github.com/nghiencuuthuoc/PrajnaPlayer-Assets/archive/refs/heads/main.zip"
ASSET_SRC_FOLDER = "PrajnaPlayer-Assets-main/Assets_v17"   # trong ZIP
# LOCAL_ASSETS_DIR = Path("./assets")
LOCAL_ASSETS_DIR = Path("./")
ASSET_VERSION_FILE = LOCAL_ASSETS_DIR / ".asset_version"
ASSET_VERSION = "v17"

def download_and_extract_assets():
    print("[Info] Downloading assets from GitHub...")
    with urlopen(GITHUB_ZIP_URL) as resp:
        data = resp.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "assets.zip"
        with open(zip_path, "wb") as f:
            f.write(data)

        with zipfile.ZipFile(zip_path, "r") as zf:
            # chỉ copy thư mục Assets_v17
            members = [m for m in zf.namelist() if m.startswith(ASSET_SRC_FOLDER)]
            if not members:
                raise RuntimeError("Assets_v17 not found in ZIP. Check folder name.")

            # đảm bảo thư mục local
            LOCAL_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

            for member in members:
                rel_path = Path(member).relative_to(ASSET_SRC_FOLDER)  # phần sau Assets_v17
                if not rel_path.name:  # chính thư mục
                    continue
                target_path = LOCAL_ASSETS_DIR / rel_path
                if member.endswith("/"):
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

    # tạo file version để lần sau check
    ASSET_VERSION_FILE.write_text(ASSET_VERSION, encoding="utf-8")
    print("[Info] Assets downloaded and extracted to ./")

def ensure_assets():
    # nếu đã có và version khớp thì thôi
    if LOCAL_ASSETS_DIR.exists() and ASSET_VERSION_FILE.exists():
        current_ver = ASSET_VERSION_FILE.read_text(encoding="utf-8").strip()
        if current_ver == ASSET_VERSION:
            # đã có đúng version
            return

    # nếu không có thì tải
    download_and_extract_assets()

if __name__ == "__main__":
    ensure_assets()
    print("[OK] Assets ready.")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PrajnaPlayer v17
+ Get assets from gitihub: https://github.com/nghiencuuthuoc/PrajnaPlayer-Assets/tree/main/Assets_v17
PrajnaPlayer v16.5 — Dual Subtitles + Global Sub-Index + Smart-Hold
+ Session resume (state_recent + state_<hash>.json)
+ Static cache (static.json trong từng thư mục nhạc; atomic write với static.lock)
+ Ảnh trung tâm chọn ngẫu nhiên từ assets/
+ Thư mục config_state/ chứa prajna_config.json, state_*.json, state_recent.json

Giữ nguyên toàn bộ tính năng v15.6/v16 (shortcuts, lọc/sắp xếp, cặp phụ đề EN/VI, auto-resume...).
"""

import os
import re
import sys
import json
import time
import random
import hashlib
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from prajna_packaging_runtime import apply_app_identity_and_icons

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

# ----- Optional libs -----
try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None

try:
    import vlc
except Exception:
    vlc = None

# ----- Theme / Consts -----
BG = "#fdf5e6"
FG = "#2a2a2a"
BTN_BG = "#f4a261"
BTN_BG_ACTIVE = "#e76f51"
TV_HEAD_BG = "#b5838d"

AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma")
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
SUB_EXTS   = (".vtt", ".srt")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

SORT_CHOICES = [
    "Title (A→Z)", "Title (Z→A)",
    "Modified (Newest)", "Modified (Oldest)",
    "Size (Large→Small)", "Size (Small→Large)",
    "Duration (Long→Short)", "Duration (Short→Long)",
]

DEFAULT_STATE = {
    "folder": "",
    "index": 0,
    "volume": 70,
    "song": "",
    "position": 0,  # ms
    "saved_at": 0
}

_YT_ID_RE = re.compile(r"\[([A-Za-z0-9_\-]{6,})\]")

# ===== Helpers for bundled runtime (PyInstaller onefile) =====
def _bundle_dir() -> Path:
    """Directory where bundled data is available (PyInstaller _MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent

def _runtime_dir() -> Path:
    """
    Directory for runtime read-write data.
    - Windows/Linux: folder of the executable (or script dir when dev)
    - macOS onefile: prefer ~/Library/Application Support/PrajnaPlayer
    """
    if sys.platform == "darwin":
        # Persist config/state outside the temp dir of onefile
        base = Path.home() / "Library" / "Application Support" / "PrajnaPlayer"
        try: base.mkdir(parents=True, exist_ok=True)
        except Exception: pass
        return base
    # Others: exe folder or script folder
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

def _prepare_vlc_runtime():
    """
    Prepare environment so python-vlc finds libvlc & plugins from the bundle.
    Call this BEFORE creating vlc.Instance().
    """
    try:
        here = _bundle_dir()
        if sys.platform == "darwin":
            # macOS: libvlc.dylib & plugins should be added as data/binary to the bundle
            os.environ.setdefault("PYTHON_VLC_MODULE_PATH", str(here))
            plug = here / "plugins"
            if plug.exists():
                os.environ["VLC_PLUGIN_PATH"] = str(plug)
        elif os.name == "nt":
            # Windows: allow DLL search in bundle dir and set plugins
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(here))
            plug = here / "plugins"
            if plug.exists():
                os.environ["VLC_PLUGIN_PATH"] = str(plug)
        else:
            # Linux: optional, usually system VLC available; if bundled, mirror mac logic
            plug = here / "plugins"
            if plug.exists():
                os.environ["VLC_PLUGIN_PATH"] = str(plug)
    except Exception:
        pass

# ----- UI helpers -----
def apply_pharmapp_theme(root: tk.Tk):
    s = ttk.Style(root)
    try: s.theme_use("default")
    except Exception: pass
    s.configure("TFrame", background=BG)
    s.configure("TLabelframe", background=BG)
    s.configure("TLabel", background=BG, foreground=FG, font=("Arial", 11))
    s.configure("TEntry", padding=5)
    s.configure("TCombobox", padding=5)
    s.configure("TButton", font=("Arial", 10))
    s.configure("Muted.TLabel", background=BG, foreground="#666666", font=("Segoe UI", 9))
    s.configure("Link.TLabel", background=BG, foreground="#0a66c2", font=("Segoe UI", 9, "underline"))
    s.configure("Donate.TButton", font=("Segoe UI", 9, "bold"), padding=(8, 3))
    s.configure("Expander.TButton", background=BG, relief="flat", padding=6)
    s.map("Expander.TButton", background=[("active", BG)])
    s.configure("Treeview",
               background="white", foreground=FG, fieldbackground="white",
               font=("Arial", 10), rowheight=24)
    s.configure("Treeview.Heading",
               background=TV_HEAD_BG, foreground="white", font=("Arial", 10, "bold"))
    s.map("Treeview", background=[("selected", "#e9c46a")])

def style_btn(b: tk.Button) -> None:
    b.configure(bg=BTN_BG, fg="black",
                activebackground=BTN_BG_ACTIVE, activeforeground="black",
                bd=0, relief=tk.FLAT, font=("Arial", 9, "bold"),
                cursor="hand2", padx=6, pady=3)

# ----- String utils -----
def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _token_set(s: str) -> set:
    """Tokenize a filename/stem for matching.

    We intentionally drop very short numeric fragments (e.g., "06") which commonly
    come from dates and cause false matches across unrelated tracks.
    """
    toks = set(_norm(s).split())
    out = set()
    for t in toks:
        if t in {"en", "vi", "vtt", "srt"}:
            continue
        # ignore 1-2 digit numeric fragments (often day/month)
        if t.isdigit() and len(t) <= 2:
            continue
        if len(t) < 3:
            continue
        out.add(t)
    return out

def _extract_track_no(name: str) -> Optional[str]:
    m = re.match(r"^\s*(\d{1,4})\b", name)
    return m.group(1) if m else None

def _extract_yt_id(name: str) -> Optional[str]:
    m = _YT_ID_RE.search(name)
    return m.group(1) if m else None

def _match_name_score(candidate: str, audio_stem: str) -> float:
    """Score how well a subtitle 'base' (or filename) matches the audio stem."""
    target = _norm(audio_stem)
    cand_n = _norm(candidate)

    # fast exact-ish checks
    if cand_n == target:
        score = 100.0
    elif cand_n.startswith(target) or cand_n.endswith(target):
        score = 90.0
    elif target and target in cand_n:
        score = 80.0
    else:
        toks = _token_set(audio_stem)
        cand_toks = _token_set(candidate)
        inter = cand_toks & toks
        score = 0.0
        if toks:
            score = 60.0 * (len(inter) / max(1, len(toks)))

    # strong anchors: track number and YouTube ID (if present)
    a_no = _extract_track_no(audio_stem)
    c_no = _extract_track_no(candidate)
    if a_no and c_no and a_no == c_no:
        score = max(score, 88.0)

    a_yt = _extract_yt_id(audio_stem)
    c_yt = _extract_yt_id(candidate)
    if a_yt and c_yt:
        if a_yt == c_yt:
            score = max(score, 92.0)
        else:
            # hard penalty for different YT IDs
            score *= 0.15
    elif a_yt and a_yt in candidate:
        score = max(score, 90.0)

    return score

def _cleanup_sub_text(txt: str) -> str:
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    return txt.strip()

# ----- Subtitle parsing -----
def _read_text_best_effort(path: Path) -> str:
    """Read subtitle text with best-effort encoding detection.

    Tries common encodings seen in subtitle files exported from YouTube, Windows tools,
    and various editors (UTF-8/UTF-16/CP1258).
    """
    data = path.read_bytes()
    for enc in (
        "utf-8-sig",
        "utf-8",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "cp1258",   # Vietnamese Windows
        "cp1252",
        "latin-1",
    ):
        try:
            return data.decode(enc)
        except Exception:
            pass
    return data.decode("utf-8", errors="replace")


def parse_vtt_or_srt(path: Path) -> List[Tuple[int, int, str]]:
    """Return list of (start_ms, end_ms, text) for .vtt/.srt."""
    raw = _read_text_best_effort(path)
    lines = raw.splitlines()

    # Detect VTT via header
    first_nonempty = ""
    for ln in lines[:15]:
        if ln.strip():
            first_nonempty = ln.strip().lower()
            break
    is_vtt = first_nonempty.startswith("webvtt")

    # SRT typically has commas in timestamps, but some tools output dots.
    ts_hint = 0
    for ln in lines[:80]:
        if "-->" in ln and re.search(r"\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}", ln):
            ts_hint += 1
    is_srt = (not is_vtt) and ts_hint > 0

    cues: List[Tuple[int, int, str]] = []

    def to_ms(x: str) -> int:
        x = x.strip().replace(",", ".")
        parts = x.split(":")
        if len(parts) == 2:
            h = 0
            m = int(parts[0])
            s = float(parts[1])
        else:
            h = int(parts[0])
            m = int(parts[1])
            s = float(parts[2])
        return int(round((h * 3600 + m * 60 + s) * 1000))

    buf: List[str] = []
    t_start = t_end = None

    for i, ln in enumerate(lines):
        if "-->" in ln:
            # flush previous cue
            if t_start is not None and buf:
                txt = "\n".join(buf).strip()
                if txt:
                    cues.append((t_start, t_end, _cleanup_sub_text(txt)))
            buf = []
            try:
                left, right = ln.split("-->")
                t_start = to_ms(left.strip())
                right = right.strip().split(" ", 1)[0]
                t_end = to_ms(right.strip())
            except Exception:
                t_start = t_end = None
        else:
            # skip SRT numeric index lines
            if is_srt and ln.strip().isdigit() and (i + 1 < len(lines) and "-->" in lines[i + 1]):
                continue

            # blank line = cue separator
            if ln.strip() == "" and buf:
                if t_start is not None:
                    txt = "\n".join(buf).strip()
                    if txt:
                        cues.append((t_start, t_end, _cleanup_sub_text(txt)))
                buf = []
                t_start = t_end = None
            else:
                if t_start is not None:
                    buf.append(ln)

    if t_start is not None and buf:
        txt = "\n".join(buf).strip()
        if txt:
            cues.append((t_start, t_end, _cleanup_sub_text(txt)))

    cues.sort(key=lambda x: x[0])
    return cues

# ----- Pairing helpers -----
def _suffix2_base(p: Path) -> Optional[str]:
    """
    Hỗ trợ cả:
      - BASE.en.srt / BASE.vi.vtt
      - BASE_en.srt / BASE_vi.vtt
      - BASE-en.srt / BASE-vi.vtt
    Trả về 'BASE' nếu nhận diện được, ngược lại None.
    """
    name = p.name
    suf = Path(name).suffixes  # ví dụ ['.en', '.vtt'] hoặc ['.srt']

    # dạng BASE.<lang>.<ext>
    if len(suf) >= 2:
        ext = suf[-1].lower()
        lang = suf[-2].lower().lstrip('.')
        if ext in ('.vtt', '.srt') and lang in ('en', 'vi'):
            base = name[: -len(suf[-2] + suf[-1])]
            if base.endswith('.'):
                base = base[:-1]
            return base

    # dạng BASE_<lang>.<ext> / BASE-<lang>.<ext>
    if p.suffix.lower() in ('.vtt', '.srt'):
        stem = p.stem
        m = re.match(r'^(.*?)[_\-](en|vi)$', stem, flags=re.IGNORECASE)
        if m:
            base = (m.group(1) or '').rstrip(' ._-')
            return base or None

    return None


def _suffix2_lang(p: Path) -> Optional[str]:
    """Return 'en'/'vi' for supported dual-sub naming styles, else None."""
    name = p.name.lower()
    suf = Path(name).suffixes
    if len(suf) >= 2 and suf[-1] in ('.vtt', '.srt') and suf[-2].lstrip('.') in ('en', 'vi'):
        return suf[-2].lstrip('.')
    if p.suffix.lower() in ('.vtt', '.srt'):
        m = re.match(r'^(.*?)[_\-](en|vi)$', p.stem, flags=re.IGNORECASE)
        if m:
            return m.group(2).lower()
    return None

def _pair_subtitles_in_folder(folder: Path) -> Dict[str, Dict[str, Path]]:
    """Trả map: base -> {'en': Path, 'vi': Path} cho các cặp strict."""
    pairs: Dict[str, Dict[str, Path]] = {}
    for p in folder.glob("*"):
        base = _suffix2_base(p)
        if not base:
            continue
        d = pairs.setdefault(base, {})
        lang = _suffix2_lang(p)
        if lang not in ('en', 'vi'):
            continue
        d[lang] = p
    return {b: d for b, d in pairs.items() if "en" in d and "vi" in d}

def _closest_base_to_audio(bases: List[str], audio_stem: str, min_score: float = 35.0) -> Optional[str]:
    """Pick the closest subtitle base to an audio stem.

    Returns None if the best candidate is still a weak match (prevents accidental
    loading of an unrelated subtitle like 090 when playing 001).
    """
    if not bases:
        return None
    best, best_score = None, -1.0
    for b in bases:
        score = _match_name_score(b, audio_stem)
        if score > best_score:
            best, best_score = b, score
    if best_score < min_score:
        return None
    return best

# ----- Alignment diagnostics -----
def _has_text_at(cues: List[Tuple[int,int,str]], t: int) -> bool:
    for a, b, _ in cues:
        if t < a: return False
        if a <= t < b: return True
    return False

def _median_offset_en_to_vi(en_cues, vi_cues) -> Optional[float]:
    if not en_cues or not vi_cues: return None
    en_st = [a for a,_,_ in en_cues]
    vi_st = [a for a,_,_ in vi_cues]
    i = j = 0; diffs = []
    while i < len(en_st) and j < len(vi_st):
        e = en_st[i]; v = vi_st[j]
        diffs.append((v - e)/1000.0)
        if e <= v: i += 1
        else: j += 1
        if len(diffs) >= 200: break
    if not diffs: return None
    try: return statistics.median(diffs)
    except statistics.StatisticsError: return None

def _alignment_diagnostics(en_cues, vi_cues) -> Tuple[str, float, Optional[float]]:
    if not en_cues or not vi_cues:
        return "Sub: (one track missing)", 0.0, None
    end_ms = max(en_cues[-1][1], vi_cues[-1][1])
    if end_ms <= 0:
        return "Sub: (invalid duration)", 0.0, None
    samples = 48; both = either = 0
    for k in range(samples):
        t = int((k + 0.5) * end_ms / samples)
        e_on = _has_text_at(en_cues, t)
        v_on = _has_text_at(vi_cues, t)
        if e_on or v_on: either += 1
        if e_on and v_on: both += 1
    overlap = (both / either) if either else 0.0
    med_off = _median_offset_en_to_vi(en_cues, vi_cues)
    ok_overlap = overlap >= 0.55
    ok_offset = (med_off is None) or (abs(med_off) <= 1.5)
    if ok_overlap and ok_offset:
        msg = f"✓ aligned · overlap {overlap*100:.0f}%"
        if med_off is not None: msg += f", Δ {med_off:+.1f}s"
    else:
        msg = f"⚠ mismatch · overlap {overlap*100:.0f}%"
        if med_off is not None: msg += f", Δ {med_off:+.1f}s"
        msg += " (check EN/VI files)"
    return msg, overlap, med_off

# ----- Simple expander -----
class Expander(ttk.Frame):
    def __init__(self, master, title: str, open_=True):
        super().__init__(master)
        self.var_open = tk.BooleanVar(value=open_)
        self.btn = ttk.Button(self, text=title, style="Expander.TButton", command=self.toggle)
        self.btn.pack(fill=tk.X)
        self.body = ttk.Frame(self)
        if open_: self.body.pack(fill=tk.BOTH, expand=True)
    def toggle(self):
        if self.var_open.get():
            self.var_open.set(False); self.body.forget()
        else:
            self.var_open.set(True); self.body.pack(fill=tk.BOTH, expand=True)

# ----- Atomic JSON helpers -----
def _atomic_write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            try: tmp.unlink()
            except Exception: pass

class _FileLock:
    def __init__(self, path: Path, timeout: float = 10.0):
        self.path = Path(path)
        self.timeout = timeout
        self._acquired = False
    def __enter__(self):
        t0 = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self._acquired = True
                break
            except FileExistsError:
                if time.time() - t0 > self.timeout:
                    break
                time.sleep(0.1)
        return self
    def __exit__(self, exc_type, exc, tb):
        if self._acquired:
            try: os.remove(self.path)
            except Exception: pass

# ----- Main App -----
class PrajnaPlayerApp:
    def __init__(self, root: tk.Tk):
        self.root = root

        # Base title + dynamic folder
        self.base_title = "PrajnaPlayer v17"
        self.root.title(self.base_title)

        # --- Data dirs ---
        self.app_root = _bundle_dir()              # read-only data (assets, bundled stuff)
        self.app_dir  = _runtime_dir() / "config_state"  # read-write config/state
        self.app_dir.mkdir(parents=True, exist_ok=True)
        # migrate JSONs from runtime root (or old location) to config_state/
        self._migrate_old_jsons(_runtime_dir(), self.app_dir)

        # Config path (in config_state/)
        self.config_path = self.app_dir / "prajna_config.json"
        self._cfg = self._load_config()

        # Flags / timers
        self.allow_write_static = tk.BooleanVar(value=bool(self._cfg.get("allow_write_static", True)))
        self.state_autosave_interval = int(self._cfg.get("state_autosave_ms", 30000))
        self._last_state_save = 0.0

        # Data
        self.items_all: List[Dict] = []
        self.items_view: List[int] = []
        self.current_index: int = -1
        self.is_repeat = False
        self.is_shuffle = False
        self.current_folder: Optional[str] = None

        # Sub-index
        self.sub_index: Dict[str, Dict[str, Path]] = {}

        # Subtitles (dual)
        self.sub_enabled = tk.BooleanVar(value=True)
        self.sub_en_file: Optional[Path] = None
        self.sub_vi_file: Optional[Path] = None
        self.sub_en_cues: List[Tuple[int, int, str]] = []
        self.sub_vi_cues: List[Tuple[int, int, str]] = []
        self._last_en_text = ""
        self._last_vi_text = ""
        self.sub_delay_ms = tk.IntVar(value=int(self._cfg.get("sub_delay_ms", 0)))

        # SMART-HOLD params
        self.sub_linger_ms   = tk.IntVar(value=int(self._cfg.get("sub_linger_ms",   800)))
        self.sub_min_hold_ms = tk.IntVar(value=int(self._cfg.get("sub_min_hold_ms", 1200)))
        self.sub_per_char_ms = tk.IntVar(value=int(self._cfg.get("sub_per_char_ms", 28)))

        # VLC end guard
        self._end_fired = False

        # Vars
        self.search_var = tk.StringVar()
        self.sort_mode = tk.StringVar(value=SORT_CHOICES[0])  # Default A→Z
        self.folder_filter = tk.StringVar(value="(All)")
        self.volume = tk.IntVar(value=int(self._cfg.get("volume", 70)))
        self.playback_rate = tk.DoubleVar(value=float(self._cfg.get("playback_rate", 1.0)))
        self.now_playing_var = tk.StringVar(value="")  # UI: show current track

        # Subtitle shared font
        self.sub_font = tkfont.Font(family="Arial", size=int(self._cfg.get("sub_font_size", 18)), weight="bold")

        # Center image
        self._current_img_w = 420
        self._logo_tk = None
        self._logo_pil = None
        self.center_img = None

        # VLC
        self.vlc_instance = None
        self.player = None
        if vlc:
            self.vlc_instance = vlc.Instance()

        # Build UI
        apply_pharmapp_theme(self.root)
        self._build_hero_paned()
        self._build_bottom_filters_bar()
        self._build_progress_row()
        self._build_subtitle_row_dual()
        self._build_playlist_expander()
        self.footer_wrap = ttk.Frame(self.root)
        self.footer_wrap.pack(fill=tk.X, padx=8, pady=(0, 6))
        self._build_footer_singleline(self.footer_wrap)

        # Shortcuts
        self._bind_shortcuts()

        # Wraplength responsive
        self.root.bind("<Configure>", self._on_root_configure, add="+")

        # Restore geometry
        geo = self._cfg.get("geometry")
        if geo:
            try: self.root.geometry(geo)
            except Exception: pass

        # periodic
        self.root.after(300, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Restore session
        self.root.after(200, self._restore_last_session)

    # --- Migrate JSONs to config_state/ ---
    def _migrate_old_jsons(self, old_dir: Path, new_dir: Path):
        """Chuyển prajna_config.json, state_*.json, state_recent.json vào thư mục data."""
        patterns = ["prajna_config.json", "state_*.json", "state_recent.json"]
        for pat in patterns:
            for p in old_dir.glob(pat):
                try:
                    target = new_dir / p.name
                    if p.resolve() == target.resolve():
                        continue
                    if not target.exists():
                        os.replace(str(p), str(target))
                except Exception:
                    pass

    # --- Config ---
    def _load_config(self) -> dict:
        try:
            if self.config_path.exists():
                return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}
    def _save_config(self):
        try:
            self._cfg["sub_font_size"]   = int(self.sub_font.cget("size"))
            self._cfg["volume"]          = int(self.volume.get())
            self._cfg["playback_rate"]   = float(self.playback_rate.get())
            self._cfg["sub_delay_ms"]    = int(self.sub_delay_ms.get())
            self._cfg["sub_linger_ms"]   = int(self.sub_linger_ms.get())
            self._cfg["sub_min_hold_ms"] = int(self.sub_min_hold_ms.get())
            self._cfg["sub_per_char_ms"] = int(self.sub_per_char_ms.get())
            self._cfg["geometry"]        = self.root.geometry()
            self._cfg["allow_write_static"] = bool(self.allow_write_static.get())
            self._cfg["state_autosave_ms"]  = int(self.state_autosave_interval)
            self.config_path.write_text(json.dumps(self._cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # --- Title ---
    # def _update_title(self):
    #     folder_name = Path(self.current_folder).name if self.current_folder else ""
    #     title = self.base_title + (f" — {folder_name}" if folder_name else "")
    #     try: self.root.title(title)
    #     except Exception: pass

    # --- Title ---
    def _update_title(self):
        folder_name = Path(self.current_folder).name if self.current_folder else ""
        # folder trước, base_title sau
        title = f"{folder_name} // {self.base_title}" if folder_name else self.base_title
        try:
            self.root.title(title)
        except Exception:
            pass


    # --- Shortcuts ---
    def _bind_shortcuts(self):
        self.root.bind("<space>", lambda e: self.toggle_play())
        self.root.bind("<Return>", lambda e: self.play_selected())
        self.root.bind("<Control-Right>", lambda e: self.next())
        self.root.bind("<Control-Left>", lambda e: self.prev())
        self.root.bind("<Escape>", lambda e: self.clear_filter())
        self.root.bind("<Control-o>", lambda e: self.open_folder())
        self.root.bind("<Control-j>", lambda e: self.open_static_file())
        self.root.bind("<F5>", lambda e: self.rescan_current_folder())
        self.root.bind("s", lambda e: self.stop())
        self.root.bind("r", lambda e: self.toggle_repeat())
        self.root.bind("h", lambda e: self.toggle_shuffle())
        self.root.bind("<Control-Up>",   lambda e: self._bump_volume(+5))
        self.root.bind("<Control-Down>", lambda e: self._bump_volume(-5))
        self.root.bind("<Control-i>", lambda e: self.choose_center_image())
        self.root.bind("<Control-l>", lambda e: self.load_subtitle_manual())
        self.root.bind("<Control-k>", lambda e: self.toggle_sub_enabled())
        self.root.bind("<Control-equal>", lambda e: self._bump_sub_font(+2))
        self.root.bind("<Control-plus>",  lambda e: self._bump_sub_font(+2))
        self.root.bind("<Control-minus>", lambda e: self._bump_sub_font(-2))
        self.root.bind("<Control-1>", lambda e: self._toggle_left())
        self.root.bind("<Control-2>", lambda e: self._toggle_right())
        self.root.bind("<Control-3>", lambda e: self._toggle_playlist())
        self.root.bind("<Alt-bracketleft>", lambda e: self._bump_playback_rate(-0.1))
        self.root.bind("<Alt-bracketright>", lambda e: self._bump_playback_rate(+0.1))
        self.root.bind("<Alt-backslash>", lambda e: self.set_playback_rate(1.0))

    def _bump_volume(self, delta):
        try: v = int(self.volume.get())
        except Exception: v = 50
        v = max(0, min(100, v + delta))
        self.volume.set(v); self.set_volume()

    def _bump_sub_font(self, delta):
        new_size = max(10, min(56, int(self.sub_font.cget("size")) + delta))
        self.sub_font.configure(size=new_size)
        self._relayout_sub_labels()

    def _refresh_speed_label(self):
        try:
            r = float(self.playback_rate.get())
        except Exception:
            r = 1.0
        r = max(0.1, min(4.0, r))
        txt = f"{r:.2f}x".rstrip("0").rstrip(".")
        if hasattr(self, "speed_status_lbl") and self.speed_status_lbl:
            self.speed_status_lbl.config(text=f"Speed: {txt}")

    def set_playback_rate(self, rate: float):
        try:
            rate = float(rate)
        except Exception:
            rate = 1.0
        rate = max(0.1, min(4.0, round(rate, 2)))
        self.playback_rate.set(rate)
        self._refresh_speed_label()
        self._apply_playback_rate()

    def _bump_playback_rate(self, delta: float):
        try:
            cur = float(self.playback_rate.get())
        except Exception:
            cur = 1.0
        self.set_playback_rate(cur + float(delta))

    def _apply_playback_rate(self):
        if not (self.player and vlc):
            return
        try:
            r = max(0.1, min(4.0, float(self.playback_rate.get())))
            self.player.set_rate(r)
        except Exception:
            pass

    def _relayout_sub_one(self, box, main_lbl, shadow_lbl):
        try:
            box.update_idletasks()
            h = max(main_lbl.winfo_reqheight(), shadow_lbl.winfo_reqheight()) + 3
            box.configure(height=max(8, h))
            shadow_lbl.place(relx=0.5, x=1, y=1, anchor="n")
            main_lbl.place(relx=0.5, x=0, y=0, anchor="n")
        except Exception:
            pass

    def _relayout_sub_labels(self):
        if not hasattr(self, "subtitle_en_lbl"):
            return
        self._relayout_sub_one(self.subtitle_en_box, self.subtitle_en_lbl, self.subtitle_en_shadow_lbl)
        self._relayout_sub_one(self.subtitle_vi_box, self.subtitle_vi_lbl, self.subtitle_vi_shadow_lbl)

    def _toggle_left(self): self.exp_left.toggle()
    def _toggle_right(self): self.exp_right.toggle()
    def _toggle_playlist(self):
        if getattr(self, "_playlist_visible", False):
            self.playlist_wrap.forget(); self._playlist_visible = False
        else:
            self.playlist_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 8))
            self._playlist_visible = True

    def _link_label(self, parent, text: str, url: str):
        import webbrowser
        lbl = ttk.Label(parent, text=text, style="Link.TLabel", cursor="hand2")
        lbl.bind("<Button-1>", lambda _e: webbrowser.open_new_tab(url))
        return lbl

    def _build_footer_singleline(self, parent: ttk.Frame):
        import webbrowser
        ttk.Separator(parent, orient="horizontal").grid(row=0, column=0, columnspan=3, sticky="ew", pady=(6, 8))
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_columnconfigure(1, weight=0)
        parent.grid_columnconfigure(2, weight=0)

        left = ttk.Frame(parent); left.grid(row=1, column=0, sticky="w")
        def dot(): ttk.Label(left, text=" • ", style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left")
        def bar(): ttk.Label(left, text=" | ", style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left")

        ttk.Label(left, text="© 2009-2026", style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left"); dot()
        ttk.Label(left, text="🥣 Pharma R&D Platforms", style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left"); dot()
        ttk.Label(left, text="🧠 PharmApp", style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left"); dot()
        ttk.Label(left, text="Discover • Design • Develop • Validate • Deliver", style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left"); bar()
        self._link_label(left, "www.nghiencuuthuoc.com", "https://www.nghiencuuthuoc.com").pack(side="left"); bar()
        self._link_label(left, "Zalo: +84888999311", "https://zalo.me/84888999311").pack(side="left"); bar()
        self._link_label(left, "www.pharmapp.dev", "https://www.pharmapp.dev").pack(side="left")

        right = ttk.Frame(parent); right.grid(row=1, column=2, sticky="e")
        ttk.Button(right, text="💝 Donate NCT", style="Donate.TButton",
                   command=lambda: webbrowser.open_new_tab("https://www.nghiencuuthuoc.com/p/donate.html")
                   ).pack(side="right", padx=(0, 6))
        ttk.Button(right, text="💙 Donate to PharmApp", style="Donate.TButton",
                   command=lambda: webbrowser.open_new_tab("https://www.pharmapp.dev/Donate")
                   ).pack(side="right")

    # --- Build UI ---
    def _build_hero_paned(self):
        self.hero = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        self.hero.pack(fill=tk.X, padx=6, pady=(8, 4))

        self.p_left = ttk.Frame(self.hero)
        self.exp_left = Expander(self.p_left, "Left Controls (Ctrl+1)", open_=True)
        self.exp_left.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self._populate_left_controls(self.exp_left.body)
        self.hero.add(self.p_left, weight=1)

        self.p_center = ttk.Frame(self.hero)
        f = ttk.Frame(self.p_center); f.pack(fill=tk.BOTH, expand=True)
        self._load_center_image(f)
        self.hero.add(self.p_center, weight=2)

        self.p_right = ttk.Frame(self.hero)
        self.exp_right = Expander(self.p_right, "Right Controls (Ctrl+2)", open_=True)
        self.exp_right.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self._populate_right_controls(self.exp_right.body)
        self.hero.add(self.p_right, weight=1)

    def _build_bottom_filters_bar(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill=tk.X, padx=8, pady=(0, 4))

        left = tk.Frame(bar, bg=BG); left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(left, text="Search:").pack(side=tk.LEFT, padx=(0, 4))
        ent = ttk.Entry(left, textvariable=self.search_var, width=28)
        ent.pack(side=tk.LEFT); ent.bind("<KeyRelease>", lambda e: self.apply_filter())

        btn_clear = tk.Button(left, text="Clear [Esc]", command=self.clear_filter); style_btn(btn_clear)
        btn_clear.pack(side=tk.LEFT, padx=(6, 12))

        btn_img = tk.Button(left, text="Set Image [Ctrl+I]", command=self.choose_center_image); style_btn(btn_img)
        btn_img.pack(side=tk.LEFT, padx=(0, 12))

        # Nút đổi ảnh ngẫu nhiên
        btn_img_rand = tk.Button(left, text="Random Img", command=self.shuffle_center_image); style_btn(btn_img_rand)
        btn_img_rand.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(left, text="Sort:").pack(side=tk.LEFT, padx=(0, 4))
        self.sort_dropdown = ttk.Combobox(left, textvariable=self.sort_mode, values=SORT_CHOICES, width=22, state="readonly")
        self.sort_dropdown.pack(side=tk.LEFT)
        self.sort_mode.set(SORT_CHOICES[0])
        try: self.sort_dropdown.current(0)
        except Exception: pass
        self.sort_dropdown.bind("<<ComboboxSelected>>", lambda e: self.resort())

        self.folder_frame = tk.Frame(left, bg=BG)
        ttk.Label(self.folder_frame, text="Folder:").pack(side=tk.LEFT, padx=(12, 4))
        self.folder_combo = ttk.Combobox(self.folder_frame, textvariable=self.folder_filter, values=["(All)"], width=24, state="readonly")
        self.folder_combo.pack(side=tk.LEFT); self.folder_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())

        right = tk.Frame(bar, bg=BG); right.pack(side=tk.RIGHT)
        ttk.Label(right, text="Vol:").pack(side=tk.LEFT, padx=(0, 4))
        self.vol_scale = tk.Scale(right, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.volume,
                                  command=self.set_volume, length=240, bg=BG, highlightthickness=0)
        self.vol_scale.pack(side=tk.LEFT)
        self.vol_value_lbl = ttk.Label(right, text=str(self.volume.get())); self.vol_value_lbl.pack(side=tk.LEFT, padx=(6, 0))
        self.volume.trace_add("write", lambda *_: self.vol_value_lbl.config(text=str(self.volume.get())))

        # Static write toggle
        chk = tk.Checkbutton(right, text="Static write", bg=BG, variable=self.allow_write_static, onvalue=True, offvalue=False)
        chk.pack(side=tk.LEFT, padx=(12,0))

    def _build_progress_row(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.time_label = ttk.Label(bar, text="00:00 / 00:00")
        self.time_label.pack(side=tk.LEFT)

        self.now_playing_lbl = ttk.Label(bar, textvariable=self.now_playing_var, width=60)
        self.now_playing_lbl.pack(side=tk.LEFT, padx=(10, 0))

        self.progress = ttk.Scale(bar, from_=0, to=1000, orient=tk.HORIZONTAL, length=760)
        self.progress.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        self.progress.bind("<ButtonPress-1>", lambda e: self._on_seek_press())
        self.progress.bind("<ButtonRelease-1>", lambda e: self._on_seek_release())

    def _build_subtitle_row_dual(self):
        outer = tk.Frame(self.root, bg=BG); outer.pack(fill=tk.X, padx=8, pady=(0, 6))

        top = tk.Frame(outer, bg=BG); top.pack(fill=tk.X)
        self.sub_status_lbl = ttk.Label(top, text="Sub: (auto)")
        self.sub_status_lbl.pack(side=tk.LEFT)

        btn_load = tk.Button(top, text="Load Sub [Ctrl+L]", command=self.load_subtitle_manual); style_btn(btn_load)
        btn_load.pack(side=tk.LEFT, padx=(12, 6))

        self.btn_sub_toggle = tk.Button(top, text="Sub: On [Ctrl+K]", command=self.toggle_sub_enabled); style_btn(self.btn_sub_toggle)
        self.btn_sub_toggle.pack(side=tk.LEFT, padx=(0, 12))

        btn_dec = tk.Button(top, text="A− [Ctrl+-]", command=lambda: self._bump_sub_font(-2)); style_btn(btn_dec)
        btn_inc = tk.Button(top, text="A+ [Ctrl+=]", command=lambda: self._bump_sub_font(+2)); style_btn(btn_inc)
        btn_dec.pack(side=tk.LEFT, padx=(0, 4)); btn_inc.pack(side=tk.LEFT, padx=(0, 12))

        btn_delay_m = tk.Button(top, text="Delay −5s", command=lambda: self.sub_delay_ms.set(self.sub_delay_ms.get()-5000)); style_btn(btn_delay_m)
        btn_delay_p = tk.Button(top, text="Delay +5s", command=lambda: self.sub_delay_ms.set(self.sub_delay_ms.get()+5000)); style_btn(btn_delay_p)
        btn_delay_m.pack(side=tk.LEFT, padx=(0, 4)); btn_delay_p.pack(side=tk.LEFT, padx=(0, 12))

        self.delay_lbl = ttk.Label(top, text="Δ 0.0s"); self.delay_lbl.pack(side=tk.LEFT)
        self.sub_delay_ms.trace_add("write", lambda *_: self.delay_lbl.config(text=f"Δ {self.sub_delay_ms.get()/1000:.1f}s"))

        # Dual subtitle lines with fake outline/shadow (stacked labels)
        self.subtitle_en_box = tk.Frame(outer, bg=BG, height=30)
        self.subtitle_vi_box = tk.Frame(outer, bg=BG, height=30)
        self.subtitle_en_box.pack(fill=tk.X, pady=(6, 0))
        self.subtitle_vi_box.pack(fill=tk.X, pady=(2, 0))
        self.subtitle_en_box.pack_propagate(False)
        self.subtitle_vi_box.pack_propagate(False)

        self.subtitle_en_shadow_lbl = tk.Label(self.subtitle_en_box, text="", bg=BG, fg="#111111",
                                               font=self.sub_font, justify="center", wraplength=1200)
        self.subtitle_en_lbl = tk.Label(self.subtitle_en_box, text="", bg=BG, fg="#FFD700",
                                        font=self.sub_font, justify="center", wraplength=1200)
        self.subtitle_vi_shadow_lbl = tk.Label(self.subtitle_vi_box, text="", bg=BG, fg="#111111",
                                               font=self.sub_font, justify="center", wraplength=1200)
        self.subtitle_vi_lbl = tk.Label(self.subtitle_vi_box, text="", bg=BG, fg="#90EE90",
                                        font=self.sub_font, justify="center", wraplength=1200)

        # one-pixel fake outline / shadow
        self.subtitle_en_shadow_lbl.place(relx=0.5, x=1, y=1, anchor="n")
        self.subtitle_en_lbl.place(relx=0.5, x=0, y=0, anchor="n")
        self.subtitle_vi_shadow_lbl.place(relx=0.5, x=1, y=1, anchor="n")
        self.subtitle_vi_lbl.place(relx=0.5, x=0, y=0, anchor="n")
        self._relayout_sub_labels()

    def _build_playlist_expander(self):
        self.playlist_wrap = ttk.Frame(self.root); self._playlist_visible = True
        self.playlist_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 8))

        self.tv = ttk.Treeview(self.playlist_wrap, columns=("no", "title", "folder", "dur", "size", "mod"), show="headings")
        self.tv.heading("no", text="#")
        self.tv.heading("title", text="Title")
        self.tv.heading("folder", text="Folder")
        self.tv.heading("dur", text="Duration")
        self.tv.heading("size", text="Size")
        self.tv.heading("mod", text="Modified")

        self.tv.column("no", width=40, anchor="center")
        self.tv.column("title", width=520, anchor="w")
        self.tv.column("folder", width=240, anchor="w")
        self.tv.column("dur", width=90, anchor="e")
        self.tv.column("size", width=90, anchor="e")
        self.tv.column("mod", width=150, anchor="center")

        self.tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tv.bind("<Double-1>", lambda e: self.play_selected())

        sb = ttk.Scrollbar(self.playlist_wrap, orient="vertical", command=self.tv.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tv.configure(yscrollcommand=sb.set)

    def _populate_left_controls(self, parent):
        btn_open = tk.Button(parent, text="Open Folder [Ctrl+O]", command=self.open_folder)
        btn_open_json = tk.Button(parent, text="Open static.json [Ctrl+J]", command=self.open_static_file)
        btn_rescan = tk.Button(parent, text="Rescan [F5]", command=self.rescan_current_folder)
        for r, b in enumerate((btn_open, btn_open_json, btn_rescan)):
            style_btn(b); b.grid(row=r, column=0, padx=2, pady=4, sticky="ew")
        parent.grid_columnconfigure(0, weight=1)

    def _populate_right_controls(self, parent):
        btn_prev = tk.Button(parent, text="Prev [Ctrl+←]", command=self.prev)
        btn_play = tk.Button(parent, text="Play/Pause [Space]", command=self.toggle_play)
        btn_next = tk.Button(parent, text="Next [Ctrl+→]", command=self.next)
        btn_stop = tk.Button(parent, text="Stop [S]", command=self.stop)
        self._btn_repeat = tk.Button(parent, text="Repeat: Off [R]", command=self.toggle_repeat)
        self._btn_shuffle = tk.Button(parent, text="Shuffle: Off [H]", command=self.toggle_shuffle)
        self._btn_speed_dn = tk.Button(parent, text="0.1x −", command=lambda: self._bump_playback_rate(-0.1))
        self._btn_speed_1x = tk.Button(parent, text="1.0x", command=lambda: self.set_playback_rate(1.0))
        self._btn_speed_up = tk.Button(parent, text="0.1x +", command=lambda: self._bump_playback_rate(+0.1))
        self._btn_speed_fast = tk.Button(parent, text="2.0x", command=lambda: self.set_playback_rate(2.0))
        for b in (btn_prev, btn_play, btn_next, btn_stop, self._btn_repeat, self._btn_shuffle,
                  self._btn_speed_dn, self._btn_speed_1x, self._btn_speed_up, self._btn_speed_fast):
            style_btn(b)
        btn_prev.grid(row=0, column=0, padx=2, pady=4, sticky="ew")
        btn_play.grid(row=0, column=1, padx=2, pady=4, sticky="ew")
        btn_next.grid(row=0, column=2, padx=2, pady=4, sticky="ew")
        btn_stop.grid(row=1, column=0, padx=2, pady=4, sticky="ew")
        self._btn_repeat.grid(row=1, column=1, padx=2, pady=4)
        self._btn_shuffle.grid(row=1, column=2, padx=2, pady=4)

        self._btn_speed_dn.grid(row=2, column=0, padx=2, pady=4, sticky="ew")
        self._btn_speed_1x.grid(row=2, column=1, padx=2, pady=4, sticky="ew")
        self._btn_speed_up.grid(row=2, column=2, padx=2, pady=4, sticky="ew")
        self._btn_speed_fast.grid(row=3, column=0, padx=2, pady=(4, 2), sticky="ew")

        self.speed_status_lbl = ttk.Label(parent, text="Speed: 1.0x")
        self.speed_status_lbl.grid(row=3, column=1, columnspan=2, padx=2, pady=(4, 2), sticky="w")
        self._refresh_speed_label()

        for c in range(3):
            parent.grid_columnconfigure(c, weight=1)

    # --- Image handling ---
    def _find_default_image(self) -> Optional[Path]:
        here = _bundle_dir()
        candidates = [
            here / "prajna.png", here / "buddha.png", here / "buddha.jpg",
            here / "logo.png",  here / "logo.jpg",
        ]
        assets = here / "assets"
        if assets.exists():
            for name in ["prajna.png","buddha.png","buddha.jpg","logo.png","logo.jpg"]:
                candidates.append(assets / name)
            candidates.extend(sorted([p for p in assets.glob("*") if p.suffix.lower() in IMAGE_EXTS], key=lambda x: x.name))
        candidates.extend(sorted([p for p in here.glob("*") if p.suffix.lower() in IMAGE_EXTS], key=lambda x: x.name))
        for p in candidates:
            if p.exists(): return p
        return None

    def _random_asset_image(self) -> Optional[Path]:
        assets = _bundle_dir() / "assets"
        if not assets.exists():
            return None
        try:
            imgs = [p for p in assets.glob("*") if p.suffix.lower() in IMAGE_EXTS]
            return random.choice(imgs) if imgs else None
        except Exception:
            return None

    def _get_saved_image_path(self) -> Optional[Path]:
        try:
            p = Path(self._cfg.get("center_image", ""))
            if p.exists() and p.suffix.lower() in IMAGE_EXTS:
                return p
        except Exception:
            pass
        return None

    def _save_image_path(self, p: Path):
        self._cfg["center_image"] = str(p); self._save_config()

    def _load_center_image(self, parent):
        if Image is None or ImageTk is None:
            ph = tk.Label(parent, text="(Install Pillow: pip install pillow)\nOr press Set Image [Ctrl+I]",
                          bg=BG, fg="black", font=("Arial", 12, "bold"), justify="center")
            ph.pack(pady=10); self.center_img = ph; return
        # Ưu tiên ảnh ngẫu nhiên trong assets → rồi mới rơi về mặc định
        img_path = self._random_asset_image() or self._find_default_image()
        if img_path: self._set_center_image(img_path, parent)
        else:
            ph = tk.Label(parent, text="Put images into ./assets or press Set Image [Ctrl+I]",
                          bg=BG, fg="black", font=("Arial", 12, "bold"), justify="center")
            ph.pack(pady=10); self.center_img = ph

    def _set_center_image(self, img_path: Path, parent_widget=None):
        if Image is None or ImageTk is None: return
        try:
            self._logo_pil = Image.open(str(img_path)).convert("RGB")
        except Exception as e:
            messagebox.showerror("Image error", f"Cannot open image:\n{e}"); return
        self._current_img_w = max(260, min(760, 420))
        im = self._resize_logo(self._current_img_w)
        self._logo_tk = ImageTk.PhotoImage(im)
        if isinstance(self.center_img, tk.Label) and getattr(self.center_img, "winfo_exists", lambda: False)():
            self.center_img.configure(image=self._logo_tk, text="")
        else:
            parent = parent_widget or self.p_center
            self.center_img = tk.Label(parent, image=self._logo_tk, bg=BG, bd=0, highlightthickness=0)
            self.center_img.pack(pady=2)

        container = self.center_img.master
        def _on_cfg(e):
            avail = max(260, e.width - 24); target = max(260, min(760, avail))
            if abs(target - self._current_img_w) < 12: return
            self._current_img_w = target
            im = self._resize_logo(target)
            self._logo_tk = ImageTk.PhotoImage(im)
            self.center_img.configure(image=self._logo_tk)
        container.bind("<Configure>", _on_cfg, add="+")

    def shuffle_center_image(self):
        p = self._random_asset_image()
        if p:
            self._set_center_image(p)

    def _resize_logo(self, target_w):
        return self._logo_pil.resize((target_w, int(self._logo_pil.size[1] * target_w / self._logo_pil.size[0])), Image.LANCZOS)

    def choose_center_image(self):
        initial = Path(self.current_folder or _runtime_dir())
        f = filedialog.askopenfilename(title="Choose an image", initialdir=str(initial),
                                       filetypes=(("Image files", "*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp"), ("All files", "*.*")))
        if not f: return
        p = Path(f); self._save_image_path(p)
        if Image is None or ImageTk is None:
            messagebox.showinfo("Info", "Pillow not installed. Run: pip install pillow"); return
        self._set_center_image(p)

    # --- Sub-index builders ---
    def _build_sub_index(self, root: Path):
        idx: Dict[str, Dict[str, Path]] = {}
        for p in root.rglob("*"):
            base = _suffix2_base(p)
            if not base:
                continue
            lang = _suffix2_lang(p)
            if lang not in ("en", "vi"):
                continue
            d = idx.setdefault(base, {})
            d[lang] = p
        self.sub_index = idx

    def _index_find_exact(self, base: str) -> Tuple[Optional[Path], Optional[Path]]:
        d = self.sub_index.get(base)
        if not d: return None, None
        return d.get("en"), d.get("vi")

    def _index_find_closest(self, audio_stem: str) -> Tuple[Optional[Path], Optional[Path]]:
        if not self.sub_index:
            return None, None
        base = _closest_base_to_audio(list(self.sub_index.keys()), audio_stem)
        if not base: return None, None
        d = self.sub_index.get(base, {})
        return d.get("en"), d.get("vi")

    # === STATIC cache helpers ===
    def _static_path_for(self, folder: str) -> Path:
        return Path(folder) / "static.json"

    def _read_static(self, folder: str) -> Optional[dict]:
        try:
            p = self._static_path_for(folder)
            if not p.exists(): return None
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "tracks" in data and isinstance(data["tracks"], list):
                return data
        except Exception:
            return None
        return None

    def _write_static(self, folder: str, items: List[Dict], title: Optional[str] = None) -> None:
        if not self.allow_write_static.get():
            return
        try:
            folder_p = Path(folder)
            payload = {
                "title": title or Path(folder).name,
                "base_folder": str(folder_p.resolve()),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "tracks": []
            }
            for it in items:
                payload["tracks"].append({
                    "path": it["path"],
                    "title": it["name"],
                    "folder": it["folder"],
                    "size": it.get("size", 0),
                    "mtime": it.get("mtime", 0),
                    "duration_ms": it.get("duration_ms"),
                })
            spath = self._static_path_for(folder)
            lock = spath.with_suffix(".lock")
            with _FileLock(lock, timeout=6.0):
                _atomic_write_json(spath, payload)
        except Exception:
            pass

    def _items_from_static(self, data: dict) -> List[Dict]:
        items = []
        try:
            for t in data.get("tracks", []):
                p = Path(t.get("path",""))
                if not p.exists():
                    continue
                items.append({
                    "path": str(p),
                    "name": p.stem if not t.get("title") else t.get("title"),
                    "folder": p.parent.name,
                    "size": int(t.get("size", 0)),
                    "mtime": float(t.get("mtime", 0)),
                    "duration_ms": t.get("duration_ms")
                })
        except Exception:
            items = []
        return items

    # === STATE helpers ===
    def _recent_file(self) -> Path:
        return self.app_dir / "state_recent.json"

    def _state_file_for(self, folder: str) -> Path:
        abs_path = str(Path(folder).resolve())
        h = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:12]
        return self.app_dir / f"state_{h}.json"

    def _load_recent(self) -> Optional[str]:
        try:
            f = self._recent_file()
            if f.exists():
                data = json.loads(f.read_text(encoding="utf-8"))
                folder = data.get("folder")
                if folder and Path(folder).exists():
                    return folder
        except Exception:
            return None
        return None

    def _save_recent(self, folder: str):
        try:
            payload = {"folder": folder, "saved_at": time.time()}
            _atomic_write_json(self._recent_file(), payload)
        except Exception:
            pass

    def _load_state_for_folder(self, folder: str) -> dict:
        p = self._state_file_for(folder)
        if not p.exists():
            s = DEFAULT_STATE.copy(); s["folder"] = folder
            return s
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out = DEFAULT_STATE.copy()
            out.update(data or {})
            out["folder"] = folder
            return out
        except Exception:
            s = DEFAULT_STATE.copy(); s["folder"] = folder
            return s

    def _save_state_now(self):
        if not self.current_folder:
            return
        try:
            idx = self.current_index if self.current_index is not None else 0
            pos = int(self.player.get_time()) if (self.player and vlc) else 0
            song = self.items_all[idx]["path"] if (0 <= idx < len(self.items_all)) else ""
            payload = {
                "folder": self.current_folder,
                "index": int(idx),
                "volume": int(self.volume.get()),
                "song": song,
                "position": int(max(0, pos)),
                "saved_at": time.time(),
            }
            _atomic_write_json(self._state_file_for(self.current_folder), payload)
            self._save_recent(self.current_folder)
            self._last_state_save = time.time()
        except Exception:
            pass

    # --- Data / View ---
    def open_folder(self):
        folder = filedialog.askdirectory(title="Select folder with audio/talks")
        if not folder: return
        self.current_folder = folder
        self._update_title()
        self._save_recent(folder)
        self.scan_folder(folder)
        self.sort_mode.set(SORT_CHOICES[0])   # ensure A→Z
        self.resort(); self.apply_filter()
        st = self._load_state_for_folder(folder)
        if st.get("song") and Path(st["song"]).exists():
            idx = self._index_of_path(st["song"])
            if idx is not None:
                self.volume.set(int(st.get("volume", 70)))
                self._play_index(idx, resume_ms=int(st.get("position", 0)))

    def open_static_file(self):
        base = Path(self.current_folder or Path.cwd())
        f = filedialog.askopenfilename(title="Open static.json", initialdir=str(base),
                                       filetypes=(("JSON", "*.json"), ("All files", "*.*")))
        if not f: return
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            messagebox.showerror("Invalid JSON", f"Cannot parse:\n{e}"); return

        items = []
        if isinstance(data, dict) and "tracks" in data:
            items = self._items_from_static(data)
        elif isinstance(data, list):
            for p in data:
                p = Path(p)
                if (p.suffix.lower() in AUDIO_EXTS or p.suffix.lower() in VIDEO_EXTS) and p.exists():
                    items.append(self._make_item_from_path(p))
        elif isinstance(data, dict) and "files" in data and isinstance(data["files"], list):
            for p in data["files"]:
                p = Path(p)
                if (p.suffix.lower() in AUDIO_EXTS or p.suffix.lower() in VIDEO_EXTS) and p.exists():
                    items.append(self._make_item_from_path(p))
        else:
            messagebox.showwarning("Unsupported", "Unsupported JSON format"); return

        self.items_all = items
        self.current_folder = str(Path(items[0]["path"]).parent) if items else self.current_folder
        self._update_title()
        if self.current_folder:
            self._build_sub_index(Path(self.current_folder))
        self.resort(); self.apply_filter()

    def rescan_current_folder(self):
        if not self.current_folder: return
        self.scan_folder(self.current_folder)
        self.sort_mode.set(SORT_CHOICES[0])   # ensure A→Z
        self.resort(); self.apply_filter()

    def scan_folder(self, folder: str):
        root = Path(folder)

        # Try static cache
        data = self._read_static(folder)
        items = []
        if data:
            items = self._items_from_static(data)

        # Fallback to filesystem scan
        if not items:
            files = []
            for ext in (*AUDIO_EXTS, *VIDEO_EXTS):
                files.extend(root.rglob(f"*{ext}"))
            items = [self._make_item_from_path(p) for p in files]
            self._write_static(folder, items, title=root.name)

        self.items_all = items
        self._build_sub_index(root)
        self._refresh_folder_filter_visibility()

    def _refresh_folder_filter_visibility(self):
        folders = sorted({it["folder"] for it in self.items_all})
        if len(folders) > 1:
            values = ["(All)"] + folders
            self.folder_combo.configure(values=values)
            if self.folder_filter.get() not in values: self.folder_filter.set("(All)")
            if not self.folder_frame.winfo_ismapped(): self.folder_frame.pack(side=tk.LEFT)
        else:
            if self.folder_frame.winfo_ismapped(): self.folder_frame.pack_forget()
            self.folder_filter.set("(All)")

    def _format_bytes(self, n):
        if n <= 0: return "0 MB"
        return f"{n/1_000_000:.1f} MB"

    def _format_dt(self, ts):
        if ts <= 0: return ""
        lt = time.localtime(ts); return time.strftime("%Y-%m-%d %H:%M", lt)

    def _format_dur(self, ms):
        if ms is None or ms <= 0: return "—"
        s = int(ms/1000); m, s = divmod(s, 60); h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _make_item_from_path(self, p: Path) -> Dict:
        try:
            st = p.stat(); mtime = st.st_mtime; size = st.st_size
        except Exception:
            mtime = 0; size = 0
        dur = None
        if MutagenFile is not None and p.suffix.lower() in AUDIO_EXTS:
            try:
                mf = MutagenFile(str(p))
                if mf and mf.info and getattr(mf.info, 'length', None):
                    dur = int(float(mf.info.length) * 1000)
            except Exception:
                dur = None
        return {"path": str(p), "name": p.stem, "folder": str(p.parent.name),
                "size": size, "mtime": mtime, "duration_ms": dur}

    def _index_of_path(self, path: str) -> Optional[int]:
        for i, it in enumerate(self.items_all):
            if Path(it["path"]) == Path(path):
                return i
        return None

    def resort(self):
        key = self.sort_mode.get()
        items = self.items_all[:]
        if key == "Title (A→Z)":
            items.sort(key=lambda x: x["name"].lower())
        elif key == "Title (Z→A)":
            items.sort(key=lambda x: x["name"].lower(), reverse=True)
        elif key == "Modified (Newest)":
            items.sort(key=lambda x: x["mtime"], reverse=True)
        elif key == "Modified (Oldest)":
            items.sort(key=lambda x: x["mtime"])
        elif key == "Size (Large→Small)":
            items.sort(key=lambda x: x["size"], reverse=True)
        elif key == "Size (Small→Large)":
            items.sort(key=lambda x: x["size"])
        elif key == "Duration (Long→Short)":
            items.sort(key=lambda x: (x["duration_ms"] if x["duration_ms"] else -1), reverse=True)
        elif key == "Duration (Short→Long)":
            items.sort(key=lambda x: (x["duration_ms"] if x["duration_ms"] else 10**12))
        self.items_all = items; self.apply_filter()

    def apply_filter(self):
        q = (self.search_var.get() or "").strip().lower()
        folder = self.folder_filter.get()
        view_idx = []
        for i, it in enumerate(self.items_all):
            if folder and folder != "(All)" and it["folder"] != folder: continue
            if q and q not in it["name"].lower(): continue
            view_idx.append(i)
        self.items_view = view_idx
        self._refresh_tree()

    def _refresh_tree(self):
        self.tv.delete(*self.tv.get_children())
        for n, idx in enumerate(self.items_view, start=1):
            it = self.items_all[idx]
            self.tv.insert("", "end", iid=str(idx), values=(
                n, it["name"], it["folder"],
                self._format_dur(it["duration_ms"]),
                self._format_bytes(it["size"]),
                self._format_dt(it["mtime"]),
            ))

    def clear_filter(self):
        self.search_var.set(""); self.folder_filter.set("(All)"); self.apply_filter()

    # --- Playback / VLC ---
    def _ensure_player(self):
        if not vlc:
            messagebox.showwarning("VLC not available", "python-vlc is not installed. Install:\n\npip install python-vlc")
            return False
        if not self.player:
            self.player = self.vlc_instance.media_player_new()
            self.player.audio_set_volume(int(self.volume.get()))
            self._apply_playback_rate()
            self._attach_vlc_events()
        return True

    def _attach_vlc_events(self):
        if not vlc or not self.player: return
        try:
            self._em = self.player.event_manager()
            self._em.event_attach(vlc.EventType.MediaPlayerEndReached, self._vlc_on_end)
        except Exception:
            pass

    def _vlc_on_end(self, event):
        if getattr(self, "_end_fired", False): return
        self._end_fired = True
        self.root.after(0, self._on_track_end)

    def _on_track_end(self):
        idx = self._next_index()
        if idx != -1:
            self._play_index(idx)
        self._end_fired = False

    def selected_view_index(self):
        sel = self.tv.selection()
        if not sel: return None
        iid = sel[0]
        try: idx = int(iid)
        except Exception: return None
        if idx < 0 or idx >= len(self.items_all): return None
        return idx

    def _set_now_playing(self, path: Optional[Path]):
        try:
            if not path:
                self.now_playing_var.set("")
                return
            name = Path(path).name
            self.now_playing_var.set(f"Now: {name}")
        except Exception:
            # avoid any UI crash from a label update
            pass

    def play_selected(self):
        idx = self.selected_view_index()
        if idx is None:
            if self.items_view: idx = self.items_view[0]
            else: return
        self._play_index(idx)

    def _play_index(self, idx, resume_ms: int = 0):
        if not self._ensure_player(): return
        self._end_fired = False
        self.current_index = idx
        path = Path(self.items_all[idx]["path"])
        self._set_now_playing(path)
        if vlc:
            media = self.vlc_instance.media_new(str(path))
            self.player.set_media(media); self.player.play()
            self.root.after(250, self._apply_playback_rate)
            self.root.after(900, self._apply_playback_rate)
        self._refresh_speed_label()
        # auto load dual subtitles (with global sub-index)
        self._auto_load_subtitle_for(path)
        self.root.after(700, lambda: self._maybe_fill_duration(idx))
        if resume_ms > 0:
            self.root.after(900, lambda m=resume_ms: self._seek_ms_safe(m))
        # Save state immediately on play
        self._save_state_now()

    def _seek_ms_safe(self, ms: int):
        if not self.player or not vlc: return
        try:
            self.player.set_time(int(ms))
        except Exception:
            length = int(self.player.get_length() or 0)
            if length > 0:
                pos = max(0.0, min(1.0, ms/length))
                try: self.player.set_position(pos)
                except Exception: pass

    def _maybe_fill_duration(self, idx):
        if not vlc or not self.player: return
        try:
            length = self.player.get_length()
            if length > 0:
                self.items_all[idx]["duration_ms"] = length
                self._refresh_tree()
        except Exception:
            pass

    def toggle_play(self):
        if not self.player:
            self.play_selected(); return
        state = self.player.get_state() if vlc else None
        if vlc and state in (vlc.State.Playing, vlc.State.Buffering):
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        if self.player:
            self.player.stop()
        self._set_now_playing(None)

    # ---- Sequence based on current view ----
    def _play_sequence(self) -> List[int]:
        if self.items_view:
            return self.items_view[:]
        return list(range(len(self.items_all)))

    def _next_index(self):
        seq = self._play_sequence()
        if not seq:
            return -1

        if self.is_shuffle:
            if len(seq) == 1:
                return seq[0]
            choices = [i for i in seq if i != self.current_index]
            return random.choice(choices) if choices else seq[0]

        if self.current_index in seq:
            pos = seq.index(self.current_index)
            if pos + 1 < len(seq):
                return seq[pos + 1]
            return seq[0] if self.is_repeat else -1
        else:
            return seq[0]

    def next(self):
        idx = self._next_index()
        if idx == -1:
            return
        self._play_index(idx)

    def prev(self):
        seq = self._play_sequence()
        if not seq:
            return
        if self.is_shuffle:
            if len(seq) == 1:
                idx = seq[0]
            else:
                choices = [i for i in seq if i != self.current_index]
                idx = random.choice(choices) if choices else seq[0]
            self._play_index(idx)
            return

        if self.current_index in seq:
            pos = seq.index(self.current_index)
            if pos > 0:
                idx = seq[pos - 1]
            else:
                idx = seq[-1] if self.is_repeat else seq[0]
        else:
            idx = seq[0]
        self._play_index(idx)

    def toggle_repeat(self):
        self.is_repeat = not self.is_repeat
        try: self._btn_repeat.config(text=f"Repeat: {'On' if self.is_repeat else 'Off'} [R]")
        except Exception: pass

    def toggle_shuffle(self):
        self.is_shuffle = not self.is_shuffle
        try: self._btn_shuffle.config(text=f"Shuffle: {'On' if self.is_shuffle else 'Off'} [H]")
        except Exception: pass

    # --- Seeking & volume ---
    def _on_seek_press(self): self._seeking = True
    def _on_seek_release(self): self._seeking = False; self.seek()

    def set_volume(self, *_):
        if self.player and vlc:
            try: self.player.audio_set_volume(int(self.volume.get()))
            except Exception: pass

    def seek(self):
        if not self.player or not vlc: return
        try: x = float(self.progress.get())
        except Exception: return
        pos = max(0.0, min(1.0, x / 1000.0))
        try: self.player.set_position(pos)
        except Exception: pass

    # --- Subtitles ---
    def _guess_lang_from_name(self, name: str) -> str:
        n = name.lower()
        if ".en." in n or n.endswith(".en") or " english" in n or "(en)" in n or "_en" in n or "-en" in n:
            return "en"
        if ".vi." in n or n.endswith(".vi") or " viet" in n or " vietnam" in n or "(vi)" in n or "_vi" in n or "-vi" in n:
            return "vi"
        return "other"

    def toggle_sub_enabled(self):
        self.sub_enabled.set(not self.sub_enabled.get())
        self.btn_sub_toggle.config(text=f"Sub: {'On' if self.sub_enabled.get() else 'Off'} [Ctrl+K]")
        if not self.sub_enabled.get():
            self.subtitle_en_lbl.config(text="")
            self.subtitle_vi_lbl.config(text="")
            self.subtitle_en_shadow_lbl.config(text="")
            self.subtitle_vi_shadow_lbl.config(text="")
        else:
            self._last_en_text = ""
            self._last_vi_text = ""

    def load_subtitle_manual(self):
        base = Path(self.current_folder or Path.cwd())
        f = filedialog.askopenfilename(title="Open subtitle", initialdir=str(base),
                                       filetypes=(("Subtitles", "*.vtt;*.srt"), ("All files", "*.*")))
        if not f: return
        sel = Path(f)
        base_str = _suffix2_base(sel)
        if base_str:
            folder = sel.parent
            pairs = _pair_subtitles_in_folder(folder)
            d = pairs.get(base_str, {})
            en_candidate = d.get("en") if self._guess_lang_from_name(sel.name) != "en" else sel
            vi_candidate = d.get("vi") if self._guess_lang_from_name(sel.name) != "vi" else sel
        else:
            lang = self._guess_lang_from_name(sel.name)
            if lang == "vi":
                en_candidate, vi_candidate = self._auto_pair_for(sel, expect="vi")
            elif lang == "en":
                en_candidate, vi_candidate = self._auto_pair_for(sel, expect="en")
            else:
                en_candidate, vi_candidate = self._auto_pair_for(sel, expect="en")
        self._load_dual_subtitles(en_candidate, vi_candidate)

    def _auto_pair_for(self, picked: Path, expect: str) -> Tuple[Optional[Path], Optional[Path]]:
        folder = picked.parent
        strict_base = _suffix2_base(picked)
        if strict_base:
            pairs = _pair_subtitles_in_folder(folder)
            d = pairs.get(strict_base)
            if d: return d.get("en"), d.get("vi")

        base_norm = _norm(Path(picked.stem).name)
        en_best = picked if expect == "en" else None
        vi_best = picked if expect == "vi" else None
        best_en_dist = 10**9
        best_vi_dist = 10**9

        for p in folder.glob("*"):
            if p.suffix.lower() not in SUB_EXTS and not _suffix2_base(p):
                continue
            lang = self._guess_lang_from_name(p.name)
            nm = _norm(Path(p.stem).name)
            distance = abs(len(nm) - len(base_norm))
            if lang == "en" and (distance < best_en_dist):
                en_best = p; best_en_dist = distance
            elif lang == "vi" and (distance < best_vi_dist):
                vi_best = p; best_vi_dist = distance
        return en_best, vi_best

    def _exact_pair_for_audio(self, audio_path: Path) -> Tuple[Optional[Path], Optional[Path]]:
        folder = audio_path.parent
        stem = audio_path.stem
        en = vi = None
        for ext in (".vtt", ".srt"):
            e = folder / f"{stem}.en{ext}"
            v = folder / f"{stem}.vi{ext}"
            if e.exists(): en = e
            if v.exists(): vi = v
        return en, vi


    def _exact_single_sub_for_audio(self, audio_path: Path) -> Optional[Path]:
        """Return a single subtitle file that matches the audio stem exactly.

        Supports files like: <stem>.srt or <stem>.vtt (no .en/.vi suffix).
        """
        folder = audio_path.parent
        stem = audio_path.stem
        for ext in (".vtt", ".srt"):
            p = folder / f"{stem}{ext}"
            if p.exists():
                return p
        return None

    def _auto_load_subtitle_for(self, audio_path: Path):
        folder = audio_path.parent

        # (1) Exact pair theo tên bài (same folder)
        en_path, vi_path = self._exact_pair_for_audio(audio_path)
        if en_path or vi_path:
            self._load_dual_subtitles(en_path, vi_path)
            return

        # (1b) Exact SINGLE subtitle (same stem) — treat as EN track
        single = self._exact_single_sub_for_audio(audio_path)
        if single:
            self._load_dual_subtitles(single, None)
            return

        # (2) Strict pairs trong thư mục
        pairs = _pair_subtitles_in_folder(folder)
        if pairs:
            base2 = _closest_base_to_audio(list(pairs.keys()), audio_path.stem)
            if base2:
                en2 = pairs[base2].get("en")
                vi2 = pairs[base2].get("vi")
                if en2 or vi2:
                    self._load_dual_subtitles(en2, vi2)
                    return

        # (3) Sub-index exact (toàn thư mục đã mở)
        en3, vi3 = self._index_find_exact(audio_path.stem)
        if en3 or vi3:
            self._load_dual_subtitles(en3, vi3)
            return

        # (4) Sub-index closest
        en4, vi4 = self._index_find_closest(audio_path.stem)
        if en4 or vi4:
            self._load_dual_subtitles(en4, vi4)
            return

        # (5) Fallback fuzzy (same folder)
        en5, vi5 = self._fuzzy_pick_for_audio(audio_path)
        self._load_dual_subtitles(en5, vi5)

    def _fuzzy_pick_for_audio(self, audio_path: Path) -> Tuple[Optional[Path], Optional[Path]]:
        """Fallback subtitle picker (same folder) with a safety threshold.

        Groups subtitle files by strict base name (e.g., '001 ...' for both .en/.vi) and
        only returns a match if the group is sufficiently close to the current audio.
        """
        folder = audio_path.parent
        stem = audio_path.stem
        min_score = 35.0

        groups: Dict[str, Dict[str, Path]] = {}
        for p in sorted(folder.glob("*")):
            if p.suffix.lower() not in SUB_EXTS and not _suffix2_base(p):
                continue
            base = _suffix2_base(p) or p.stem
            lang = self._guess_lang_from_name(p.name)
            if lang not in ("en", "vi"):
                continue
            groups.setdefault(base, {})[lang] = p

        best_base, best_score = None, -1.0
        for base in groups.keys():
            score = _match_name_score(base, stem)
            if score > best_score:
                best_base, best_score = base, score

        if not best_base or best_score < min_score:
            return None, None

        d = groups.get(best_base, {})
        return d.get("en"), d.get("vi")

    def _load_dual_subtitles(self, en_path: Optional[Path], vi_path: Optional[Path]):
        en_cues, vi_cues = [], []
        en_name, vi_name = "(none)", "(none)"
        if en_path:
            try:
                en_cues = parse_vtt_or_srt(en_path); en_name = en_path.name
            except Exception as e:
                messagebox.showerror("Subtitle error", f"Cannot parse EN subtitle:\n{e}")
        if vi_path:
            try:
                vi_cues = parse_vtt_or_srt(vi_path); vi_name = vi_path.name
            except Exception as e:
                messagebox.showerror("Subtitle error", f"Cannot parse VI subtitle:\n{e}")

        self.sub_en_file, self.sub_vi_file = en_path, vi_path
        self.sub_en_cues, self.sub_vi_cues = en_cues, vi_cues
        self._last_en_text = ""; self._last_vi_text = ""

        if en_cues or vi_cues:
            parts = []
            if en_cues: parts.append(f"EN:{en_name}({len(en_cues)})")
            if vi_cues: parts.append(f"VI:{vi_name}({len(vi_cues)})")
            base_info = " • ".join(parts)
        else:
            base_info = "Sub: (not found)"

        if en_cues and vi_cues:
            msg, _, _ = _alignment_diagnostics(en_cues, vi_cues)
            self.sub_status_lbl.config(text=f"{base_info} | {msg}")
        else:
            self.sub_status_lbl.config(text=base_info)

        self._update_subtitle_by_time(self._current_play_ms())

    def _current_play_ms(self) -> int:
        if not self.player or not vlc: return -1
        try: return int(self.player.get_time())
        except Exception: return -1

    # >>> SMART-HOLD lookup <<<
    def _lookup_cue_text(self, cues, t_ms_adj: int):
        if not cues:
            return None

        LINGER   = int(self.sub_linger_ms.get())
        MIN_HOLD = int(self.sub_min_hold_ms.get())
        PER_CHAR = int(self.sub_per_char_ms.get())

        def eff_end(i, a, b, s, next_start):
            n_chars = len(re.sub(r"\s+", "", s or ""))
            hold_ms = max(MIN_HOLD, PER_CHAR * n_chars)
            end = max(b, a + hold_ms, b + LINGER)
            return min(end, next_start - 40)

        for i, (a, b, s) in enumerate(cues):
            next_a = cues[i+1][0] if i+1 < len(cues) else 10**12
            end_i = eff_end(i, a, b, s, next_a)

            if a <= t_ms_adj < end_i:
                return s

            if t_ms_adj < a:
                if i > 0:
                    pa, pb, ps = cues[i-1]
                    prev_end = eff_end(i-1, pa, pb, ps, a)
                    if t_ms_adj < prev_end:
                        return ps
                break

        la, lb, ls = cues[-1]
        last_end = eff_end(len(cues)-1, la, lb, ls, 10**12)
        if t_ms_adj < last_end:
            return ls
        return None

    def _update_subtitle_by_time(self, t_ms: int):
        if not self.sub_enabled.get() or t_ms < 0: return
        t_ms_adj = t_ms + int(self.sub_delay_ms.get())

        en_txt = self._lookup_cue_text(self.sub_en_cues, t_ms_adj)
        if en_txt != self._last_en_text:
            txt = en_txt or ""
            self.subtitle_en_lbl.config(text=txt)
            self.subtitle_en_shadow_lbl.config(text=txt)
            self._last_en_text = txt
            self._relayout_sub_labels()

        vi_txt = self._lookup_cue_text(self.sub_vi_cues, t_ms_adj)
        if vi_txt != self._last_vi_text:
            txt = vi_txt or ""
            self.subtitle_vi_lbl.config(text=txt)
            self.subtitle_vi_shadow_lbl.config(text=txt)
            self._last_vi_text = txt
            self._relayout_sub_labels()

        if self.sub_en_cues:
            if not self.subtitle_en_box.winfo_ismapped():
                self.subtitle_en_box.pack(fill=tk.X, pady=(6, 0))
        else:
            if self.subtitle_en_box.winfo_ismapped():
                self.subtitle_en_box.pack_forget()

        if self.sub_vi_cues:
            if not self.subtitle_vi_box.winfo_ismapped():
                self.subtitle_vi_box.pack(fill=tk.X, pady=(2, 0))
        else:
            if self.subtitle_vi_box.winfo_ismapped():
                self.subtitle_vi_box.pack_forget()

    # --- UI updater ---
    def _tick(self):
        self._update_progress()
        self._update_subtitle_by_time(self._current_play_ms())
        if self.current_folder and (time.time() - self._last_state_save) * 1000 >= self.state_autosave_interval:
            self._save_state_now()
        self.root.after(300, self._tick)

    def _update_progress(self):
        if not self.player or not vlc:
            self.time_label.config(text="00:00 / 00:00"); return
        length_ms = int(self.player.get_length())
        time_ms = int(self.player.get_time())
        state = self.player.get_state()

        def fmt(ms):
            if ms < 0: ms = 0
            s = int(ms / 1000); m, s = divmod(s, 60); h, m = divmod(m, 60)
            return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

        self.time_label.config(text=f"{fmt(time_ms)} / {fmt(length_ms)}")

        if not getattr(self, "_seeking", False) and length_ms > 0:
            try: self.progress.set(max(0, min(1000, int(1000 * time_ms / length_ms))))
            except Exception: pass

        near_end = (length_ms > 0 and time_ms >= length_ms - 1000)
        if (state == vlc.State.Ended or near_end) and not getattr(self, "_end_fired", False):
            seq = self._play_sequence()
            has_next_in_seq = False
            if self.current_index in seq:
                pos = seq.index(self.current_index)
                has_next_in_seq = (pos + 1 < len(seq))
            should_continue = self.is_repeat or self.is_shuffle or has_next_in_seq
            if should_continue:
                self._end_fired = True
                self.root.after(0, self._on_track_end)

    # --- Resize/wrap ---
    def _on_root_configure(self, event):
        try:
            w = max(600, self.root.winfo_width() - 80)
            self.subtitle_en_lbl.configure(wraplength=w)
            self.subtitle_vi_lbl.configure(wraplength=w)
            self.subtitle_en_shadow_lbl.configure(wraplength=w)
            self.subtitle_vi_shadow_lbl.configure(wraplength=w)
            self._relayout_sub_labels()
        except Exception:
            pass

    # --- Resume last session ---
    def _restore_last_session(self):
        folder = self._load_recent()
        if not folder or not Path(folder).exists():
            return
        self.current_folder = folder
        self._update_title()
        self.scan_folder(folder)
        self.sort_mode.set(SORT_CHOICES[0])
        self.resort(); self.apply_filter()
        st = self._load_state_for_folder(folder)
        self.volume.set(int(st.get("volume", 70)))
        idx = None
        if st.get("song") and Path(st["song"]).exists():
            idx = self._index_of_path(st["song"])
        if idx is None and isinstance(st.get("index"), int) and 0 <= st["index"] < len(self.items_all):
            idx = st["index"]
        if idx is not None:
            self._play_index(idx, resume_ms=int(st.get("position", 0)))

    # --- Close ---
    def _on_close(self):
        self._save_state_now()
        self._save_config()
        try:
            if self.player: self.player.stop()
        except Exception:
            pass
        self.root.destroy()

# ---- Icon helper ----
def set_app_icon(root: tk.Tk):
    here = _bundle_dir()
    # Prefer .ico on Windows if present
    try:
        if os.name == "nt":
            ico = here / "prajna.ico"
            if ico.exists():
                root.iconbitmap(default=str(ico))
                return
    except Exception:
        pass
    # PNG/JPG fallback (also for macOS/Linux)
    try:
        if Image is not None and ImageTk is not None:
            for name in ("prajna.png", "buddha.png", "logo.png"):
                p = here / name
                if p.exists():
                    img = Image.open(p)
                    root.iconphoto(True, ImageTk.PhotoImage(img))
                    break
    except Exception:
        pass

def main():
    _prepare_vlc_runtime()      # IMPORTANT: prepare before creating vlc instance inside app
    root = tk.Tk()
    set_app_icon(root)
    app = PrajnaPlayerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
