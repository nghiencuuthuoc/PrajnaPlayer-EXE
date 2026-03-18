import os
import re
import sys
import bisect
import json
import time
import hashlib
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

from prajna_packaging_runtime import apply_app_identity_and_icons

try:
    import vlc
except ImportError:
    raise SystemExit("python-vlc is not installed. Run: pip install python-vlc")


# ============================================================
# Subtitle parsing (.srt / .vtt)
# ============================================================

def parse_time_to_ms(value: str) -> int:
    """
    Parse subtitle time formats:
    - SRT: 00:01:02,345
    - VTT: 00:01:02.345
    - VTT short: 01:02.345
    """
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        hh = 0
        mm = int(parts[0])
        ss_ms = parts[1]
    elif len(parts) == 3:
        hh = int(parts[0])
        mm = int(parts[1])
        ss_ms = parts[2]
    else:
        raise ValueError(f"Invalid timestamp: {value}")

    if "." in ss_ms:
        ss, ms = ss_ms.split(".", 1)
        ss = int(ss)
        ms = int((ms + "000")[:3])
    else:
        ss = int(ss_ms)
        ms = 0

    return ((hh * 3600 + mm * 60 + ss) * 1000) + ms


def normalize_sub_text(lines):
    text = "\n".join(line.strip() for line in lines if line.strip())
    text = re.sub(r"<[^>]+>", "", text)  # basic cleanup for VTT tags
    return text.strip()


def parse_srt_text(content: str):
    cues = []
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*\n", content)

    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        if "-->" in lines[0]:
            timing_line = lines[0]
            text_lines = lines[1:]
        elif len(lines) >= 2 and "-->" in lines[1]:
            timing_line = lines[1]
            text_lines = lines[2:]
        else:
            continue

        try:
            start_str, end_str = [x.strip() for x in timing_line.split("-->", 1)]
            start_ms = parse_time_to_ms(start_str.split()[0])
            end_ms = parse_time_to_ms(end_str.split()[0])
        except Exception:
            continue

        text = normalize_sub_text(text_lines)
        if text:
            cues.append((start_ms, end_ms, text))

    cues.sort(key=lambda x: x[0])
    return cues


def parse_vtt_text(content: str):
    cues = []
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = content.lstrip("\ufeff")
    lines = content.split("\n")

    blocks = []
    current = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    skip_heads = ("WEBVTT", "NOTE", "STYLE", "REGION")

    for block in blocks:
        if not block:
            continue
        first = block[0].strip().upper()
        if any(first.startswith(h) for h in skip_heads):
            continue

        if "-->" in block[0]:
            timing_line = block[0]
            text_lines = block[1:]
        elif len(block) >= 2 and "-->" in block[1]:
            timing_line = block[1]
            text_lines = block[2:]
        else:
            continue

        try:
            start_str, end_str = [x.strip() for x in timing_line.split("-->", 1)]
            start_ms = parse_time_to_ms(start_str.split()[0])
            end_ms = parse_time_to_ms(end_str.split()[0])
        except Exception:
            continue

        text = normalize_sub_text(text_lines)
        if text:
            cues.append((start_ms, end_ms, text))

    cues.sort(key=lambda x: x[0])
    return cues


def load_subtitle_file(path: str):
    if not path or not os.path.isfile(path):
        return [], []

    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        content = f.read()

    ext = os.path.splitext(path)[1].lower()
    cues = parse_vtt_text(content) if ext == ".vtt" else parse_srt_text(content)
    starts = [c[0] for c in cues]
    return cues, starts


def find_active_cue(cues, starts, current_ms: int) -> str:
    if not cues or current_ms < 0:
        return ""

    idx = bisect.bisect_right(starts, current_ms) - 1
    if idx < 0 or idx >= len(cues):
        return ""

    start_ms, end_ms, text = cues[idx]
    if start_ms <= current_ms <= end_ms:
        return text
    return ""


# ============================================================
# Flexible subtitle auto-mapping
# ============================================================

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"}
SUB_EXTS = {".srt", ".vtt"}

VI_TAGS = {"vi", "vie", "viet", "vietnamese", "vn", "tiengviet", "tviet", "vietnam"}
EN_TAGS = {"en", "eng", "english"}
GENERIC_SUB_TAGS = {"sub", "subs", "subtitle", "captions", "cc", "closedcaptions"}


def split_tokens(text: str):
    text = text.lower()
    text = re.sub(r"\.[a-z0-9]{2,4}$", "", text)
    tokens = re.split(r"[\s\.\-_\[\]\(\)\{\}]+", text)
    return [t for t in tokens if t]


