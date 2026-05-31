import colorsys
import ctypes
import json
import os
import threading
import time
import tkinter as tk
import unicodedata
import wave
from tkinter import ttk, colorchooser, filedialog, messagebox

import numpy as np
from rapidocr_onnxruntime import RapidOCR
import sounddevice as sd
import torch
try:
    import espeakng_loader
    import glob as _gl
    _pkg = os.path.dirname(os.path.abspath(espeakng_loader.__file__))
    _hits = _gl.glob(os.path.join(_pkg, "**", "phontab"), recursive=True)
    if _hits:
        _data_dir = os.path.dirname(_hits[0])
        espeakng_loader.get_data_path = lambda: _data_dir
    espeakng_loader.make_library_available()
except Exception:
    pass
from kokoro import KPipeline
from PIL import Image, ImageDraw, ImageFont, ImageTk, ImageGrab
import keyboard

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SelectAndRead.App")
except Exception:
    pass

_SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".tts_reader.json")

VOICES = [
    # American English — Female
    ("af_heart",   "Heart (AF)"),
    ("af_sky",     "Sky (AF)"),
    ("af_bella",   "Bella (AF)"),
    ("af_nova",    "Nova (AF)"),
    ("af_river",   "River (AF)"),
    ("af_sarah",   "Sarah (AF)"),
    ("af_nicole",  "Nicole (AF)"),
    ("af_aoede",   "Aoede (AF)"),
    ("af_kore",    "Kore (AF)"),
    ("af_jessica", "Jessica (AF)"),
    # American English — Male
    ("am_michael", "Michael (AM)"),
    ("am_adam",    "Adam (AM)"),
    ("am_echo",    "Echo (AM)"),
    ("am_eric",    "Eric (AM)"),
    ("am_liam",    "Liam (AM)"),
    ("am_onyx",    "Onyx (AM)"),
    ("am_puck",    "Puck (AM)"),
    # British English — Female
    ("bf_emma",    "Emma (BF)"),
    ("bf_isabella","Isabella (BF)"),
    ("bf_alice",   "Alice (BF)"),
    ("bf_lily",    "Lily (BF)"),
    # British English — Male
    ("bm_george",  "George (BM)"),
    ("bm_lewis",   "Lewis (BM)"),
    ("bm_daniel",  "Daniel (BM)"),
]

_ocr_reader: RapidOCR | None = None
_tts_pipeline: KPipeline | None = None

# Where _download_models.py writes the PP-OCRv5 mobile EN ONNX files.
# Same model weights as PaddleOCR's PP-OCRv5 mobile EN — just exported to
# ONNX so they can run through onnxruntime instead of the 2+ GB PaddlePaddle
# stack. Identical OCR output, ~10× lighter install.
_OCR_MODEL_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "SelectAndRead", "onnx")
_OCR_DET_PATH = os.path.join(_OCR_MODEL_DIR, "ch_PP-OCRv5_det_mobile.onnx")
_OCR_REC_PATH = os.path.join(_OCR_MODEL_DIR, "en_PP-OCRv5_rec_mobile.onnx")
# Note: no cls model. We pass use_cls=False at call time. The PP-OCRv5 cls
# model has a different input shape ([3,80,160]) than rapidocr-onnxruntime's
# preprocessor expects ([3,48,192]), and orientation classification is
# pointless for screen text anyway (it's always upright).



def _ocr(img_array: np.ndarray) -> list[tuple[str, tuple]]:
    """Run RapidOCR with PP-OCRv5 mobile EN ONNX weights on an image array.
    Returns [(word, (x1, y1, x2, y2)), ...] in image pixel coords.

    RapidOCR returns line-level entries with 4-point polygons; we collapse
    each polygon to its axis-aligned bounding rect, then split the line
    into per-word boxes proportionally by character count. _tighten_x_bbox
    later snaps each word bbox to its actual ink columns.
    """
    global _ocr_reader
    if _ocr_reader is None:
        return []
    # RapidOCR.__call__ returns (result, elapse). result is either a list of
    # [polygon, text, score] entries, or None if nothing was detected.
    # use_cls=False skips the text-orientation classifier (screen text is
    # always upright; also avoids a shape mismatch with the v5 cls model).
    try:
        result, _elapse = _ocr_reader(img_array, use_cls=False)
    except Exception as exc:
        # ONNX Runtime CUDA inference can fail at runtime even when our
        # provider check passed at model-load time (driver / cuDNN / CUDA
        # ABI mismatches only surface on the first real GPU call). Rebuild
        # the engine on CPU and retry once so the user keeps working.
        import traceback
        traceback.print_exc()
        print(f"OCR inference failed ({exc.__class__.__name__}); "
              f"rebuilding on CPU and retrying.")
        try:
            _ocr_reader = RapidOCR(
                det_model_path=_OCR_DET_PATH,
                rec_model_path=_OCR_REC_PATH,
                det_use_cuda=False,
                rec_use_cuda=False,
            )
            result, _elapse = _ocr_reader(img_array, use_cls=False)
        except Exception:
            traceback.print_exc()
            return []
    if not result:
        return []

    out: list[tuple[str, tuple]] = []
    for entry in result:
        if entry is None:
            continue
        try:
            poly, line_text, _score = entry[0], entry[1], entry[2]
        except (TypeError, IndexError):
            continue
        if not line_text or not str(line_text).strip():
            continue
        try:
            pts = np.asarray(poly, dtype=float).reshape(-1, 2)
            x1, y1 = int(pts[:, 0].min()), int(pts[:, 1].min())
            x2, y2 = int(pts[:, 0].max()), int(pts[:, 1].max())
        except (TypeError, ValueError, IndexError):
            continue
        words = str(line_text).strip().split()
        if not words:
            continue
        if len(words) == 1:
            out.append((words[0], (x1, y1, x2, y2)))
            continue
        line_w = max(1, x2 - x1)
        total_chars = sum(len(w) for w in words) + (len(words) - 1)
        if total_chars <= 0:
            continue
        char_w = line_w / total_chars
        cursor = float(x1)
        for w in words:
            w_pixels = char_w * len(w)
            out.append((w, (int(cursor), y1,
                            int(cursor + w_pixels), y2)))
            cursor += w_pixels + char_w
    return out


def _dedupe_words(words: list[tuple[str, tuple]],
                  y_tol: int = 8, x_tol: int = 8
                  ) -> list[tuple[str, tuple]]:
    """Remove near-duplicate words from a list — used after chunked OCR
    where words sitting in the overlap region between two chunks get
    detected twice. Two entries are "the same" if their bbox top-lefts
    are within (x_tol, y_tol) pixels AND the words match (case-insensitive)."""
    sorted_words = sorted(words, key=lambda w: (w[1][1], w[1][0]))
    deduped: list[tuple[str, tuple]] = []
    for word, bbox in sorted_words:
        x1, y1, x2, y2 = bbox
        is_dup = False
        # Scan recent additions within the y-tolerance band; entries past
        # that band are too far up the page to be duplicates of this one.
        for i in range(len(deduped) - 1, -1, -1):
            other_word, (ox1, oy1, ox2, oy2) = deduped[i]
            if oy1 < y1 - y_tol:
                break
            if (abs(ox1 - x1) <= x_tol and
                abs(oy1 - y1) <= y_tol and
                other_word.lower() == word.lower()):
                is_dup = True
                break
        if not is_dup:
            deduped.append((word, bbox))
    return deduped


def _ocr_image_chunked(image: "Image.Image",
                      chunk_height: int = 3000,
                      overlap: int = 120,
                      ) -> list[tuple[str, tuple]]:
    """OCR an image at native resolution, chunking vertically when it's
    too tall to OCR in one pass without hurting accuracy.

    Why this matters: RapidOCR (and PP-OCR generally) internally resize
    the input image to fit a target size before detection. For a tall
    capture like 1200×12000, that internal resize squashes the text so
    badly that recognition starts misreading. By feeding the model a
    series of 1200×3000 chunks at native resolution, every character
    keeps its original pixel size.

    Returns (word, (x1, y1, x2, y2)) tuples in *original-image* pixel
    coordinates — chunk y-offsets are added back here so the caller can
    use the bboxes against the full image."""
    w, h = image.size
    img_array_full = np.array(image)
    # Small enough → single pass, no chunking overhead.
    if h <= chunk_height + overlap:
        word_data = _ocr(img_array_full)
        return [(word, _tighten_x_bbox(img_array_full, *b))
                for word, b in word_data]

    all_words: list[tuple[str, tuple]] = []
    y = 0
    while y < h:
        y2 = min(y + chunk_height, h)
        chunk_arr = img_array_full[y:y2]
        word_data = _ocr(chunk_arr)
        # Tighten against the chunk (cheaper than slicing the full
        # array each time) then shift y back to global coords.
        for word, b in word_data:
            b_tight = _tighten_x_bbox(chunk_arr, *b)
            x1, by1, x2, by2 = b_tight
            all_words.append((word, (x1, by1 + y, x2, by2 + y)))
        if y2 >= h:
            break
        y = y2 - overlap   # overlap keeps lines at chunk boundaries readable

    return _dedupe_words(all_words)


def _load_models(on_status: callable, on_done: callable, on_error: callable,
                 gpu_tts: bool = False, gpu_ocr: bool = False) -> None:
    global _ocr_reader, _tts_pipeline
    try:
        # Speech device: only "cuda" if both requested AND torch.cuda exists.
        tts_device = "cuda" if (gpu_tts and torch.cuda.is_available()) else "cpu"

        # OCR CUDA: only enabled if the user ticked GPU-OCR AND the installed
        # onnxruntime build exposes CUDAExecutionProvider (i.e. onnxruntime-gpu
        # is on the venv, not the CPU runtime). Otherwise fall back silently.
        ocr_use_cuda = False
        if gpu_ocr:
            try:
                import onnxruntime as _ort
                ocr_use_cuda = (
                    "CUDAExecutionProvider" in _ort.get_available_providers())
            except Exception:
                ocr_use_cuda = False

        on_status("Loading OCR model (PP-OCRv5 mobile)…")
        # Pin RapidOCR to the PP-OCRv5 mobile EN ONNX weights downloaded
        # by _download_models.py — identical bytes to the v5 weights used
        # by PaddleOCR, so OCR output is bit-identical to the previous
        # paddle install. The use_cuda flags only take effect if the
        # installed runtime is onnxruntime-gpu; with CPU onnxruntime they
        # silently fall back to CPU execution. We skip the cls (text
        # orientation classifier) entirely — screen text is always upright.
        _ocr_reader = RapidOCR(
            det_model_path=_OCR_DET_PATH,
            rec_model_path=_OCR_REC_PATH,
            det_use_cuda=ocr_use_cuda,
            rec_use_cuda=ocr_use_cuda,
        )

        on_status("Loading speech model…")
        _tts_pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M",
                                  device=tts_device)
        on_done()
    except Exception as exc:
        on_error(exc)


def _virtual_screen() -> tuple[int, int, int, int]:
    u = ctypes.windll.user32
    return (
        u.GetSystemMetrics(76), u.GetSystemMetrics(77),
        u.GetSystemMetrics(78), u.GetSystemMetrics(79),
    )


# ── Win32 RegisterHotKey-based hotkey manager ─────────────────────────────────
# A drop-in replacement for keyboard.add_hotkey for the use-case of "fire a
# callback when this combo is pressed". Reliable across long uptime, session
# changes, screen lock/unlock, sleep/resume, RDP transitions — none of which
# can silently kill a RegisterHotKey-managed binding (unlike low-level
# keyboard hooks, which can be dropped for `LowLevelHooksTimeout` and don't
# survive several Windows session events).

_MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
_WM_QUIT, _WM_HOTKEY = 0x0012, 0x0312
_WHK_MSG_REGISTER   = 0x8001    # wParam=id, lParam=(mod<<16)|vk
_WHK_MSG_UNREGISTER = 0x8002    # wParam=id


