"""Object post-processing and a deliberately limited visual paper candidate.

COCO IDs are from Ultralytics' official cfg/datasets/coco.yaml. A paper
candidate is a rectangle with text-like contrast, not an identified cheat sheet.
It has no model probability and must never trigger automatic disqualification.
"""

COCO_OBJECTS = {
    62: ("monitor", "Экран / телевизор", 0.50),
    63: ("laptop", "Ноутбук", 0.50),
    67: ("phone", "Телефон", 0.40),
    73: ("book", "Книга", 0.50),
}


def decode_coco(output, image_shape, scale, left, top):
    """Decode one standard raw YOLOv8 output; NMS is independent per class."""
    import cv2
    import numpy as np

    rows = np.asarray(output)
    if rows.ndim == 3 and rows.shape[0] == 1:
        rows = rows[0]
    if rows.ndim != 2:
        raise RuntimeError("Ожидается стандартный YOLOv8 COCO ONNX (84 канала).")
    if rows.shape[0] == 84:
        rows = rows.T
    if rows.shape[1] != 84:
        raise RuntimeError("Ожидается стандартный YOLOv8 COCO ONNX без встроенного NMS (84 канала).")
    if scale <= 0 or not np.isfinite(scale):
        raise ValueError("Некорректный масштаб кадра")
    h, w = image_shape[:2]
    rows = rows[np.isfinite(rows).all(axis=1)]
    if not len(rows):
        return []
    winner = rows[:, 4:].argmax(axis=1)
    detections = []
    for class_id, (kind, label, threshold) in COCO_OBJECTS.items():
        selected = rows[(winner == class_id) & (rows[:, 4 + class_id] >= threshold)
                        & (rows[:, 4 + class_id] <= 1)]
        boxes, confidence = [], []
        for row in selected:
            cx, cy, bw, bh = map(float, row[:4])
            if bw <= 0 or bh <= 0:
                continue
            x1, x2 = sorted([max(0.0, min(float(w), (cx - bw / 2 - left) / scale)),
                             max(0.0, min(float(w), (cx + bw / 2 - left) / scale))])
            y1, y2 = sorted([max(0.0, min(float(h), (cy - bh / 2 - top) / scale)),
                             max(0.0, min(float(h), (cy + bh / 2 - top) / scale))])
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append([x1, y1, x2 - x1, y2 - y1])
            confidence.append(float(row[4 + class_id]))
        # OpenCV applies a strict > cutoff. Candidates equal to the declared
        # confidence threshold were already validated above and must survive.
        indices = cv2.dnn.NMSBoxes(boxes, confidence, max(0.0, threshold - 1e-6), 0.45)
        for index in np.asarray(indices).flatten():
            index = int(index)
            detections.append({"kind": kind, "label": label, "class_id": class_id,
                               "box": [round(v, 1) for v in boxes[index]],
                               "confidence": round(confidence[index], 3), "method": "yolo_coco"})
    return sorted(detections, key=lambda item: item["confidence"], reverse=True)


def _intersection_fraction(box, excluded):
    x, y, w, h = box
    xx, yy, ww, hh = excluded
    intersection = max(0, min(x + w, xx + ww) - max(x, xx)) * max(0, min(y + h, yy + hh) - max(y, yy))
    return intersection / max(1, w * h)


