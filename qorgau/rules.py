"""Temporal rules independent of the camera, UI and ML frameworks."""
from dataclasses import dataclass, field
import math


LABELS = {
    "phone": ("Обнаружен телефон", "high"),
    "phone_raised": ("Телефон поднят к экрану", "high"),
    "phone_aimed": ("Возможное наведение телефона на экран", "high"),
    "phone_photo_attempt": ("Признаки возможной съёмки", "high"),
    "book": ("Книга в кадре", "medium"),
    "additional_device": ("Экран или ноутбук в кадре", "medium"),
    "paper_candidate": ("Возможный лист с текстом", "medium"),
    "look_down": ("Длительный взгляд вниз", "medium"),
    "look_left": ("Длительный взгляд влево", "medium"),
    "look_right": ("Длительный взгляд вправо", "medium"),
    "look_side": ("Длительный взгляд в сторону", "medium"),
    "no_face": ("Человек отсутствует в кадре", "high"),
    "multiple_faces": ("В кадре несколько лиц", "high"),
    "low_light": ("Недостаточное освещение", "technical"),
    "camera_lost": ("Камера недоступна", "technical"),
    "analysis_lost": ("Распознавание камеры недоступно", "technical"),
    "focus_lost": ("Окно теста потеряло фокус", "medium"),
    "tab_hidden": ("Тест скрыт или переключена вкладка", "medium"),
    "fullscreen_exit": ("Выход из полноэкранного режима", "medium"),
    "shortcut": ("Попытка запрещённого действия", "medium"),
    "navigation": ("Переход или открытие окна заблокированы", "medium"),
    "emergency_exit": ("Аварийный выход из экзамена", "technical"),
    "guard_lost": ("Защита окна недоступна", "technical"),
    "monitor": ("Обнаружено несколько мониторов", "technical"),
    "gaze_outside": ("Взгляд за пределами экрана", "high"),
    "head_outside": ("Голова отклонена от калиброванного положения", "high"),
    "auto_disqualified": ("Попытка автоматически остановлена", "high"),
    "instructor_unlock": ("Преподаватель разрешил новую попытку", "technical"),
}


@dataclass
class Policy:
    gaze_seconds: float = 4.0
    no_face_seconds: float = 3.0
    multiple_faces_seconds: float = 1.5
    phone_seconds: float = 0.0
    yaw_degrees: float = 25.0
    pitch_degrees: float = 20.0
    iris_delta: float = 0.19
    cooldown: float = 12.0
    gaze_enabled: bool = True
    strict: bool = False


