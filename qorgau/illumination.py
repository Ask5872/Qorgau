"""Measure the tracked face's exposure without penalizing a dark room."""
import math


def measure_illumination(bgr, face_box=None):
    import cv2
    import numpy as np

    height, width = bgr.shape[:2]
    # Downsampling keeps this inexpensive alongside the existing CV pipeline.
    small = cv2.resize(bgr, (min(width, 320), max(1, round(height * min(width, 320) / width))))
    frame = round(float(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean()), 1)
    result = {"brightness": frame, "frame_brightness": frame,
              "face_brightness": None, "illumination_source": "frame"}
    if face_box is None:
        return result
    try:
        x, y, box_width, box_height = map(float, face_box)
        if not all(math.isfinite(v) for v in (x, y, box_width, box_height)) or min(box_width, box_height) < 20:
            return result
        # Trim the rectangle border: it contains hair and background around
        # the mesh. A median resists a small bright reflection on eyeglasses.
        x1, x2 = max(0, round(x + .15 * box_width)), min(width, round(x + .85 * box_width))
        y1, y2 = max(0, round(y + .15 * box_height)), min(height, round(y + .85 * box_height))
        if x2 - x1 < 12 or y2 - y1 < 12:
            return result
        roi = bgr[y1:y2, x1:x2]
        roi = cv2.resize(roi, (min(roi.shape[1], 160), min(roi.shape[0], 160)))
        brightness = round(float(np.median(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY))), 1)
        result.update(brightness=brightness, face_brightness=brightness, illumination_source="face")
    except (TypeError, ValueError, OverflowError):
        pass
    return result
