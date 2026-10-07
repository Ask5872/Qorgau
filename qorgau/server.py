from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import asyncio
import csv
import html
import io
import json
import secrets
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .auth import Auth
from .exam import BANK, make_exam, public_exam, grade
from .guard import DesktopGuard
from .rules import Policy, TemporalRules, PhoneRemovalGate, AttentionRemovalGate, FreshDetectionGate, LABELS, attention_summary, matching_objects
from .store import Store
from .vision import Camera


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalibrationViewport(StrictModel):
    width: float = Field(gt=0, le=32000, allow_inf_nan=False)
    height: float = Field(gt=0, le=32000, allow_inf_nan=False)
    screen_width: float = Field(gt=0, le=32000, allow_inf_nan=False)
    screen_height: float = Field(gt=0, le=32000, allow_inf_nan=False)
    device_pixel_ratio: float = Field(gt=0, le=10, allow_inf_nan=False)


class Start(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    group: str = Field(default="", max_length=60)
    consent: bool = False
    snapshots: bool = False
    duration_minutes: int = Field(default=20, ge=1, le=180)
    gaze_seconds: float = Field(default=AttentionRemovalGate.seconds, ge=0, le=15)
    gaze_enabled: bool = True
    calibration_viewport: CalibrationViewport | None = None


class CameraAction(StrictModel):
    on: bool = True
    index: int = Field(default=0, ge=0, le=8)


class CalibrationTarget(StrictModel):
    calibration_id: str = Field(min_length=1, max_length=100)
    # The current wizard has five targets: center and four corners.
    step: int = Field(ge=0, le=4)
    viewport: CalibrationViewport | None = None


class CalibrationCancel(StrictModel):
    calibration_id: str = Field(min_length=1, max_length=100)


class Answer(StrictModel):
    question_id: str
    option: int = Field(ge=0, le=3)


class ClientEvent(StrictModel):
    kind: str = Field(max_length=60)
    detail: str = Field(default="", max_length=500)


class Review(StrictModel):
    decision: str
    note: str = Field(default="", max_length=1000)


class Login(StrictModel):
    pin: str = Field(min_length=1, max_length=100)


class ParticipantLogin(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    group: str = Field(min_length=1, max_length=60)

    @field_validator("name", "group", mode="before")
    @classmethod
    def trim_identity(cls, value):
        # The local entry form identifies an attempt; it is not an account
        # password and never grants access to teacher APIs.
        return value.strip() if isinstance(value, str) else value


class ExamQuestion(StrictModel):
    id: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9_-]*$")
    text: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(min_length=4, max_length=4)
    correct: int = Field(ge=0, le=3)

    @field_validator("text")
    @classmethod
    def nonempty_text(cls, value):
        if not value.strip():
            raise ValueError("Введите текст вопроса")
        return value.strip()

    @field_validator("options")
    @classmethod
    def valid_options(cls, values):
        if any(not v.strip() or len(v) > 1000 for v in values):
            raise ValueError("Заполните четыре варианта ответа, до 1000 символов каждый")
        return [v.strip() for v in values]


class ExamConfig(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    duration_minutes: int = Field(default=20, ge=1, le=180)
    gaze_seconds: float = Field(default=AttentionRemovalGate.seconds, ge=0, le=15)
    questions: list[ExamQuestion] = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if not value.strip():
            raise ValueError("Введите название экзамена")
        return value.strip()

    @model_validator(mode="after")
    def unique_questions(self):
        ids = [q.id or f"q{i+1}" for i, q in enumerate(self.questions)]
        if len(ids) != len(set(ids)):
            raise ValueError("Идентификаторы вопросов должны быть уникальными")
        for q, qid in zip(self.questions, ids):
            q.id = qid
        return self


def calibration_capabilities(state):
    """Capabilities belong to the measured profile, never to the start form."""
    calibration = state.get("calibration") or {}
    full = (state.get("calibrated") is True and state.get("calibration_version") == 2
            and state.get("calibration_mode", "full") == "full")
    mode = "full" if full else "partial" if state.get("calibration_mode") == "partial" else "none"
    gaze = "calibrated" if full else "approximate" if (
        mode == "partial" and state.get("gaze_monitoring") == "approximate") else "unavailable"
    head = state.get("head_calibrated", full) is True
    completed = calibration.get("completed_points") or []
    saved = calibration.get("saved_points", len(completed) if isinstance(completed, list) else 0)
    return {"calibration_mode": mode, "gaze_monitoring": gaze, "head_calibrated": head,
            "gaze_auto_remove": full, "head_auto_remove": head,
            "calibration_saved_points": saved,
            "calibration_total_points": calibration.get("total", 5),
            "calibration_quality": calibration.get("quality") or {}}


class Runtime:
    def __init__(self, root, data, demo=False, reset_pin=False, initial_pin=None):
        self.root, self.demo = Path(root), demo
        self.store = Store(data)
        self.auth = Auth(data, reset_pin, initial_pin)
        self.csrf = secrets.token_urlsafe(32)
        self.active_id = None
        self.last_id = None
        # This is a local shared workstation, not a student account system.
        # Keep the participant's result separate from the teacher's last report
        # so a handoff hides it without deleting the preserved attempt.
        self.participant_visible_id = None
        self.participant_profile = None
        self.last_seen = time.monotonic()
        self.last_events = {}
        self.lock = threading.RLock()
        self.cleanup_in_progress = False
        self.rules = TemporalRules()
        self.phone_gate = PhoneRemovalGate()
        self.attention_gate = AttentionRemovalGate()
        self.frame_gate = FreshDetectionGate()
        self.armed_at = None
        self.entry_block = self.store.get_setting("entry_block")
        self.exam_config = self.store.get_setting("exam_config")
        if self.entry_block:
            self.last_id = self.entry_block["session_id"]
        self.camera = Camera(self.root / "models", self.on_frame)
        self.guard = DesktopGuard(self.emit, self.emergency)
        self.isolation_control = None
        self.closed = threading.Event()

    def emit(self, kind, detail="", **kwargs):
        with self.lock:
            if not self.active_id:
                return None
            key = (kind, detail)
            if time.monotonic() - self.last_events.get(key, -1e9) < 2:
                return None
            self.last_events[key] = time.monotonic()
            return self.store.add_event(self.active_id, kind, detail, **kwargs)

    def on_frame(self, signals, jpeg):
        finished = None
        with self.lock:
            if self.demo or not self.active_id:
                return
            now = time.monotonic()
            session = self.store.session(self.active_id)
            deadline = session["started"] + session["config"]["duration_minutes"] * 60
            if time.time() >= deadline:
                finished = self._finish_locked()
            elif self.armed_at is not None and self.frame_gate.fresh(signals, now):
                # A later frame cannot silently upgrade the capabilities that
                # were recorded and disclosed when this attempt started.
                config = session["config"]
                signals = dict(signals)
                full = (signals.get("calibrated") is True and signals.get("calibration_version") == 2
                        and signals.get("calibration_mode", "full") == "full")
                signals["head_calibrated"] = (config.get("head_auto_remove") is True
                                               and signals.get("head_calibrated", full) is True)
                signals["calibrated"] = config.get("gaze_auto_remove") is True and full
                if not signals["calibrated"]:
                    signals["gaze_outside_confirmable"] = False
                for event in self.rules.evaluate(signals, now):
                    evidence = None
                    if session["config"]["snapshots"] and jpeg:
                        evidence = self.active_id + "_" + secrets.token_hex(6) + ".jpg"
                        (self.store.folder / "evidence" / evidence).write_bytes(jpeg)
                    detail = ""
                    observations = None
                    if event["kind"] == "phone_raised":
                        detail = "Наблюдалось движение телефона вверх в область перед экраном. Направление его камеры и факт съёмки не установлены."
                    elif event["kind"] == "phone_aimed":
                        detail = "Телефон устойчиво удерживается перед экраном; видимая плоскость близка к направлению веб-камеры. Возможное наведение: сторона с камерой и нажатие кнопки съёмки не установлены."
                    elif event["kind"] == "phone_photo_attempt":
                        detail = "Один и тот же телефон поднят и затем устойчиво удерживается плоскостью к веб-камере рядом с лицом. Последовательность совместима с подготовкой к съёмке экрана; направление объектива и факт фотографии не установлены."
                    elif event["kind"] == "book":
                        detail = "Модель устойчиво обнаруживает книгу. Содержание и использование книги не установлены; требуется просмотр преподавателем."
                    elif event["kind"] == "additional_device":
                        detail = "В кадре устойчиво виден экран или ноутбук. Не установлено, принадлежит ли он экзаменационному компьютеру и используется ли для подсказок."
                    elif event["kind"] == "paper_candidate":
                        detail = "Наблюдается светлый прямоугольный предмет с признаками строк текста. Это геометрическая оценка возможного листа; содержание и наличие шпаргалки не установлены."
                    elif event["kind"] in {"look_left", "look_right", "look_down", "look_side"}:
                        directions = {"look_left": "влево", "look_right": "вправо", "look_down": "вниз", "look_side": "в сторону"}
                        detail = "Длительное отклонение взгляда " + directions[event["kind"]] + " относительно калибровки. Направление указано со стороны участника; источник подсказки и точная точка взгляда не установлены."
                    if event["kind"] in {"phone_raised", "phone_aimed", "phone_photo_attempt"}:
                        observations = signals.get("photo_attempt_details" if event["kind"] == "phone_photo_attempt" else "phone_aim_details")
                    elif event["kind"] in {"book", "additional_device", "paper_candidate"}:
                        observations = {"objects": matching_objects(signals)[event["kind"]][:8], "review_required": True}
                    elif event["kind"] in {"look_left", "look_right", "look_down", "look_side"}:
                        observations = {key: signals.get(key) for key in
                                        ("gaze_direction", "gaze_valid", "gaze_source", "head_yaw", "head_pitch", "yaw_delta", "pitch_delta")}
                    self.emit(**event, detail=detail, evidence=evidence, observations=observations)
                phone = self.phone_gate.evaluate(signals, now)
                attention = self.attention_gate.evaluate(signals, now)
                reason = "phone" if phone else attention
                if reason:
                    descriptions = {
                        "phone": "Модель распознала телефон на первом свежем кадре (порог уверенности 0,40).",
                        "gaze": "Несколько последовательных измерений подтверждают взгляд за экран с учётом погрешности калибровки.",
                        "head": "Голова отклонена более чем на 5° от калиброванного положения.",
                    }
                    observations = {key: signals.get(key) for key in (
                        "analyzed_seq", "analyzed_at", "phone_confidence", "calibration_version",
                        "gaze_screen_x", "gaze_screen_y", "gaze_uncertainty", "gaze_valid", "screen_outside",
                        "gaze_boundary_uncertain", "gaze_outside_confirmable",
                        "head_pose_valid", "head_outside", "yaw_delta", "pitch_delta", "roll_delta")}
                    duration = (max(0., self.attention_gate.last_at - self.attention_gate.started)
                                if reason == "gaze" else 0.)
                    observations.update(reason=reason,
                                        required_frames=self.attention_gate.min_frames if reason == "gaze" else 1,
                                        delay_seconds=self.attention_gate.seconds if reason == "gaze" else 0,
                                        observed_frames=self.attention_gate.frames if reason == "gaze" else 1,
                                        uncertainty_considered=reason == "gaze")
                    evidence = None
                    if session["config"]["snapshots"] and jpeg:
                        evidence = self.active_id + "_" + secrets.token_hex(6) + ".jpg"
                        (self.store.folder / "evidence" / evidence).write_bytes(jpeg)
                    if reason in {"gaze", "head"}:
                        self.emit(reason + "_outside", descriptions[reason], duration=duration,
                                  evidence=evidence, observations=observations)
                    self.emit("auto_disqualified", descriptions[reason] +
                              " Экзамен остановлен; новая попытка доступна после решения преподавателя.",
                              duration=duration, confidence=signals.get("phone_confidence") if phone else None,
                              evidence=evidence, observations=observations)
                    finished = self._finish_locked("disqualified", reason=reason)
            elif self.armed_at is not None:
                # A rejected capture must break the pending gaze sequence even
                # when the outer freshness gate never calls AttentionRemovalGate.
                self.attention_gate.interrupt_gaze()
        if finished:
            self._release_exam()

    def public_exam_config(self):
        config = self.exam_config
        return {"configured": config is not None,
                "title": config["title"] if config else "Экзамен не настроен",
                "duration_minutes": config["duration_minutes"] if config else 20,
                "gaze_seconds": self.attention_gate.seconds, "strict": True,
                "question_count": len(config["questions"]) if config else 0}

    def unlock(self):
        with self.lock:
            if self.active_id:
                raise ValueError("Сначала завершите текущий экзамен")
            if self.entry_block:
                sid = self.entry_block["session_id"]
                if self.store.session(sid):
                    self.store.add_event(sid, "instructor_unlock", "Преподаватель вошёл по PIN и разрешил новую попытку. Предыдущий результат сохранён.")
                self.store.set_setting("entry_block", None)
                self.entry_block = None
                self.phone_gate.reset()
                self.attention_gate.reset()
                self.frame_gate.reset()
        return self.state()

    def start(self, body):
        start_error = None
        with self.lock:
            if self.active_id:
                raise ValueError("Экзамен уже идёт.")
            if self.cleanup_in_progress:
                raise ValueError("Завершается предыдущая попытка. Повторите через несколько секунд.")
            if self.entry_block:
                raise ValueError("Попытка автоматически остановлена. Новую попытку должен разрешить преподаватель по PIN.")
            if not self.participant_profile:
                raise ValueError("Сначала войдите как участник: укажите имя и группу.")
            if not body.consent:
                raise ValueError("Подтвердите условия локальной обработки камеры.")
            if not self.demo:
                capabilities = self.guard.capabilities()
                managed = capabilities.get("isolation_available", False)
                if sys.platform == "win32" and not (managed or capabilities.get("desktop_isolated")):
                    raise ValueError("Для защищённого теста в Windows запустите 02_start.cmd. Изоляция включится только при начале попытки. Браузерный режим предназначен для диагностики.")
                if sys.platform == "win32" and not managed and not capabilities.get("navigation_guard"):
                    raise ValueError("Защита навигации WebView2 не готова. Закройте окно и повторно запустите 02_start.cmd.")
                if not self.exam_config:
                    raise ValueError("Преподаватель должен настроить и сохранить экзамен в разделе «Настройки».")
                state = self.camera.status()
                if not state["camera_ok"]:
                    raise ValueError("Сначала включите камеру.")
                if not state.get("analysis_ready", False):
                    raise ValueError("Дождитесь готовности распознавания камеры.")
                # Calibration is optional. Freeze accepted measurements now,
                # so an unfinished wizard cannot keep fitting during the exam.
                use_calibration = getattr(self.camera, "use_calibration", None)
                if use_calibration:
                    state = use_calibration(viewport=body.calibration_viewport.model_dump()
                                            if body.calibration_viewport else None)
                elif (state.get("calibration") or {}).get("active"):
                    raise ValueError("Завершите текущую настройку камеры перед началом экзамена.")
                monitoring = calibration_capabilities(state)
                if not monitoring["gaze_auto_remove"] and hasattr(self.guard, "calibration_display"):
                    # A failed/canceled wizard may retain native display state.
                    # It must not prevent an exam that uses no screen mapping.
                    self.guard.calibration_display = None
            # A participant cannot turn off proctoring or weaken its threshold
            # by changing the start request. Live policy belongs to the teacher.
            gaze_seconds = body.gaze_seconds if self.demo else self.attention_gate.seconds
            gaze_enabled = body.gaze_enabled if self.demo else True
            self.rules = TemporalRules(Policy(gaze_seconds=gaze_seconds, gaze_enabled=gaze_enabled, strict=not self.demo))
            self.phone_gate.reset()
            self.attention_gate.reset()
            self.frame_gate.reset()
            self.armed_at = None
            self.camera.policy = self.rules.policy
            exam = make_exam(secrets.token_hex(8), None if self.demo else self.exam_config["questions"])
            config = body.model_dump(exclude={"name", "group"})
            config.update(gaze_seconds=gaze_seconds, gaze_enabled=gaze_enabled)
            if not self.demo:
                config["duration_minutes"] = self.exam_config["duration_minutes"]
                config["exam_title"] = self.exam_config["title"]
                config["phone_auto_remove"] = True
                config["phone_seconds"] = self.phone_gate.seconds
                config["phone_confidence"] = self.phone_gate.confidence
                config.update(strict=True, min_frames=1, **monitoring,
                              head_limit_deg=self.attention_gate.head_limit_deg,
                              gaze_screen_bounds=[0, 1], gaze_allowance=0,
                              gaze_mode="confirmed" if monitoring["gaze_auto_remove"] else monitoring["gaze_monitoring"],
                              gaze_min_frames=self.attention_gate.min_frames,
                              gaze_uncertainty_aware=True,
                              gaze_uncertainty_max=self.attention_gate.gaze_uncertainty_max,
                              calibration=state.get("calibration", {}),
                              calibration_version=state.get("calibration_version"))
            config["guard"] = self.guard.capabilities()
            self.active_id = self.store.create(self.participant_profile["name"], self.participant_profile["group"],
                                              "demo" if self.demo else "live", config, exam)
            self.last_id = self.active_id
            self.participant_visible_id = self.active_id
            self.last_seen = time.monotonic()
            self.last_events.clear()
            if not self.demo:
                try:
                    self.guard.enter()
                    protection = self.guard.capabilities()
                    if sys.platform == "win32" and managed and not (
                            protection.get("desktop_isolated") and protection.get("active")
                            and protection.get("keyboard_hook") and protection.get("navigation_guard")
                            and protection.get("window_controller") == "pyautogui"):
                        raise RuntimeError("Защищённое окно не подтвердило готовность. Попытка остановлена.")
                    # Starting the separate window must not consume exam time.
                    self.store.arm_session(self.active_id, protection)
                    self.armed_at = time.monotonic()
                    baseline_seq = self.camera.status().get("analyzed_seq")
                    for gate in (self.frame_gate, self.phone_gate, self.attention_gate):
                        gate.reset(self.armed_at, baseline_seq)
                    self.rules.last_object_seq = baseline_seq
                    self.last_seen = self.armed_at
                except Exception as exc:
                    start_error = str(exc)
                    self.emit("guard_lost", "Экзамен не запущен: " + start_error)
                    self._finish_locked("interrupted")
        if start_error is not None:
            self._release_exam()
            raise ValueError("Защита экзамена не включилась: " + start_error)
        return self.state(instructor=False)

    def _finish_locked(self, status="completed", reason=None):
        if not self.active_id:
            return None
        sid = self.active_id
        session = self.store.session(sid, True)
        result = grade(session["exam"], session["answers"])
        block = None
        if reason:
            result["termination_reason"] = reason
        if status == "disqualified":
            block = {"session_id": sid, "reason": reason or "phone", "at": time.time()}
        self.store.finish(sid, result, status, block=block)
        if block:
            self.entry_block = block
        self.last_id, self.active_id = sid, None
        self.armed_at = None
        self.cleanup_in_progress = not self.demo
        return {"session_id": sid, **result, "status": status}

    def _release_exam(self):
        try:
            self.guard.leave()
        finally:
            try:
                if not self.demo:
                    self.camera.stop()
            finally:
                with self.lock:
                    self.cleanup_in_progress = False

    def finish(self, status="completed"):
        with self.lock:
            result = self._finish_locked(status)
        if result:
            self._release_exam()
        return result

    def emergency(self):
        # Ask the independent supervisor to restore the desktop before camera
        # shutdown, which may be slow or stuck inside a native driver.
        request_exit = getattr(self.guard, "request_exit", None)
        if request_exit:
            request_exit("Аварийный выход из экзамена")
        if self.isolation_control:
            self.isolation_control.request_exit("Аварийный выход из экзамена")
        self.emit("emergency_exit", "Аварийное завершение по команде выхода или при потере защиты. Попытка прервана, ограничения сняты.")
        self.finish("interrupted")

    def login_participant(self, body):
        with self.lock:
            if self.active_id or self.cleanup_in_progress:
                raise ValueError("Сначала завершите текущую попытку")
            cancel = getattr(self.camera, "cancel_calibration", None)
            if cancel:
                cancel()
            self.participant_profile = body.model_dump()
            self.participant_visible_id = None
            return self.state(instructor=False)

    def reset_participant(self):
        with self.lock:
            if self.active_id or self.cleanup_in_progress:
                raise ValueError("Сначала завершите текущую попытку")
            cancel = getattr(self.camera, "cancel_calibration", None)
            if cancel:
                cancel()
            self.participant_profile = None
            self.participant_visible_id = None
            return self.state(instructor=False)

    def state(self, instructor=True):
        with self.lock:
            sid = self.active_id or (self.last_id if instructor else self.participant_visible_id)
            session = self.store.session(sid, True) if sid else None
            events = self.store.events(session["id"]) if session and instructor else []
            if session:
                session["exam"] = public_exam(session["exam"])
                session["remaining"] = max(0, int(session["started"] + session["config"]["duration_minutes"] * 60 - time.time())) if session["status"] == "running" else 0
                if not instructor:
                    public_config = {"consent", "snapshots", "duration_minutes", "exam_title",
                                     "gaze_seconds", "gaze_enabled", "phone_auto_remove",
                                     "phone_seconds", "phone_confidence", "strict", "min_frames",
                                     "gaze_auto_remove", "head_auto_remove", "head_limit_deg",
                                     "calibration", "calibration_version", "gaze_screen_bounds",
                                     "gaze_allowance", "gaze_uncertainty_max", "gaze_mode",
                                     "gaze_min_frames", "gaze_uncertainty_aware", "calibration_mode",
                                     "calibration_saved_points", "calibration_total_points", "calibration_quality",
                                     "gaze_monitoring", "head_calibrated"}
                    session["config"] = {key: value for key, value in session["config"].items()
                                         if key in public_config}
            camera = self.camera.status()
            monitoring = (session["config"] if session else calibration_capabilities(camera))
            return {"version": __version__, "demo": self.demo, "models_ready": self.camera.available(),
                    "viewer_role": "teacher" if instructor else "participant",
                    "participant": dict(self.participant_profile) if self.participant_profile else None,
                    "camera": camera, "guard": self.guard.capabilities(),
                    "exam_config": self.public_exam_config(),
                    "enforcement": {"blocked": bool(self.entry_block),
                                    "session_id": self.entry_block["session_id"] if self.entry_block else None,
                                    "reason": self.entry_block["reason"] if self.entry_block else "",
                                    "phone_seconds": self.phone_gate.seconds,
                                    "confidence": self.phone_gate.confidence,
                                    "min_frames": self.phone_gate.min_frames, "strict": True,
                                    "gaze_auto_remove": monitoring.get("gaze_auto_remove", False),
                                    "head_auto_remove": monitoring.get("head_auto_remove", False),
                                    "calibration_mode": monitoring.get("calibration_mode", "none"),
                                    "gaze_monitoring": monitoring.get("gaze_monitoring", "unavailable"),
                                    "head_calibrated": monitoring.get("head_calibrated", False),
                                    "head_limit_deg": self.attention_gate.head_limit_deg,
                                    "gaze_screen_bounds": [0, 1], "gaze_allowance": 0,
                                    "gaze_mode": "confirmed" if monitoring.get("gaze_auto_remove") else monitoring.get("gaze_monitoring", "unavailable"),
                                    "gaze_seconds": self.attention_gate.seconds,
                                    "gaze_min_frames": self.attention_gate.min_frames,
                                    "gaze_uncertainty_aware": True,
                                    "gaze_uncertainty_max": self.attention_gate.gaze_uncertainty_max},
                    "session": session, "events": events, "summary": attention_summary(events)}

    def tick(self):
        while not self.closed.wait(1):
            finished = None
            with self.lock:
                if not self.active_id:
                    continue
                s = self.store.session(self.active_id)
                deadline = s["started"] + s["config"]["duration_minutes"] * 60
                lost = time.monotonic() - self.last_seen
                if time.time() >= deadline:
                    finished = self._finish_locked()
                elif lost > 20:
                    self.emit("guard_lost", "Нет связи с интерфейсом более 20 секунд. Экзамен остановлен, блокировка снята.")
                    finished = self._finish_locked("interrupted")
                elif not self.demo:
                    camera = self.camera.status()
                    if not camera.get("camera_ok") or not camera.get("analysis_ready"):
                        # Outage signals never carry fresh detections or cause dismissal.
                        # Never reset sequence/armed-time guards on an outage;
                        # cached frames must not become fresh when it recovers.
                        for event in self.rules.evaluate(camera, time.monotonic()):
                            self.emit(**event)
            if finished:
                self._release_exam()

    def close(self):
        self.closed.set()
        self.finish("interrupted")
        self.guard.leave()
        self.camera.stop()


def report_data(rt, sid):
    session = rt.store.session(sid)
    if not session:
        raise HTTPException(404, "Экзамен не найден")
    events = rt.store.events(sid)
    config = session.get("config") or {}
    # Describe the policy saved with THIS attempt, including historical reports.
    if session.get("mode") == "demo":
        notice = "Демонстрационная попытка: события искусственные и не подтверждают реальное распознавание камерой. "
    elif config.get("calibration_mode") in {"full", "partial", "none"}:
        labels = {"full": "полная", "partial": "частичная", "none": "без пригодного профиля"}
        notice = (f"Калибровка: {labels[config['calibration_mode']]}; сохранено точек "
                  f"{config.get('calibration_saved_points', 0)} из {config.get('calibration_total_points', 5)}. ")
        if config.get("gaze_auto_remove") is True:
            frames = config.get("gaze_min_frames", 3)
            delay = str(config.get("gaze_seconds", .25)).replace(".", ",")
            notice += (f"Взгляд за экран подтверждается минимум {frames} последовательными измерениями "
                       f"за не менее {delay} с с учётом погрешности калибровки. ")
        elif config.get("gaze_monitoring") == "approximate":
            notice += "Направление взгляда оценивается приблизительно; автоматическая остановка по взгляду отключена. "
        else:
            notice += "Контроль направления взгляда недоступен; автоматическая остановка по взгляду отключена. "
        notice += ("Контроль головы включён по сохранённому нейтральному положению: отклонение более 5° "
                   "прекращает попытку по первому пригодному свежему кадру. " if config.get("head_auto_remove") is True
                   else "Контроль отклонения головы отключён: пригодное нейтральное положение не сохранено. ")
        notice += ("Распознанный телефон прекращает попытку по первому пригодному свежему кадру. "
                   "Контроль присутствия, второго лица и защита экзамена действуют независимо от калибровки. "
                   "Частичный телефон может остаться нераспознанным; измерения веб-камеры могут ошибаться. ")
    elif config.get("gaze_mode") == "confirmed":
        frames = config.get("gaze_min_frames", 3)
        delay = str(config.get("gaze_seconds", .25)).replace(".", ",")
        notice = (f"Взгляд за экран подтверждается минимум {frames} последовательными измерениями "
                  f"за не менее {delay} с с учётом погрешности калибровки. Неуверенная оценка у края "
                  "не считается подтверждением. Телефон и отклонение головы более 5° прекращают "
                  "попытку по первому пригодному свежему кадру. Частичный телефон может остаться "
                  "нераспознанным; систематическая погрешность веб-камеры всё ещё может вызвать ошибочное решение. ")
    elif config.get("strict") is True:
        notice = ("В этой попытке действовало прежнее строгое правило: первый пригодный свежий кадр "
                  "с телефоном, оценкой взгляда за экран или отклонением головы более 5° прекращал доступ. "
                  "Погрешность у края не исключалась, поэтому была возможна ложная остановка. ")
    else:
        notice = ("Историческая попытка: правила и задержки сохранены в её настройках. "
                  "Наблюдения камеры являются приблизительными и требуют проверки преподавателем. ")
    notice += ("Ответы, баллы и события сохраняются. При споре требуется проверка преподавателем; "
               "новый вход после блокировки разрешает преподаватель по PIN. Автоматическое решение "
               "не доказывает намерение списать. Цепочка SHA-256 проверяет согласованность журнала "
               "и не является внешней цифровой подписью.")
    return {"product": "Qorgau", "version": __version__, "session": session, "events": events,
            "summary": attention_summary(events), "integrity": rt.store.verify(sid),
            "review_history": rt.store.review_history(sid),
            "notice": notice}


def html_report(data):
    esc = lambda v: html.escape(str(v if v is not None else ""))
    session = data["session"]
    rows = "".join(f"<tr><td>{e['offset']:.0f} с</td><td>{esc(e['label'])}</td><td>{esc(e['detail'])}</td><td>{esc(e['review'])}</td><td>{esc(e['note'])}</td></tr>" for e in data["events"])
    score = session.get("result") or {}
    stamp = datetime.fromtimestamp(session["started"], timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    reasons = {"phone": "Обнаружен телефон", "gaze": "Взгляд за пределами экрана",
               "head": "Отклонение головы от калиброванного положения"}
    reason = score.get("termination_reason")
    termination = "<p>Причина остановки: <b>" + esc(reasons.get(reason, reason)) + "</b>.</p>" if reason else ""
    return f"""<!doctype html><html lang='ru'><meta charset='utf-8'><title>Qorgau — отчёт</title>
    <style>body{{font:15px/1.6 Arial,sans-serif;max-width:1000px;margin:45px auto;padding:20px;color:#171717}}h1{{font-size:36px}}.tag{{color:#333333}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #e5e5e5}}.notice{{padding:20px;background:#f5f5f5}}small{{word-break:break-all}}@media print{{body{{margin:0}}}}</style>
    <div class='tag'>QORGAU / ЛОКАЛЬНЫЙ ПРОКТОРИНГ</div><h1>Отчёт об экзамене</h1>
    <p><b>{esc(session['name'])}</b> · {esc(session['group_name'])} · {esc(stamp)}</p>
    <p>Режим: <b>{'ДЕМОНСТРАЦИЯ — искусственные события' if session['mode']=='demo' else 'Реальная камера'}</b><br>Статус: {esc(session['status'])}. Результат теста: {esc(score.get('correct','—'))} / {esc(score.get('total','—'))}.</p>
    {termination}<div class='notice'>{esc(data['notice'])}</div><h2>События ({len(data['events'])})</h2>
    <table><thead><tr><th>От начала</th><th>Событие</th><th>Описание</th><th>Решение</th><th>Комментарий</th></tr></thead><tbody>{rows}</tbody></table>
    <p>Решения: pending — ожидает просмотра; confirmed — наблюдение подтверждено; dismissed — ложное срабатывание.</p>
    <p>Целостность цепочки: {'OK' if data['integrity']['ok'] else 'ОШИБКА'}</p><small>SHA-256: {esc(data['integrity'].get('root',''))}</small>
    <p>Снимки, если включены, доступны отдельно в локальном приложении преподавателю.</p></html>"""


def create_app(root, data, demo=False, reset_pin=False, initial_pin=None):
    rt = Runtime(root, data, demo, reset_pin, initial_pin)

    @asynccontextmanager
    async def lifespan(app):
        thread = threading.Thread(target=rt.tick, daemon=True)
        thread.start()
        yield
        rt.close()

    app = FastAPI(title="Qorgau Local", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = rt
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_security(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host')}"
            if origin and origin != expected:
                return Response("Origin forbidden", status_code=403)
            if not secrets.compare_digest(request.headers.get("x-qorgau-token", ""), rt.csrf):
                return Response("Session token required", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def bearer_token(request):
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        return token if scheme.lower() == "bearer" else ""

    def is_instructor(request):
        if "authorization" not in request.headers:
            return False
        if not rt.auth.valid(bearer_token(request)):
            raise HTTPException(401, "Сеанс преподавателя завершён. Введите PIN снова")
        return True

    def instructor(request: Request):
        if not is_instructor(request):
            raise HTTPException(401, "Нужен PIN преподавателя")

    def active():
        if not rt.active_id:
            raise HTTPException(409, "Нет активного экзамена")
        s = rt.store.session(rt.active_id, True)
        if time.time() >= s["started"] + s["config"]["duration_minutes"] * 60:
            rt.finish()
            raise HTTPException(409, "Время экзамена истекло")
        return s

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (rt.root / "web" / "index.html").read_text(encoding="utf-8").replace("__CSRF_TOKEN__", rt.csrf)

    @app.get("/api/state")
    def state(request: Request):
        return rt.state(instructor=is_instructor(request))

    @app.post("/api/participant/reset")
    def reset_participant():
        try:
            return rt.reset_participant()
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/participant/login")
    def participant_login(body: ParticipantLogin):
        try:
            return rt.login_participant(body)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/heartbeat")
    def heartbeat():
        rt.last_seen = time.monotonic()
        if rt.isolation_control:
            rt.isolation_control.heartbeat()
        return {"ok": True}

    @app.post("/api/camera")
    def camera(body: CameraAction):
        if rt.active_id:
            raise HTTPException(409, "Настройки камеры доступны до начала экзамена")
        if rt.demo:
            return {"demo": True}
        try:
            rt.camera.start(body.index) if body.on else rt.camera.stop()
        except ValueError as e:
            raise HTTPException(400, str(e))
        return rt.camera.status()

    def calibration_available():
        if rt.active_id or rt.cleanup_in_progress:
            raise HTTPException(409, "Калибровка доступна до начала экзамена")
        if not rt.participant_profile:
            raise HTTPException(409, "Сначала войдите как участник")

    @app.post("/api/calibrate")
    def calibrate():
        with rt.lock:
            calibration_available()
            if rt.demo:
                return {"demo": True}
            try:
                rt.camera.calibrate()
            except ValueError as e:
                raise HTTPException(400, str(e))
            return {"ok": True, "camera": rt.camera.status()}

    @app.post("/api/calibration/target")
    def calibration_target(body: CalibrationTarget):
        with rt.lock:
            calibration_available()
            if rt.demo:
                return {"demo": True}
            try:
                rt.camera.calibration_target(body.step, body.viewport.model_dump() if body.viewport else None,
                                             calibration_id=body.calibration_id)
            except ValueError as e:
                raise HTTPException(400, str(e))
            return {"ok": True, "camera": rt.camera.status()}

    @app.post("/api/calibration/cancel")
    def calibration_cancel(body: CalibrationCancel):
        with rt.lock:
            calibration_available()
            if rt.demo:
                return {"demo": True}
            rt.camera.cancel_calibration(calibration_id=body.calibration_id)
            return {"ok": True, "camera": rt.camera.status()}

    @app.get("/api/camera/frame")
    def frame():
        if rt.camera.jpeg:
            return Response(rt.camera.jpeg, media_type="image/jpeg")
        raise HTTPException(404, "Нет кадра")

    @app.get("/api/camera/stream")
    async def camera_stream(request: Request):
        async def frames():
            sequence = -1
            while not rt.closed.is_set():
                if await request.is_disconnected():
                    break
                sequence, jpeg = await asyncio.to_thread(rt.camera.wait_for_jpeg, sequence, 1.0)
                if jpeg:
                    yield (b"--qorgau-frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(jpeg)).encode("ascii") + b"\r\n\r\n" + jpeg + b"\r\n")
                elif not rt.camera.status().get("running"):
                    break
        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=qorgau-frame",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/sessions")
    def start(body: Start):
        try:
            return rt.start(body)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/answer")
    def answer(body: Answer):
        with rt.lock:
            s = active()
            if body.question_id not in {q["id"] for q in s["exam"]}:
                raise HTTPException(400, "Вопрос не найден")
            answers = rt.store.save_answer(s["id"], body.question_id, body.option)
        return {"answers": answers}

    @app.post("/api/finish")
    def finish():
        return rt.finish()

    @app.post("/api/emergency")
    def emergency():
        rt.emergency()
        return {"ok": True}

    @app.post("/api/client-event")
    def client_event(body: ClientEvent):
        if body.kind not in {"focus_lost", "tab_hidden", "fullscreen_exit", "shortcut"}:
            raise HTTPException(400, "Неподдерживаемое событие")
        rt.emit(body.kind, body.detail)
        return {"ok": True}

    @app.post("/api/demo-event")
    def demo_event(body: ClientEvent, request: Request):
        if not rt.demo:
            raise HTTPException(403, "Демонстрационные события отключены в реальном режиме")
        teacher = is_instructor(request)
        active()
        if body.kind not in LABELS:
            raise HTTPException(400, "Неверное событие")
        rt.emit(body.kind, "Искусственное событие для демонстрации", duration=4, confidence=0.91 if "phone" in body.kind else None, simulated=True)
        return rt.state(instructor=teacher)

    @app.post("/api/login")
    def login(body: Login):
        try:
            token = rt.auth.login(body.pin)
        except ValueError as e:
            raise HTTPException(429, str(e))
        if token is None:
            raise HTTPException(401, "Неверный PIN")
        return {"token": token}

    @app.post("/api/logout")
    def logout(request: Request):
        # Idempotent even for an expired session so handoff can always finish.
        rt.auth.logout(bearer_token(request))
        return rt.state(instructor=False)

    @app.get("/api/exam-config", dependencies=[Depends(instructor)])
    def get_exam_config():
        with rt.lock:
            config = rt.exam_config or {"title": "Основы информационных технологий", "duration_minutes": 20, "questions": BANK}
            return {**config, "gaze_seconds": rt.attention_gate.seconds, "strict": True, "configured": rt.exam_config is not None}

    @app.put("/api/exam-config", dependencies=[Depends(instructor)])
    def set_exam_config(body: ExamConfig):
        with rt.lock:
            if rt.active_id:
                raise HTTPException(409, "Изменение вопросов доступно после завершения экзамена")
            config = body.model_dump()
            config["gaze_seconds"] = rt.attention_gate.seconds
            rt.store.set_setting("exam_config", config)
            rt.exam_config = config
            return {**config, "configured": True}

    @app.post("/api/unlock", dependencies=[Depends(instructor)])
    def unlock():
        try:
            return rt.unlock()
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/sessions", dependencies=[Depends(instructor)])
    def sessions():
        return rt.store.sessions()

    @app.get("/api/sessions/{sid}", dependencies=[Depends(instructor)])
    def session(sid: str):
        return report_data(rt, sid)

    @app.post("/api/events/{event_id}/review", dependencies=[Depends(instructor)])
    def review(event_id: int, body: Review):
        try:
            rt.store.review(event_id, body.decision, body.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except KeyError:
            raise HTTPException(404, "Событие не найдено")
        return {"ok": True}

    @app.get("/api/sessions/{sid}/export/{format}", dependencies=[Depends(instructor)])
    def export(sid: str, format: str):
        data = report_data(rt, sid)
        if format == "json":
            content, mime = json.dumps(data, ensure_ascii=False, indent=2), "application/json"
        elif format == "html":
            content, mime = html_report(data), "text/html"
        elif format == "csv":
            stream = io.StringIO()
            writer = csv.writer(stream)
            writer.writerow(["Секунды", "Событие", "Уровень", "Длительность", "Демо", "Решение", "Комментарий"])
            for e in data["events"]:
                # Neutralize spreadsheet formulas in any user-provided field.
                clean = lambda v: "'" + str(v) if str(v).startswith(("=", "+", "-", "@", "\t", "\r")) else str(v)
                writer.writerow([e["offset"], e["label"], e["severity"], e["duration"], e["simulated"], e["review"], clean(e["note"])])
            content, mime = "\ufeff" + stream.getvalue(), "text/csv"
        else:
            raise HTTPException(400, "Формат не поддерживается")
        return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="qorgau-{sid}.{format}"'})

    @app.get("/api/evidence/{event_id}", dependencies=[Depends(instructor)])
    def evidence(event_id: int):
        with rt.store.lock:
            row = rt.store.db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        name = json.loads(row[0]).get("evidence")
        if not name or Path(name).name != name:
            raise HTTPException(404)
        path = rt.store.folder / "evidence" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.delete("/api/sessions/{sid}", dependencies=[Depends(instructor)])
    def delete(sid: str):
        if sid == rt.active_id:
            raise HTTPException(409, "Сначала завершите экзамен")
        if rt.entry_block and rt.entry_block["session_id"] == sid:
            raise HTTPException(409, "Сначала преподаватель должен разрешить новую попытку")
        if not rt.store.session(sid):
            raise HTTPException(404, "Экзамен не найден")
        rt.store.delete(sid)
        if rt.last_id == sid:
            rt.last_id = None
        if rt.participant_visible_id == sid:
            rt.participant_visible_id = None
        return {"ok": True}

    app.mount("/assets", StaticFiles(directory=rt.root / "web"), name="assets")
    return app