class TemporalRules:
    """One event per sustained episode. Brief blinks never trigger absence."""
    def __init__(self, policy=None):
        self.policy = policy or Policy()
        self.started = {}
        self.fired = set()
        self.last = {}
        self.object_frames = {}
        self.last_object_seq = None
        self.last_object_at = None

    def reset(self):
        self.started.clear()
        self.fired.clear()
        self.last.clear()
        self.object_frames.clear()
        self.last_object_seq = self.last_object_at = None

    def evaluate(self, signals, now):
        p = self.policy
        # Poor light is a technical problem, not evidence that somebody left.
        image_ok = signals.get("camera_ok", True)
        lit = signals.get("brightness", 100) >= 30
        usable = image_ok and lit and signals.get("analysis_ready", True)
        faces = signals.get("faces", 0)
        calibrated = signals.get("calibrated", False)
        left = signals.get("looking_left", False)
        right = signals.get("looking_right", False)
        objects = matching_objects(signals)
        # Numbered camera results must be fresh and consecutive. A blind gap
        # cannot prove continuous gaze, absence or a second face either.
        sequence = signals.get("analyzed_seq", now)
        fresh = self.last_object_seq is None or sequence > self.last_object_seq
        numbered = signals.get("analyzed_seq") is not None
        if self.last_object_at is not None and now - self.last_object_at > 1.0:
            interrupted = set(OBJECT_KINDS)
            if numbered:
                interrupted.update({"phone", "phone_raised", "phone_aimed", "phone_photo_attempt",
                                    "look_down", "look_left", "look_right", "look_side", "no_face", "multiple_faces"})
            for kind in interrupted:
                self.started.pop(kind, None)
                self.fired.discard(kind)
                self.object_frames.pop(kind, None)
        if fresh:
            self.last_object_seq, self.last_object_at = sequence, now
        if numbered and not fresh:
            usable = False
        gaze_valid = usable and faces == 1 and calibrated and p.gaze_enabled and not p.strict and signals.get("gaze_valid", True)
        phone_confidence = signals.get("phone_confidence")
        phone_detected = signals.get("phone", False) and (not p.strict or (
            finite_number(phone_confidence) and 0.40 <= phone_confidence <= 1.0))
        conditions = {
            "phone": (usable and phone_detected, p.phone_seconds),
            # Motion/hold are already validated by the vision tracker. Record
            # only an observation actually available on this first frame.
            "phone_raised": (usable and phone_detected and signals.get("phone_raised", False), 0.0),
            "phone_aimed": (usable and phone_detected and signals.get("phone_aimed", False), 0.0),
            "phone_photo_attempt": (usable and phone_detected and signals.get("phone_photo_attempt", False), 0.0),
            "book": (usable and fresh and bool(objects["book"]), 1.2),
            "additional_device": (usable and fresh and bool(objects["additional_device"]), 1.2),
            "paper_candidate": (usable and fresh and bool(objects["paper_candidate"]), 2.0),
            "look_down": (gaze_valid and signals.get("looking_down", False), p.gaze_seconds),
            "look_left": (gaze_valid and left, p.gaze_seconds),
            "look_right": (gaze_valid and right, p.gaze_seconds),
            "look_side": (gaze_valid and not (left or right) and signals.get("looking_side", False), p.gaze_seconds),
            "no_face": (usable and faces == 0, p.no_face_seconds),
            "multiple_faces": (usable and faces > 1, p.multiple_faces_seconds),
            "low_light": (image_ok and not lit, 4.0),
            "camera_lost": (not image_ok, 2.0),
            "analysis_lost": (image_ok and not signals.get("analysis_ready", True), 2.0),
        }
        events = []
        for kind, (active, delay) in conditions.items():
            if not active:
                self.started.pop(kind, None)
                self.fired.discard(kind)
                self.object_frames.pop(kind, None)
                continue
            self.started.setdefault(kind, now)
            if kind in OBJECT_KINDS:
                self.object_frames[kind] = self.object_frames.get(kind, 0) + 1
            duration = now - self.started[kind]
            enough_frames = kind not in OBJECT_KINDS or self.object_frames[kind] >= 3
            if enough_frames and duration >= delay and kind not in self.fired and now - self.last.get(kind, -1e9) >= p.cooldown:
                self.fired.add(kind)
                self.last[kind] = now
                confidence = signals.get("phone_confidence") if kind.startswith("phone") else None
                if kind in {"book", "additional_device"}:
                    confidence = max(item["confidence"] for item in objects[kind])
                events.append({"kind": kind, "duration": round(duration, 1), "confidence": confidence})
        return events


OBJECT_KINDS = ("book", "additional_device", "paper_candidate")


def matching_objects(signals):
    """Review candidates. No material other than a phone ends exam access."""
    groups = {kind: [] for kind in OBJECT_KINDS}
    for item in signals.get("objects", []):
        kind = item.get("kind")
        if kind == "paper_candidate" and item.get("method") == "paper_geometry":
            groups["paper_candidate"].append(item)
        elif kind in {"book", "laptop", "monitor"}:
            confidence = item.get("confidence")
            if isinstance(confidence, (int, float)) and math.isfinite(confidence) and confidence >= 0.55:
                groups["book" if kind == "book" else "additional_device"].append(item)
    return groups


