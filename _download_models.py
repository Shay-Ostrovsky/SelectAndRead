"""
Called by setup.bat to download all AI models and voice packs.
Must run inside the venv after all packages are installed.
"""
import glob
import hashlib
import os
import sys
import urllib.request

# Fix espeakng-loader before Kokoro/misaki imports it.
# The wheel's get_data_path() returns the CI build path (D:/a/...) which
# doesn't exist on real machines. Find the actual installed data directory
# and patch get_data_path so misaki gets the correct path at import time.
try:
    import espeakng_loader
    _pkg = os.path.dirname(os.path.abspath(espeakng_loader.__file__))
    _hits = glob.glob(os.path.join(_pkg, "**", "phontab"), recursive=True)
    if _hits:
        _data_dir = os.path.dirname(_hits[0])
        espeakng_loader.get_data_path = lambda: _data_dir
    espeakng_loader.make_library_available()
except Exception as e:
    print(f"Warning: espeakng setup: {e}")


# ── PP-OCRv5 mobile EN ONNX models ────────────────────────────────────────────
# Same model weights as PaddleOCR's PP-OCRv5 mobile EN, exported to ONNX so
# they can run through the lightweight onnxruntime instead of the 2+ GB
# PaddlePaddle stack. URLs + SHA-256 from RapidAI's default model registry
# (https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/default_models.yaml).
# Sourced from ModelScope (Alibaba's HuggingFace equivalent, generally fast).

_OCR_CACHE_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "SelectAndRead", "onnx")
os.makedirs(_OCR_CACHE_DIR, exist_ok=True)

_OCR_MODELS = [
    {
        "name": "ch_PP-OCRv5_det_mobile.onnx",     # detection is language-agnostic
        "url":  "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.8.0/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        "sha256": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    },
    {
        "name": "en_PP-OCRv5_rec_mobile.onnx",     # English recognition
        "url":  "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.8.0/onnx/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx",
        "sha256": "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8",
    },
    # No cls model: rapidocr-onnxruntime's preprocessor is wired for the
    # v4 cls input shape ([3,48,192]); the v5 cls model uses [3,80,160]
    # and would crash here. We pass use_cls=False at inference time.
]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_with_progress(url: str, dest: str):
    """Stream a download to disk with a single-line progress indicator."""
    def _hook(blocks, block_size, total):
        if total <= 0:
            return
        done = min(blocks * block_size, total)
        pct  = done * 100.0 / total
        mb_done  = done / (1 << 20)
        mb_total = total / (1 << 20)
        print(f"\r    {mb_done:6.1f} / {mb_total:6.1f} MB  ({pct:5.1f}%)",
              end="", flush=True)
    urllib.request.urlretrieve(url, dest, reporthook=_hook)
    print()


print("Downloading PP-OCRv5 mobile (English) ONNX models...")
for m in _OCR_MODELS:
    dest = os.path.join(_OCR_CACHE_DIR, m["name"])
    if os.path.exists(dest) and _sha256(dest) == m["sha256"]:
        print(f"  {m['name']}  (cached, SHA-256 OK)")
        continue
    print(f"  {m['name']}")
    try:
        _download_with_progress(m["url"], dest)
    except Exception as exc:
        print(f"  ERROR downloading {m['name']}: {exc}")
        sys.exit(1)
    actual = _sha256(dest)
    if actual != m["sha256"]:
        print(f"  ERROR: SHA-256 mismatch for {m['name']}")
        print(f"         expected {m['sha256']}")
        print(f"         got      {actual}")
        try: os.remove(dest)
        except OSError: pass
        sys.exit(1)
    print(f"    SHA-256 OK")
print("OCR models ready.")
print()

print("Downloading Kokoro-82M speech model...")
from kokoro import KPipeline
p = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
print("Kokoro model ready.")
print()

voices = [
    "af_heart", "af_sky", "af_bella", "af_nova", "af_river",
    "af_sarah", "af_nicole", "af_aoede", "af_kore", "af_jessica",
    "am_michael", "am_adam", "am_echo", "am_eric", "am_liam",
    "am_onyx", "am_puck",
    "bf_emma", "bf_isabella", "bf_alice", "bf_lily",
    "bm_george", "bm_lewis", "bm_daniel",
]
print(f"Downloading {len(voices)} voice packs...")
failed = []
for i, voice in enumerate(voices, 1):
    print(f"  [{i:2d}/{len(voices)}] {voice}", end="", flush=True)
    try:
        next(iter(p("Hi", voice=voice)), None)
        print(" OK")
    except Exception as e:
        print(f" FAILED: {e}")
        failed.append(voice)

if failed:
    print(f"\nWarning: {len(failed)} voice(s) failed: {', '.join(failed)}")
    print("They will retry on first use.")
    sys.exit(1)
else:
    print("\nAll voices ready.")
