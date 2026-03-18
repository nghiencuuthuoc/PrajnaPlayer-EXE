# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

ICON_RELATIVE_SEARCH_ORDER = ['../', './', '../../', '../../../', '../../../../']

def _bundle_dir() -> Path:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

def _script_dir(script_file: str | None = None) -> Path:
    if script_file:
        try:
            return Path(script_file).resolve().parent
        except Exception:
            pass
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _unique_paths(paths):
    seen = set(); out = []
    for p in paths:
        try:
            key = str(Path(p).resolve())
        except Exception:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(Path(p))
    return out

def candidate_icon_paths(filename: str, script_file: str | None = None):
    script_dir = _script_dir(script_file)
    bundle_dir = _bundle_dir()
    runtime_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else script_dir
    candidates = [bundle_dir / filename, runtime_dir / filename]
    for rel in ICON_RELATIVE_SEARCH_ORDER:
        candidates.append((script_dir / rel / filename).resolve())
        candidates.append((runtime_dir / rel / filename).resolve())
    return _unique_paths(candidates)

def find_first_existing(paths):
    for p in paths:
        try:
            if Path(p).exists():
                return str(Path(p))
        except Exception:
            pass
    return None

def get_preferred_ico(script_file: str | None = None):
    return find_first_existing(candidate_icon_paths('nct_logo.ico', script_file))

def get_preferred_png(script_file: str | None = None):
    return find_first_existing(candidate_icon_paths('nct_logo.png', script_file))

def set_windows_app_user_model_id(app_id: str):
    if os.name != 'nt':
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
        return True
    except Exception:
        return False

def _load_hicon_from_ico(ico_path: str, big: bool):
    try:
        return ctypes.windll.user32.LoadImageW(None, str(ico_path), 1, 64 if big else 32, 64 if big else 32, 0x00000010)
    except Exception:
        return None

def force_native_window_icons(root, ico_path: str | None):
    if os.name != 'nt' or not ico_path:
        return False
    try:
        root.update_idletasks()
        hwnd = root.winfo_id()
        small = _load_hicon_from_ico(ico_path, big=False)
        big = _load_hicon_from_ico(ico_path, big=True)
        if small: ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, small)
        if big: ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, big)
        return bool(small or big)
    except Exception:
        return False

def apply_tk_window_icon(root, script_file: str | None = None):
    png_path = get_preferred_png(script_file)
    ico_path = get_preferred_ico(script_file)
    try:
        if png_path and Image is not None and ImageTk is not None:
            img = Image.open(png_path)
            root._nct_iconphoto_ref = ImageTk.PhotoImage(img)
            root.iconphoto(True, root._nct_iconphoto_ref)
    except Exception:
        pass
    try:
        if os.name == 'nt' and ico_path:
            root.iconbitmap(default=ico_path)
    except Exception:
        pass
    try:
        root.after(80, lambda: force_native_window_icons(root, ico_path))
        root.after(350, lambda: force_native_window_icons(root, ico_path))
    except Exception:
        pass
    return {'png': png_path, 'ico': ico_path}

def apply_app_identity_and_icons(root, app_id: str, script_file: str | None = None):
    set_windows_app_user_model_id(app_id)
    return apply_tk_window_icon(root, script_file)