class FreshDetectionGate:
    """Trust only distinct, recent captures taken after the attempt was armed.

    A rejected result still consumes its sequence, preventing a cached frame
    from becoming new evidence when its signal flags later change.
    """
    seconds = 0.0
    min_frames = 1
    max_gap = 1.0

    def __init__(self):
        self.reset()

    def reset(self, armed_at=None, baseline_seq=None):
        self.armed_at = armed_at
        self.last_seq = baseline_seq if isinstance(baseline_seq, int) and not isinstance(baseline_seq, bool) else None
        self.started = self.last_at = None
        self.frames = 0

    def fresh(self, signals, now):
        seq = signals.get("analyzed_seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= 0:
            return False
        if self.last_seq is not None and seq <= self.last_seq:
            return False
        self.last_seq = seq
        captured_at = signals.get("analyzed_at")
        if not finite_number(captured_at) or not finite_number(now):
            return False
        if not 0 <= now - captured_at <= self.max_gap:
            return False
        if self.armed_at is not None and captured_at < self.armed_at:
            return False
        return True


def usable_image(signals):
    brightness = signals.get("brightness")
    return (signals.get("camera_ok") is True and signals.get("analysis_ready") is True
            and finite_number(brightness) and brightness >= 30)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class PhoneRemovalGate(FreshDetectionGate):
    """The first recognized phone ends access; there is no hold/area filter."""
    confidence = 0.40

    def evaluate(self, signals, now):
        fresh = self.fresh(signals, now)
        confidence = signals.get("phone_confidence")
        if not (fresh and usable_image(signals) and signals.get("phone") is True and finite_number(confidence)
                and self.confidence <= confidence <= 1.0):
            return False
        self.started = self.last_at = now
        self.frames = 1
        return True


class AttentionRemovalGate(FreshDetectionGate):
    """Repeated resolvable gaze, or immediate independent head movement.

    The gaze interval uses the calibration's measured uncertainty allowance,
    not a confidence probability. One noisy position near the edge is not
    sufficient evidence. Capture sequence numbers may skip because inference
    consumes the newest camera frame; their order and capture times must still
    increase, and every observed result must remain valid for the whole episode.
    """
    head_limit_deg = 5.0
    gaze_uncertainty_max = 0.10
    seconds = 0.25
    min_frames = 3
    max_observation_gap = 3.0

    def reset(self, armed_at=None, baseline_seq=None):
        super().reset(armed_at=armed_at, baseline_seq=baseline_seq)
        self._last_capture_at = None
        self._gaze_sides = set()

    def _clear_gaze(self):
        # Keep the anti-replay sequence and capture-time high-water marks.
        self.started = self.last_at = None
        self.frames = 0
        self._gaze_sides.clear()

    def interrupt_gaze(self):
        """Forget pending evidence when the outer runtime rejects a frame."""
        self._clear_gaze()

    def evaluate(self, signals, now):
        if not self.fresh(signals, now):
            self._clear_gaze()
            return None
        captured_at = signals["analyzed_at"]
        previous_capture = self._last_capture_at
        if previous_capture is not None and captured_at <= previous_capture:
            self._clear_gaze()
            return None
        self._last_capture_at = captured_at
        if previous_capture is not None and captured_at - previous_capture > self.max_observation_gap:
            self._clear_gaze()
        if not (usable_image(signals) and signals.get("faces") == 1
                and signals.get("landmarks_valid") is True):
            self._clear_gaze()
            return None
        full_calibration = (signals.get("calibrated") is True
                            and signals.get("calibration_version") == 2
                            and signals.get("calibration_mode", "full") == "full")
        head_calibrated = signals.get("head_calibrated", full_calibration) is True
        if (head_calibrated and signals.get("head_pose_valid") is True and signals.get("head_outside") is True
                and all(finite_number(signals.get(key)) for key in ("yaw_delta", "pitch_delta", "roll_delta"))
                and max(abs(signals[key]) for key in ("yaw_delta", "pitch_delta", "roll_delta")) > self.head_limit_deg):
            self._clear_gaze()
            return "head"
        if not (full_calibration and signals.get("gaze_outside_confirmable") is not False
                and signals.get("gaze_valid") is True and signals.get("eye_open") is True
                and signals.get("eye_features_valid") is not False
                and signals.get("screen_outside") is True
                and all(finite_number(signals.get(key)) for key in ("gaze_screen_x", "gaze_screen_y", "gaze_uncertainty"))
                and 0 <= signals["gaze_uncertainty"] <= self.gaze_uncertainty_max):
            self._clear_gaze()
            return None
        x, y, margin = (signals[key] for key in ("gaze_screen_x", "gaze_screen_y", "gaze_uncertainty"))
        sides = {side for side, outside in (
            ("left", x + margin < 0), ("right", x - margin > 1),
            ("up", y + margin < 0), ("down", y - margin > 1)) if outside}
        if not sides:
            self._clear_gaze()
            return None
        if self._gaze_sides and not (self._gaze_sides & sides):
            self._clear_gaze()
        if self.started is None:
            self.started = captured_at
            self._gaze_sides = sides
        else:
            # The entire episode needs at least one consistently outside edge;
            # alternating left/right positions do not prove sustained gaze.
            self._gaze_sides.intersection_update(sides)
        self.last_at = captured_at
        self.frames += 1
        if self.frames >= self.min_frames and captured_at - self.started >= self.seconds:
            return "gaze"
        return None


def attention_summary(events):
    """Number of episodes to review; never a probability of cheating."""
    unresolved = [e for e in events if e.get("review", "pending") == "pending"]
    high = sum(e["severity"] == "high" for e in unresolved)
    medium = sum(e["severity"] == "medium" for e in unresolved)
    technical = sum(e["severity"] == "technical" for e in unresolved)
    return {"pending": len(unresolved), "high": high, "medium": medium,
            "technical": technical, "label": "Нужен просмотр" if high or medium else "Без эпизодов для проверки"}
