import colorsys
import ctypes
import json
import os
import threading
import time
import tkinter as tk
import unicodedata
import wave
from tkinter import ttk, colorchooser, filedialog

import numpy as np
from paddleocr import PaddleOCR
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

_ocr_reader: PaddleOCR | None = None
_tts_pipeline: KPipeline | None = None


def _paddle_ocr(img_array: np.ndarray) -> list[tuple[str, tuple]]:
    """Run PaddleOCR (PP-OCRv5 mobile EN) on a numpy image array.
    Returns [(word, (x1, y1, x2, y2)), ...] in image pixel coords.

    PaddleOCR's recognition is line-level, so each line bbox is split into
    word bboxes proportionally by character count. _tighten_x_bbox later
    snaps each word bbox to its actual ink columns.
    """
    if _ocr_reader is None:
        return []
    result = _ocr_reader.predict(img_array)
    if not result:
        return []
    res = result[0]

    texts: list[str] = list(res.get("rec_texts", []) or [])
    boxes_raw       = res.get("rec_boxes", None)
    if boxes_raw is None or len(texts) == 0:
        return []
    boxes = boxes_raw.tolist() if hasattr(boxes_raw, "tolist") else list(boxes_raw)

    out: list[tuple[str, tuple]] = []
    for line_text, box in zip(texts, boxes):
        if not line_text or not line_text.strip():
            continue
        try:
            x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        except (TypeError, ValueError, IndexError):
            continue
        words = line_text.strip().split()
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


def _load_models(on_status: callable, on_done: callable, on_error: callable,
                 gpu: bool = False) -> None:
    global _ocr_reader, _tts_pipeline
    try:
        use_cuda = gpu and torch.cuda.is_available()
        device   = "cuda" if use_cuda else "cpu"
        on_status("Loading OCR model (PP-OCRv5 mobile)…")
        # enable_mkldnn=False avoids a PaddlePaddle PIR+OneDNN crash
        # ("ConvertPirAttribute2RuntimeAttribute not support
        # [pir::ArrayAttribute<pir::DoubleAttribute>]") that fires on some
        # CPUs when the OneDNN instruction set is used during inference.
        _ocr_reader = PaddleOCR(
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
            lang="en",
        )
        on_status("Loading speech model…")
        _tts_pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M",
                                  device=device)
        on_done()
    except Exception as exc:
        on_error(exc)


