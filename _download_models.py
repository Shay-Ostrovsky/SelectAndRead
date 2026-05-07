"""
Called by setup.bat to download all AI models and voice packs.
Must run inside the venv after all packages are installed.
"""
import sys

# Make the espeak-ng DLL findable on Windows before Kokoro/misaki imports it
try:
    import espeakng_loader
    espeakng_loader.make_library_available()
except Exception as e:
    print(f"Warning: espeakng setup: {e}")

print("Downloading EasyOCR model (English)...")
import easyocr
easyocr.Reader(["en"], verbose=True)
print("EasyOCR model ready.")
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
