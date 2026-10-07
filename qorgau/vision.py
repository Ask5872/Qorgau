"""CPU-only YOLOv8 ONNX + MediaPipe Tasks. Nothing is sent to a cloud."""
import math
import threading
import time
from collections import deque
from pathlib import Path


class VisionModels:
    def __init__(self, model_dir):
        import cv2
        import numpy as np
        import onnxruntime as ort
        import mediapipe as mp
        ort.disable_telemetry_events()
        self.cv2, self.np, self.mp = cv2, np, mp
        from .face_detector import YoloFaceDetector
        self.face_detector = YoloFaceDetector(model_dir)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.phone = ort.InferenceSession(str(Path(model_dir) / "yolov8n.onnx"),
                                         sess_options=options, providers=["CPUExecutionProvider"])
        self.input_name = self.phone.get_inputs()[0].name
        self.size = int(self.phone.get_inputs()[0].shape[-1])
        # Python handles Unicode paths on Windows; some MediaPipe native file
        # loaders do not. Keep the bytes alive while the landmarker uses them.
        self._face_model_buffer = (Path(model_dir) / "face_landmarker.task").read_bytes()
        self.face = mp.tasks.vision.FaceLandmarker.create_from_options(
            mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_buffer=self._face_model_buffer),
                running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=3,
                min_face_detection_confidence=0.5, min_tracking_confidence=0.5,
                output_facial_transformation_matrixes=True))
        self.ts = 0
        from .phone_pose import PhonePoseTracker
        self.phone_pose = PhonePoseTracker()

    def detect_objects(self, bgr):
        """One CPU inference for every supported COCO object class."""
        from .objects import decode_coco
        cv2, np, size = self.cv2, self.np, self.size
        h, w = bgr.shape[:2]
        scale = min(size / w, size / h)
        nw, nh = round(w * scale), round(h * scale)
        left, top = (size - nw) // 2, (size - nh) // 2
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[top:top + nh, left:left + nw] = cv2.resize(bgr, (nw, nh))
        x = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        output = self.phone.run(None, {self.input_name: x})[0]
        return decode_coco(output, bgr.shape, scale, left, top)

    def phones(self, bgr):
        """Compatibility helper; analyze() itself performs only one inference."""
        return [{"box": item["box"], "confidence": item["confidence"]}
                for item in self.detect_objects(bgr) if item["kind"] == "phone"]

    def analyze(self, bgr):
        cv2, np, mp = self.cv2, self.np, self.mp
        # Preview resolution does not need to multiply the face model workload.
        # Landmarks are normalized, so boxes still map to the original image.
        h, w = bgr.shape[:2]
        face_frame = cv2.resize(bgr, (640, max(1, round(h * 640 / w))),
                                interpolation=cv2.INTER_AREA) if w > 640 else bgr
        rgb = cv2.cvtColor(face_frame, cv2.COLOR_BGR2RGB)
        self.ts = max(self.ts + 1, int(time.monotonic() * 1000))
        result = self.face.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), self.ts)
        faces = result.face_landmarks
        yolo_faces = self.face_detector.detect(bgr)
        detections = self.detect_objects(bgr)
        out = {"faces": len(faces), "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
               "pose_valid": False, "iris_x": 0.5, "iris_y": 0.5,
               "eye_open": False, "head_vertical": 0.0, "face_boxes": [],
               "phone_boxes": [{"box": item["box"], "confidence": item["confidence"]}
                               for item in detections if item["kind"] == "phone"],
               "objects": [item for item in detections if item["kind"] != "phone"]}
        landmark_boxes = []
        for landmarks in faces:
            xs, ys = [v.x for v in landmarks], [v.y for v in landmarks]
            landmark_boxes.append([round(min(xs) * w), round(min(ys) * h),
                                      round((max(xs) - min(xs)) * w), round((max(ys) - min(ys)) * h)])
        from .face_detector import associate_faces
        face_detections = associate_faces(yolo_faces, landmark_boxes)
        out.update(faces=len(face_detections),
                   face_boxes=[item["box"] for item in face_detections],
                   face_detections=face_detections, landmark_face_boxes=landmark_boxes,
                   yolo_face_count=len(yolo_faces), landmark_faces=len(faces),
                   landmarks_valid=bool(faces),
                   face_detector="YOLOv8n-face + MediaPipe")
        primary_face_box = None
        if faces:
            # Largest mesh is the participant used for gaze. Merged YOLO boxes
            # have a different order and must never index the landmark array.
            index = max(range(len(faces)), key=lambda i: landmark_boxes[i][2] * landmark_boxes[i][3])
            primary_face_box = landmark_boxes[index]
            points = faces[index]
            if index < len(result.facial_transformation_matrixes):
                matrix = np.asarray(result.facial_transformation_matrixes[index])[:3, :3]
                if matrix.shape == (3, 3) and np.isfinite(matrix).all():
                    angles = cv2.RQDecomp3x3(matrix)[0]
                    out["pitch"], out["yaw"], out["roll"] = map(float, angles)
                    out["pose_valid"] = bool(np.isfinite(angles).all())
            from .gaze import extract_eye_features
            out.update(extract_eye_features(points, w, h))
        from .illumination import measure_illumination
        out.update(measure_illumination(bgr, primary_face_box))
        from .objects import paper_candidates
        exclusions = out["face_boxes"] + [item["box"] for item in detections]
        out["objects"].extend(paper_candidates(bgr, exclusions))
        out["phone"] = bool(out["phone_boxes"])
        out["phone_confidence"] = max([b["confidence"] for b in out["phone_boxes"]], default=0)
        # Geometry is only applied to YOLO phone crops, followed across frames.
        # A possible screen-directed pose is never proof of shutter activation.
        out.update(self.phone_pose.update(bgr, out["phone_boxes"], face_boxes=out["face_boxes"]))
        return out

    def close(self):
        self.face.close()
        self.face_detector.close()