def normalize_for_match(text: str):
    text = text.lower()
    text = re.sub(r"\.[a-z0-9]{2,4}$", "", text)
    text = re.sub(r"[\[\]\(\)\{\}]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_sub_language(filename: str):
    tokens = set(split_tokens(filename))

    vi_score = len(tokens & VI_TAGS)
    en_score = len(tokens & EN_TAGS)

    n = normalize_for_match(filename)
    if "tieng viet" in n:
        vi_score += 2
    if "vietnamese" in n:
        vi_score += 2
    if "english" in n:
        en_score += 2

    if vi_score > en_score and vi_score > 0:
        return "vi"
    if en_score > vi_score and en_score > 0:
        return "en"
    return "unknown"


def subtitle_match_score(video_path: str, sub_path: str, preferred_lang: str = None):
    video_dir = os.path.dirname(video_path)
    video_stem = os.path.splitext(os.path.basename(video_path))[0]
    sub_name = os.path.basename(sub_path)
    sub_stem = os.path.splitext(sub_name)[0]

    vnorm = normalize_for_match(video_stem)
    snorm = normalize_for_match(sub_stem)
    vtokens = set(split_tokens(video_stem))
    stokens = set(split_tokens(sub_stem))

    lang = detect_sub_language(sub_name)
    score = 0

    if os.path.dirname(sub_path) == video_dir:
        score += 80

    if snorm == vnorm:
        score += 120

    if snorm.startswith(vnorm):
        score += 70
    if vnorm in snorm:
        score += 40

    overlap = len((vtokens & stokens) - GENERIC_SUB_TAGS - VI_TAGS - EN_TAGS)
    score += overlap * 8

    if preferred_lang:
        if lang == preferred_lang:
            score += 35
        elif lang in ("en", "vi") and lang != preferred_lang:
            score -= 15

    if stokens & GENERIC_SUB_TAGS:
        score += 4

    if overlap == 0 and vnorm not in snorm and snorm != vnorm:
        score -= 20

    return score, lang


def find_subtitles_for_video(video_path: str, search_paths):
    if not video_path:
        return None, None

    candidates = []
    for p in search_paths:
        if os.path.isfile(p) and os.path.splitext(p)[1].lower() in SUB_EXTS:
            candidates.append(p)

    if not candidates:
        return None, None

    scored_en = []
    scored_vi = []
    scored_any = []

    for sub in candidates:
        s_en, lang_en = subtitle_match_score(video_path, sub, "en")
        s_vi, lang_vi = subtitle_match_score(video_path, sub, "vi")
        s_any, lang_any = subtitle_match_score(video_path, sub, None)

        scored_en.append((s_en, sub, lang_en))
        scored_vi.append((s_vi, sub, lang_vi))
        scored_any.append((s_any, sub, lang_any))

    scored_en.sort(key=lambda x: x[0], reverse=True)
    scored_vi.sort(key=lambda x: x[0], reverse=True)
    scored_any.sort(key=lambda x: x[0], reverse=True)

    en_path = scored_en[0][1] if scored_en and scored_en[0][0] > 30 else None
    vi_path = scored_vi[0][1] if scored_vi and scored_vi[0][0] > 30 else None

    if en_path and vi_path and os.path.abspath(en_path) == os.path.abspath(vi_path):
        for score, sub, _lang in scored_vi[1:]:
            if score > 25 and os.path.abspath(sub) != os.path.abspath(en_path):
                vi_path = sub
                break

    if not en_path and scored_any and scored_any[0][0] > 40:
        en_path = scored_any[0][1]

    return en_path, vi_path


# ============================================================
# Session Resume / Playback State Persistence
# ============================================================

class StateManager:
    """Reusable state manager for media player session persistence.

    Storage layout (inside runtime folder):
    - prajna_config.json
    - state_recent.json
    - state_<sha1_12>.json

    Notes:
    - Uses atomic JSON write (.tmp + os.replace) to avoid corrupted files.
    - Gracefully handles missing/corrupt JSON files.
    - Includes migration logic for old state files in the base directory.
    """

    DEFAULT_CONFIG = {
        "autosave_interval_ms": 30000,
        "recursive_scan": True,
        "version": 1,
    }

    def __init__(self, base_dir=None, runtime_folder='config_state'):
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        self.runtime_dir = (self.base_dir / runtime_folder).resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.config_path = self.runtime_dir / 'prajna_config.json'
        self.recent_path = self.runtime_dir / 'state_recent.json'

        self.migrate_legacy_files()

    def _atomic_write_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + '.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp_path, path)

    def _read_json_safe(self, path: Path, default=None):
        if not path.exists() or not path.is_file():
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default

    def migrate_legacy_files(self):
        """Move legacy state/config files from base_dir into config_state/."""
        legacy_candidates = [
            self.base_dir / 'prajna_config.json',
            self.base_dir / 'state_recent.json',
        ]
        legacy_candidates.extend(sorted(self.base_dir.glob('state_*.json')))
        for old_path in legacy_candidates:
            try:
                if not old_path.exists() or not old_path.is_file():
                    continue
                new_path = self.runtime_dir / old_path.name
                if new_path.exists():
                    continue
                os.replace(str(old_path), str(new_path))
            except Exception:
                continue

    @staticmethod
    def _clamp_int(value, default, min_value=None, max_value=None):
        try:
            out = int(value)
        except Exception:
            out = int(default)
        if min_value is not None:
            out = max(int(min_value), out)
        if max_value is not None:
            out = min(int(max_value), out)
        return out

    def load_config(self):
        cfg = self._read_json_safe(self.config_path, default={})
        if not isinstance(cfg, dict):
            cfg = {}
        merged = dict(self.DEFAULT_CONFIG)
        merged.update(cfg)
        merged['autosave_interval_ms'] = self._clamp_int(merged.get('autosave_interval_ms', 30000), 30000, 1000, 3600000)
        merged['recursive_scan'] = bool(merged.get('recursive_scan', True))
        merged['version'] = self._clamp_int(merged.get('version', 1), 1, 1)
        try:
            self.save_config(merged)
        except Exception:
            pass
        return merged

    def save_config(self, cfg: dict):
        out = dict(self.DEFAULT_CONFIG)
        if isinstance(cfg, dict):
            out.update(cfg)
        out['autosave_interval_ms'] = self._clamp_int(out.get('autosave_interval_ms', 30000), 30000, 1000, 3600000)
        out['recursive_scan'] = bool(out.get('recursive_scan', True))
        out['version'] = self._clamp_int(out.get('version', 1), 1, 1)
        self._atomic_write_json(self.config_path, out)
        return out

    def _normalize_folder(self, folder: str) -> str:
        return str(Path(folder).expanduser().resolve())

    def folder_hash12(self, folder: str) -> str:
        norm = self._normalize_folder(folder)
        return hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]

    def state_path_for_folder(self, folder: str) -> Path:
        return self.runtime_dir / f"state_{self.folder_hash12(folder)}.json"

    def load_recent_folder(self):
        data = self._read_json_safe(self.recent_path, default={})
        if not isinstance(data, dict):
            return None
        folder = data.get('folder')
        if not isinstance(folder, str) or not folder.strip():
            return None
        return folder

    def save_recent_folder(self, folder: str):
        if not folder:
            return
        try:
            folder = self._normalize_folder(folder)
        except Exception:
            return
        payload = {'folder': folder, 'saved_at': float(time.time())}
        self._atomic_write_json(self.recent_path, payload)

    def default_playback_state(self, folder: str):
        return {
            'folder': self._normalize_folder(folder) if folder else '',
            'index': 0,
            'volume': 100,
            'song': '',
            'position': 0,
            'saved_at': 0.0,
        }

    def _sanitize_playback_state(self, folder: str, data):
        base = self.default_playback_state(folder)
        if not isinstance(data, dict):
            return base
        out = dict(base)
        out['folder'] = self._normalize_folder(data.get('folder', folder) or folder) if folder else str(data.get('folder') or '')
        out['index'] = self._clamp_int(data.get('index', 0), 0, 0)
        out['volume'] = self._clamp_int(data.get('volume', 100), 100, 0, 200)
        song = data.get('song', '')
        out['song'] = song if isinstance(song, str) else ''
        out['position'] = self._clamp_int(data.get('position', 0), 0, 0)
        try:
            out['saved_at'] = float(data.get('saved_at', 0.0))
            if out['saved_at'] < 0:
                out['saved_at'] = 0.0
        except Exception:
            out['saved_at'] = 0.0
        return out

    def load_playback_state(self, folder: str):
        if not folder:
            return self.default_playback_state('')
        path = self.state_path_for_folder(folder)
        data = self._read_json_safe(path, default={})
        return self._sanitize_playback_state(folder, data)

    def save_playback_state(self, folder: str, index: int, volume: int, song: str, position: int):
        if not folder:
            return None
        folder_norm = self._normalize_folder(folder)
        payload = self._sanitize_playback_state(folder_norm, {
            'folder': folder_norm,
            'index': index,
            'volume': volume,
            'song': song or '',
            'position': position,
            'saved_at': float(time.time()),
        })
        path = self.state_path_for_folder(folder_norm)
        self._atomic_write_json(path, payload)
        self.save_recent_folder(folder_norm)
        return payload


# ============================================================
# Main App
# ============================================================