def paper_candidates(bgr, exclusions=()):
    """Return at most three visible text-sheet candidates; no OCR or intent claim.

    Processing is capped at 640 px on the long side and twelve large contours.
    Known phone/screen/laptop/book and face boxes exclude overlapping candidates.
    A missed screen detection can still resemble paper; this is advisory only.
    """
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    scale = min(1.0, 640 / max(h, w))
    frame = cv2.resize(bgr, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    fh, fw = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, (0, 0, 155), (179, 55, 255))
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    found = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        area = cv2.contourArea(contour)
        if not 0.018 <= area / (fh * fw) <= 0.32:
            continue
        quad = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
        if len(quad) != 4 or not cv2.isContourConvex(quad):
            continue
        x, y, bw, bh = cv2.boundingRect(quad)
        if min(bw, bh) < 50 or x < 4 or y < 4 or x + bw > fw - 4 or y + bh > fh - 4:
            continue
        if area / (bw * bh) < 0.70:
            continue
        original_box = [x / scale, y / scale, bw / scale, bh / scale]
        if any(_intersection_fraction(original_box, excluded) > 0.15 for excluded in exclusions):
            continue
        corners = quad.reshape(4, 2).astype(np.float32)
        # Contours follow the perimeter. Rotate so the upper-left point is first.
        corners = np.roll(corners, -int(np.argmin(corners.sum(axis=1))), axis=0)
        if corners[1, 0] < corners[-1, 0]:
            corners = corners[[0, 3, 2, 1]]
        lengths = np.linalg.norm(corners - np.roll(corners, -1, axis=0), axis=1)
        if min(lengths) <= 0 or min(lengths[0], lengths[2]) / max(lengths[0], lengths[2]) < 0.65 or min(lengths[1], lengths[3]) / max(lengths[1], lengths[3]) < 0.65:
            continue
        width, height = (lengths[0] + lengths[2]) / 2, (lengths[1] + lengths[3]) / 2
        if not 1.12 <= max(width, height) / min(width, height) <= 1.90:
            continue
        mask = np.zeros_like(gray)
        cv2.fillConvexPoly(mask, corners.astype(np.int32), 255)
        dilated = cv2.dilate(mask, np.ones((7, 7), np.uint8))
        eroded = cv2.erode(mask, np.ones((7, 7), np.uint8))
        inside = gray[(mask > 0) & (eroded == 0)]
        outside = gray[(dilated > 0) & (mask == 0)]
        contrast = float(inside.mean() - outside.mean()) if inside.size and outside.size else 0
        if contrast < 22:
            continue
        shrink = min(1.0, 280 / max(width, height))
        tw, th = max(1, round(width * shrink)), max(1, round(height * shrink))
        transform = cv2.getPerspectiveTransform(corners, np.asarray([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]], np.float32))
        page = cv2.warpPerspective(gray, transform, (tw, th))
        mx, my = max(4, round(tw * 0.07)), max(4, round(th * 0.07))
        page = page[my:-my, mx:-mx]
        if min(page.shape) < 25:
            continue
        paper_level = float(np.percentile(page, 85))
        ink = (page < paper_level - 45).astype(np.uint8) * 255
        ink_fraction = float(np.count_nonzero(ink) / ink.size)
        if not 0.012 <= ink_fraction <= 0.26:
            continue
        # Join nearby letter strokes into short lines, without an OCR claim.
        lines = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((1, max(5, round(page.shape[1] * 0.05))), np.uint8))
        count, _, stats, _ = cv2.connectedComponentsWithStats(lines)
        line_rows = []
        for sx, sy, sw, sh, pixels in stats[1:count]:
            if sw >= page.shape[1] * 0.16 and 2 <= sh <= page.shape[0] * 0.12 and sw / sh >= 3 and pixels >= sw:
                center = float(sy + sh / 2)
                if all(abs(center - previous) >= max(4, sh) for previous in line_rows):
                    line_rows.append(center)
        if not 3 <= len(line_rows) <= 30:
            continue
        if any(_intersection_fraction(original_box, item["box"]) > 0.5 for item in found):
            continue
        found.append({"kind": "paper_candidate", "label": "Возможный лист с текстом",
                      "class_id": None, "box": [round(v, 1) for v in original_box],
                      "confidence": None, "method": "paper_geometry",
                      "observations": {"text_like_lines": len(line_rows), "border_contrast": round(contrast, 1),
                                       "ink_fraction": round(ink_fraction, 3), "confirmed_content": False}})
        if len(found) == 3:
            break
    return found