class Camera:
    """Independent preview and inference workers with one replaceable frame slot.

    Slow inference skips old frames; it never holds up capture or builds a queue.
    Callback evidence is encoded from the exact analyzed frame, not the preview.
    """
    PREVIEW_FPS = 24
    ANALYSIS_FPS = 10
    ANALYSIS_MAX_AGE = 3.0

    def __init__(self, model_dir, callback):
        self.model_dir, self.callback = Path(model_dir), callback
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.thread = self.analysis_thread = None
        self.stop_flag = threading.Event()
        self.state = self._initial_state()
        self.jpeg = self.raw_jpeg = None
        self.baseline = None
        self.samples = []
        self.calibrating = False
        self.calibration = None
        self.policy = None
        self._latest_frame = None
        self._capture_at = self._analyzed_at = 0.0
        self._preview_sequence = 0

    @staticmethod
    def _initial_state():
        return {"running": False, "camera_ok": False, "calibrated": False,
                "calibration_mode": "none", "gaze_monitoring": "unavailable", "head_calibrated": False,
                "faces": 0, "fps": 0, "capture_fps": 0, "analysis_fps": 0,
                "analysis_ready": False, "analysis_ms": 0, "frame_seq": 0,
                "analyzed_seq": 0, "analyzed_at": 0, "width": 0, "height": 0,
                "error": "", "brightness": 0, "calibration_progress": 0,
                "calibrating": False, "calibration_version": 0, "calibration": None,
                "objects": [], "phone_boxes": [], "face_boxes": []}

    def status(self):
        with self.lock:
            now = time.monotonic()
            # A stopped/overloaded analyzer cannot drive collect(). Keep the
            # wizard deadline alive through ordinary status polling as well.
            if self.calibration is not None and self.calibration.active:
                self.calibration.check_timeout(now)
                progress = self.calibration.status(now=now)
                self.calibrating = self.calibration.active
                self.state.update(calibrating=self.calibrating, calibration=progress,
                                  calibration_progress=progress["progress"])
                if progress["phase"] == "failed":
                    self.state["error"] = progress["message"]
                self._sync_calibration()
            state = dict(self.state)
            age = now - self._analyzed_at if self._analyzed_at else None
            state["last_analysis_age_ms"] = round(age * 1000) if age is not None else None
            state["camera_ok"] = bool(state["camera_ok"] and now - self._capture_at < 2)
            state["analysis_ready"] = bool(state["analysis_ready"] and state["camera_ok"]
                                           and age is not None and age < self.ANALYSIS_MAX_AGE)
            return state

    def available(self):
        return all((self.model_dir / f).exists() for f in ("yolov8n.onnx", "yolov8n-face.onnx", "face_landmarker.task"))

    def start(self, index=0):
        with self.lock:
            if any(t and t.is_alive() for t in (self.thread, self.analysis_thread)):
                if self.stop_flag.is_set():
                    raise ValueError("Камера ещё завершается. Повторите включение через несколько секунд.")
                return
            if not self.available():
                raise ValueError("Модели не найдены. Выполните установку из инструкции.")
            # Never clear a stopped worker's event: it may still be returning
            # from a slow camera driver. Each run owns its own stop event.
            self.stop_flag = stop = threading.Event()
            self.state = self._initial_state()
            self.state["running"] = True
            self.jpeg = self.raw_jpeg = None
            self.baseline = None
            self.samples = []
            self.calibrating = False
            self.calibration = None
            self._latest_frame = None
            self._capture_at = self._analyzed_at = 0.0
            # Stream sequence remains monotonic across camera restarts.
            self.thread = threading.Thread(target=self._capture_loop, args=(index, stop),
                                           daemon=True, name="qorgau-camera")
            self.analysis_thread = threading.Thread(target=self._analysis_loop, args=(stop,),
                                                    daemon=True, name="qorgau-analysis")
            self.thread.start()
            self.analysis_thread.start()

    def stop(self):
        with self.condition:
            self.stop_flag.set()
            self.condition.notify_all()
            workers = (self.thread, self.analysis_thread)
        deadline = time.monotonic() + 5
        for worker in workers:
            if worker and worker != threading.current_thread():
                worker.join(timeout=max(0, deadline - time.monotonic()))
        with self.condition:
            self.state.update(running=False, camera_ok=False, analysis_ready=False, calibrated=False,
                              fps=0, capture_fps=0, analysis_fps=0)
            self.jpeg = self.raw_jpeg = None
            self._latest_frame = None
            self.baseline = None
            self.samples = []
            self.calibrating = False
            self.calibration = None
            self.state.update(calibrating=False, calibration_version=0, calibration=None, calibration_progress=0)
            self._sync_calibration()
            self.condition.notify_all()

    def wait_for_jpeg(self, previous_seq=0, timeout=1.0):
        """Return a fresh (sequence, JPEG), or (sequence, None) on stop/timeout."""
        with self.condition:
            self.condition.wait_for(lambda: self.stop_flag.is_set() or
                                    (self.jpeg is not None and self._preview_sequence != previous_seq), timeout)
            if self.stop_flag.is_set() or self._preview_sequence == previous_seq:
                return self._preview_sequence, None
            return self._preview_sequence, self.jpeg

    def calibrate(self):
        with self.lock:
            state = self.status()
            if not state["camera_ok"] or not state["analysis_ready"]:
                raise ValueError("Дождитесь изображения камеры и запуска анализа.")
            from .screen_calibration import ScreenCalibration
            self.calibration = ScreenCalibration()
            self.samples, self.calibrating = [], True
            self.baseline = None
            self.state.update(calibrated=False, calibrating=True, calibration_version=0,
                              calibration=self.calibration.status(), calibration_progress=0, error="")
            self._sync_calibration()
            return self.calibration.status()

    def calibration_target(self, step, viewport=None, calibration_id=None):
        with self.lock:
            if self.calibration is None:
                raise ValueError("Сначала запустите калибровку.")
            if calibration_id is not None and calibration_id != self.calibration.id:
                raise ValueError("Эта калибровка уже заменена новой попыткой.")
            try:
                result = self.calibration.acknowledge(step, viewport)
            finally:
                self._sync_calibration()
            return result

    def cancel_calibration(self, calibration_id=None):
        """Keep wizard checkpoints by ID; an explicit no-ID participant reset clears all."""
        with self.lock:
            if calibration_id is not None and (self.calibration is None or calibration_id != self.calibration.id):
                return self.calibration.status() if self.calibration else None
            if calibration_id is None:
                self.calibration = None
            elif self.calibration is not None:
                self.calibration.cancel()
            self.samples = []
            self._sync_calibration()
            return self.state.get("calibration")

    def use_calibration(self, viewport=None):
        """Freeze available stable observations when starting an exam."""
        with self.lock:
            if self.calibration is not None:
                if viewport is not None and self.calibration.viewport:
                    try:
                        keys = ("screen_width", "screen_height", "device_pixel_ratio")
                        values = [float(viewport[key]) for key in keys]
                        matches = all(math.isfinite(value) and value > 0 and
                                      abs(value - self.calibration.viewport[key]) <= (0.001 if key == "device_pixel_ratio" else 2)
                                      for key, value in zip(keys, values))
                    except (KeyError, TypeError, ValueError, OverflowError):
                        matches = False
                    if not matches:
                        self.calibration.geometry_valid = False
                        self.calibration.fail("Экран изменился. Тест доступен без калиброванного взгляда.",
                                              "viewport_changed")
                self.calibration.use_saved()
            self.samples = []
            self._sync_calibration()
            return self.status()

    def _sync_calibration(self):
        """Caller holds the camera lock; refresh capabilities and discard stale estimates."""
        from .gaze import estimate_screen_gaze
        calibration = self.calibration
        self.baseline = (calibration.profile or calibration.partial_profile) if calibration else None
        self.calibrating = bool(calibration and calibration.active)
        status = calibration.status() if calibration else None
        capabilities = (calibration.capabilities() if calibration else
                        {"calibrated": False, "calibration_mode": "none",
                         "gaze_monitoring": "unavailable", "head_calibrated": False})
        self.state.update(capabilities, calibrating=self.calibrating,
                          calibration_version=2 if capabilities["calibrated"] else 0,
                          calibration=status, calibration_progress=status["progress"] if status else 0)
        self.state.update(estimate_screen_gaze(self.state, self.baseline))

    @staticmethod
    def _fps(samples, now):
        samples.append(now)
        while len(samples) > 2 and now - samples[0] > 1.5:
            samples.popleft()
        return round((len(samples) - 1) / max(now - samples[0], 0.001), 1) if len(samples) > 1 else 0

    def _failed(self, exc, stop):
        if stop.is_set():
            return
        with self.condition:
            self.state.update(error=str(exc), camera_ok=False, analysis_ready=False, running=False)
            stop.set()
            self.condition.notify_all()
        self.callback(self.status(), None)

    @staticmethod
    def _open_capture(cv2, index):
        import sys
        backends = (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY) if sys.platform == "win32" else (cv2.CAP_ANY,)
        for backend in backends:
            capture = cv2.VideoCapture(index, backend)
            if capture.isOpened():
                # MJPEG avoids pushing uncompressed 720p through a USB webcam.
                capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                capture.set(cv2.CAP_PROP_FPS, 30)
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return capture
            capture.release()
        raise RuntimeError("Камера не открылась. Разрешите доступ и закройте другие приложения с камерой.")

    def _capture_loop(self, index, stop):
        capture = None
        try:
            import cv2
            # OpenCV transforms should not compete with the two ONNX CPU threads.
            cv2.setNumThreads(1)
            capture = self._open_capture(cv2, index)
            capture_times, preview_times = deque(maxlen=90), deque(maxlen=90)
            sequence, next_preview = 0, 0.0
            last_callback = -1e9
            while not stop.is_set():
                started = time.monotonic()
                ok, frame = capture.read()
                captured_at = time.monotonic()
                if stop.is_set():
                    break
                if not ok or frame is None:
                    with self.lock:
                        self.state.update(camera_ok=False, analysis_ready=False,
                                          error="Потеряно изображение камеры")
                    if captured_at - last_callback >= 1:
                        self.callback(self.status(), None)
                        last_callback = captured_at
                    stop.wait(0.1)
                    continue
                sequence += 1
                h, w = frame.shape[:2]
                with self.condition:
                    if stop.is_set():
                        break
                    self._latest_frame = sequence, captured_at, frame
                    self._capture_at = captured_at
                    self.state.update(camera_ok=True, width=w, height=h,
                                      capture_fps=self._fps(capture_times, captured_at))
                    if self.state.get("error") == "Потеряно изображение камеры":
                        self.state["error"] = ""
                    overlays = dict(self.state) if captured_at - self._analyzed_at < 0.6 else {}
                    self.condition.notify_all()
                if captured_at >= next_preview:
                    next_preview = max(next_preview + 1 / self.PREVIEW_FPS, captured_at)
                    annotated = frame.copy()
                    for box in overlays.get("face_boxes", []):
                        x, y, bw, bh = map(int, box)
                        cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (0, 0, 0), 4)
                        cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (255, 255, 255), 2)
                    for phone in overlays.get("phone_boxes", []):
                        x, y, bw, bh = map(int, phone["box"])
                        cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (0, 0, 0), 4)
                        cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (255, 255, 255), 2)
                        cv2.putText(annotated, f"PHONE {phone['confidence']:.0%}", (x, max(18, y - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
                        cv2.putText(annotated, f"PHONE {phone['confidence']:.0%}", (x, max(18, y - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                    object_labels = {"book": "BOOK", "laptop": "LAPTOP", "monitor": "SCREEN", "paper_candidate": "POSSIBLE TEXT SHEET"}
                    for item in overlays.get("objects", []):
                        x, y, bw, bh = map(int, item["box"])
                        color = (255, 255, 255)
                        cv2.rectangle(annotated, (x, y), (x + bw, y + bh), (0, 0, 0), 4)
                        cv2.rectangle(annotated, (x, y), (x + bw, y + bh), color, 2)
                        label = object_labels.get(item["kind"], "OBJECT")
                        if item.get("confidence") is not None:
                            label += f" {item['confidence']:.0%}"
                        cv2.putText(annotated, label, (x, max(18, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
                        cv2.putText(annotated, label, (x, max(18, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
                    encoded, jpg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 88])
                    if encoded:
                        with self.condition:
                            if stop.is_set():
                                break
                            self._preview_sequence += 1
                            self.jpeg = jpg.tobytes()
                            self.state.update(frame_seq=self._preview_sequence,
                                              fps=self._fps(preview_times, time.monotonic()))
                            self.condition.notify_all()
                # A backend that immediately returns buffered frames must not spin.
                stop.wait(max(0, 1 / 30 - (time.monotonic() - started)))
        except Exception as exc:
            self._failed(exc, stop)
        finally:
            try:
                if capture is not None:
                    capture.release()
            finally:
                with self.condition:
                    self.state.update(running=False, camera_ok=False, analysis_ready=False)
                    self.condition.notify_all()

    def _calibrate_and_gaze(self, signals, np):
        """Accept only acknowledged visible targets; caller holds self.lock."""
        from .gaze import estimate_screen_gaze
        if self.calibration is not None:
            if self.calibration.active:
                self.calibration.collect(signals)
            self.calibrating = self.calibration.active
            self.baseline = self.calibration.profile or self.calibration.partial_profile
            status = self.calibration.status()
            self.state["calibration_progress"] = status["progress"]
            signals["calibration"] = status
            if status["phase"] == "failed":
                self.state["error"] = status["message"]
        full = bool(isinstance(self.baseline, dict) and self.baseline.get("version") == 2
                    and self.baseline.get("approximate") is not True)
        signals.update(self.calibration.capabilities() if self.calibration else {
            "calibrated": full, "calibration_mode": "full" if full else "none",
            "gaze_monitoring": "calibrated" if full else "unavailable", "head_calibrated": full})
        signals["calibration_version"] = 2 if signals["calibrated"] else 0
        signals["calibrating"] = self.calibrating
        signals.update(estimate_screen_gaze(signals, self.baseline if isinstance(self.baseline, dict) else None))

    def _analysis_loop(self, stop):
        models = None
        try:
            import cv2
            import numpy as np
            models = VisionModels(self.model_dir)
            processed = 0
            analysis_times = deque(maxlen=60)
            while not stop.is_set():
                with self.condition:
                    self.condition.wait_for(lambda: stop.is_set() or
                        (self._latest_frame is not None and self._latest_frame[0] != processed), 0.5)
                    if stop.is_set():
                        break
                    if self._latest_frame is None or self._latest_frame[0] == processed:
                        continue
                    processed, captured_at, frame = self._latest_frame
                started = time.monotonic()
                signals = models.analyze(frame)
                completed = time.monotonic()
                if stop.is_set():
                    break
                # A hung/overloaded model must not apply an old detection to a
                # current exam; capture continues while this result is discarded.
                if completed - captured_at >= self.ANALYSIS_MAX_AGE:
                    with self.lock:
                        self.state.update(analysis_ready=False, analysis_ms=round((completed - started) * 1000, 1))
                    continue
                encoded, raw = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
                evidence = raw.tobytes() if encoded else None
                with self.lock:
                    if stop.is_set():
                        break
                    # Target acknowledgement compares capture time, never the
                    # later inference completion time or a reused preview.
                    signals.update(analyzed_seq=processed, analyzed_at=captured_at,
                                   analysis_ready=True, camera_ok=completed - self._capture_at < 2,
                                   running=True)
                    self._calibrate_and_gaze(signals, np)
                    signals.update(analysis_ms=round((completed - started) * 1000, 1),
                                   analysis_fps=self._fps(analysis_times, completed),
                                   analysis_ready=True, camera_ok=completed - self._capture_at < 2,
                                   running=True, error=self.state.get("error", ""),
                                   calibration_progress=self.state.get("calibration_progress", 0))
                    self._analyzed_at = captured_at
                    self.state.update(signals)
                    self.raw_jpeg = evidence
                    callback_signals = dict(self.state)
                self.callback(callback_signals, evidence)
                stop.wait(max(0, 1 / self.ANALYSIS_FPS - (time.monotonic() - started)))
        except Exception as exc:
            self._failed(exc, stop)
        finally:
            try:
                if models is not None:
                    models.close()
            finally:
                with self.condition:
                    self.state["analysis_ready"] = False
                    self.condition.notify_all()