class DualSubtitleVLCPlayer:
    def __init__(self, root):
        self.root = root
        self.root.title("PrajnaPlayer Dual Subtitle")
        self.root.geometry("1320x860")
        self.root.minsize(1080, 700)

        # Session state persistence (config_state/)
        try:
            base_dir = Path(__file__).resolve().parent
        except Exception:
            base_dir = Path.cwd()
        self.state_manager = StateManager(base_dir=base_dir, runtime_folder="config_state")
        self.app_config = self.state_manager.load_config()
        self.autosave_interval_ms = int(self.app_config.get("autosave_interval_ms", 30000))
        self._autosave_job = None
        self._resume_seek_job = None
        self._resume_pending_ms = 0
        self._restoring_session = False

        # ---- UI init guard (IMPORTANT for callback race during widget construction) ----
        self._ui_ready = False

        # VLC
        self.vlc_instance = vlc.Instance()
        self.player = self.vlc_instance.media_player_new()

        # Playlist / media state
        self.current_video_path = None
        self.current_video_index = -1
        self.video_files = []
        self.sub_files = []
        self.scanned_root_folder = None

        # Subtitle paths + parsed cues
        self.en_sub_path = None
        self.vi_sub_path = None
        self.en_cues, self.en_starts = [], []
        self.vi_cues, self.vi_starts = [], []

        # Subtitle delay (ms)
        self.en_delay_ms = 0
        self.vi_delay_ms = 0

        # Playback UI state
        self.is_paused = False
        self.is_seeking = False
        self.last_length_ms = 0

        # UI visibility / focus mode
        self.focus_play_only = False
        self._left_pane_hidden = False
        self._aux_controls_hidden = False
        self._sub_panel_hidden = False

        # Subtitle style vars
        self.font_family_en = tk.StringVar(value="Segoe UI")
        self.font_family_vi = tk.StringVar(value="Segoe UI")
        self.font_size_en = tk.IntVar(value=18)
        self.font_size_vi = tk.IntVar(value=17)
        self.wrap_margin_px = tk.IntVar(value=24)

        # Subtitle Y positions (use IntVar so callbacks can read vars before both scales exist)
        self.en_y_pct = tk.IntVar(value=32)
        self.vi_y_pct = tk.IntVar(value=72)

        # Speed
        self.speed_var = tk.DoubleVar(value=1.0)

        # Scan option
        self.recursive_scan = tk.BooleanVar(value=bool(self.app_config.get("recursive_scan", True)))

        # Fonts
        self.font_en = tkfont.Font(family=self.font_family_en.get(), size=self.font_size_en.get(), weight="bold")
        self.font_vi = tkfont.Font(family=self.font_family_vi.get(), size=self.font_size_vi.get(), weight="bold")

        # Build UI / events
        self._build_ui()
        self._bind_events()

        # Start timer loops after UI is ready
        self._subtitle_tick()
        self._start_autosave_loop()

        # Restore previous session (if any) after UI settles
        self.root.after(250, self.restore_last_session)

    # --------------------------------------------------------
    # UI build
    # --------------------------------------------------------
    def _build_ui(self):
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=0)
        self.root.columnconfigure(0, weight=1)
        self._setup_footer_styles()

        self.main_pane = ttk.Panedwindow(self.root, orient="horizontal")
        self.main_pane.grid(row=0, column=0, sticky="nsew")

        # ===== Left pane =====
        self.left_frame = ttk.Frame(self.main_pane, padding=8)
        self.left_frame.columnconfigure(0, weight=1)
        self.left_frame.rowconfigure(2, weight=1)
        self.main_pane.add(self.left_frame, weight=1)

        ttk.Label(self.left_frame, text="Library / Playlist").grid(row=0, column=0, sticky="w")

        top_scan_row = ttk.Frame(self.left_frame)
        top_scan_row.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        top_scan_row.columnconfigure(0, weight=1)

        self.entry_folder_var = tk.StringVar(value="")
        self.entry_folder = ttk.Entry(top_scan_row, textvariable=self.entry_folder_var)
        self.entry_folder.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_scan_folder = ttk.Button(top_scan_row, text="Scan Folder", command=self.scan_folder_dialog)
        self.btn_scan_folder.grid(row=0, column=1, padx=(0, 6))

        self.chk_recursive = ttk.Checkbutton(top_scan_row, text="Recursive", variable=self.recursive_scan)
        self.chk_recursive.grid(row=0, column=2)

        list_frame = ttk.Frame(self.left_frame)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.playlist_listbox = tk.Listbox(list_frame, activestyle="dotbox", selectmode="browse")
        self.playlist_listbox.grid(row=0, column=0, sticky="nsew")

        self.playlist_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.playlist_listbox.yview)
        self.playlist_scroll.grid(row=0, column=1, sticky="ns")
        self.playlist_listbox.configure(yscrollcommand=self.playlist_scroll.set)

        playlist_btn_row = ttk.Frame(self.left_frame)
        playlist_btn_row.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        for i in range(4):
            playlist_btn_row.columnconfigure(i, weight=1)

        ttk.Button(playlist_btn_row, text="Open Video", command=self.open_video_dialog).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(playlist_btn_row, text="Prev", command=self.play_prev_video).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(playlist_btn_row, text="Next", command=self.play_next_video).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(playlist_btn_row, text="Refresh Match", command=self.refresh_auto_sub_match).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        # ===== Right pane =====
        self.right_frame = ttk.Frame(self.main_pane, padding=8)
        self.right_frame.columnconfigure(0, weight=1)
        self.right_frame.rowconfigure(0, weight=1)
        self.main_pane.add(self.right_frame, weight=4)

        # Player container
        self.player_container = ttk.Frame(self.right_frame)
        self.player_container.grid(row=0, column=0, sticky="nsew")
        self.player_container.columnconfigure(0, weight=1)
        self.player_container.rowconfigure(0, weight=1)

        # VLC video frame
        self.video_frame = tk.Frame(self.player_container, bg="black", height=520)
        self.video_frame.grid(row=0, column=0, sticky="nsew")

        # Subtitle panel
        self.sub_frame = tk.Frame(self.player_container, bg="#101010", height=120)
        self.sub_frame.grid(row=1, column=0, sticky="ew")
        self.sub_frame.grid_propagate(False)

        self.sub_canvas = tk.Canvas(self.sub_frame, bg="#101010", highlightthickness=0, bd=0)
        self.sub_canvas.pack(fill="both", expand=True)

        # Subtitle shadow/main items
        self.en_shadows = []
        self.vi_shadows = []
        self.shadow_offsets = [(-2, 0), (2, 0), (0, -2), (0, 2), (2, 2)]

        for _ in self.shadow_offsets:
            self.en_shadows.append(self.sub_canvas.create_text(
                0, 0, text="", fill="black", font=self.font_en,
                anchor="center", justify="center", width=1000
            ))
        self.en_main = self.sub_canvas.create_text(
            0, 0, text="", fill="#FFD84D", font=self.font_en,
            anchor="center", justify="center", width=1000
        )

        for _ in self.shadow_offsets:
            self.vi_shadows.append(self.sub_canvas.create_text(
                0, 0, text="", fill="black", font=self.font_vi,
                anchor="center", justify="center", width=1000
            ))
        self.vi_main = self.sub_canvas.create_text(
            0, 0, text="", fill="#9AF59A", font=self.font_vi,
            anchor="center", justify="center", width=1000
        )

        # Seek bar
        self.seek_row = ttk.Frame(self.right_frame)
        self.seek_row.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        self.seek_row.columnconfigure(1, weight=1)

        self.current_time_label = ttk.Label(self.seek_row, text="00:00")
        self.current_time_label.grid(row=0, column=0, sticky="w", padx=(0, 6))

        self.seek_scale = ttk.Scale(self.seek_row, from_=0, to=1000, orient="horizontal")
        self.seek_scale.grid(row=0, column=1, sticky="ew")

        self.total_time_label = ttk.Label(self.seek_row, text="00:00")
        self.total_time_label.grid(row=0, column=2, sticky="e", padx=(6, 0))

        # Playback buttons
        self.control_row = ttk.Frame(self.right_frame)
        self.control_row.grid(row=2, column=0, sticky="ew", pady=(0, 6))

        ttk.Button(self.control_row, text="Play", command=self.play).pack(side="left", padx=(0, 4))
        ttk.Button(self.control_row, text="Pause/Resume", command=self.pause_resume).pack(side="left", padx=4)
        ttk.Button(self.control_row, text="Stop", command=self.stop).pack(side="left", padx=4)
        ttk.Button(self.control_row, text="Open EN Sub", command=self.open_en_sub_dialog).pack(side="left", padx=(12, 4))
        ttk.Button(self.control_row, text="Open VI Sub", command=self.open_vi_sub_dialog).pack(side="left", padx=4)

        ttk.Label(self.control_row, text="Speed").pack(side="left", padx=(16, 4))
        self.speed_spin = ttk.Spinbox(self.control_row, from_=0.1, to=4.0, increment=0.1, width=6, textvariable=self.speed_var)
        self.speed_spin.pack(side="left", padx=4)
        ttk.Button(self.control_row, text="Apply", command=self.apply_speed).pack(side="left", padx=4)

        ttk.Separator(self.control_row, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(self.control_row, text="Focus Play Only (F11)", command=self.toggle_focus_play_only).pack(side="left", padx=4)
        ttk.Button(self.control_row, text="Toggle Panels", command=self.toggle_aux_controls).pack(side="left", padx=4)

        # Subtitle controls
        self.style_box = ttk.LabelFrame(self.right_frame, text="Subtitle UI Controls")
        self.style_box.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        for c in range(8):
            self.style_box.columnconfigure(c, weight=1)

        # EN row
        ttk.Label(self.style_box, text="EN Font").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        self.combo_en_font = ttk.Combobox(self.style_box, textvariable=self.font_family_en, values=self._safe_font_list(), width=20)
        self.combo_en_font.grid(row=0, column=1, sticky="ew", padx=4, pady=3)

        ttk.Label(self.style_box, text="EN Size").grid(row=0, column=2, sticky="w", padx=4, pady=3)
        self.spin_en_size = ttk.Spinbox(self.style_box, from_=8, to=72, increment=1, width=6, textvariable=self.font_size_en)
        self.spin_en_size.grid(row=0, column=3, sticky="w", padx=4, pady=3)

        ttk.Label(self.style_box, text="EN Y%").grid(row=0, column=4, sticky="w", padx=4, pady=3)
        # FIXED: bind variable=self.en_y_pct so callback can safely read vars even during init
        self.scale_en_y = ttk.Scale(
            self.style_box, from_=5, to=90, orient="horizontal",
            variable=self.en_y_pct,
            command=lambda _v=None: self._reposition_subtitles()
        )
        self.scale_en_y.grid(row=0, column=5, sticky="ew", padx=4, pady=3)

        self.lbl_en_delay = ttk.Label(self.style_box, text="EN Delay: 0 ms")
        self.lbl_en_delay.grid(row=0, column=6, sticky="w", padx=4, pady=3)
        ttk.Button(self.style_box, text="Reset EN", command=lambda: self.set_sub_delay("en", 0)).grid(row=0, column=7, sticky="ew", padx=4, pady=3)

        # VI row
        ttk.Label(self.style_box, text="VI Font").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        self.combo_vi_font = ttk.Combobox(self.style_box, textvariable=self.font_family_vi, values=self._safe_font_list(), width=20)
        self.combo_vi_font.grid(row=1, column=1, sticky="ew", padx=4, pady=3)

        ttk.Label(self.style_box, text="VI Size").grid(row=1, column=2, sticky="w", padx=4, pady=3)
        self.spin_vi_size = ttk.Spinbox(self.style_box, from_=8, to=72, increment=1, width=6, textvariable=self.font_size_vi)
        self.spin_vi_size.grid(row=1, column=3, sticky="w", padx=4, pady=3)

        ttk.Label(self.style_box, text="VI Y%").grid(row=1, column=4, sticky="w", padx=4, pady=3)
        # FIXED: bind variable=self.vi_y_pct
        self.scale_vi_y = ttk.Scale(
            self.style_box, from_=5, to=95, orient="horizontal",
            variable=self.vi_y_pct,
            command=lambda _v=None: self._reposition_subtitles()
        )
        self.scale_vi_y.grid(row=1, column=5, sticky="ew", padx=4, pady=3)

        self.lbl_vi_delay = ttk.Label(self.style_box, text="VI Delay: 0 ms")
        self.lbl_vi_delay.grid(row=1, column=6, sticky="w", padx=4, pady=3)
        ttk.Button(self.style_box, text="Reset VI", command=lambda: self.set_sub_delay("vi", 0)).grid(row=1, column=7, sticky="ew", padx=4, pady=3)

        # Wrap/apply row
        ttk.Label(self.style_box, text="Wrap Margin (px)").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        self.spin_wrap = ttk.Spinbox(self.style_box, from_=0, to=200, increment=2, width=6, textvariable=self.wrap_margin_px)
        self.spin_wrap.grid(row=2, column=1, sticky="w", padx=4, pady=3)

        ttk.Button(self.style_box, text="Apply UI Style", command=self.apply_subtitle_style).grid(row=2, column=2, columnspan=2, sticky="ew", padx=4, pady=3)
        ttk.Button(self.style_box, text="Nudge EN -100ms", command=lambda: self.adjust_sub_delay("en", -100)).grid(row=2, column=4, sticky="ew", padx=4, pady=3)
        ttk.Button(self.style_box, text="Nudge EN +100ms", command=lambda: self.adjust_sub_delay("en", +100)).grid(row=2, column=5, sticky="ew", padx=4, pady=3)
        ttk.Button(self.style_box, text="Nudge VI -100ms", command=lambda: self.adjust_sub_delay("vi", -100)).grid(row=2, column=6, sticky="ew", padx=4, pady=3)
        ttk.Button(self.style_box, text="Nudge VI +100ms", command=lambda: self.adjust_sub_delay("vi", +100)).grid(row=2, column=7, sticky="ew", padx=4, pady=3)

        # Current mapping info
        self.info_box = ttk.LabelFrame(self.right_frame, text="Current Media / Subtitle Mapping")
        self.info_box.grid(row=4, column=0, sticky="ew")
        self.info_box.columnconfigure(1, weight=1)

        ttk.Label(self.info_box, text="Video").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.video_info_var = tk.StringVar(value="-")
        ttk.Entry(self.info_box, textvariable=self.video_info_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(self.info_box, text="EN").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.en_info_var = tk.StringVar(value="-")
        ttk.Entry(self.info_box, textvariable=self.en_info_var, state="readonly").grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(self.info_box, text="VI").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        self.vi_info_var = tk.StringVar(value="-")
        ttk.Entry(self.info_box, textvariable=self.vi_info_var, state="readonly").grid(row=2, column=1, sticky="ew", padx=4, pady=2)

        ttk.Label(self.info_box, text="Status").grid(row=3, column=0, sticky="w", padx=4, pady=2)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Entry(self.info_box, textvariable=self.status_var, state="readonly").grid(row=3, column=1, sticky="ew", padx=4, pady=2)

        # ---- Bind scale values AFTER both scales exist (prevents early callback crash) ----
        self.scale_en_y.set(self.en_y_pct.get())
        self.scale_vi_y.set(self.vi_y_pct.get())

        self.footer_wrap = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        self.footer_wrap.grid(row=1, column=0, sticky="ew")
        self._build_footer_singleline(self.footer_wrap)

        # UI is now ready
        self._ui_ready = True
        self.root.update_idletasks()

        self._attach_video_handle()
        self._update_delay_labels()
        self.apply_subtitle_style()
        self._reposition_subtitles()

    def _setup_footer_styles(self):
        try:
            style = ttk.Style(self.root)
            style.configure("Muted.TLabel", foreground="#666666", font=("Segoe UI", 9))
            style.configure("Link.TLabel", foreground="#0a66c2", font=("Segoe UI", 9, "underline"))
            style.configure("Donate.TButton", font=("Segoe UI", 9, "bold"), padding=(8, 3))
        except Exception:
            pass

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

    def _safe_font_list(self):
        try:
            fonts = sorted(set(tkfont.families()))
            preferred = ["Segoe UI", "Arial", "Tahoma", "Calibri", "Verdana", "Times New Roman", "Courier New"]
            out = []
            for f in preferred + fonts:
                if f not in out:
                    out.append(f)
            return out
        except Exception:
            return ["Segoe UI", "Arial", "Tahoma", "Calibri"]

    # --------------------------------------------------------
    # Events
    # --------------------------------------------------------
    def _bind_events(self):
        self.root.bind("<space>", lambda e: self.pause_resume())
        self.root.bind("<Control-o>", lambda e: self.open_video_dialog())
        self.root.bind("<Control-f>", lambda e: self.scan_folder_dialog())

        self.root.bind("<Up>", lambda e: self.adjust_font_size_all(+1))
        self.root.bind("<Down>", lambda e: self.adjust_font_size_all(-1))

        self.root.bind("<Left>", lambda e: self.seek_relative_ms(-5000))
        self.root.bind("<Right>", lambda e: self.seek_relative_ms(+5000))
        self.root.bind("<F11>", lambda e: self.toggle_focus_play_only())
        self.root.bind("<Escape>", lambda e: self.exit_focus_play_only())

        # EN delay
        self.root.bind("<KeyPress-bracketleft>", lambda e: self.adjust_sub_delay("en", -100))
        self.root.bind("<KeyPress-bracketright>", lambda e: self.adjust_sub_delay("en", +100))
        # VI delay (Shift+[ ])
        self.root.bind("<KeyPress-braceleft>", lambda e: self.adjust_sub_delay("vi", -100))
        self.root.bind("<KeyPress-braceright>", lambda e: self.adjust_sub_delay("vi", +100))

        self.playlist_listbox.bind("<Double-Button-1>", self._on_playlist_double_click)
        self.playlist_listbox.bind("<Return>", self._on_playlist_enter)

        self.sub_canvas.bind("<Configure>", lambda e: self._reposition_subtitles())

        self.seek_scale.bind("<ButtonPress-1>", self._on_seek_press)
        self.seek_scale.bind("<ButtonRelease-1>", self._on_seek_release)

        self.combo_en_font.bind("<<ComboboxSelected>>", lambda e: self.apply_subtitle_style())
        self.combo_vi_font.bind("<<ComboboxSelected>>", lambda e: self.apply_subtitle_style())
        self.spin_en_size.bind("<Return>", lambda e: self.apply_subtitle_style())
        self.spin_vi_size.bind("<Return>", lambda e: self.apply_subtitle_style())
        self.spin_wrap.bind("<Return>", lambda e: self.apply_subtitle_style())
        self.speed_spin.bind("<Return>", lambda e: self.apply_speed())


    # --------------------------------------------------------
    # UI visibility / focus mode
    # --------------------------------------------------------
    def _show_left_pane(self):
        if not getattr(self, "_left_pane_hidden", False):
            return
        try:
            # Re-insert at the left side (index 0) to keep original layout order
            self.main_pane.insert(0, self.left_frame, weight=1)
        except Exception:
            try:
                self.main_pane.add(self.left_frame, weight=1)
            except Exception:
                pass
        self._left_pane_hidden = False

    def _hide_left_pane(self):
        if getattr(self, "_left_pane_hidden", False):
            return
        try:
            self.main_pane.forget(self.left_frame)
            self._left_pane_hidden = True
        except Exception:
            # Fallback: leave pane visible if forget is not available on platform/theme
            self._left_pane_hidden = False

    def _show_sub_panel(self):
        if not getattr(self, "_sub_panel_hidden", False):
            return
        try:
            self.sub_frame.grid()
            self._sub_panel_hidden = False
            self.player_container.update_idletasks()
            self._reposition_subtitles()
        except Exception:
            pass

    def _hide_sub_panel(self):
        if getattr(self, "_sub_panel_hidden", False):
            return
        try:
            self.sub_frame.grid_remove()
            self._sub_panel_hidden = True
            self.player_container.update_idletasks()
        except Exception:
            pass

    def _show_aux_control_rows(self):
        if not getattr(self, "_aux_controls_hidden", False):
            return
        for w in (getattr(self, "seek_row", None), getattr(self, "control_row", None),
                  getattr(self, "style_box", None), getattr(self, "info_box", None)):
            if w is not None:
                try:
                    w.grid()
                except Exception:
                    pass
        self._aux_controls_hidden = False
        try:
            self.right_frame.rowconfigure(0, weight=1)
        except Exception:
            pass

    def _hide_aux_control_rows(self, keep_control_row=False):
        targets = [getattr(self, "seek_row", None), getattr(self, "style_box", None), getattr(self, "info_box", None)]
        if not keep_control_row:
            targets.insert(1, getattr(self, "control_row", None))
        for w in targets:
            if w is not None:
                try:
                    w.grid_remove()
                except Exception:
                    pass
        self._aux_controls_hidden = not keep_control_row

    def toggle_aux_controls(self):
        if self.focus_play_only:
            # In focus mode, Toggle Panels is interpreted as restore all panels first
            self.exit_focus_play_only()
            return
        if getattr(self, "_aux_controls_hidden", False):
            self._show_aux_control_rows()
            self._set_status("Panels shown")
        else:
            # Keep control row visible so user can re-open panels by button
            self._hide_aux_control_rows(keep_control_row=True)
            self._set_status("Panels hidden (main play + control bar)")

    def enter_focus_play_only(self):
        if self.focus_play_only:
            return
        self.focus_play_only = True
        self._hide_left_pane()
        self._hide_aux_control_rows(keep_control_row=False)
        # Hide external subtitle panel too, so only the main video play area remains
        self._hide_sub_panel()
        try:
            self.right_frame.update_idletasks()
            self.player_container.update_idletasks()
        except Exception:
            pass
        self._attach_video_handle()
        self._reposition_subtitles()
        self._set_status("Focus Play Only mode ON (video only, F11/Esc to exit)")

    def exit_focus_play_only(self):
        if not self.focus_play_only:
            return
        self.focus_play_only = False
        self._show_left_pane()
        self._show_aux_control_rows()
        self._show_sub_panel()
        try:
            self.right_frame.update_idletasks()
            self.player_container.update_idletasks()
        except Exception:
            pass
        self._attach_video_handle()
        self._reposition_subtitles()
        self._set_status("Focus Play Only mode OFF")

    def toggle_focus_play_only(self):
        if self.focus_play_only:
            self.exit_focus_play_only()
        else:
            self.enter_focus_play_only()

    # --------------------------------------------------------
    # VLC embedding
    # --------------------------------------------------------
    def _attach_video_handle(self):
        if not self._ui_ready:
            return
        self.root.update_idletasks()
        wid = self.video_frame.winfo_id()
        try:
            if sys.platform.startswith("win"):
                self.player.set_hwnd(wid)
            elif sys.platform == "darwin":
                self.player.set_nsobject(wid)
            else:
                self.player.set_xwindow(wid)
        except Exception:
            pass

    def _disable_vlc_subtitles(self):
        try:
            self.player.video_set_spu(-1)
        except Exception:
            pass

    # --------------------------------------------------------
    # Helpers
    # --------------------------------------------------------
    def _set_status(self, text):
        if hasattr(self, "status_var"):
            self.status_var.set(text)

    def _format_ms(self, ms: int) -> str:
        if ms is None or ms < 0:
            return "00:00"
        sec = ms // 1000
        hh = sec // 3600
        mm = (sec % 3600) // 60
        ss = sec % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}" if hh > 0 else f"{mm:02d}:{ss:02d}"

    def _clear_subtitles(self):
        self._set_subtitle_text("", "")

    def _set_subtitle_text(self, en_text: str, vi_text: str):
        for item in self.en_shadows:
            self.sub_canvas.itemconfigure(item, text=en_text)
        self.sub_canvas.itemconfigure(self.en_main, text=en_text)

        for item in self.vi_shadows:
            self.sub_canvas.itemconfigure(item, text=vi_text)
        self.sub_canvas.itemconfigure(self.vi_main, text=vi_text)

    # --------------------------------------------------------
    # Subtitle panel positioning & style
    # --------------------------------------------------------
    def _reposition_subtitles(self):
        # Guards against early callbacks during UI construction
        if not getattr(self, "_ui_ready", False):
            return
        if not hasattr(self, "sub_canvas"):
            return
        if not hasattr(self, "en_main") or not hasattr(self, "vi_main"):
            return

        w = max(1, self.sub_canvas.winfo_width())
        h = max(1, self.sub_canvas.winfo_height())

        # FIXED: read from IntVar instead of widget.get() during init race
        en_pct = float(self.en_y_pct.get())
        vi_pct = float(self.vi_y_pct.get())

        center_x = w // 2
        en_y = int(h * (en_pct / 100.0))
        vi_y = int(h * (vi_pct / 100.0))

        wrap_margin = max(0, int(self.wrap_margin_px.get()))
        wrap_w = max(200, w - (wrap_margin * 2))

        for item in self.en_shadows + [self.en_main] + self.vi_shadows + [self.vi_main]:
            self.sub_canvas.itemconfigure(item, width=wrap_w)

        for item, (dx, dy) in zip(self.en_shadows, self.shadow_offsets):
            self.sub_canvas.coords(item, center_x + dx, en_y + dy)
        self.sub_canvas.coords(self.en_main, center_x, en_y)

        for item, (dx, dy) in zip(self.vi_shadows, self.shadow_offsets):
            self.sub_canvas.coords(item, center_x + dx, vi_y + dy)
        self.sub_canvas.coords(self.vi_main, center_x, vi_y)

    def apply_subtitle_style(self):
        if not getattr(self, "_ui_ready", False):
            return
        try:
            en_family = self.font_family_en.get().strip() or "Segoe UI"
            vi_family = self.font_family_vi.get().strip() or "Segoe UI"
            en_size = max(8, int(self.font_size_en.get()))
            vi_size = max(8, int(self.font_size_vi.get()))
            self.font_size_en.set(en_size)
            self.font_size_vi.set(vi_size)

            self.font_en.configure(family=en_family, size=en_size, weight="bold")
            self.font_vi.configure(family=vi_family, size=vi_size, weight="bold")

            for item in self.en_shadows + [self.en_main]:
                self.sub_canvas.itemconfigure(item, font=self.font_en)

            for item in self.vi_shadows + [self.vi_main]:
                self.sub_canvas.itemconfigure(item, font=self.font_vi)

            self._reposition_subtitles()
            self._set_status(f"Applied style | EN {en_family} {en_size}px | VI {vi_family} {vi_size}px")
        except Exception as e:
            self._set_status(f"Style apply error: {e}")

    def adjust_font_size_all(self, delta: int):
        try:
            self.font_size_en.set(max(8, int(self.font_size_en.get()) + delta))
            self.font_size_vi.set(max(8, int(self.font_size_vi.get()) + delta))
            self.apply_subtitle_style()
        except Exception:
            pass

    # --------------------------------------------------------
    # Subtitle delays
    # --------------------------------------------------------
    def _update_delay_labels(self):
        if hasattr(self, "lbl_en_delay"):
            self.lbl_en_delay.config(text=f"EN Delay: {self.en_delay_ms:+d} ms")
        if hasattr(self, "lbl_vi_delay"):
            self.lbl_vi_delay.config(text=f"VI Delay: {self.vi_delay_ms:+d} ms")

    def set_sub_delay(self, lang: str, value_ms: int):
        if lang == "en":
            self.en_delay_ms = int(value_ms)
        else:
            self.vi_delay_ms = int(value_ms)
        self._update_delay_labels()
        self._set_status(f"{lang.upper()} delay set to {value_ms:+d} ms")

    def adjust_sub_delay(self, lang: str, delta_ms: int):
        if lang == "en":
            self.en_delay_ms += int(delta_ms)
            value = self.en_delay_ms
        else:
            self.vi_delay_ms += int(delta_ms)
            value = self.vi_delay_ms
        self._update_delay_labels()
        self._set_status(f"{lang.upper()} delay = {value:+d} ms")

    # --------------------------------------------------------
    # Scan folder / playlist
    # --------------------------------------------------------
    def scan_folder_dialog(self):
        folder = filedialog.askdirectory(title="Select folder to scan")
        if folder:
            self.scan_folder(folder, recursive=self.recursive_scan.get())

    def scan_folder(self, folder: str, recursive: bool = True):
        if not os.path.isdir(folder):
            messagebox.showerror("Error", "Folder not found.")
            return

        self.scanned_root_folder = folder
        self.entry_folder_var.set(folder)

        video_files = []
        sub_files = []

        if recursive:
            for root_dir, _dirs, files in os.walk(folder):
                for name in files:
                    ext = os.path.splitext(name)[1].lower()
                    full = os.path.join(root_dir, name)
                    if ext in VIDEO_EXTS:
                        video_files.append(full)
                    elif ext in SUB_EXTS:
                        sub_files.append(full)
        else:
            for name in os.listdir(folder):
                full = os.path.join(folder, name)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in VIDEO_EXTS:
                    video_files.append(full)
                elif ext in SUB_EXTS:
                    sub_files.append(full)

        video_files.sort(key=lambda p: p.lower())
        sub_files.sort(key=lambda p: p.lower())

        self.video_files = video_files
        self.sub_files = sub_files

        self.playlist_listbox.delete(0, tk.END)
        for p in self.video_files:
            display = os.path.relpath(p, folder) if folder else os.path.basename(p)
            self.playlist_listbox.insert(tk.END, display)

        self._set_status(f"Scanned: {len(video_files)} videos | {len(sub_files)} subtitles | recursive={recursive}")

        try:
            self.state_manager.save_recent_folder(folder)
        except Exception:
            pass

        if self.video_files:
            self.current_video_index = 0
            self._select_playlist_index(0)
        else:
            self.current_video_index = -1

    def _select_playlist_index(self, idx: int):
        self.playlist_listbox.selection_clear(0, tk.END)
        self.playlist_listbox.selection_set(idx)
        self.playlist_listbox.activate(idx)
        self.playlist_listbox.see(idx)

    def _on_playlist_double_click(self, _event=None):
        self.load_selected_playlist_video(auto_play=True)

    def _on_playlist_enter(self, _event=None):
        self.load_selected_playlist_video(auto_play=True)

    def load_selected_playlist_video(self, auto_play=False):
        sel = self.playlist_listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.video_files):
            self.current_video_index = idx
            self.load_video(self.video_files[idx], auto_match_subs=True)
            if auto_play:
                self.play()

    def play_prev_video(self):
        if not self.video_files:
            return
        if self.current_video_index <= 0:
            self.current_video_index = 0
        else:
            self.current_video_index -= 1
        self._select_playlist_index(self.current_video_index)
        self.load_video(self.video_files[self.current_video_index], auto_match_subs=True)
        self.play()

    def play_next_video(self):
        if not self.video_files:
            return
        if self.current_video_index < 0:
            self.current_video_index = 0
        elif self.current_video_index >= len(self.video_files) - 1:
            self.current_video_index = len(self.video_files) - 1
        else:
            self.current_video_index += 1
        self._select_playlist_index(self.current_video_index)
        self.load_video(self.video_files[self.current_video_index], auto_match_subs=True)
        self.play()

    # --------------------------------------------------------
    # Open / load media and subtitles
    # --------------------------------------------------------
    def open_video_dialog(self):
        path = filedialog.askopenfilename(
            title="Open video",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v *.ts"), ("All files", "*.*")]
        )
        if not path:
            return

        if path in self.video_files:
            self.current_video_index = self.video_files.index(path)
            self._select_playlist_index(self.current_video_index)
        else:
            self.current_video_index = -1

        self.load_video(path, auto_match_subs=True)

    def load_video(self, path: str, auto_match_subs=True):
        if not os.path.isfile(path):
            messagebox.showerror("Error", "Video file not found.")
            return

        self.current_video_path = path
        self.video_info_var.set(path)
        self.current_time_label.config(text="00:00")
        self.total_time_label.config(text="00:00")
        self.last_length_ms = 0
        self._clear_subtitles()

        media = self.vlc_instance.media_new(path)
        self.player.set_media(media)
        self._attach_video_handle()

        # Reset subs
        self.en_sub_path = None
        self.vi_sub_path = None
        self.en_cues, self.en_starts = [], []
        self.vi_cues, self.vi_starts = [], []

        if auto_match_subs:
            search_pool = list(self.sub_files)

            local_dir = os.path.dirname(path)
            if os.path.isdir(local_dir):
                for name in os.listdir(local_dir):
                    full = os.path.join(local_dir, name)
                    if os.path.isfile(full) and os.path.splitext(name)[1].lower() in SUB_EXTS and full not in search_pool:
                        search_pool.append(full)

            auto_en, auto_vi = find_subtitles_for_video(path, search_pool)
            if auto_en:
                self.load_subtitle("en", auto_en)
            if auto_vi:
                self.load_subtitle("vi", auto_vi)

        self._refresh_sub_info()
        self._set_status(f"Loaded video: {os.path.basename(path)}")

    def refresh_auto_sub_match(self):
        if self.current_video_path:
            self.load_video(self.current_video_path, auto_match_subs=True)
            self._set_status("Subtitle auto-match refreshed")

    def load_subtitle(self, lang: str, path: str):
        cues, starts = load_subtitle_file(path)
        if lang == "en":
            self.en_sub_path = path
            self.en_cues, self.en_starts = cues, starts
        else:
            self.vi_sub_path = path
            self.vi_cues, self.vi_starts = cues, starts

        self._refresh_sub_info()
        self._set_status(f"Loaded {lang.upper()} subtitle: {os.path.basename(path)} ({len(cues)} cues)")

    def _refresh_sub_info(self):
        self.en_info_var.set(self.en_sub_path if self.en_sub_path else "-")
        self.vi_info_var.set(self.vi_sub_path if self.vi_sub_path else "-")

    def open_en_sub_dialog(self):
        path = filedialog.askopenfilename(
            title="Open English subtitle",
            filetypes=[("Subtitle files", "*.srt *.vtt"), ("All files", "*.*")]
        )
        if path:
            self.load_subtitle("en", path)

    def open_vi_sub_dialog(self):
        path = filedialog.askopenfilename(
            title="Open Vietnamese subtitle",
            filetypes=[("Subtitle files", "*.srt *.vtt"), ("All files", "*.*")]
        )
        if path:
            self.load_subtitle("vi", path)

    # --------------------------------------------------------
    # Playback controls
    # --------------------------------------------------------
    def play(self):
        if not self.current_video_path:
            self.open_video_dialog()
            if not self.current_video_path:
                return

        self._attach_video_handle()
        result = self.player.play()
        if result == -1:
            messagebox.showerror("Playback Error", "Cannot play this media.")
            return

        self.root.after(200, self._disable_vlc_subtitles)
        self.root.after(800, self._disable_vlc_subtitles)

        self.is_paused = False
        self.apply_speed()

        # Save state immediately when a track starts playing
        self.save_state_now(reason="track_start")
        self._set_status("Playing")

    def pause_resume(self):
        try:
            self.player.pause()
            self.is_paused = not self.is_paused
            self._set_status("Paused" if self.is_paused else "Playing")
        except Exception:
            pass

    def stop(self):
        try:
            self.player.stop()
            self.is_paused = False
            self._clear_subtitles()
            self.current_time_label.config(text="00:00")
            self._set_status("Stopped")
        except Exception:
            pass

    def apply_speed(self):
        try:
            rate = float(self.speed_var.get())
        except Exception:
            messagebox.showwarning("Invalid speed", "Please enter a valid speed (e.g., 0.8, 1.0, 1.5).")
            return

        if rate < 0.1:
            rate = 0.1
            self.speed_var.set(rate)

        try:
            ok = self.player.set_rate(rate)
            if ok == -1:
                self._set_status(f"Speed may not be supported for this media/codec (requested {rate:.1f}x)")
            else:
                self._set_status(f"Playback speed set to {rate:.1f}x")
        except Exception:
            self._set_status(f"Failed to set speed to {rate:.1f}x")

    # --------------------------------------------------------
    # Seek bar
    # --------------------------------------------------------
    def _on_seek_press(self, _event=None):
        self.is_seeking = True

    def _on_seek_release(self, _event=None):
        try:
            if self.last_length_ms > 0:
                pos = float(self.seek_scale.get()) / 1000.0
                new_time = int(self.last_length_ms * pos)
                self.player.set_time(new_time)
        except Exception:
            pass
        finally:
            self.is_seeking = False

    def seek_relative_ms(self, delta_ms: int):
        try:
            cur = self.player.get_time()
            if cur is None or cur < 0:
                return
            target = max(0, cur + int(delta_ms))
            length = self.player.get_length()
            if length and length > 0:
                target = min(target, length - 100)
            self.player.set_time(target)
        except Exception:
            pass

    def _update_seek_ui(self):
        try:
            cur = self.player.get_time()
            length = self.player.get_length()

            if length and length > 0:
                self.last_length_ms = length

            self.current_time_label.config(text=self._format_ms(cur))
            total_display = self.last_length_ms if self.last_length_ms > 0 else (length or 0)
            self.total_time_label.config(text=self._format_ms(total_display))

            if not self.is_seeking and self.last_length_ms > 0 and cur is not None and cur >= 0:
                pos = (cur / self.last_length_ms) * 1000.0
                self.seek_scale.set(max(0.0, min(1000.0, pos)))
        except Exception:
            pass

    # --------------------------------------------------------
    # Subtitle sync loop
    # --------------------------------------------------------
    def _subtitle_tick(self):
        try:
            state = self.player.get_state()
            if state in (vlc.State.Playing, vlc.State.Paused):
                current_ms = self.player.get_time()

                # Positive delay => subtitle later => query current - delay
                en_query_ms = current_ms - self.en_delay_ms
                vi_query_ms = current_ms - self.vi_delay_ms

                en_text = find_active_cue(self.en_cues, self.en_starts, en_query_ms)
                vi_text = find_active_cue(self.vi_cues, self.vi_starts, vi_query_ms)
                self._set_subtitle_text(en_text, vi_text)
            else:
                if state in (vlc.State.Stopped, vlc.State.Ended, vlc.State.NothingSpecial, vlc.State.Error):
                    self._clear_subtitles()
        except Exception:
            pass

        self._update_seek_ui()
        self.root.after(80, self._subtitle_tick)

    # --------------------------------------------------------
    # Session state persistence integration
    # --------------------------------------------------------
    def _get_safe_current_volume(self) -> int:
        """Read VLC volume and clamp to valid range. Fallback to 100 if unavailable."""
        try:
            vol = self.player.audio_get_volume()
            if vol is None or vol < 0:
                return 100
            return max(0, min(200, int(vol)))
        except Exception:
            return 100

    def _get_safe_current_position_ms(self) -> int:
        """Read VLC playback time (ms), clamp negatives to 0."""
        try:
            pos = self.player.get_time()
            if pos is None:
                return 0
            return max(0, int(pos))
        except Exception:
            return 0

    def _resolve_current_folder_for_state(self):
        """Prefer scanned root folder; fall back to current video directory."""
        folder = self.scanned_root_folder
        if folder and os.path.isdir(folder):
            return folder
        if self.current_video_path:
            d = os.path.dirname(self.current_video_path)
            if d and os.path.isdir(d):
                return d
        return None

    def save_state_now(self, reason: str = "manual"):
        """Save playback state immediately using StateManager. Fails gracefully."""
        try:
            folder = self._resolve_current_folder_for_state()
            if not folder:
                return

            self.app_config["autosave_interval_ms"] = int(self.autosave_interval_ms)
            self.app_config["recursive_scan"] = bool(self.recursive_scan.get())
            self.state_manager.save_config(self.app_config)

            idx = int(self.current_video_index) if self.current_video_index is not None else 0
            vol = self._get_safe_current_volume()
            song = self.current_video_path or ""
            pos = self._get_safe_current_position_ms()

            self.state_manager.save_playback_state(
                folder=folder,
                index=idx,
                volume=vol,
                song=song,
                position=pos,
            )
        except Exception as e:
            try:
                self._set_status(f"State save skipped ({reason}): {e}")
            except Exception:
                pass

    def _start_autosave_loop(self):
        """Start periodic autosave loop using configured interval."""
        try:
            if self._autosave_job is not None:
                self.root.after_cancel(self._autosave_job)
        except Exception:
            pass
        self._autosave_job = self.root.after(max(1000, int(self.autosave_interval_ms)), self._autosave_tick)

    def _autosave_tick(self):
        self._autosave_job = None
        self.save_state_now(reason="autosave")
        self._start_autosave_loop()

    def _schedule_delayed_resume_seek(self, position_ms: int, delay_ms: int = 800):
        """Schedule delayed seek because some backends need warm-up before seeking."""
        try:
            if self._resume_seek_job is not None:
                self.root.after_cancel(self._resume_seek_job)
        except Exception:
            pass
        self._resume_pending_ms = max(0, int(position_ms))
        self._resume_seek_job = self.root.after(max(100, int(delay_ms)), self._apply_delayed_resume_seek)

    def _apply_delayed_resume_seek(self):
        self._resume_seek_job = None
        try:
            target = max(0, int(self._resume_pending_ms))
            if target > 0:
                self.player.set_time(target)
                self.root.after(500, lambda: self.player.set_time(target))
        except Exception:
            pass

    def restore_last_session(self):
        """Restore the most recent folder, track, volume, and playback position."""
        if getattr(self, "_restoring_session", False):
            return

        self._restoring_session = True
        try:
            recent_folder = self.state_manager.load_recent_folder()
            if not recent_folder:
                self._set_status("Ready (no previous session)")
                return

            try:
                recent_folder = str(Path(recent_folder).expanduser().resolve())
            except Exception:
                pass

            if not os.path.isdir(recent_folder):
                self._set_status("Previous media folder not found; skipping session restore")
                return

            self.scan_folder(recent_folder, recursive=bool(self.recursive_scan.get()))
            if not self.video_files:
                self._set_status("Session restore skipped (no videos in previous folder)")
                return

            state = self.state_manager.load_playback_state(recent_folder)
            restore_volume = max(0, min(200, int(state.get("volume", 100))))
            restore_index = max(0, int(state.get("index", 0)))
            restore_position = max(0, int(state.get("position", 0)))
            restore_song = state.get("song") if isinstance(state.get("song"), str) else ""

            target_idx = None
            if restore_song:
                song_abs = os.path.abspath(restore_song)
                for i, p in enumerate(self.video_files):
                    if os.path.abspath(p) == song_abs:
                        target_idx = i
                        break

            if target_idx is None:
                target_idx = min(max(restore_index, 0), len(self.video_files) - 1)

            if not (0 <= target_idx < len(self.video_files)):
                self._set_status("Session restore skipped (invalid saved index)")
                return

            self.current_video_index = target_idx
            self._select_playlist_index(target_idx)
            self.load_video(self.video_files[target_idx], auto_match_subs=True)

            try:
                self.player.audio_set_volume(restore_volume)
            except Exception:
                pass

            self.play()  # also triggers immediate track_start state save
            try:
                self.player.audio_set_volume(restore_volume)
                self.root.after(400, lambda v=restore_volume: self.player.audio_set_volume(v))
            except Exception:
                pass

            if restore_position > 0:
                self._schedule_delayed_resume_seek(restore_position, delay_ms=900)

            self._set_status(f"Session restored: {os.path.basename(self.video_files[target_idx])}")
        except Exception as e:
            self._set_status(f"Session restore skipped: {e}")
        finally:
            self._restoring_session = False

    def on_close(self):
        """Graceful app shutdown: save state/config, cancel timers, then close."""
        try:
            self.app_config["recursive_scan"] = bool(self.recursive_scan.get())
            self.app_config["autosave_interval_ms"] = int(self.autosave_interval_ms)
            self.state_manager.save_config(self.app_config)
        except Exception:
            pass

        try:
            self.save_state_now(reason="on_close")
        except Exception:
            pass

        for job_attr in ("_autosave_job", "_resume_seek_job"):
            try:
                job = getattr(self, job_attr, None)
                if job is not None:
                    self.root.after_cancel(job)
                    setattr(self, job_attr, None)
            except Exception:
                pass

        try:
            self.player.stop()
        except Exception:
            pass
        self.root.destroy()

    def play_track(self, index: int):
        """Public example method requested by spec (alias to internal playlist play)."""
        self._play_index(index)

    # --------------------------------------------------------
    # Utility method (optional)
    # --------------------------------------------------------
    def _play_index(self, idx: int):
        if 0 <= idx < len(self.video_files):
            self.current_video_index = idx
            self._select_playlist_index(idx)
            self.load_video(self.video_files[idx], auto_match_subs=True)
            self.play()


# ============================================================
# Main
# ============================================================

def main():
    root = tk.Tk()
    apply_app_identity_and_icons(root, app_id="com.nghiencuuthuoc.PrajnaPlayer.v3", script_file=__file__)
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names() and sys.platform.startswith("win"):
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    app = DualSubtitleVLCPlayer(root)
    apply_app_identity_and_icons(root, app_id="com.nghiencuuthuoc.PrajnaPlayer.v3", script_file=__file__)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()