def _virtual_screen() -> tuple[int, int, int, int]:
    u = ctypes.windll.user32
    return (
        u.GetSystemMetrics(76), u.GetSystemMetrics(77),
        u.GetSystemMetrics(78), u.GetSystemMetrics(79),
    )


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

        self._full_audio: np.ndarray | None = None
        self._word_schedule: list[tuple[int, float]] = []
        self._word_bboxes_canvas: list[tuple] = []
        self._pause_pos = 0.0
        self._play_state = "idle"
        self._idle_seeked = False   # user moved the slider/clicked a word after audio ended
        self._extracted_text: str | None = None

        self.status_var        = tk.StringVar(value="Loading models…")
        self._highlight_color  = "#fff200"
        self._highlight_mode   = tk.StringVar(value="auto")
        self._text_view_var    = tk.BooleanVar(value=False)

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
        self._gpu_var           = tk.BooleanVar(value=False)
        self._load_settings()

        self._build_ui()
        # Save whenever the text-view toggle changes
        self._text_view_var.trace_add(
            "write", lambda *_: self._save_settings())
        self.root.bind_all("<Control-v>", self._do_paste)
        self.root.bind_all("<Control-V>", self._do_paste)
        self._register_hotkey()
        threading.Thread(
            target=_load_models,
            args=(
                lambda msg: self.root.after(0, lambda m=msg: self.status_var.set(m)),
                self._on_models_ready,
                lambda exc: self.root.after(0, lambda e=exc: self.status_var.set(
                    f"Model load failed: {e}")),
            ),
            kwargs={"gpu": self._gpu_var.get()},
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
        ttk.Checkbutton(gf, text="Use GPU for speech  (requires CUDA)",
                        variable=self._gpu_var,
                        command=self._on_gpu_toggle).pack(side=tk.LEFT)

        ttk.Button(self.root, text="⚙  Settings",
                   command=self._open_settings, width=28).pack(**pad)

        ttk.Label(self.root, textvariable=self.status_var,
                  foreground="gray").pack(pady=(2, 10))

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

    def _register_hotkey(self):
        try:
            keyboard.add_hotkey(self._hotkey_trigger, self._trigger)
        except Exception:
            pass
        try:
            keyboard.add_hotkey(self._hotkey_pause,
                                lambda: self.root.after(0, self._on_play_btn))
        except Exception:
            pass

    def _reregister_hotkeys(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self._register_hotkey()

    def _load_settings(self):
        try:
            with open(_SETTINGS_PATH) as f:
                d = json.load(f)
            self._hotkey_trigger = d.get("hotkey_trigger", self._hotkey_trigger)
            self._hotkey_pause   = d.get("hotkey_pause",   self._hotkey_pause)
            self._voice_id       = d.get("voice",          self._voice_id)
            self._highlight_color = d.get("highlight_color", self._highlight_color)
            self._highlight_mode.set(d.get("highlight_mode", "auto"))
            self._text_view_var.set(bool(d.get("text_view", False)))
            self._speed_var.set(float(d.get("speed", 1.0)))
            self._gpu_var.set(bool(d.get("gpu", False)))
        except Exception:
            pass

    def _save_settings(self):
        try:
            with open(_SETTINGS_PATH, "w") as f:
                json.dump({
                    "hotkey_trigger":  self._hotkey_trigger,
                    "hotkey_pause":    self._hotkey_pause,
                    "voice":           self._voice_id,
                    "highlight_color": self._highlight_color,
                    "highlight_mode":  self._highlight_mode.get(),
                    "text_view":       bool(self._text_view_var.get()),
                    "speed":           round(self._speed_var.get(), 2),
                    "gpu":             bool(self._gpu_var.get()),
                }, f, indent=2)
        except Exception:
            pass

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

        ttk.Button(dlg, text="Close", command=dlg.destroy).grid(
            row=2, column=0, columnspan=3, pady=(0, 14))

    def _capture_hotkey(self, which: str, lbl_var: tk.StringVar,
                        parent: tk.Toplevel):
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
                    self._reregister_hotkeys()
                    self._save_settings()
            cap.destroy()

        confirm_btn.configure(command=lambda: _cleanup(apply=True))
        st["hook"] = keyboard.hook(_on_key, suppress=False)
        cap.protocol("WM_DELETE_WINDOW", lambda: _cleanup())

    def _on_models_ready(self):
        self.root.after(0, lambda: self.status_var.set(
            f"Ready  ({self._hotkey_trigger.upper()})"))

    def _on_gpu_toggle(self):
        self._save_settings()
        self._reload_models()

    def _reload_models(self):
        global _ocr_reader, _tts_pipeline
        _ocr_reader = None
        _tts_pipeline = None
        label = "GPU" if self._gpu_var.get() else "CPU"
        self.status_var.set(f"Reloading models ({label})…")
        threading.Thread(
            target=_load_models,
            args=(
                lambda msg: self.root.after(0, lambda m=msg: self.status_var.set(m)),
                self._on_models_ready,
                lambda exc: self.root.after(0, lambda e=exc: self.status_var.set(
                    f"Model load failed: {e}")),
            ),
            kwargs={"gpu": self._gpu_var.get()},
            daemon=True,
        ).start()

    # ── Session control ───────────────────────────────────────────────────────

    def _trigger(self):
        if _ocr_reader is None or _tts_pipeline is None:
            return
        if self._play_state != "idle":
            return
        self.root.after(0, self._do_select)

    def _do_select(self):
        region = select_region(self.root)
        if not region:
            return
        self._begin_pipeline(region=region)

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

    def _begin_pipeline(self, *, region=None, image=None, text=None):
        self.stop_event.clear()
        self._play_event.clear()
        self._full_audio = None
        self._word_schedule = []
        self._word_bboxes_canvas = []
        self._extracted_text = None
        self._pause_pos = 0.0
        self._play_state = "generating"
        self.status_var.set("Scanning…")
        threading.Thread(
            target=self._generate,
            kwargs={"region": region, "image": image, "text": text},
            daemon=True).start()

    def _stop(self):
        self.stop_event.set()
        self._play_event.clear()
        sd.stop()
        self._play_state = "idle"
        self._play_btn.configure(state="disabled", text="▶  Play")
        self.root.after(0, self._close_reader)
        self.root.after(0, lambda: self.status_var.set(
            "Stopped — Shift+Z to read again"))

    # ── Generation ────────────────────────────────────────────────────────────

    def _generate(self, *, region=None, image=None, text=None):
        try:
            if text is not None:
                tokens = [t for t in text.split() if t]
                if not tokens:
                    self.root.after(0, lambda: self.status_var.set("No text to read"))
                    return
                tts_text = _tts_safe(tokens)
                ocr_words = [w for w in tokens if any(c.isalnum() for c in w)]
                if not ocr_words:
                    self.root.after(0, lambda: self.status_var.set("No readable text"))
                    return
                pil_img, disp_bboxes = _make_text_image(ocr_words)
            else:
                if image is None:
                    image = ImageGrab.grab(bbox=region, all_screens=True)
                img_array = np.array(image)
                # PaddleOCR returns line-level bboxes; _paddle_ocr splits each
                # line into per-word boxes proportionally by character count.
                word_data = _paddle_ocr(img_array)
                # Pixel-tight each bbox so highlights snap to the ink columns.
                word_data = [(w, _tighten_x_bbox(img_array, *b))
                             for w, b in word_data]

                if not word_data:
                    self.root.after(0, lambda: self.status_var.set("No text detected"))
                    return

                # TTS text: all tokens (punctuation attached to preceding words by
                # _tts_safe so Kokoro gets "Hello, world." not "Hello , world .").
                all_ocr_words = [w for w, _ in word_data]
                tts_text = _tts_safe(all_ocr_words)

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
            if self._highlight_mode.get() == "auto":
                arr = np.array(pil_img)
                bg_hex, text_hex = _detect_image_colors(arr, disp_bboxes)
                self.root.after(0, self._apply_highlight_color,
                                optimal_highlight(bg_hex, text_hex))
            self.root.after(0, self._show_reader, pil_img, disp_bboxes, region)
            self.root.after(0, lambda: self.status_var.set("Generating speech…"))

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
            used_ocr = set()   # OCR indices already scheduled
            seq_oi   = 0       # sequential cursor: next expected OCR position

            audio_chunks: list = []
            schedule: list[tuple[int, float]] = []
            running = 0.0

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

            if self.stop_event.is_set() or not audio_chunks:
                return

            self._full_audio    = np.concatenate(audio_chunks)
            self._word_schedule = schedule
            self.root.after(0, lambda: self._set_play_state("ready"))

        except Exception as exc:
            self.root.after(0, lambda e=exc: self.status_var.set(f"Error: {e}"))

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
                    if not self._play_event.is_set():
                        paused_at = start_pos + (time.monotonic() - t0) * speed
                        sd.stop()
                        break
                    if now - last_ui >= 0.1:
                        cur = start_pos + (now - t0) * speed
                        self.root.after(0, self._update_timeline, cur)
                        last_ui = now

                if paused_at is not None:
                    break
                if self.stop_event.is_set():
                    sd.stop()
                    return

                self.root.after(0, self._highlight_word, word_idx)

            if paused_at is not None:
                self._pause_pos = paused_at
                self.root.after(0, self._update_timeline, paused_at)
                while not self._play_event.wait(0.05):
                    if self.stop_event.is_set():
                        return
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
                     region: tuple | None = None):
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

        cv = tk.Canvas(win, width=disp_w, height=disp_h,
                       highlightthickness=0, bg="#1a1a1a")
        cv.pack(fill=tk.BOTH, expand=True)
        cv.bind("<Button-1>", self._on_word_click)
        cv.bind("<Motion>",   self._on_canvas_motion)

        tk_img = ImageTk.PhotoImage(disp_img)
        img_id = cv.create_image(0, 0, image=tk_img, anchor="nw")
        cv.tk_img = tk_img

        self._reader_base_img   = disp_img.copy()
        self._reader_canvas_img = img_id

        # ── bottom bar ────────────────────────────────────────────────
        bar = ttk.Frame(win)
        bar.pack(fill=tk.X, padx=10, pady=(4, 4))

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

        sv = tk.StringVar(value="Generating speech…")
        ttk.Label(ctrl, textvariable=sv, foreground="gray").pack(side=tk.LEFT)

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

        total_h = disp_h + 115
        if region:
            # Drag-region flow: align canvas content (not the outer frame) with
            # the top-left corner of the captured region. We set an initial
            # geometry, let Tkinter compute the frame decorations, measure the
            # offsets, then correct so content_origin == region origin.
            wx, wy = region[0], region[1]
            win.geometry(f"{disp_w}x{total_h}+{wx}+{wy}")
            win.update_idletasks()
            off_x = win.winfo_rootx() - win.winfo_x()
            off_y = win.winfo_rooty() - win.winfo_y()
            win.geometry(f"{disp_w}x{total_h}+{wx - off_x}+{wy - off_y}")
        else:
            # Paste / no-region flow: center the window on the primary screen.
            win.update_idletasks()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            cx = max(0, (sw - disp_w) // 2)
            cy = max(0, (sh - total_h) // 2)
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
        was_playing = self._play_state == "playing"
        if was_playing:
            self._play_event.clear()
            sd.stop()
        pos = max(0.0, min(self._timeline_var.get() + delta,
                           self._timeline_max - 0.05))
        self._pause_pos = pos
        self._update_timeline(pos)
        if was_playing:
            self._play_event.set()
        else:
            self._highlight_at_time(pos)
            self._mark_idle_seeked()

    def _seek_to_time(self, target_time: float, auto_play: bool = True):
        """Seek audio to target_time (seconds). Resumes playback unless
        auto_play is False and the audio is currently paused/idle."""
        if self._full_audio is None or self._play_state == "generating":
            return
        pos = max(0.0, min(target_time, self._timeline_max - 0.05))
        state = self._play_state
        if state == "playing":
            self._play_event.clear()
            sd.stop()
            self._pause_pos = pos
            self._update_timeline(pos)
            self._play_event.set()
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
        except tk.TclError:
            pass

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
