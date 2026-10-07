"""Local YOLOv8n-face inference and association with MediaPipe face landmarks.

The bundled author-exported ONNX has one real class named ``face``. It is
separate from the COCO object model: COCO ``person`` is never treated as a face.
"""
from ast import literal_eval
from pathlib import Path


def _overlap(a, b):
    """Return IoU and intersection relative to the smaller xywh rectangle."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0., min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0., min(ay + ah, by + bh) - max(ay, by))
    area_a, area_b = max(0., aw) * max(0., ah), max(0., bw) * max(0., bh)
    return (intersection / max(area_a + area_b - intersection, 1e-9),
            intersection / max(min(area_a, area_b), 1e-9))


def decode_faces(output, image_shape, scale, left, top, threshold=0.5):
    """Decode the pinned export's [1, N, 21] post-NMS face/pose rows.

    Each row starts x1, y1, x2, y2, confidence, class_id. The remaining fifteen
    values are five face keypoints and are not used for gaze (MediaPipe is).
    """
    import numpy as np
    rows = np.asarray(output)
    if rows.ndim != 3 or rows.shape[0] != 1 or rows.shape[2] != 21:
        raise ValueError("YOLO face model output does not match its pinned export")
    if scale <= 0:
        raise ValueError("Invalid face image scale")
    h, w = image_shape[:2]
    detections = []
    for row in rows[0]:
        x1, y1, x2, y2, confidence, class_id = map(float, row[:6])
        if not np.isfinite(row[:6]).all() or not threshold <= confidence <= 1.0 or class_id != 0:
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        x1, x2 = sorted((max(0., min(w, (x - left) / scale)) for x in (x1, x2)))
        y1, y2 = sorted((max(0., min(h, (y - top) / scale)) for y in (y1, y2)))
        box = [round(x1), round(y1), round(x2 - x1), round(y2 - y1)]
        if box[2] < 4 or box[3] < 4:
            continue
        detections.append({"box": box, "confidence": round(confidence, 4), "source": "yolov8n-face"})
    # Export includes NMS; this second bounded check protects duplicate rows and
    # prevents a malformed export from inflating the participant count.
    kept = []
    for item in sorted(detections, key=lambda item: item["confidence"], reverse=True):
        if all(_overlap(item["box"], other["box"])[0] < 0.5 for other in kept):
            kept.append(item)
            if len(kept) >= 16:
                break
    return kept


def associate_faces(yolo_faces, landmark_boxes):
    """Union of two real face detectors with one-to-one geometric matching.

    Face-mesh boxes are usually tighter than detector boxes. Accept either IoU
    or containment, then greedily match the strongest pair first. Unmatched
    MediaPipe faces remain visible; matching does not double-count a student.
    """
    result = [{**item, "box": list(item["box"])} for item in yolo_faces]
    candidates = []
    for i, item in enumerate(result):
        for j, box in enumerate(landmark_boxes):
            iou, covered = _overlap(item["box"], box)
            if iou >= 0.25 or covered >= 0.65:
                candidates.append((max(iou, covered), i, j))
    used_yolo, used_landmarks = set(), set()
    for _, i, j in sorted(candidates, reverse=True):
        if i in used_yolo or j in used_landmarks:
            continue
        result[i]["source"] = "yolov8n-face+mediapipe"
        result[i]["landmark_index"] = j
        used_yolo.add(i)
        used_landmarks.add(j)
    for j, box in enumerate(landmark_boxes):
        if j not in used_landmarks:
            result.append({"box": list(box), "confidence": None, "source": "mediapipe", "landmark_index": j})
    return result


class YoloFaceDetector:
    """Required, bounded CPU detector. Missing/wrong models fail setup openly."""
    MODEL_NAME = "yolov8n-face.onnx"

    def __init__(self, model_dir):
        import cv2
        import numpy as np
        import onnxruntime as ort
        ort.disable_telemetry_events()
        self.cv2, self.np = cv2, np
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        path = Path(model_dir) / self.MODEL_NAME
        # Python path handling preserves Cyrillic Windows usernames.
        self.session = ort.InferenceSession(path.read_bytes(), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        info = self.session.get_inputs()[0]
        if list(info.shape) != [1, 3, 640, 640] or info.type != "tensor(float)":
            raise ValueError("Unexpected YOLO face model input; reinstall verified model files")
        metadata = self.session.get_modelmeta().custom_metadata_map
        try:
            names = literal_eval(metadata.get("names", "{}"))
        except (ValueError, SyntaxError):
            names = {}
        if names != {0: "face"} or metadata.get("task") != "pose":
            raise ValueError("Expected YOLO face class; a COCO person model is not a face detector")
        output = self.session.get_outputs()[0]
        if list(output.shape) != [1, 300, 21]:
            raise ValueError("Unexpected YOLO face output; reinstall verified model files")
        self.input_name, self.size = info.name, 640

    def detect(self, bgr):
        cv2, np, size = self.cv2, self.np, self.size
        h, w = bgr.shape[:2]
        scale = min(size / w, size / h)
        nw, nh = round(w * scale), round(h * scale)
        left, top = (size - nw) // 2, (size - nh) // 2
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[top:top + nh, left:left + nw] = cv2.resize(bgr, (nw, nh))
        tensor = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode_faces(output, bgr.shape, scale, left, top)

    def close(self):
        self.session = None
