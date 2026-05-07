# SelectAndRead

Select any region of your screen and have it read aloud — with word-by-word highlighting, a full playback timeline, speed control, and audio export.

---

## How It Works

1. Press the global hotkey (default **Shift+Z**) from anywhere on your desktop
2. Drag to select any region of the screen
3. OCR extracts the text, the neural TTS model generates speech
4. A reader window opens — synchronized word highlighting follows the audio in real time

---

## Features

- **Word-by-word highlight** — each word lights up exactly as it's spoken
- **Full playback timeline** — scrub to any position, highlights update instantly
- **Speed control** — 0.5× to 2.0× without re-generating audio (samplerate trick)
- **Skip forward / backward** — ±5 seconds via buttons or arrow keys
- **Global hotkeys** — trigger and pause/resume from anywhere, fully customizable
- **Auto highlight color** — analyzes the screenshot's background and text colors using WCAG contrast ratios and opponent-channel color science to pick the most perceptually salient highlight
- **Custom highlight color** — pick your own if you prefer
- **Text view mode** — show clean OCR text instead of the screenshot
- **Export to WAV** — save the generated audio as a 16-bit 24 kHz WAV file
- **GPU acceleration** — toggle GPU mode for both OCR and TTS (requires CUDA)
- **25 English voices** — American and British, male and female (Kokoro voice pack)
- **Persistent settings** — all preferences saved across launches

---

## Technologies

| Technology | Role |
|---|---|
| [Kokoro TTS](https://huggingface.co/hexgrad/Kokoro-82M) | Neural text-to-speech — 82M parameter model with word-level timestamps |
| [EasyOCR](https://github.com/JaidedAI/EasyOCR) | Deep learning OCR — extracts per-word bounding boxes from screenshots |
| [PyTorch](https://pytorch.org) | GPU-accelerated inference for both OCR and TTS models |
| [Pillow (PIL)](https://python-pillow.org) | Screenshot capture, image processing, alpha-composited highlight overlays |
| [sounddevice](https://python-sounddevice.readthedocs.io) | Low-latency audio playback with samplerate-based speed control |
| [keyboard](https://github.com/boppreh/keyboard) | System-wide hotkey registration and live key capture |
| [NumPy](https://numpy.org) | Audio array manipulation, pixel-level bbox tightening, color analysis |
| [tkinter](https://docs.python.org/3/library/tkinter.html) | Desktop GUI — main panel, reader window, settings dialogs |

---

## Under the Hood

**Per-pixel bounding box tightening**
EasyOCR bboxes often include surrounding whitespace. Before highlighting, each bbox is shrunk to the columns that actually contain ink by analyzing per-column pixel brightness variance — so highlights cover only the word, never the space around it.

**Content-based word alignment**
TTS segments don't always map 1:1 to OCR words (Kokoro sometimes defers words like "from" near special characters to a later segment). Alignment is done by normalised text content rather than position, with a sequential cursor as fallback — so every word gets highlighted at its true audio timestamp.

**WCAG contrast + opponent-channel color science**
The auto highlight color sweeps 120 hues × 46 lightness levels, scoring each candidate by chroma × luminance proximity to the background, subject to a minimum 4.5:1 WCAG AA contrast ratio against the text color. This is why fluorescent yellow is the default on white backgrounds — maximum chroma at near-white luminance, firing the blue-yellow opponent channel at peak salience.

**Samplerate speed control**
Speed is applied by passing `samplerate=int(24000 * speed)` to `sounddevice.play()` — the same audio samples play faster or slower without pitch artifacts and without re-running the TTS model.

**Instant seek**
The timeline scrubber suppresses tkinter's built-in Scale widget behavior entirely (`return "break"` on press, drag, and release), computing position directly from cursor x-coordinate as a fraction of the widget width. This gives frame-accurate instant seeks instead of the widget's incremental thumb movement.

**Pixel-gap word splitting**
When EasyOCR returns multiple words in a single detection chunk, the image strip is analyzed for inter-word whitespace gaps by finding columns whose brightness variance falls below 15% of the maximum. The detected gap centers are matched to ideal split positions, giving accurate per-word bboxes without relying on character-count estimation.

---

## Installation

### Fresh Windows machine

1. Install **Python 3.12** from [python.org](https://python.org) — tick **"Add Python to PATH"** during install
   > ⚠️ Python 3.13+ is not supported. Use Python 3.10, 3.11, or 3.12.
2. Download this repo — click **Code → Download ZIP** on GitHub, extract it anywhere
3. Double-click **`setup.bat`**
   - It will ask: **"Use GPU? (Y/N)"** — if you have an NVIDIA GPU say Y, otherwise N
   - It installs all packages and downloads the AI models (~400 MB, takes a few minutes)
4. Double-click **`run.bat`** to launch

### Already have Anaconda / an existing Python environment

1. Download and extract the ZIP same as above
2. Double-click **`link_env.bat`** and paste the path to your `pythonw.exe`
3. Manually install the dependencies in your environment:
   ```
   pip install kokoro sounddevice easyocr Pillow keyboard numpy
   ```
   And PyTorch separately — CPU:
   ```
   pip install torch torchvision torchaudio
   ```
   Or GPU (CUDA 12.1):
   ```
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
   ```
4. Double-click **`run.bat`** to launch

### Optional — Desktop shortcut

After setup, right-click **`create_shortcut.ps1`** → **"Run with PowerShell"** to add a **SelectAndRead** shortcut to your Desktop.

---

## Hotkeys

| Action | Default |
|---|---|
| Select & Read | Shift+Z |
| Pause / Resume | Shift+X |
| Skip forward 5s | → (inside reader) |
| Skip backward 5s | ← (inside reader) |
| Pause / Resume | Space (inside reader) |

All hotkeys are reassignable from the **⚙ Settings** dialog.

---

## License

MIT
