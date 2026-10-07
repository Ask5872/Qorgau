"""Verify model bytes and optionally run a CPU inference without a webcam."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--load-models", action="store_true")
    args = parser.parse_args()
    print(f"Python: {sys.version.split()[0]} ({struct.calcsize('P') * 8}-bit)")
    print(f"Interpreter: {sys.executable}")
    manifest = json.loads((ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = ROOT / "models" / item["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise SystemExit(f"ERROR: model missing or checksum mismatch: {path.name}")
        print(f"OK: {path.name} SHA-256")
    if args.load_models:
        import numpy as np
        from qorgau.vision import VisionModels
        model = VisionModels(ROOT / "models")
        try:
            result = model.analyze(np.full((480, 640, 3), 120, dtype=np.uint8))
            assert result["faces"] == 0 and not result["phone"]
        finally:
            model.close()
        print("OK: all vision models ran locally on CPU; blank frame has no face or phone")
    if sys.platform == "win32":
        import pyautogui
        if not callable(getattr(pyautogui, "getActiveWindow", None)) or not callable(getattr(pyautogui, "Window", None)):
            raise SystemExit("ERROR: PyAutoGUI window controls unavailable. Re-run 01_install.cmd.")
        print("OK: PyAutoGUI window controls available; native keyboard/desktop checks require 02_start.cmd")
    print("Ready. Start: python run.py --isolate (Windows) / python run.py --demo")


if __name__ == "__main__":
    main()
