"""Run the actual local CV pipeline on a user-chosen image."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, default=None, help="Optional annotated image path")
    args = parser.parse_args()
    import cv2
    import numpy as np
    from qorgau.vision import VisionModels
    frame = cv2.imdecode(np.fromfile(str(args.image), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise SystemExit("Cannot decode image")
    model = VisionModels(ROOT / "models")
    try:
        result = model.analyze(frame)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.output:
            for box in result["face_boxes"]:
                x, y, w, h = map(int, box)
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 0), 4)
                cv2.rectangle(frame, (x, y), (x+w, y+h), (255, 255, 255), 2)
            for detection in result["phone_boxes"]:
                x, y, w, h = map(int, detection["box"])
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 0), 4)
                cv2.rectangle(frame, (x, y), (x+w, y+h), (255, 255, 255), 2)
            for detection in result.get("objects", []):
                x, y, w, h = map(int, detection["box"])
                color = (255, 255, 255)
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 0), 4)
                cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
                label = "POSSIBLE TEXT SHEET" if detection["kind"] == "paper_candidate" else detection["kind"].upper()
                cv2.putText(frame, label, (x, max(18, y-8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
                cv2.putText(frame, label, (x, max(18, y-8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            ok, buffer = cv2.imencode(args.output.suffix or ".jpg", frame)
            if not ok:
                raise SystemExit("Unsupported output format")
            buffer.tofile(str(args.output))
    finally:
        model.close()


if __name__ == "__main__":
    main()