def _key_to_vk(name: str) -> int | None:
    """Convert a key name ("z", "f5", "space", ".") to a Win32 virtual-key
    code. Returns None for unrecognised names."""
    n = name.lower()
    if len(n) == 1:
        c = n.upper()
        if "A" <= c <= "Z" or "0" <= c <= "9":
            return ord(c)
    _SPECIAL = {
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
        "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
        "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
        "space": 0x20, "enter": 0x0D, "return": 0x0D,
        "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
        "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
        "insert": 0x2D, "ins": 0x2D,
        "home": 0x24, "end": 0x23,
        "pageup": 0x21, "pgup": 0x21,
        "pagedown": 0x22, "pgdn": 0x22,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD,
        ".": 0xBE, "/": 0xBF, "`": 0xC0,
        "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    }
    return _SPECIAL.get(n)


def _parse_combo(combo: str) -> tuple[int, int] | None:
    """Parse "shift+z" → (MOD_SHIFT, VK_Z=0x5A). Returns None on failure."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return None
    mod, vk = 0, None
    for p in parts:
        if p in ("shift", "shft"):
            mod |= _MOD_SHIFT
        elif p in ("ctrl", "control"):
            mod |= _MOD_CONTROL
        elif p in ("alt", "menu"):
            mod |= _MOD_ALT
        elif p in ("win", "super", "windows", "cmd"):
            mod |= _MOD_WIN
        else:
            if vk is not None:
                return None    # more than one non-modifier key
            vk = _key_to_vk(p)
            if vk is None:
                return None
    if vk is None:
        return None
    return mod, vk


class WinHotkey:
    """Process-wide global hotkey manager using Win32 RegisterHotKey.

    RegisterHotKey is per-thread (the thread that calls RegisterHotKey is
    the one that receives WM_HOTKEY messages), so we run a dedicated
    daemon thread with a message pump. Add/remove operations are
    dispatched to that thread via PostThreadMessageW so the registrations
    happen on the right thread.

    The pump thread is started lazily on first add() and torn down via
    PostThreadMessageW(WM_QUIT). When the thread exits, Windows
    automatically unregisters all hotkeys it owned, so cleanup is
    best-effort but doesn't leak anything across process restarts.
    """

    def __init__(self):
        self._bindings: dict = {}    # combo_str → (id, mod, vk, callback)
        self._next_id = 1
        self._thread = None
        self._thread_id = 0
        self._lock = threading.Lock()
        # Set by the caller; takes a no-arg callable and runs it on the
        # main (Tk) thread. None → run on the pump thread directly.
        self.dispatch = None

    def add(self, combo: str, callback) -> bool:
        """Register a hotkey combo (e.g. "shift+z"). Returns True if the
        combo is syntactically valid. Note: actual Win32 RegisterHotKey
        success is determined asynchronously on the pump thread; if the
        combo is already claimed by another process, registration will
        silently fail there."""
        parsed = _parse_combo(combo)
        if parsed is None:
            return False
        mod, vk = parsed
        with self._lock:
            existing = self._bindings.pop(combo, None)
            hk_id = self._next_id
            self._next_id += 1
            self._bindings[combo] = (hk_id, mod, vk, callback)
            need_start = self._thread is None or not self._thread.is_alive()
        if need_start:
            self._start_thread()
        else:
            if existing is not None:
                self._post(_WHK_MSG_UNREGISTER, existing[0], 0)
            self._post(_WHK_MSG_REGISTER, hk_id, (mod << 16) | vk)
        return True

    def remove_all(self):
        """Unregister every binding but keep the pump thread alive so a
        subsequent add() is cheap (no thread restart)."""
        with self._lock:
            ids = [v[0] for v in self._bindings.values()]
            self._bindings.clear()
        for hk_id in ids:
            self._post(_WHK_MSG_UNREGISTER, hk_id, 0)

    def restart(self):
        """Tear down the pump thread and start a fresh one, re-registering
        all current bindings. Defensive — RegisterHotKey rarely needs this,
        but cheap insurance against any imaginable corruption."""
        with self._lock:
            saved = [(c, v[3]) for c, v in self._bindings.items()]
            self._bindings.clear()
        self._stop_thread()
        for combo, cb in saved:
            self.add(combo, cb)

    def _start_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(ready,),
            daemon=True, name="WinHotkeyPump")
        self._thread.start()
        ready.wait(timeout=1.0)

    def _stop_thread(self):
        tid = self._thread_id
        if tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(tid, _WM_QUIT, 0, 0)
            except Exception:
                pass
        t = self._thread
        if t is not None:
            t.join(timeout=1.5)
        self._thread = None
        self._thread_id = 0

    def _post(self, msg, wparam, lparam):
        tid = self._thread_id
        if not tid:
            return
        try:
            ctypes.windll.user32.PostThreadMessageW(tid, msg, wparam, lparam)
        except Exception:
            pass

    def _run(self, ready: threading.Event):
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        msg = wintypes.MSG()
        # Force-create the thread's message queue before anyone calls
        # PostThreadMessage against us (otherwise the post can silently
        # fail because the queue doesn't exist yet).
        user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0)
        # Register any bindings that were added before the thread started.
        with self._lock:
            initial = list(self._bindings.values())
        for hk_id, mod, vk, _ in initial:
            try: user32.RegisterHotKey(0, hk_id, mod, vk)
            except Exception: pass
        ready.set()
        # Message pump.
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if ret <= 0:
                # 0 = WM_QUIT received, <0 = error
                break
            if msg.message == _WM_HOTKEY:
                hk_id = int(msg.wParam)
                cb = None
                with self._lock:
                    for v in self._bindings.values():
                        if v[0] == hk_id:
                            cb = v[3]
                            break
                if cb is not None:
                    d = self.dispatch
                    if d:
                        try: d(cb)
                        except Exception: pass
                    else:
                        try: cb()
                        except Exception: pass
            elif msg.message == _WHK_MSG_REGISTER:
                hk_id = int(msg.wParam)
                lp = int(msg.lParam)
                mod = (lp >> 16) & 0xFFFF
                vk  = lp & 0xFFFF
                try: user32.RegisterHotKey(0, hk_id, mod, vk)
                except Exception: pass
            elif msg.message == _WHK_MSG_UNREGISTER:
                try: user32.UnregisterHotKey(0, int(msg.wParam))
                except Exception: pass
            # Other messages: ignore (no TranslateMessage/DispatchMessage
            # needed — we don't run window procs on this thread).
        # Thread exit: unregister everything we still own.
        with self._lock:
            remaining = [v[0] for v in self._bindings.values()]
        for hk_id in remaining:
            try: user32.UnregisterHotKey(0, hk_id)
            except Exception: pass


# ── In-process scrolling capture ──────────────────────────────────────────────
# Capture a vertically-scrolling screenshot of an arbitrary screen region by
# sending mouse-wheel events to the window under the region's centre and
# stitching the resulting frames. Drop-in replacement for ShareX's scrolling
# capture, with the advantage that we know the original region's screen
# coordinates (so the reader window can align to the same top-left as a
# normal drag-region capture).

def _find_vertical_overlap(template: np.ndarray,
                           image: np.ndarray) -> int | None:
    """Find the y-offset in `image` where `template` best matches. Returns
    None if no acceptable match is found.

    template: shape (strip_h, w, channels) — bottom strip of the previous
              stitched result.
    image:    shape (h, w, channels) — the new frame we're trying to align.
    """
    if image.shape[0] < template.shape[0]:
        return None
    try:
        import cv2
        # TM_SQDIFF_NORMED returns 0.0 for perfect match, 1.0 for worst.
        res = cv2.matchTemplate(image, template, cv2.TM_SQDIFF_NORMED)
        min_val, _, min_loc, _ = cv2.minMaxLoc(res)
        if min_val > 0.20:
            return None  # match too poor
        return int(min_loc[1])
    except Exception:
        # Numpy fallback (slower but no extra dependency).
        strip_h = template.shape[0]
        n = image.shape[0] - strip_h + 1
        if n <= 0:
            return None
        template_i = template.astype(np.int32)
        best_y, best_diff = -1, None
        for y in range(n):
            d = np.sum((image[y:y + strip_h].astype(np.int32) - template_i) ** 2)
            if best_diff is None or d < best_diff:
                best_diff = d
                best_y = y
        if best_y < 0:
            return None
        # Reject very poor matches (heuristic threshold on per-pixel error).
        per_pixel = best_diff / (strip_h * image.shape[1] * image.shape[2])
        if per_pixel > 4000:
            return None
        return best_y


def _stitch_frames(frames: list[np.ndarray]) -> np.ndarray:
    """Stitch a list of overlapping vertical frames into one tall image.
    Each frame is appended below the running stitched result, with the
    overlapping region detected by template-matching the bottom strip of
    the stitched result against the new frame."""
    if not frames:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    stitched = frames[0]
    for new_frame in frames[1:]:
        strip_h = min(80, stitched.shape[0] // 4, new_frame.shape[0] // 2)
        if strip_h < 10:
            stitched = np.vstack([stitched, new_frame])
            continue
        template = stitched[-strip_h:]
        y_offset = _find_vertical_overlap(template, new_frame)
        if y_offset is None:
            # No reliable match — assume the scroll was a full frame.
            stitched = np.vstack([stitched, new_frame])
            continue
        new_start = y_offset + strip_h
        if new_start >= new_frame.shape[0]:
            continue   # this frame added no new rows
        stitched = np.vstack([stitched, new_frame[new_start:]])
    return stitched


def _capture_scrolling(region: tuple,
                       stop_event: "threading.Event | None" = None,
                       status_cb=None,
                       max_frames: int = 40,
                       scroll_delta: int = -360,
                       scroll_delay: float = 0.25) -> Image.Image:
    """Capture a vertically-scrolling screenshot of `region`. Sends
    mouse-wheel events to the window under the centre of `region`,
    captures after each scroll, and stops when the captured content
    stops changing (or after `max_frames`, whichever comes first).

    Returns the stitched composite as a PIL Image. The image's width
    equals the region width; its height is at least the region height
    and may be many times taller for long pages."""
    u = ctypes.windll.user32
    x1, y1, x2, y2 = region
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

    # Park the cursor over the region so wheel events go to the right
    # window (Windows' default is "scroll the window the cursor hovers
    # over" — Settings → Mouse → "Scroll inactive windows...").
    u.SetCursorPos(int(cx), int(cy))
    time.sleep(0.20)   # let any focus / hover state settle

    def _grab() -> np.ndarray:
        img = ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")
        return np.array(img)

    frames: list[np.ndarray] = [_grab()]
    last_frame = frames[0]
    stall_count = 0

    if status_cb:
        status_cb("Scrolling capture: frame 1…")

    # Split each scroll step into several smaller wheel events for
    # *smooth* scrolling. Browsers, PDF viewers, and most modern apps
    # interpret sub-notch wheel deltas as smooth-scroll input (the same
    # mechanism trackpads use) — many small events feel like a continuous
    # scroll, while one big -360 event looks like a discrete jump.
    SUB_EVENTS = 6
    sub_delta = scroll_delta // SUB_EVENTS    # e.g. -60 when scroll_delta = -360
    # Allocate the per-step time budget: 60% on the sub-events themselves,
    # 40% as a "settle" wait so the page's smooth-scroll animation can
    # finish before we grab.
    sub_event_gap = (scroll_delay * 0.60) / SUB_EVENTS
    settle_time   = scroll_delay * 0.40

    for i in range(max_frames):
        if stop_event is not None and stop_event.is_set():
            break
        # Re-park the cursor every iteration so a stray hand on the
        # mouse can't redirect wheel events mid-capture.
        u.SetCursorPos(int(cx), int(cy))
        # 0x0800 = MOUSEEVENTF_WHEEL. Negative delta scrolls the content
        # *down* (i.e. viewport reveals lower content). We send several
        # smaller events in a tight burst rather than one big jump.
        for _ in range(SUB_EVENTS):
            u.mouse_event(0x0800, 0, 0, sub_delta, 0)
            time.sleep(sub_event_gap)
        time.sleep(settle_time)

        new_frame = _grab()
        diff = float(np.mean(np.abs(
            new_frame.astype(np.int32) - last_frame.astype(np.int32))))
        if diff < 0.5:
            # Frame didn't change → we've hit the bottom (or this window
            # doesn't scroll). Stall twice in a row to be sure.
            stall_count += 1
            if stall_count >= 2:
                break
            continue
        stall_count = 0
        frames.append(new_frame)
        last_frame = new_frame
        if status_cb:
            status_cb(f"Scrolling capture: frame {len(frames)}…")

    if len(frames) == 1:
        return Image.fromarray(frames[0])
    if status_cb:
        status_cb(f"Stitching {len(frames)} frames…")
    return Image.fromarray(_stitch_frames(frames))


def select_region(root: tk.Tk) -> tuple[int, int, int, int] | None:
    vx, vy, vw, vh = _virtual_screen()
    screenshot = ImageGrab.grab(all_screens=True)
    result: dict = {"region": None}

    overlay = tk.Toplevel(root)
    overlay.geometry(f"{vw}x{vh}+{vx}+{vy}")
    overlay.attributes("-topmost", True)
    overlay.overrideredirect(True)
    overlay.configure(cursor="crosshair")

    dimmed = screenshot.point(lambda p: int(p * 0.45))
    tk_img = ImageTk.PhotoImage(dimmed)
    canvas = tk.Canvas(overlay, highlightthickness=0, cursor="crosshair")
    canvas.pack(fill=tk.BOTH, expand=True)
    canvas.create_image(0, 0, image=tk_img, anchor="nw")
    canvas.tk_img = tk_img
    canvas.create_text(vw // 2, 30,
                       text="Drag to select region  •  Esc to cancel",
                       fill="white", font=("Segoe UI", 14))

    rect_id = [None]
    start = [0, 0]

    def on_press(e):
        start[0], start[1] = e.x, e.y
        if rect_id[0]:
            canvas.delete(rect_id[0])

    def on_drag(e):
        if rect_id[0]:
            canvas.delete(rect_id[0])
        rect_id[0] = canvas.create_rectangle(
            start[0], start[1], e.x, e.y, outline="#00e676", width=2)

    def on_release(e):
        x1, y1 = min(start[0], e.x), min(start[1], e.y)
        x2, y2 = max(start[0], e.x), max(start[1], e.y)
        if x2 - x1 > 4 and y2 - y1 > 4:
            result["region"] = (x1 + vx, y1 + vy, x2 + vx, y2 + vy)
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    overlay.bind("<Escape>", lambda e: overlay.destroy())
    overlay.focus_force()
    root.wait_window(overlay)
    return result["region"]


# ── Highlight color science ───────────────────────────────────────────────────

def _rel_lum(r: int, g: int, b: int) -> float:
    def f(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r / 255) + 0.7152 * f(g / 255) + 0.0722 * f(b / 255)


def _cr(l1: float, l2: float) -> float:
    a, b = max(l1, l2), min(l1, l2)
    return (a + 0.05) / (b + 0.05)


def optimal_highlight(bg_hex: str, text_hex: str) -> str:
    """
    Return the most attention-grabbing highlight for the given bg / text pair.

    Formula: maximise chroma (colorfulness) at the background's perceptual
    luminance level, subject to text contrast >= 4.5:1 (WCAG AA).

    For white bg + black text this converges to yellow (#ffff00) — the
    same reason fluorescent highlighters are yellow: maximum chroma at
    near-white luminance keeps text contrast near-maximum (19.6:1) while
    the blue-yellow opponent channel fires at peak salience.
    """
    def parse(h): return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    bg_r,  bg_g,  bg_b  = parse(bg_hex)
    tx_r,  tx_g,  tx_b  = parse(text_hex)
    L_bg = _rel_lum(bg_r, bg_g, bg_b)
    L_tx = _rel_lum(tx_r, tx_g, tx_b)

    best_hex, best_score = "#fff200", -1.0
    for h_step in range(120):               # 3° steps → full 360° hue sweep
        h = h_step / 120.0
        for l_int in range(5, 96, 2):       # HSL lightness 5 %–95 %
            r, g, b = colorsys.hls_to_rgb(h, l_int / 100.0, 1.0)
            r, g, b = int(r * 255), int(g * 255), int(b * 255)
            L_h = _rel_lum(r, g, b)
            if _cr(L_tx, L_h) < 4.5:       # text must stay readable
                continue
            chroma        = (max(r, g, b) - min(r, g, b)) / 255.0
            lum_proximity = 1.0 - min(1.0, abs(L_h - L_bg) * 3.0)
            score         = chroma * lum_proximity
            if score > best_score:
                best_score = score
                best_hex   = f"#{r:02x}{g:02x}{b:02x}"
    return best_hex


def _detect_image_colors(img_array: np.ndarray,
                         bboxes: list) -> tuple[str, str]:
    """
    Estimate background color (from image borders) and text color
    (darkest pixels inside OCR bboxes).  Returns (bg_hex, text_hex).
    """
    h, w = img_array.shape[:2]
    bw, bh = max(1, w // 20), max(1, h // 20)
    border = np.concatenate([
        img_array[:bh,  :,   :3].reshape(-1, 3),
        img_array[-bh:, :,   :3].reshape(-1, 3),
        img_array[:,  :bw,  :3].reshape(-1, 3),
        img_array[:, -bw:,  :3].reshape(-1, 3),
    ])
    bg_rgb = np.median(border, axis=0).astype(int)

    text_samples = []
    for x1, y1, x2, y2 in bboxes[:10]:
        patch = img_array[int(y1):int(y2), int(x1):int(x2), :3]
        if patch.size == 0:
            continue
        diff = np.abs(patch.astype(float) - bg_rgb).sum(axis=2)
        py, px = np.unravel_index(diff.argmax(), diff.shape)
        text_samples.append(patch[py, px])

    if text_samples:
        text_rgb = np.median(text_samples, axis=0).astype(int)
    else:
        text_rgb = (np.array([0, 0, 0]) if bg_rgb.mean() > 127
                    else np.array([255, 255, 255]))

    return (
        "#{:02x}{:02x}{:02x}".format(*bg_rgb),
        "#{:02x}{:02x}{:02x}".format(*text_rgb),
    )


def _make_text_image(words: list[str], width: int = 800
                     ) -> tuple[Image.Image, list[tuple]]:
    """Render OCR words as flowing plain text on a clean background.
    Returns (PIL image, per-word bboxes)."""
    font_size = 20
    for path in ("C:/Windows/Fonts/segoeui.ttf",
                 "C:/Windows/Fonts/arial.ttf"):
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except OSError:
            pass
    else:
        font = ImageFont.load_default()

    pad    = 24
    line_h = font_size + 14

    def ww(word):
        try:
            b = font.getbbox(word)
            return b[2] - b[0]
        except Exception:
            return len(word) * (font_size // 2)

    space_w = ww(" ") + 2

    positions = []
    x, y = pad, pad
    for word in words:
        w = ww(word)
        if x + w > width - pad and x > pad:
            x = pad
            y += line_h
        positions.append((word, x, y, x + w, y + font_size + 2))
        x += w + space_w

    img_h = y + line_h + pad
    img   = Image.new("RGB", (width, img_h), (252, 252, 252))
    draw  = ImageDraw.Draw(img)
    for word, x1, y1, _, _ in positions:
        draw.text((x1, y1), word, fill=(25, 25, 25), font=font)

    bboxes = [(x1, y1, x2, y2) for _, x1, y1, x2, y2 in positions]
    return img, bboxes


# ── OCR helpers ───────────────────────────────────────────────────────────────

def _tighten_x_bbox(img_array: np.ndarray,
                    x1: float, y1: float, x2: float, y2: float
                    ) -> tuple[float, float, float, float]:
    """Shrink x1/x2 to the columns that actually contain ink, dropping space margins."""
    xa, ya, xb, yb = int(x1), int(y1), int(x2), int(y2)
    strip = img_array[ya:yb, xa:xb]
    if strip.shape[0] < 1 or strip.shape[1] < 2:
        return x1, y1, x2, y2
    gray = strip.mean(axis=2) if strip.ndim == 3 else strip.astype(float)
    col_dark = gray.max() - gray.min(axis=0)
    thresh = col_dark.max() * 0.12
    text_cols = np.where(col_dark >= thresh)[0]
    if len(text_cols) == 0:
        return x1, y1, x2, y2
    return float(xa + text_cols[0]), y1, float(xa + text_cols[-1] + 1), y2


# ── Misc helpers ─────────────────────────────────────────────────────────────

def _fmt(secs: float) -> str:
    m, s = divmod(int(max(0, secs)), 60)
    return f"{m}:{s:02d}"


# ── TTS timing / alignment helpers ───────────────────────────────────────────

def _norm(w: str) -> str:
    """Lowercase alphanumeric key used for content-based word matching."""
    return ''.join(c.lower() for c in w if c.isalnum())


def _override_map(pairs) -> dict:
    """Build {normalized_key: replacement} from a list of [from, to] pairs.
    Keys are normalized with _norm so "API", "api", "Api" all match the
    same override. Later pairs win on key collision. Pairs with an empty
    key or empty replacement are skipped."""
    omap: dict = {}
    for pair in pairs or []:
        try:
            frm, to = pair[0], pair[1]
        except (TypeError, IndexError, KeyError):
            continue
        key = _norm(frm)
        repl = str(to).strip()
        if key and repl:
            omap[key] = repl
    return omap


def _apply_overrides(words: list[str], omap: dict) -> list[str]:
    """Apply pronunciation overrides to a token list, for the SPOKEN text
    only (the displayed/highlighted words are never changed).

    A token whose normalized form is in `omap` is replaced by the
    override's replacement string, split into tokens — so a single OCR
    word can expand to several spoken words (e.g. "API" → "A P I" or
    "PyTorch" → "pie torch"). Tokens with no override pass through
    unchanged."""
    if not omap:
        return list(words)
    out: list[str] = []
    for w in words:
        repl = omap.get(_norm(w))
        if repl is not None:
            out.extend(repl.split())
        else:
            out.append(w)
    return out


def _tts_safe(words: list[str]) -> str:
    """
    Build TTS input text from OCR words.
    - Strips Unicode symbol characters (®, ™, ©, …) that cause Kokoro to defer
      tokens to a late segment (the "reads at end" bug).
    - Attaches standalone punctuation tokens to the preceding word so Kokoro
      receives "Hello, world." instead of "Hello , world ." — preserving
      prosody without triggering the deferred-normalisation bug.
    """
    out = []
    for w in words:
        cleaned = ''.join(
            c for c in w
            if unicodedata.category(c)[0] not in ('S', 'C')
        ).strip()
        if not cleaned:
            continue
        if not any(c.isalnum() for c in cleaned) and out:
            out[-1] += cleaned   # attach "," "." etc. to preceding word
        else:
            out.append(cleaned)
    return ' '.join(out)

def _count_syllables(word: str) -> int:
    w = "".join(c for c in word.lower() if c.isalpha())
    if not w:
        return 1
    vowels = set("aeiouy")
    count, prev_v = 0, False
    for c in w:
        v = c in vowels
        if v and not prev_v:
            count += 1
        prev_v = v
    if w.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def _token_starts(tokens, expected: int) -> list[float] | None:
    """Extract per-word start times from Kokoro tokens, or None if unavailable."""
    starts, expecting = [], True
    for t in tokens:
        txt = getattr(t, "text", "") or ""
        ws  = getattr(t, "whitespace", "") or ""
        ts  = getattr(t, "start_ts", None)
        if any(c.isalnum() for c in txt) and expecting:
            if ts is None:
                return None
            starts.append(float(ts))
            expecting = False
        if ws:
            expecting = True
    return starts if len(starts) == expected else None


def _word_starts(tokens, seg_words: list[str], duration: float) -> list[float]:
    """Return per-word start times: model timestamps when available, syllable estimate otherwise."""
    ts = _token_starts(tokens, len(seg_words))
    if ts is not None:
        return ts
    weights = [_count_syllables(w) for w in seg_words]
    total = sum(weights) or 1
    elapsed, result = 0.0, []
    for wt in weights:
        result.append(elapsed)
        elapsed += duration * wt / total
    return result


# ── Main app ──────────────────────────────────────────────────────────────────

class App:
    # _play_state: "idle" | "generating" | "ready" | "playing" | "paused"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SelectAndRead")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(_ico):
            self.root.iconbitmap(_ico)

        # Signal the launcher splash that the app window is up
        def _signal_ready():
            try:
                self.root.update_idletasks()
                self.root.update()
                with open(os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                                      "SelectAndRead.ready"), "w") as _f:
                    _f.write(str(os.getpid()))
            except Exception:
                pass
        self.root.after(50, _signal_ready)

        self.stop_event  = threading.Event()
        self._play_event = threading.Event()   # set=playing  clear=paused/stopped
        self._seek_event = threading.Event()   # set=seek requested while playing

        self._full_audio: np.ndarray | None = None
        self._word_schedule: list[tuple[int, float]] = []
        self._word_bboxes_canvas: list[tuple] = []
        self._pause_pos = 0.0
        self._play_state = "idle"
        self._idle_seeked = False   # user moved the slider/clicked a word after audio ended
        self._extracted_text: str | None = None

        # Live elapsed-time counter + progress bar for the scanning phase.
        # During OCR the bar is indeterminate (no data to compute %). During
        # TTS, _scan_observed_rate (fraction-of-work-per-second-of-TTS) is
        # recomputed per Kokoro segment and the tick handler uses it to:
        #   - extrapolate displayed progress smoothly between segments
        #   - compute total expected time as ocr_elapsed + 1/rate (stable
        #     between segments, only refines when a new segment arrives)
        self._scan_start: float | None = None
        self._tts_start:  float | None = None
        self._scan_base_msg: str = ""
        self._scan_phase: str = ""                # "ocr" or "tts"
        self._scan_actual_progress: float = 0.0   # 0..1, last segment's value
        self._scan_observed_rate: float | None = None
        self._scan_tick_running = False
        self._scan_progress_bar = None
        self._reader_progress_bar = None

        self.status_var        = tk.StringVar(value="Loading models…")
        self._highlight_color  = "#fff200"
        self._highlight_mode   = tk.StringVar(value="auto")
        self._text_view_var    = tk.BooleanVar(value=False)
        # When enabled, the global trigger drags a region then captures
        # a vertically-scrolling screenshot of that region (in-process —
        # we send wheel events and stitch the frames ourselves; no
        # external dependency). Max frames bounds how far down the page
        # we'll scroll — the capture also stops earlier if the page
        # stops changing.
        self._scrolling_capture_var = tk.BooleanVar(value=False)
        self._scrolling_max_frames_var = tk.IntVar(value=40)

        self._reader_win        = None
        self._reader_canvas     = None
        self._reader_canvas_img = None
        self._reader_base_img   = None
        self._reader_status_var = None
        self._play_btn          = None
        self._timeline_var      = tk.DoubleVar(value=0.0)
        self._timeline_scale    = None
        self._timeline_max      = 0.0
        self._time_label_var    = None
        self._user_seeking      = False
        self._speed_var         = tk.DoubleVar(value=1.0)
        self._speed_label_var   = None
        self._speed_spinbox     = None

        self._hotkey_trigger    = "shift+z"
        self._hotkey_pause      = "shift+x"
        self._voice_id          = VOICES[0][0]
        # Pronunciation overrides: list of [from, to] pairs. Applied to the
        # spoken text only — e.g. ["API", "A P I"], ["PyTorch", "pie torch"].
        # The displayed/highlighted words are never altered.
        self._pron_overrides: list = []
        # Auto-scroll: when on, the scrolling-capture reader scrolls itself
        # to keep the highlighted word in view as TTS reads. Only has an
        # effect in the scrolling reader (the only one with a scrollable
        # canvas); the checkbox is shown there. Persisted across sessions.
        self._autoscroll_var = tk.BooleanVar(value=True)
        # Two independent GPU toggles. TTS = Kokoro on torch CUDA;
        # OCR = RapidOCR on onnxruntime-gpu. Either or both may be CUDA;
        # at runtime each falls back to CPU if its backend is missing.
        self._gpu_tts_var       = tk.BooleanVar(value=False)
        self._gpu_ocr_var       = tk.BooleanVar(value=False)
        # Populated by _load_settings / _register_hotkey if anything went
        # wrong; surfaced to the user once the UI is alive.
        self._settings_load_error: str | None = None
        self._hotkey_register_error: list[str] | None = None
        # Win32 RegisterHotKey-based global hotkey manager. Far more
        # reliable than the keyboard library's low-level hook for the
        # "fire callback on combo" use case — RegisterHotKey survives
        # session changes, screen lock/unlock, sleep/resume, RDP, and
        # isn't subject to LowLevelHooksTimeout silent drops.
        self._win_hotkeys = WinHotkey()
        # Hotkey callbacks fire on the pump thread; bounce them onto
        # the Tk thread so they can safely touch UI state.
        self._win_hotkeys.dispatch = lambda cb: self.root.after(0, cb)
        self._load_settings()

        self._build_ui()
        # Save whenever the text-view toggle changes
        self._text_view_var.trace_add(
            "write", lambda *_: self._save_settings())
        self.root.bind_all("<Control-v>", self._do_paste)
        self.root.bind_all("<Control-V>", self._do_paste)
        self._register_hotkey()
        # Defensive watchdog — restarts the Win32 hotkey pump every 10 min
        # just in case. RegisterHotKey is reliable enough that this rarely
        # matters in practice; it's belt-and-suspenders.
        self.root.after(10 * 60_000, self._hotkey_watchdog)
        # Surface a corrupt-settings error to the user once the window has
        # painted. Done via after() so the messagebox doesn't block the
        # window from becoming visible first.
        if self._settings_load_error:
            self.root.after(500, lambda: messagebox.showwarning(
                "SelectAndRead — settings", self._settings_load_error,
                parent=self.root))
        threading.Thread(
            target=_load_models,
            args=(
                lambda msg: self.root.after(0, lambda m=msg: self.status_var.set(m)),
                self._on_models_ready,
                lambda exc: self.root.after(0, lambda e=exc: self.status_var.set(
                    f"Model load failed: {e}")),
            ),
            kwargs={"gpu_tts": self._gpu_tts_var.get(),
                    "gpu_ocr": self._gpu_ocr_var.get()},
            daemon=True,
        ).start()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 14, "pady": 4}
        ttk.Label(self.root, text="SelectAndRead",
                  font=("Segoe UI", 11, "bold")).pack(pady=(10, 4))

        self._trigger_btn = ttk.Button(
            self.root,
            text=f"Select & Read   ({self._hotkey_trigger.upper()})",
            command=self._trigger, width=28)
        self._trigger_btn.pack(**pad)

        ttk.Button(self.root, text="📋  Paste & Read   (Ctrl+V)",
                   command=self._do_paste, width=28).pack(**pad)

        ttk.Button(self.root, text="■  Stop",
                   command=self._stop, width=28).pack(**pad)

        vf = ttk.Frame(self.root)
        vf.pack(**pad)
        ttk.Label(vf, text="Voice:").pack(side=tk.LEFT)
        voice_labels = [lbl for _, lbl in VOICES]
        voice_idx    = next((i for i, (vid, _) in enumerate(VOICES)
                             if vid == self._voice_id), 0)
        self.voice_var = tk.StringVar(value=VOICES[voice_idx][1])
        vcb = ttk.Combobox(vf, textvariable=self.voice_var,
                           values=voice_labels,
                           state="readonly", width=18)
        vcb.current(voice_idx)
        vcb.pack(side=tk.LEFT, padx=(6, 0))
        vcb.bind("<<ComboboxSelected>>", self._on_voice_select)

        cf = ttk.Frame(self.root)
        cf.pack(**pad)
        ttk.Label(cf, text="Highlight:").pack(side=tk.LEFT)
        ttk.Radiobutton(cf, text="Auto", variable=self._highlight_mode,
                        value="auto",
                        command=self._on_highlight_mode_change
                        ).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Radiobutton(cf, text="Custom", variable=self._highlight_mode,
                        value="custom",
                        command=self._on_highlight_mode_change
                        ).pack(side=tk.LEFT, padx=(4, 0))
        btn_state = "normal" if self._highlight_mode.get() == "custom" else "disabled"
        self._color_btn = tk.Button(cf, bg=self._highlight_color,
                                    activebackground=self._highlight_color,
                                    width=4, relief=tk.RAISED, bd=1,
                                    state=btn_state,
                                    command=self._pick_color)
        self._color_btn.pack(side=tk.LEFT, padx=(6, 0))

        tf = ttk.Frame(self.root)
        tf.pack(**pad)
        ttk.Checkbutton(tf, text="Text view  (OCR text only)",
                        variable=self._text_view_var).pack(side=tk.LEFT)

        gf = ttk.Frame(self.root)
        gf.pack(**pad)
        ttk.Checkbutton(gf, text="Use GPU — TTS  (requires CUDA)",
                        variable=self._gpu_tts_var,
                        command=self._on_gpu_toggle).pack(anchor="w")
        ttk.Checkbutton(gf, text="Use GPU — OCR  (requires CUDA)",
                        variable=self._gpu_ocr_var,
                        command=self._on_gpu_toggle).pack(anchor="w")

        sf = ttk.Frame(self.root)
        sf.pack(**pad)
        ttk.Checkbutton(sf, text="Scrolling capture",
                        variable=self._scrolling_capture_var,
                        command=self._save_settings).pack(side=tk.LEFT)
        ttk.Label(sf, text="  Max scrolls:").pack(side=tk.LEFT, padx=(8, 0))
        _max_spin = ttk.Spinbox(
            sf, from_=1, to=200, increment=5, width=4,
            textvariable=self._scrolling_max_frames_var)
        _max_spin.pack(side=tk.LEFT)
        # Save whenever the user adjusts the value (any of: spinbox
        # arrows, typed-in + Enter / FocusOut).
        for _evt in ("<<Increment>>", "<<Decrement>>",
                     "<Return>", "<FocusOut>"):
            _max_spin.bind(_evt, lambda _e: self._save_settings())

        ttk.Button(self.root, text="⚙  Settings",
                   command=self._open_settings, width=28).pack(**pad)

        ttk.Label(self.root, textvariable=self.status_var,
                  foreground="gray").pack(pady=(2, 2))

        # Progress bar shown only during the generation phase; packed/forgot
        # by _tick_scan_timer based on _play_state.
        self._scan_progress_bar = ttk.Progressbar(
            self.root, orient=tk.HORIZONTAL, mode="determinate",
            length=260, maximum=100)

    def _on_voice_select(self, _):
        lbl = self.voice_var.get()
        for vid, vlbl in VOICES:
            if vlbl == lbl:
                self._voice_id = vid
                break
        self._save_settings()

    def _on_highlight_mode_change(self):
        if self._highlight_mode.get() == "custom":
            self._color_btn.configure(state="normal")
        else:
            self._color_btn.configure(state="disabled")
        self._save_settings()

    def _apply_highlight_color(self, color: str):
        self._highlight_color = color
        self._color_btn.configure(bg=color, activebackground=color)
        self._save_settings()

    def _pick_color(self):
        chosen = colorchooser.askcolor(initialcolor=self._highlight_color,
                                       title="Highlight color", parent=self.root)
        if not chosen or not chosen[1]:
            return
        self._apply_highlight_color(chosen[1])

    def _register_hotkey(self) -> bool:
        """Install both production hotkeys via Win32 RegisterHotKey.

        RegisterHotKey is fundamentally more reliable than low-level
        keyboard hooks for "wake me on this combo" use cases: not subject
        to LowLevelHooksTimeout silent drops, survives session changes,
        screen lock/unlock, sleep/resume, and RDP transitions.

        Each combo is exclusive to one process — if another app holds it,
        registration silently fails on the pump thread. The combo string
        is still recorded in `failed` so the UI can warn the user."""
        failed: list[str] = []
        for combo in (self._hotkey_trigger, self._hotkey_pause):
            cb = (self._trigger if combo == self._hotkey_trigger
                  else self._on_play_btn)
            if not self._win_hotkeys.add(combo, cb):
                failed.append(combo)
        self._hotkey_register_error = failed or None
        return not failed

    def _reregister_hotkeys(self):
        """Remove all current hotkey bindings and re-register fresh.
        Used after the user changes a hotkey in Settings."""
        self._win_hotkeys.remove_all()
        self._register_hotkey()

    def _hotkey_watchdog(self):
        """Defensive periodic restart of the hotkey pump thread.

        RegisterHotKey is rock-solid in practice — unlike low-level hooks,
        it isn't dropped by Windows on session changes, lock/unlock,
        sleep/resume, or LowLevelHooksTimeout. So this watchdog is mostly
        paranoia: every 10 min, tear down the pump thread and start a new
        one with all bindings re-registered. Each restart blips for a few
        tens of milliseconds; a hotkey press during that window is rare
        enough to ignore (10 ms / 600 s ≈ 0.002 % of the time)."""
        try:
            self._win_hotkeys.restart()
        except Exception:
            pass
        self.root.after(10 * 60_000, self._hotkey_watchdog)

    def _load_settings(self):
        try:
            f = open(_SETTINGS_PATH)
        except FileNotFoundError:
            return
        except OSError as exc:
            self._settings_load_error = (
                f"Could not open settings file:\n{_SETTINGS_PATH}\n\n"
                f"{exc.__class__.__name__}: {exc}\n\nDefaults will be used.")
            return
        try:
            with f:
                d = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            backup = _SETTINGS_PATH + ".broken"
            saved_to = None
            try:
                os.replace(_SETTINGS_PATH, backup)
                saved_to = backup
            except Exception:
                pass
            tail = (f"\n\nThe broken file was renamed to:\n{saved_to}"
                    if saved_to else "")
            self._settings_load_error = (
                f"Settings file was corrupt ({exc.__class__.__name__}) and "
                f"could not be loaded — defaults restored.{tail}")
            return
        try:
            self._hotkey_trigger = d.get("hotkey_trigger", self._hotkey_trigger)
            self._hotkey_pause   = d.get("hotkey_pause",   self._hotkey_pause)
            self._voice_id       = d.get("voice",          self._voice_id)
            self._highlight_color = d.get("highlight_color", self._highlight_color)
            self._highlight_mode.set(d.get("highlight_mode", "auto"))
            self._text_view_var.set(bool(d.get("text_view", False)))
            self._speed_var.set(float(d.get("speed", 1.0)))
            # Pronunciation overrides — normalize to a list of [from, to]
            # string pairs, dropping anything malformed.
            _raw_ov = d.get("pron_overrides", [])
            _clean_ov = []
            if isinstance(_raw_ov, list):
                for _p in _raw_ov:
                    try:
                        _frm, _to = str(_p[0]), str(_p[1])
                    except (TypeError, IndexError, KeyError):
                        continue
                    if _frm.strip():
                        _clean_ov.append([_frm, _to])
            self._pron_overrides = _clean_ov
            self._autoscroll_var.set(bool(d.get("autoscroll", True)))
            # Legacy `gpu` flag (single toggle) becomes the default for both
            # new split toggles when an older settings file is loaded.
            legacy_gpu = bool(d.get("gpu", False))
            self._gpu_tts_var.set(bool(d.get("gpu_tts", legacy_gpu)))
            self._gpu_ocr_var.set(bool(d.get("gpu_ocr", legacy_gpu)))
            # Migrate old `sharex_scrolling` key into the new
            # `scrolling_capture` key — preserves the user's toggle state
            # across the ShareX→in-process transition.
            legacy_sharex = bool(d.get("sharex_scrolling", False))
            self._scrolling_capture_var.set(
                bool(d.get("scrolling_capture", legacy_sharex)))
            try:
                self._scrolling_max_frames_var.set(
                    int(d.get("scrolling_max_frames", 40)))
            except (TypeError, ValueError):
                self._scrolling_max_frames_var.set(40)
        except Exception as exc:
            self._settings_load_error = (
                f"Settings file had unexpected values "
                f"({exc.__class__.__name__}: {exc}) — defaults used where needed.")

    def _save_settings(self):
        # Atomic write: serialize fully to a tmp file in the same directory,
        # then os.replace() onto the real path. This guarantees that a crash
        # or power-loss mid-write can never leave a half-written settings
        # file (which json.load would reject on next launch, silently wiping
        # the user's voice / hotkeys / highlight color).
        payload = {
            "hotkey_trigger":  self._hotkey_trigger,
            "hotkey_pause":    self._hotkey_pause,
            "voice":           self._voice_id,
            "highlight_color": self._highlight_color,
            "highlight_mode":  self._highlight_mode.get(),
            "text_view":       bool(self._text_view_var.get()),
            "speed":           round(self._speed_var.get(), 2),
            "gpu_tts":         bool(self._gpu_tts_var.get()),
            "gpu_ocr":         bool(self._gpu_ocr_var.get()),
            "scrolling_capture": bool(self._scrolling_capture_var.get()),
            "scrolling_max_frames": int(self._scrolling_max_frames_var.get()),
            "pron_overrides":  list(self._pron_overrides),
            "autoscroll":      bool(self._autoscroll_var.get()),
        }
        tmp = _SETTINGS_PATH + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                try: os.fsync(f.fileno())
                except OSError: pass
            os.replace(tmp, _SETTINGS_PATH)
        except Exception:
            try: os.remove(tmp)
            except OSError: pass

    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Settings")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()

        trigger_var = tk.StringVar(value=self._hotkey_trigger)
        pause_var   = tk.StringVar(value=self._hotkey_pause)

        ttk.Label(dlg, text="Select & Read:").grid(
            row=0, column=0, padx=(16, 6), pady=(16, 8), sticky="w")
        ttk.Label(dlg, textvariable=trigger_var, width=14,
                  relief="sunken", anchor="center").grid(row=0, column=1, padx=4)
        ttk.Button(dlg, text="Change",
                   command=lambda: self._capture_hotkey(
                       "trigger", trigger_var, dlg)
                   ).grid(row=0, column=2, padx=(4, 16))

        ttk.Label(dlg, text="Pause / Resume:").grid(
            row=1, column=0, padx=(16, 6), pady=(0, 16), sticky="w")
        ttk.Label(dlg, textvariable=pause_var, width=14,
                  relief="sunken", anchor="center").grid(row=1, column=1, padx=4)
        ttk.Button(dlg, text="Change",
                   command=lambda: self._capture_hotkey(
                       "pause", pause_var, dlg)
                   ).grid(row=1, column=2, padx=(4, 16))

        ttk.Separator(dlg, orient="horizontal").grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 8))
        ttk.Button(dlg, text="🗣  Pronunciation overrides…",
                   command=lambda: self._open_pron_overrides(dlg)
                   ).grid(row=3, column=0, columnspan=3, padx=16, pady=(0, 4))

        ttk.Button(dlg, text="Close", command=dlg.destroy).grid(
            row=4, column=0, columnspan=3, pady=(8, 14))

    def _open_pron_overrides(self, parent: tk.Toplevel):
        """Editor for pronunciation overrides. Each row maps a word (as it
        appears in the text) to how it should be spoken. The replacement is
        sent to the TTS engine instead of the original word; the displayed
        and highlighted text is never changed.

        Examples:
          API       →  A P I          (spell out an acronym)
          PyTorch   →  pie torch      (fix a mispronunciation)
          k8s       →  kubernetes     (expand an abbreviation)
        """
        dlg = tk.Toplevel(parent)
        dlg.title("Pronunciation overrides")
        dlg.resizable(False, True)
        dlg.attributes("-topmost", True)
        dlg.grab_set()

        ttk.Label(
            dlg,
            text=("Map a word to how it should be spoken.\n"
                  "Only the audio changes — the on-screen text and "
                  "highlighting stay the same."),
            justify="left", padding=(14, 12, 14, 6)).pack(anchor="w")

        # Scrollable area holding the rows.
        body = ttk.Frame(dlg)
        body.pack(fill=tk.BOTH, expand=True, padx=12)
        canvas = tk.Canvas(body, highlightthickness=0, width=420, height=240)
        vbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        rows_frame = ttk.Frame(canvas)
        rows_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=rows_frame, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        # Live list of (from_var, to_var) for the rows currently shown.
        row_vars: list[tuple[tk.StringVar, tk.StringVar]] = []

        # Header.
        hdr = ttk.Frame(rows_frame)
        hdr.grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(hdr, text="Word in text", width=20,
                  font=("Segoe UI", 9, "bold")).grid(row=0, column=0, padx=(0, 4))
        ttk.Label(hdr, text="Spoken as", width=20,
                  font=("Segoe UI", 9, "bold")).grid(row=0, column=2, padx=(4, 0))

        def _add_row(frm="", to=""):
            fv, tv = tk.StringVar(value=frm), tk.StringVar(value=to)
            row_vars.append((fv, tv))
            rf = ttk.Frame(rows_frame)
            rf.grid(sticky="w", pady=2)
            ttk.Entry(rf, textvariable=fv, width=20).grid(
                row=0, column=0, padx=(0, 4))
            ttk.Label(rf, text="→").grid(row=0, column=1)
            ttk.Entry(rf, textvariable=tv, width=20).grid(
                row=0, column=2, padx=(4, 4))

            def _remove():
                if (fv, tv) in row_vars:
                    row_vars.remove((fv, tv))
                rf.destroy()

            ttk.Button(rf, text="✕", width=3, command=_remove).grid(
                row=0, column=3)
            canvas.after_idle(
                lambda: canvas.configure(scrollregion=canvas.bbox("all")))

        # Seed with existing overrides (or one blank row to start).
        if self._pron_overrides:
            for pair in self._pron_overrides:
                try:
                    _add_row(str(pair[0]), str(pair[1]))
                except (TypeError, IndexError):
                    continue
        else:
            _add_row()

        # Bottom button bar.
        btns = ttk.Frame(dlg)
        btns.pack(fill=tk.X, padx=12, pady=(8, 12))
        ttk.Button(btns, text="＋  Add row",
                   command=lambda: _add_row()).pack(side=tk.LEFT)

        def _save():
            new_pairs = []
            for fv, tv in row_vars:
                frm = fv.get().strip()
                to  = tv.get().strip()
                if frm and to:
                    new_pairs.append([frm, to])
            self._pron_overrides = new_pairs
            self._save_settings()
            dlg.destroy()

        ttk.Button(btns, text="Cancel",
                   command=dlg.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Save",
                   command=_save).pack(side=tk.RIGHT, padx=(0, 6))

    def _capture_hotkey(self, which: str, lbl_var: tk.StringVar,
                        parent: tk.Toplevel):
        # Temporarily disable our Win32 hotkeys so the user pressing
        # their current hotkey (to re-assign it) doesn't fire the
        # bound action. Re-registered in _cleanup() when the dialog
        # closes (whether they confirmed or cancelled).
        self._win_hotkeys.remove_all()

        cap = tk.Toplevel(parent)
        cap.title("Set Hotkey")
        cap.resizable(False, False)
        cap.attributes("-topmost", True)
        cap.grab_set()

        ttk.Label(cap, text="Hold your key combination, then release.",
                  padding=(20, 12)).pack()

        preview_var = tk.StringVar(value="—")
        ttk.Label(cap, textvariable=preview_var,
                  font=("Segoe UI", 12, "bold"),
                  anchor="center", width=22, relief="sunken",
                  padding=(8, 6)).pack(padx=24, pady=(0, 8), fill=tk.X)

        bf = ttk.Frame(cap)
        bf.pack(pady=(0, 12))
        confirm_btn = ttk.Button(bf, text="Confirm", state="disabled")
        confirm_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(bf, text="Cancel",
                   command=lambda: _cleanup()).pack(side=tk.LEFT, padx=6)

        _MOD_NORM = {
            "left shift": "shift", "right shift": "shift",
            "left ctrl": "ctrl",   "right ctrl": "ctrl",
            "left alt": "alt",     "right alt": "alt",
            "left windows": "windows", "right windows": "windows",
        }
        _MOD_ORDER = ("ctrl", "shift", "alt", "windows")
        st = {"keys": set(), "last": None, "hook": None}

        def _build(keys):
            norm = {_MOD_NORM.get(k, k) for k in keys}
            mods  = [m for m in _MOD_ORDER if m in norm]
            other = sorted(k for k in norm if k not in set(_MOD_ORDER))
            parts = mods + other
            return "+".join(parts) if parts else None

        def _on_key(e):
            name = (e.name or "").lower()
            if e.event_type == "down":
                st["keys"].add(name)
                combo = _build(st["keys"])
                if combo:
                    cap.after(0, lambda c=combo: preview_var.set(c))
            elif e.event_type == "up":
                st["keys"].discard(name)
                if not st["keys"]:
                    current = preview_var.get()
                    if current and current != "—":
                        st["last"] = current
                        cap.after(0, lambda: confirm_btn.configure(state="normal"))

        def _cleanup(apply=False):
            if st["hook"] is not None:
                try:
                    keyboard.unhook(st["hook"])
                except Exception:
                    pass
                st["hook"] = None
            if apply:
                hk = st["last"]
                if hk:
                    if which == "trigger":
                        self._hotkey_trigger = hk
                        self._trigger_btn.configure(
                            text=f"Select & Read   ({hk.upper()})")
                    else:
                        self._hotkey_pause = hk
                    lbl_var.set(hk)
                    self._save_settings()
            # Always re-register (whether confirmed or cancelled). If
            # confirmed, the new combos take effect; if cancelled, the
            # previous combos come back.
            self._register_hotkey()
            cap.destroy()

        confirm_btn.configure(command=lambda: _cleanup(apply=True))
        st["hook"] = keyboard.hook(_on_key, suppress=False)
        cap.protocol("WM_DELETE_WINDOW", lambda: _cleanup())

    def _on_models_ready(self):
        if self._hotkey_register_error:
            bad = "/".join(self._hotkey_register_error)
            msg = (f"Ready — but hotkey '{bad}' is unavailable "
                   "(another app may have claimed it; change it in Settings)")
        else:
            msg = f"Ready  ({self._hotkey_trigger.upper()})"
        self.root.after(0, lambda m=msg: self.status_var.set(m))

    def _on_gpu_toggle(self):
        self._save_settings()
        self._reload_models()

    def _reload_models(self):
        global _ocr_reader, _tts_pipeline
        _ocr_reader = None
        _tts_pipeline = None
        tts_lbl = "GPU" if self._gpu_tts_var.get() else "CPU"
        ocr_lbl = "GPU" if self._gpu_ocr_var.get() else "CPU"
        self.status_var.set(
            f"Reloading models (TTS {tts_lbl}, OCR {ocr_lbl})…")
        threading.Thread(
            target=_load_models,
            args=(
                lambda msg: self.root.after(0, lambda m=msg: self.status_var.set(m)),
                self._on_models_ready,
                lambda exc: self.root.after(0, lambda e=exc: self.status_var.set(
                    f"Model load failed: {e}")),
            ),
            kwargs={"gpu_tts": self._gpu_tts_var.get(),
                    "gpu_ocr": self._gpu_ocr_var.get()},
            daemon=True,
        ).start()

    # ── Session control ───────────────────────────────────────────────────────

    def _trigger(self):
        if _ocr_reader is None or _tts_pipeline is None:
            return
        if self._play_state != "idle":
            return
        if self._scrolling_capture_var.get():
            # In-process scrolling capture: drag a region, then send
            # mouse-wheel events and stitch the frames ourselves.
            self.root.after(0, self._do_scrolling_capture)
        else:
            self.root.after(0, self._do_select)

    def _do_select(self):
        region = select_region(self.root)
        if not region:
            return
        self._begin_pipeline(region=region)

    # ── In-process scrolling capture ──────────────────────────────────────────

    def _do_scrolling_capture(self):
        """Drag-region first (same UX as the non-scrolling path), then run
        the in-process scroll-and-stitch capture in a background thread."""
        region = select_region(self.root)
        if not region:
            return
        # CRITICAL: clear stop_event before launching the worker. The
        # previous reader window's close handler set stop_event=True
        # via _stop(); without this clear, _capture_scrolling's first
        # loop iteration would immediately break out and return a
        # single-frame "capture" that looks indistinguishable from the
        # non-scrolling drag-region mode. _begin_pipeline() clears
        # stop_event too — but that runs *after* the capture, so it's
        # too late.
        self.stop_event.clear()
        self.status_var.set(
            "Scrolling capture starting — keep your cursor away from "
            "the target window.")
        threading.Thread(
            target=self._scrolling_capture_worker,
            args=(region,),
            daemon=True,
        ).start()

    def _scrolling_capture_worker(self, region: tuple):
        """Worker thread: run the wheel-and-stitch capture, then hand the
        stitched image off to the standard pipeline (with `region` so the
        reader window can align its top-left to the captured area —
        identical to drag-region positioning)."""
        try:
            def _status(msg: str):
                self.root.after(0, lambda m=msg: self.status_var.set(m))
            # Read the user-chosen max-frames cap fresh on each trigger so
            # the spinbox change takes effect immediately. Clamp to a sane
            # range in case someone edited the settings file by hand.
            try:
                max_frames = max(1, min(200, int(
                    self._scrolling_max_frames_var.get())))
            except (TypeError, ValueError, tk.TclError):
                max_frames = 40
            img = _capture_scrolling(region,
                                     stop_event=self.stop_event,
                                     status_cb=_status,
                                     max_frames=max_frames)
            self.root.after(0, lambda: self._begin_pipeline(
                image=img, region=region, scrolling=True))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.root.after(0, lambda e=exc: self.status_var.set(
                f"Scrolling capture failed: {e.__class__.__name__}: {e}"))

    def _do_paste(self, event=None):
        # If focus is in an Entry/Text field (e.g. settings dialog), let the
        # widget handle the paste normally instead of triggering OCR.
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, tk.Text, ttk.Entry)):
            return
        if _ocr_reader is None or _tts_pipeline is None:
            return
        if self._play_state != "idle":
            return
        try:
            clip = ImageGrab.grabclipboard()
        except Exception:
            clip = None
        if isinstance(clip, Image.Image):
            self._begin_pipeline(image=clip)
            return
        try:
            text = self.root.clipboard_get().strip()
        except Exception:
            text = ""
        if text:
            self._begin_pipeline(text=text)
            return
        self.status_var.set("Clipboard is empty (copy text or an image first)")

    def _begin_pipeline(self, *, region=None, image=None, text=None,
                        scrolling=False):
        self.stop_event.clear()
        self._play_event.clear()
        self._full_audio = None
        self._word_schedule = []
        self._word_bboxes_canvas = []
        self._extracted_text = None
        self._pause_pos = 0.0
        self._play_state = "generating"
        # OCR phase: we have no information to predict from, so the bar runs
        # in indeterminate mode (an animated bouncing thumb). When TTS starts
        # _enter_tts_phase swaps it to determinate and the actual chars-done
        # fraction drives the value.
        self._scan_start = time.monotonic()
        self._scan_base_msg = "Scanning…"
        self._scan_phase = "ocr"
        self._scan_actual_progress = 0.0
        self.status_var.set(self._scan_base_msg)
        if self._scan_progress_bar is not None:
            try: self._scan_progress_bar.stop()
            except tk.TclError: pass
            self._scan_progress_bar.config(mode="indeterminate")
            self._scan_progress_bar.pack(pady=(0, 8))
            try: self._scan_progress_bar.start(12)
            except tk.TclError: pass
        if not self._scan_tick_running:
            self._scan_tick_running = True
            self.root.after(100, self._tick_scan_timer)
        threading.Thread(
            target=self._generate,
            kwargs={"region": region, "image": image, "text": text,
                    "scrolling": scrolling},
            daemon=True).start()

    def _tick_scan_timer(self):
        """Update elapsed-time text and (during TTS) the determinate bar."""
        if self._play_state != "generating" or self._scan_start is None:
            self._scan_start = None
            self._tts_start  = None
            self._scan_observed_rate = None
            self._scan_tick_running = False
            self._scan_phase = ""
            if self._scan_progress_bar is not None:
                try: self._scan_progress_bar.stop()
                except tk.TclError: pass
                try: self._scan_progress_bar.pack_forget()
                except tk.TclError: pass
            if self._reader_progress_bar is not None:
                try: self._reader_progress_bar.pack_forget()
                except tk.TclError: pass
            return
        now = time.monotonic()
        elapsed = now - self._scan_start
        if (self._scan_phase == "tts" and self._tts_start is not None
                and self._scan_observed_rate is not None):
            # Smooth predicted progress between Kokoro segments using the
            # rate measured at the previous segment boundary. Never goes
            # backwards: the displayed value is max(measured, predicted).
            rate = self._scan_observed_rate                 # fraction / sec
            tts_elapsed = now - self._tts_start
            predicted   = min(0.99, rate * tts_elapsed)
            display     = max(self._scan_actual_progress, predicted)
            pct_val     = max(0.0, min(99.0, display * 100.0))
            ocr_elapsed = self._tts_start - self._scan_start
            # total_expected stays steady between segments because rate
            # doesn't change; the next segment refines it.
            total_s = ocr_elapsed + 1.0 / rate
            text = (f"{self._scan_base_msg}  "
                    f"({elapsed:.1f}s / ~{total_s:.1f}s — {pct_val:.0f}%)")
            if self._scan_progress_bar is not None:
                try: self._scan_progress_bar["value"] = pct_val
                except tk.TclError: pass
            if self._reader_progress_bar is not None:
                try: self._reader_progress_bar["value"] = pct_val
                except tk.TclError: pass
        else:
            text = f"{self._scan_base_msg}  ({elapsed:.1f}s)"
        self.status_var.set(text)
        if self._reader_status_var is not None:
            try: self._reader_status_var.set(text)
            except tk.TclError: pass
        self.root.after(100, self._tick_scan_timer)

    def _set_scan_status(self, msg: str):
        """Update the base status text; the timer adds the elapsed suffix."""
        self._scan_base_msg = msg

    def _enter_tts_phase(self):
        """OCR is done; flip the bar to determinate. The worker thread
        already stamped self._tts_start just before the Kokoro loop."""
        self._scan_phase = "tts"
        self._scan_actual_progress = 0.0
        self._scan_observed_rate   = None
        if self._scan_progress_bar is not None:
            try:
                self._scan_progress_bar.stop()
                self._scan_progress_bar.config(mode="determinate", maximum=100)
                self._scan_progress_bar["value"] = 0
            except tk.TclError: pass
        if self._reader_progress_bar is not None:
            try:
                self._reader_progress_bar.config(mode="determinate", maximum=100)
                self._reader_progress_bar["value"] = 0
            except tk.TclError: pass

    def _stop(self):
        # stop_event is also checked inside _capture_scrolling, so this
        # cleanly cancels an in-flight scrolling capture too.
        self.stop_event.set()
        self._play_event.clear()
        sd.stop()
        self._play_state = "idle"
        self._play_btn.configure(state="disabled", text="▶  Play")
        self.root.after(0, self._close_reader)
        self.root.after(0, lambda: self.status_var.set(
            "Stopped — Shift+Z to read again"))

    def _abort_generation(self, message: str):
        """Return to idle when the pipeline aborts before any audio is
        produced (empty OCR, empty text, error). Without this, _play_state
        would stay 'generating' forever and Shift+Z would silently no-op."""
        self._play_state = "idle"
        self.status_var.set(message)

    # ── Generation ────────────────────────────────────────────────────────────

    def _generate(self, *, region=None, image=None, text=None,
                  scrolling=False):
        try:
            # Pronunciation overrides (normalized key → spoken replacement).
            # Applied to the spoken text in both input branches below; the
            # displayed/highlighted words are never altered.
            omap = _override_map(self._pron_overrides)
            if text is not None:
                tokens = [t for t in text.split() if t]
                if not tokens:
                    self.root.after(0, self._abort_generation, "No text to read")
                    return
                tts_text = _tts_safe(_apply_overrides(tokens, omap))
                ocr_words = [w for w in tokens if any(c.isalnum() for c in w)]
                if not ocr_words:
                    self.root.after(0, self._abort_generation, "No readable text")
                    return
                pil_img, disp_bboxes = _make_text_image(ocr_words)
            else:
                if image is None:
                    image = ImageGrab.grab(bbox=region, all_screens=True)
                img_array = np.array(image)
                # Chunked OCR at native resolution. The previous version
                # proportionally rescaled tall captures to ≤6000 px tall,
                # which halved character pixel height on a 1200×12000
                # stitched scroll capture and wrecked OCR accuracy on
                # small body text — and made the reader window narrower
                # than the user's drag region too, since we then handed
                # the resized image to _show_reader. _ocr_image_chunked
                # vertically splits the image (with overlap) and OCRs
                # each chunk at full resolution, then merges with
                # bbox-y offsets in original-image coordinates.
                word_data = _ocr_image_chunked(image)

                if not word_data:
                    self.root.after(0, self._abort_generation, "No text detected")
                    return

                # TTS text: all tokens (punctuation attached to preceding words by
                # _tts_safe so Kokoro gets "Hello, world." not "Hello , world .").
                all_ocr_words = [w for w, _ in word_data]
                tts_text = _tts_safe(_apply_overrides(all_ocr_words, omap))

                # Alignment/highlighting: only tokens with alphanumeric content.
                # Pure-punctuation tokens have no bbox worth highlighting.
                word_data  = [(w, b) for w, b in word_data
                              if any(c.isalnum() for c in w)]

                ocr_words  = [w for w, _ in word_data]
                img_bboxes = [b for _, b in word_data]

                # Show the canvas immediately after OCR so the user can see the image
                if self._text_view_var.get():
                    pil_img, disp_bboxes = _make_text_image(ocr_words)
                else:
                    pil_img      = Image.fromarray(img_array)
                    disp_bboxes  = img_bboxes
            self._extracted_text = tts_text
            # OCR done — flip the progress bar from indeterminate animation
            # to determinate mode. Stamp tts_start here on the worker thread
            # (rather than from _enter_tts_phase, which runs later on the Tk
            # thread) so the tick handler sees the real TTS start instant.
            self._tts_start = time.monotonic()
            self.root.after(0, self._enter_tts_phase)
            if self._highlight_mode.get() == "auto":
                arr = np.array(pil_img)
                bg_hex, text_hex = _detect_image_colors(arr, disp_bboxes)
                self.root.after(0, self._apply_highlight_color,
                                optimal_highlight(bg_hex, text_hex))
            self.root.after(0, self._show_reader, pil_img, disp_bboxes,
                            region, scrolling)
            self.root.after(0, self._set_scan_status, "Generating speech…")

            # Content-based alignment structures: match TTS words to OCR words
            # by normalised text rather than by position.  This handles the case
            # where Kokoro defers a word (e.g. "from" near "PubMed®") to a late
            # segment — the word still gets mapped to its correct OCR index and
            # highlighted at the actual audio timestamp.
            ocr_norms  = [_norm(w) for w in ocr_words]
            ocr_lookup = {}                          # norm → [ocr_indices …]
            for _i, _nn in enumerate(ocr_norms):
                if _nn:
                    ocr_lookup.setdefault(_nn, []).append(_i)
            # Pronunciation overrides change what Kokoro SAYS, so the spoken
            # words won't content-match their original OCR word. Register
            # each replacement word's norm against the OCR index it came
            # from, so highlighting still lands on the right word. For a
            # multi-word expansion ("API" → "A P I") the first spoken word
            # schedules the highlight; the rest are recorded here as
            # `override_expansion_norms` so the alignment loop can skip them
            # (keeping the original word highlighted) instead of drifting
            # the positional cursor onto the next OCR word.
            override_expansion_norms: set = set()
            if omap:
                for _i, _key in enumerate(ocr_norms):
                    _repl = omap.get(_key)
                    if not _repl:
                        continue
                    # Register ONLY the first replacement word against this
                    # OCR index. The content matcher then maps the spoken
                    # expansion's first word to the right occurrence (and,
                    # via the used-set, the next occurrence of the same
                    # override word to ITS index). Registering every
                    # replacement word would let the extra words of one
                    # expansion wrongly grab a *later* occurrence's index.
                    # All replacement words still go in the skip-set so the
                    # non-first ones are passed over (keeping the original
                    # word highlighted) rather than positional-drifting.
                    _rwords = _repl.split()
                    for _j, _rw in enumerate(_rwords):
                        _rn = _norm(_rw)
                        if not _rn:
                            continue
                        override_expansion_norms.add(_rn)
                        if _j == 0:
                            ocr_lookup.setdefault(_rn, []).append(_i)
            used_ocr = set()   # OCR indices already scheduled
            seq_oi   = 0       # sequential cursor: next expected OCR position

            audio_chunks: list = []
            schedule: list[tuple[int, float]] = []
            running = 0.0
            # Total characters in the TTS input. Each Kokoro segment yields
            # a `graphemes` substring; their cumulative length over total
            # gives the real fraction completed — no estimation needed.
            total_chars = max(1, len(tts_text))
            chars_done = 0

            for result in _tts_pipeline(tts_text, voice=self._voice_id):
                if self.stop_event.is_set():
                    return

                if isinstance(result, tuple):
                    graphemes, _, audio = result
                    tokens = []
                else:
                    audio     = getattr(result, "audio", None)
                    graphemes = getattr(result, "graphemes", "")
                    tokens    = getattr(result, "tokens", None) or []
                    if audio is None:
                        graphemes, _, audio = result

                duration  = len(audio) / 24000.0
                seg_text  = graphemes if isinstance(graphemes, str) else str(graphemes)
                seg_words = seg_text.split()

                if seg_words:
                    starts   = _word_starts(tokens, seg_words, duration)
                    local_oi = seq_oi
                    hi_water = seq_oi - 1

                    for si, sw in enumerate(seg_words):
                        sn = _norm(sw)
                        if not sn:
                            continue          # skip punctuation-only TTS tokens

                        # Find the best unscheduled OCR word that matches this
                        # TTS word by content.
                        cands = [i for i in ocr_lookup.get(sn, [])
                                 if i not in used_ocr]
                        if cands:
                            # Prefer the earliest candidate at/after seq_oi
                            # (normal forward flow); fall back to any candidate
                            # globally (handles deferred/late TTS segments).
                            fwd = [i for i in cands if i >= seq_oi]
                            tgt = fwd[0] if fwd else cands[0]
                        elif sn in override_expansion_norms:
                            # Extra word of a multi-word pronunciation
                            # expansion whose target OCR word was already
                            # scheduled by an earlier word of the same
                            # expansion. Skip it: the original word stays
                            # highlighted (the highlight persists until the
                            # next scheduled word) and we avoid drifting the
                            # positional cursor onto the following word.
                            continue
                        else:
                            # No content match — positional fallback
                            while (local_oi < len(ocr_words) and
                                   (not ocr_norms[local_oi] or
                                    local_oi in used_ocr)):
                                local_oi += 1
                            tgt = local_oi if local_oi < len(ocr_words) else None

                        if tgt is not None and si < len(starts):
                            schedule.append((tgt, running + starts[si]))
                            used_ocr.add(tgt)
                            hi_water = max(hi_water, tgt)
                            if tgt >= local_oi:
                                local_oi = tgt + 1

                    seq_oi = max(seq_oi, hi_water + 1)

                audio_chunks.append(audio)
                running += duration
                # Real progress: how much of the input text Kokoro has voiced.
                chars_done += len(seg_text)
                progress_now = min(1.0, chars_done / total_chars)
                # Recompute the observed rate so the tick handler can
                # interpolate progress (and ETA) smoothly until the next
                # segment lands.
                if self._tts_start is not None:
                    tts_elapsed_now = time.monotonic() - self._tts_start
                    if tts_elapsed_now > 0:
                        self._scan_observed_rate = progress_now / tts_elapsed_now
                self._scan_actual_progress = progress_now

            if self.stop_event.is_set() or not audio_chunks:
                return

            self._full_audio    = np.concatenate(audio_chunks)
            self._word_schedule = schedule
            self.root.after(0, lambda: self._set_play_state("ready"))

        except Exception as exc:
            # Keep the raw exception out of the status line — show a short
            # friendly message; the full traceback is printed for debug.bat.
            import traceback
            traceback.print_exc()
            short = f"{exc.__class__.__name__}: {exc}"
            if len(short) > 80:
                short = short[:77] + "…"
            self.root.after(0, self._abort_generation, f"Error — {short}")

    # ── Playback ──────────────────────────────────────────────────────────────

    def _on_play_btn(self):
        state = self._play_state
        if state in ("ready", "idle") and self._full_audio is not None:
            # In "idle" after a seek/skip/word-click, continue from the user's
            # new cursor position instead of restarting from the beginning.
            if not (state == "idle" and self._idle_seeked):
                self._pause_pos = 0.0
            self._idle_seeked = False
            self.stop_event.clear()
            self._set_play_state("playing")
            self._play_event.set()
            threading.Thread(target=self._playback_thread, daemon=True).start()
        elif state == "playing":
            self._set_play_state("paused")
            self._play_event.clear()
        elif state == "paused":
            self._set_play_state("playing")
            self._play_event.set()

    def _playback_thread(self):
        # Drain any stale seek signal from a previous run
        self._seek_event.clear()
        start_pos = self._pause_pos

        while True:
            audio = self._full_audio
            if audio is None:
                return
            if start_pos >= len(audio) / 24000.0 - 0.05:
                self.root.after(0, self._clear_highlight)
                self.root.after(0, lambda: self._set_play_state("idle"))
                return

            speed = max(0.1, self._speed_var.get())
            sd.play(audio[int(start_pos * 24000):],
                    samplerate=int(24000 * speed))
            t0 = time.monotonic()
            last_ui = t0 - 1.0   # force immediate first timeline update

            pending = [(wi, t) for wi, t in self._word_schedule
                       if t >= start_pos - 0.01]

            paused_at: float | None = None
            seek_to:   float | None = None
            for word_idx, abs_start in pending:
                if self.stop_event.is_set():
                    sd.stop()
                    return

                # Convert audio-time gap to real-time wait
                rel = (abs_start - start_pos) / speed
                while True:
                    now = time.monotonic()
                    remaining = rel - (now - t0)
                    if remaining <= 0.001:
                        break
                    chunk = min(remaining, 0.02)
                    if self.stop_event.wait(chunk):
                        sd.stop()
                        return
                    # IMPORTANT: check seek_event BEFORE play_event so a click-
                    # to-seek can't be misread as a pause.
                    if self._seek_event.is_set():
                        self._seek_event.clear()
                        seek_to = self._pause_pos
                        sd.stop()
                        break
                    if not self._play_event.is_set():
                        paused_at = start_pos + (time.monotonic() - t0) * speed
                        sd.stop()
                        break
                    if now - last_ui >= 0.1:
                        cur = start_pos + (now - t0) * speed
                        self.root.after(0, self._update_timeline, cur)
                        last_ui = now

                if seek_to is not None or paused_at is not None:
                    break
                if self.stop_event.is_set():
                    sd.stop()
                    return

                self.root.after(0, self._highlight_word, word_idx)

            if seek_to is not None:
                start_pos = seek_to
                continue

            if paused_at is not None:
                self._pause_pos = paused_at
                self.root.after(0, self._update_timeline, paused_at)
                while not self._play_event.wait(0.05):
                    if self.stop_event.is_set():
                        return
                    # While paused, a click-to-seek goes through the "paused"
                    # branch of _seek_to_time which just updates _pause_pos
                    # and sets _play_event — no special handling needed here.
                start_pos = self._pause_pos
                continue

            # Tail: keep timeline moving until audio finishes
            audio_end = len(audio) / 24000.0
            while not self.stop_event.is_set():
                cur = start_pos + (time.monotonic() - t0) * speed
                self.root.after(0, self._update_timeline, min(cur, audio_end))
                if cur >= audio_end - 0.05:
                    break
                if self.stop_event.wait(0.1):
                    sd.stop()
                    return

            if not self.stop_event.is_set():
                sd.wait()
            if not self.stop_event.is_set():
                self.root.after(0, self._update_timeline, audio_end)
                self.root.after(0, self._clear_highlight)
                self.root.after(0, lambda: self._set_play_state("idle"))
            return

    # ── Reader window ─────────────────────────────────────────────────────────

    def _show_reader(self, pil_img: Image.Image, img_bboxes: list,
                     region: tuple | None = None,
                     scrolling: bool = False):
        if self.stop_event.is_set():
            return
        self._close_reader()

        disp_w   = max(1, pil_img.width)
        disp_h   = max(1, pil_img.height)
        disp_img = pil_img.copy()
        scale    = 1.0

        # Scale bboxes to canvas coordinates
        self._word_bboxes_canvas = [
            (int(x1 * scale), int(y1 * scale),
             int(x2 * scale), int(y2 * scale))
            for x1, y1, x2, y2 in img_bboxes
        ]

        win = tk.Toplevel(self.root)
        win.title("Reader")
        win.attributes("-topmost", True)
        win.resizable(True, True)

        # Two layout modes:
        #   scrolling=True  → in-process scrolling captures (the stitched
        #                     image is often much taller than the screen).
        #                     Window aligned to the captured region's
        #                     top-left, height capped to fit on-screen,
        #                     canvas placed in a vertically scrollable frame.
        #   scrolling=False → drag-region / paste captures. Original layout:
        #                     canvas at natural image size, no scrollbars;
        #                     drag-region windows align to the captured
        #                     region's origin, paste windows center on screen.
        view_w = disp_w
        view_h = disp_h
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        if scrolling:
            bottom_bar_h  = 115     # ~3 rows of controls
            scrollbar_pad = 18      # tk Scrollbar thickness
            # Window dimensions mirror drag-region (non-scrolling) mode:
            # width = disp_w, top-left = region origin. The vertical
            # scrollbar lives inside that width so the canvas viewport
            # is ~18 px narrower; the rightmost strip of the image sits
            # behind the scrollbar (typically trailing whitespace).
            # Height: as much vertical space as is available below the
            # region origin, capped so the bottom controls stay visible.
            view_w = max(1, disp_w - scrollbar_pad)
            if region:
                available_h = max(200, screen_h - region[1] - 40)
                total_h_preview = min(disp_h + bottom_bar_h, available_h)
            else:
                total_h_preview = min(disp_h + bottom_bar_h,
                                      int(screen_h * 0.80))
            view_h = max(1, total_h_preview - bottom_bar_h)

            # Bottom bar packs FIRST with side=BOTTOM so it claims a fixed
            # slot; the scrollable canvas frame fills everything above it.
            bar = ttk.Frame(win)
            bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(4, 4))

            # Scrollable canvas: canvas + vbar only. No horizontal
            # scrollbar (we set viewport width = image width).
            canvas_frame = ttk.Frame(win)
            canvas_frame.pack(fill=tk.BOTH, expand=True)

            cv = tk.Canvas(canvas_frame, width=view_w, height=view_h,
                           highlightthickness=0, bg="#1a1a1a",
                           scrollregion=(0, 0, disp_w, disp_h))
            vbar = ttk.Scrollbar(canvas_frame, orient="vertical",
                                 command=cv.yview)
            cv.configure(yscrollcommand=vbar.set)
            cv.grid(row=0, column=0, sticky="nsew")
            vbar.grid(row=0, column=1, sticky="ns")
            canvas_frame.grid_rowconfigure(0, weight=1)
            canvas_frame.grid_columnconfigure(0, weight=1)

            # Mouse-wheel scrolls vertically. Tk uses event.delta in
            # multiples of 120 on Windows.
            cv.bind("<MouseWheel>",
                    lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))
        else:
            # Original drag-region / paste layout: canvas directly in the
            # window at natural image size, no scrollbars, no scrollregion.
            cv = tk.Canvas(win, width=disp_w, height=disp_h,
                           highlightthickness=0, bg="#1a1a1a")
            cv.pack(fill=tk.BOTH, expand=True)
            bar = ttk.Frame(win)
            bar.pack(fill=tk.X, padx=10, pady=(4, 4))

        cv.bind("<Button-1>", self._on_word_click)
        cv.bind("<Motion>",   self._on_canvas_motion)

        tk_img = ImageTk.PhotoImage(disp_img)
        img_id = cv.create_image(0, 0, image=tk_img, anchor="nw")
        cv.tk_img = tk_img

        self._reader_base_img   = disp_img.copy()
        self._reader_canvas_img = img_id

        # ── bottom bar (already created above; populated below) ──────

        # Row 1: controls + status
        ctrl = ttk.Frame(bar)
        ctrl.pack(fill=tk.X)

        play_btn = ttk.Button(ctrl, text="▶  Play", command=self._on_play_btn,
                              width=14, state="disabled")
        play_btn.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl, text="■  Stop", command=self._stop,
                   width=8).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl, text="💾  Export", command=self._export_audio,
                   width=10).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(ctrl, text="📋  Copy", command=self._copy_text,
                   width=9).pack(side=tk.LEFT, padx=(0, 8))

        # Auto-scroll toggle — only meaningful in the scrolling reader
        # (the one with a scrollable canvas), so only shown there. When
        # ticked, the canvas follows the highlighted word as TTS reads.
        if scrolling:
            ttk.Checkbutton(
                ctrl, text="Auto-scroll",
                variable=self._autoscroll_var,
                command=self._save_settings).pack(side=tk.LEFT, padx=(0, 8))

        sv = tk.StringVar(value="Generating speech…")
        ttk.Label(ctrl, textvariable=sv, foreground="gray").pack(side=tk.LEFT)

        # Progress bar — visible only during the generation phase. Packed
        # on the right so it doesn't push the status label around.
        self._reader_progress_bar = ttk.Progressbar(
            ctrl, orient=tk.HORIZONTAL, mode="determinate",
            length=140, maximum=100)
        if self._play_state == "generating":
            self._reader_progress_bar.pack(side=tk.RIGHT, padx=(8, 0))

        # Row 2: timeline scrubber + skip buttons
        trow = ttk.Frame(bar)
        trow.pack(fill=tk.X, pady=(4, 0))

        self._timeline_var.set(0.0)
        scl = ttk.Scale(trow, from_=0.0, to=1.0, orient=tk.HORIZONTAL,
                        variable=self._timeline_var)
        scl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        scl.bind("<ButtonPress-1>",   self._on_seek_start)
        scl.bind("<B1-Motion>",       self._on_seek_drag)
        scl.bind("<ButtonRelease-1>", self._on_seek_end)

        tlbl = tk.StringVar(value="0:00 / 0:00")
        ttk.Label(trow, textvariable=tlbl, width=11,
                  anchor="e").pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(trow, text="5s →", width=6,
                   command=lambda: self._skip(5)).pack(side=tk.RIGHT, padx=(2, 0))
        ttk.Button(trow, text="← 5s", width=6,
                   command=lambda: self._skip(-5)).pack(side=tk.RIGHT, padx=(2, 0))

        # Row 3: speed control
        srow = ttk.Frame(bar)
        srow.pack(fill=tk.X, pady=(4, 0))

        ttk.Label(srow, text="Speed:").pack(side=tk.LEFT)
        spb = ttk.Spinbox(srow, from_=0.5, to=2.0, increment=0.1,
                          width=5, format="%.1f")
        spb.set(f"{self._speed_var.get():.1f}")
        spb.pack(side=tk.LEFT, padx=(6, 0))
        spb.bind("<<Increment>>", lambda _: self._on_speed_change())
        spb.bind("<<Decrement>>", lambda _: self._on_speed_change())
        spb.bind("<Return>",      lambda _: self._on_speed_change())
        spb.bind("<FocusOut>",    lambda _: self._on_speed_change())

        self._play_btn        = play_btn
        self._timeline_scale  = scl
        self._time_label_var  = tlbl
        self._speed_label_var = None
        self._speed_spinbox   = spb

        # Keyboard shortcuts for the reader window.
        # Bind on win so they fire whenever focus is anywhere inside it.
        # Also override the Scale's built-in Left/Right handling so arrows
        # skip instead of nudging the slider thumb.
        win.bind("<Left>",  lambda _: self._skip(-5))
        win.bind("<Right>", lambda _: self._skip(5))
        win.bind("<space>", lambda e: None
                  if e.widget.winfo_class() in ("TButton", "Button") else
                  self._on_play_btn())
        scl.bind("<Left>",  lambda _: self._skip(-5) or "break")
        scl.bind("<Right>", lambda _: self._skip(5)  or "break")
        # Give the canvas default focus so shortcuts work immediately on open
        cv.focus_set()

        if scrolling:
            # Same algorithm as drag-region (non-scrolling) — top-left
            # aligned to the captured region's origin, with the
            # window-frame border offsets corrected so the canvas
            # content sits exactly where the user dragged. Height is
            # capped so the bottom controls stay on-screen even if the
            # stitched capture is much taller than the original region.
            total_w = disp_w
            if region:
                wx, wy = region[0], region[1]
                # Available vertical space below wy, with a small margin
                # for the taskbar. Falls back to centered if there's
                # almost no room below the region origin.
                available_h = max(200, screen_h - wy - 40)
                total_h = min(disp_h + 115, available_h)
                win.geometry(f"{total_w}x{total_h}+{wx}+{wy}")
                win.update_idletasks()
                off_x = win.winfo_rootx() - win.winfo_x()
                off_y = win.winfo_rooty() - win.winfo_y()
                win.geometry(f"{total_w}x{total_h}+{wx - off_x}+{wy - off_y}")
            else:
                # Paste-image-into-scrolling-mode fallback (shouldn't
                # happen with in-process capture but kept for safety).
                total_h = min(disp_h + 115, int(screen_h * 0.80))
                cx = max(0, (screen_w - disp_w) // 2)
                cy = max(0, (screen_h - total_h) // 2)
                win.geometry(f"{total_w}x{total_h}+{cx}+{cy}")
        else:
            # Original behavior for drag-region / paste:
            # - region != None  → align canvas content with the captured
            #                     region's top-left (so it visually replaces
            #                     the original text in place)
            # - region == None  → center the natural-size window on screen
            total_h = disp_h + 115
            if region:
                wx, wy = region[0], region[1]
                win.geometry(f"{disp_w}x{total_h}+{wx}+{wy}")
                win.update_idletasks()
                off_x = win.winfo_rootx() - win.winfo_x()
                off_y = win.winfo_rooty() - win.winfo_y()
                win.geometry(f"{disp_w}x{total_h}+{wx - off_x}+{wy - off_y}")
            else:
                win.update_idletasks()
                cx = max(0, (screen_w - disp_w) // 2)
                cy = max(0, (screen_h - total_h) // 2)
                win.geometry(f"{disp_w}x{total_h}+{cx}+{cy}")
        win.protocol("WM_DELETE_WINDOW", self._stop)

        self._reader_win        = win
        self._reader_canvas     = cv
        self._reader_status_var = sv

    def _set_play_state(self, state: str):
        if self.stop_event.is_set() and state != "idle":
            return
        self._play_state = state
        sv = self._reader_status_var
        pb = self._play_btn

        if state == "generating":
            if sv: sv.set("Generating audio…")
            if pb: pb.configure(state="disabled", text="▶  Play")
            self.status_var.set("Generating audio…")
        elif state == "ready":
            if sv: sv.set("Ready — press Play")
            if pb: pb.configure(state="normal", text="▶  Play")
            self.status_var.set("Ready to play")
            if self._full_audio is not None:
                total = len(self._full_audio) / 24000.0
                self._timeline_max = total
                if self._timeline_scale:
                    self._timeline_scale.configure(to=total)
                if self._time_label_var:
                    self._time_label_var.set(f"0:00 / {_fmt(total)}")
        elif state == "playing":
            if sv: sv.set("Playing…")
            if pb: pb.configure(state="normal", text="⏸  Pause")
            self.status_var.set("Playing…")
        elif state == "paused":
            if sv: sv.set("Paused")
            if pb: pb.configure(state="normal", text="▶  Resume")
            self.status_var.set("Paused")
        elif state == "idle":
            self._idle_seeked = False
            if self._full_audio is not None:
                if sv: sv.set("Done — press Play Again to replay")
                if pb: pb.configure(state="normal", text="▶  Play Again")
                self.status_var.set("Done")
            else:
                if sv: sv.set("Done")
                if pb: pb.configure(state="disabled", text="▶  Play")
                self.status_var.set("Done — Shift+Z to read again")

    def _update_timeline(self, pos: float):
        if self._user_seeking:
            return
        pos = max(0.0, min(pos, self._timeline_max))
        self._timeline_var.set(pos)
        if self._time_label_var and self._timeline_max > 0:
            self._time_label_var.set(f"{_fmt(pos)} / {_fmt(self._timeline_max)}")

    def _seek_set(self, e):
        """Compute position from a Scale mouse event and update the display."""
        frac = max(0.0, min(1.0, e.x / max(1, e.widget.winfo_width())))
        pos  = frac * self._timeline_max
        self._timeline_var.set(pos)
        if self._time_label_var and self._timeline_max > 0:
            self._time_label_var.set(f"{_fmt(pos)} / {_fmt(self._timeline_max)}")
        return pos

    def _on_seek_start(self, e):
        self._user_seeking = True
        self._seek_set(e)
        if self._play_state == "playing":
            self._play_event.clear()
            sd.stop()
        return "break"   # prevent Scale's own thumb-movement logic

    def _on_seek_drag(self, e):
        if self._user_seeking:
            self._seek_set(e)
        return "break"

    def _on_seek_end(self, _):
        pos = max(0.0, min(self._timeline_var.get(),
                           self._timeline_max - 0.05))
        self._timeline_var.set(pos)
        self._pause_pos    = pos
        self._user_seeking = False
        if self._time_label_var and self._timeline_max > 0:
            self._time_label_var.set(f"{_fmt(pos)} / {_fmt(self._timeline_max)}")
        if self._play_state == "playing":
            self._play_event.set()
        elif self._play_state in ("paused", "ready", "idle"):
            self._highlight_at_time(pos)
            self._mark_idle_seeked()

    def _mark_idle_seeked(self):
        """If the user moves the cursor after playback has ended, switch the
        Play button to 'Resume' so the next click continues from the cursor
        rather than restarting from the beginning."""
        if self._play_state == "idle":
            self._idle_seeked = True
            if self._play_btn:
                self._play_btn.configure(text="▶  Resume")

    def _highlight_at_time(self, pos: float):
        """Highlight the word that would be active at the given position."""
        schedule = sorted(self._word_schedule, key=lambda x: x[1])
        active = None
        for word_idx, start_t in schedule:
            if start_t <= pos + 0.01:
                active = word_idx
            else:
                break
        if active is not None:
            self._highlight_word(active)
        else:
            self._clear_highlight()

    def _skip(self, delta: float):
        """Jump forward (delta > 0) or backward (delta < 0) by delta seconds."""
        if self._full_audio is None or self._play_state == "generating":
            return
        pos = max(0.0, min(self._timeline_var.get() + delta,
                           self._timeline_max - 0.05))
        was_playing = self._play_state == "playing"
        # Reuse the seek path so we share its race-free seek_event handling.
        self._seek_to_time(pos, auto_play=was_playing)
        if not was_playing:
            self._mark_idle_seeked()

    def _seek_to_time(self, target_time: float, auto_play: bool = True):
        """Seek audio to target_time (seconds). Resumes playback unless
        auto_play is False and the audio is currently paused/idle."""
        if self._full_audio is None or self._play_state == "generating":
            return
        pos = max(0.0, min(target_time, self._timeline_max - 0.05))
        state = self._play_state
        if state == "playing":
            # Set the new position FIRST, then signal the worker via seek_event,
            # then stop the current sd.play(). The worker checks seek_event
            # before pause-detection, so it can't race-condition itself into
            # "I was paused" and overwrite our new _pause_pos.
            self._pause_pos = pos
            self._update_timeline(pos)
            self._seek_event.set()
            sd.stop()
        elif state == "paused":
            self._pause_pos = pos
            self._update_timeline(pos)
            if auto_play:
                self._set_play_state("playing")
                self._play_event.set()
            else:
                self._highlight_at_time(pos)
        elif state in ("ready", "idle"):
            self._pause_pos = pos
            self._update_timeline(pos)
            if auto_play:
                self.stop_event.clear()
                self._set_play_state("playing")
                self._play_event.set()
                threading.Thread(target=self._playback_thread, daemon=True).start()
            else:
                self._highlight_at_time(pos)

    def _word_at_canvas_xy(self, cx: float, cy: float) -> int | None:
        """Return the index of the word whose bbox contains (cx, cy), or None."""
        for i, (x1, y1, x2, y2) in enumerate(self._word_bboxes_canvas):
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return i
        return None

    def _on_word_click(self, event):
        """Click a word -> seek audio to that word's spoken timestamp."""
        if self._full_audio is None or self._play_state == "generating":
            return
        if not self._word_bboxes_canvas or not self._word_schedule:
            return
        cv = self._reader_canvas
        if cv is None:
            return
        idx = self._word_at_canvas_xy(cv.canvasx(event.x), cv.canvasy(event.y))
        if idx is None:
            return
        # Look up the scheduled time for the clicked word; if it has none
        # (rare — Kokoro sometimes drops a token), fall back to the latest
        # earlier word that does have a time.
        start_time = next((t for wi, t in self._word_schedule if wi == idx), None)
        if start_time is None:
            earlier = [(wi, t) for wi, t in self._word_schedule if wi < idx]
            start_time = max(earlier, key=lambda x: x[0])[1] if earlier else 0.0
        self._seek_to_time(start_time, auto_play=True)

    def _on_canvas_motion(self, event):
        """Show a hand cursor when hovering over a clickable word."""
        cv = self._reader_canvas
        if cv is None or not self._word_bboxes_canvas:
            return
        on_word = self._word_at_canvas_xy(
            cv.canvasx(event.x), cv.canvasy(event.y)) is not None
        cv.configure(cursor="hand2" if on_word else "")

    def _draw_highlight(self, base: Image.Image, bbox: tuple) -> ImageTk.PhotoImage:
        x1, y1, x2, y2 = bbox
        hc = self._highlight_color
        hr, hg, hb = int(hc[1:3], 16), int(hc[3:5], 16), int(hc[5:7], 16)
        pad = 3
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(
            [x1 - pad, y1 - pad, x2 + pad, y2 + pad],
            fill=(hr, hg, hb, 140),   # ~55 % opacity — real translucent marker
        )
        composited = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
        return ImageTk.PhotoImage(composited)

    def _highlight_word(self, idx: int):
        cv   = self._reader_canvas
        base = self._reader_base_img
        iid  = self._reader_canvas_img
        if cv is None or base is None or iid is None:
            return
        try:
            if not cv.winfo_exists():
                return
            bboxes = self._word_bboxes_canvas
            if idx >= len(bboxes):
                return
            tk_img = self._draw_highlight(base, bboxes[idx])
            cv.itemconfig(iid, image=tk_img)
            cv.tk_img = tk_img
            # Keep the active word in view when auto-scroll is enabled.
            # No-op on the non-scrolling reader (no scrollregion set).
            if self._autoscroll_var.get():
                self._scroll_into_view(bboxes[idx])
        except tk.TclError:
            pass

    def _scroll_into_view(self, bbox: tuple):
        """If the given image-coord bbox isn't fully visible in the reader
        canvas's current viewport, scroll the canvas vertically (and
        horizontally if needed) to bring it back into view. No-op when
        the image is smaller than the viewport in that dimension."""
        cv = self._reader_canvas
        if cv is None:
            return
        try:
            sr = cv.cget("scrollregion")
        except tk.TclError:
            return
        if not sr:
            return
        parts = str(sr).split()
        if len(parts) < 4:
            return
        try:
            sr_w = float(parts[2])
            sr_h = float(parts[3])
        except ValueError:
            return
        view_w = cv.winfo_width()
        view_h = cv.winfo_height()
        x1, y1, x2, y2 = bbox
        # Vertical: scroll only when content overflows the viewport.
        if view_h > 0 and sr_h > view_h:
            try:
                yfrac_top, yfrac_bot = cv.yview()
            except tk.TclError:
                yfrac_top, yfrac_bot = 0.0, 1.0
            visible_top = yfrac_top * sr_h
            visible_bot = yfrac_bot * sr_h
            if y1 < visible_top or y2 > visible_bot:
                # Park the word at about a third from the top — feels more
                # natural while reading than pinning to the very top edge.
                target = max(0.0, y1 - view_h / 3.0)
                try: cv.yview_moveto(target / sr_h)
                except tk.TclError: pass
        # Horizontal: same logic, rarely needed for screen text but handy
        # for very wide captures.
        if view_w > 0 and sr_w > view_w:
            try:
                xfrac_left, _xfrac_right = cv.xview()
            except tk.TclError:
                xfrac_left = 0.0
            visible_left = xfrac_left * sr_w
            visible_right = visible_left + view_w
            if x1 < visible_left or x2 > visible_right:
                target = max(0.0, x1 - view_w / 3.0)
                try: cv.xview_moveto(target / sr_w)
                except tk.TclError: pass

    def _clear_highlight(self):
        cv   = self._reader_canvas
        base = self._reader_base_img
        iid  = self._reader_canvas_img
        if cv is None or base is None or iid is None:
            return
        try:
            if not cv.winfo_exists():
                return
            tk_img = ImageTk.PhotoImage(base)
            cv.itemconfig(iid, image=tk_img)
            cv.tk_img = tk_img
        except tk.TclError:
            pass

    def _export_audio(self):
        if self._full_audio is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")],
            title="Save audio",
            parent=self._reader_win or self.root,
        )
        if not path:
            return
        pcm = (self._full_audio * 32767).clip(-32768, 32767).astype(np.int16)
        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm.tobytes())

    def _copy_text(self):
        """Copy the extracted text to the system clipboard."""
        if not self._extracted_text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._extracted_text)
            self.root.update()   # ensure the clipboard persists after window destroy
        except Exception:
            return
        if self._reader_status_var:
            self._reader_status_var.set("Copied to clipboard")

    def _close_reader(self):
        if self._reader_win is not None:
            try:
                self._reader_win.destroy()
            except Exception:
                pass
        self._reader_win        = None
        self._reader_canvas     = None
        self._reader_canvas_img = None
        self._reader_base_img   = None
        self._reader_status_var = None
        self._play_btn          = None
        self._timeline_scale    = None
        self._time_label_var    = None
        self._speed_label_var   = None
        self._speed_spinbox     = None

    def _on_speed_change(self):
        spb = self._speed_spinbox
        if spb is None:
            return
        try:
            spd = round(float(spb.get()), 1)
            spd = max(0.5, min(2.0, spd))
        except ValueError:
            spd = self._speed_var.get()
        self._speed_var.set(spd)
        spb.set(f"{spd:.1f}")
        self._save_settings()
        if self._play_state == "playing":
            self._pause_pos = self._timeline_var.get()
            self._play_event.clear()
            sd.stop()
            self._play_event.set()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
