"""A visible-target, fresh-frame screen calibration; no claim of eye-tracker precision.

Four corners learn the affine mapping. The separately captured center provides
the neutral head pose and held-out validation. Measured uncertainty remains
explicit; this is a webcam estimate, not an eye-tracker precision claim.
"""
import math
import secrets
import statistics
import time

from .gaze import angle_delta, calibration_sample, invalid_reason


TARGETS = (
    ("center", .5, .5), ("top_left", .08, .08), ("top_right", .92, .08),
    ("bottom_right", .92, .92), ("bottom_left", .08, .92),
)
FIT_INDICES = (1, 2, 3, 4)
VALIDATION_INDEX = 0


class ScreenCalibration:
    VERSION = 2
    MODE = "simple_5"
    SETTLE_SECONDS = .45
    SAMPLE_SECONDS = .45
    MIN_SAMPLES = 6
    MAX_SAMPLES = 64
    SAMPLE_WINDOW_SECONDS = 12.0
    MAX_SAMPLE_WINDOW_SECONDS = 30.0
    MAX_FRAME_AGE_SECONDS = 3.0  # Same freshness boundary as Camera.ANALYSIS_MAX_AGE.
    MAX_SAMPLE_GAP_SECONDS = 3.0
    PAUSE_GRACE_SECONDS = 2.0
    EYE_GAP_SECONDS = 2.0
    TARGET_TIMEOUT = 40.0
    TOTAL_TIMEOUT = 360.0
    HEAD_LIMIT_DEGREES = 5.0
    STABILITY_LIMITS = (1.2, 1.2, .018, .045, .025, 1.2)
    FIT_RMS_LIMIT = .075
    FIT_MAX_LIMIT = .14
    VALIDATION_LIMIT = .10

    def __init__(self, now=None):
        self.id = secrets.token_urlsafe(16)
        self.started_at = time.monotonic() if now is None else now
        self.step = 0
        self.active = True
        self.phase = "awaiting_target"
        self.message = "Смотрите на точку, сохраняя голову неподвижной."
        self.ack_at = None
        self.last_seq = -1
        self.last_at = -math.inf
        self.samples = []
        self.groups = []
        self.group_covariances = {}
        self.initial_head_reference = None
        self.viewport = None
        self.profile = None
        self.partial_profile = None
        self.geometry_valid = True
        self.quality = {}
        self.issue = "awaiting_target"
        self.pause_at = None
        self.pause_grace = None
        self.inlier_count = 0
        self.revision = 1
        self.point_restarts = [0] * len(TARGETS)
        self.restart_count = 0
        self.last_restart_reason = ""
        self.last_restart_message = ""
        self.last_restart_step = None
        self.pause_issue = ""
        self.pause_message = ""
        self.valid_frame_intervals = []
        self.last_valid_capture_at = None
        self.sample_window_seconds = self.SAMPLE_WINDOW_SECONDS
        self.observation_fps = None
        self.last_frame_age_ms = None

    def status(self, now=None):
        now = time.monotonic() if now is None else now
        point = TARGETS[min(self.step, len(TARGETS) - 1)]
        # A stored point is a checkpoint. Its progress cannot be undone by a
        # new point's noisy frame or a rolling sample window.
        saved_points = len(self.groups)
        partial = min(.9, self.inlier_count / self.MIN_SAMPLES)
        complete = self.phase == "complete"
        elapsed = max(0., now - self.ack_at) if self.ack_at is not None else 0.
        span = self.samples[-1][0] - self.samples[0][0] if self.samples else 0.
        return {"id": self.id, "revision": self.revision,
                "active": self.active, "step": self.step, "mode": self.MODE,
                "fit_points": len(FIT_INDICES), "validation_points": 1,
                "index": min(self.step + 1, len(TARGETS)), "total": len(TARGETS),
                "target": {"id": point[0], "x": point[1], "y": point[2]},
                "phase": self.phase, "progress": 100 if complete else
                min(99, round(saved_points / len(TARGETS) * 100)),
                "saved_points": saved_points, "completed_points": list(range(saved_points)),
                "point_progress": 100 if complete else round(partial * 100),
                "accepted_samples": min(self.inlier_count, self.MIN_SAMPLES),
                "required_samples": self.MIN_SAMPLES,
                "required_span": self.SAMPLE_SECONDS, "sample_span": round(span, 3),
                "sample_window_seconds": round(self.sample_window_seconds, 3),
                "observation_fps": (round(self.observation_fps, 3)
                                    if self.observation_fps is not None else None),
                "last_frame_age_ms": self.last_frame_age_ms,
                "target_elapsed": round(elapsed, 1),
                "target_remaining": round(max(0., self.TARGET_TIMEOUT - elapsed), 1),
                "current_point_restarts": self.point_restarts[min(self.step, len(TARGETS) - 1)],
                "restart_count": self.restart_count,
                "last_restart_reason": self.last_restart_reason,
                "last_restart_message": self.last_restart_message,
                "last_restart_step": self.last_restart_step,
                "issue": self.issue, "message": self.message,
                "quality": dict(self.quality), "viewport": dict(self.viewport or {}),
                **self.capabilities()}

    def capabilities(self):
        """Report usable observations without calling an unfinished fit calibrated."""
        full = bool(self.profile and self.geometry_valid)
        partial = bool(self.partial_profile and self.geometry_valid)
        approximate = partial and self.partial_profile.get("gaze_available") is True
        return {"calibrated": full, "calibration_mode": "full" if full else "partial" if partial else "none",
                "gaze_monitoring": "calibrated" if full else "approximate" if approximate else "unavailable",
                "head_calibrated": full or partial}

    @staticmethod
    def _viewport(value):
        if value is None:
            raise ValueError("Нужны размеры полноэкранного окна калибровки.")
        try:
            width, height = float(value["width"]), float(value["height"])
            dpr = float(value.get("device_pixel_ratio", value.get("dpr", 1)))
            if not all(math.isfinite(v) for v in (width, height, dpr)) or min(width, height) < 240 or dpr <= 0:
                raise ValueError
            result = {"width": round(width), "height": round(height), "device_pixel_ratio": dpr}
            for key in ("screen_width", "screen_height"):
                number = float(value[key])
                if not math.isfinite(number) or number <= 0:
                    raise ValueError
                result[key] = round(number)
            if abs(width - result["screen_width"]) > 2 or abs(height - result["screen_height"]) > 2:
                raise ValueError
            return result
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("Некорректные размеры окна калибровки.") from None

    def acknowledge(self, step, viewport=None, now=None):
        now = time.monotonic() if now is None else now
        if not self.active:
            raise ValueError("Калибровка уже завершена. Запустите её заново.")
        if isinstance(step, bool) or not isinstance(step, int) or step != self.step:
            raise ValueError("Точка калибровки изменилась. Обновите её положение.")
        normalized = self._viewport(viewport)
        if self.viewport is not None and normalized != self.viewport:
            self.geometry_valid = False
            self.fail("Размер экрана изменился. Сохранённые измерения нельзя использовать для этого окна.",
                      "viewport_changed")
            raise ValueError(self.message)
        self.viewport = normalized
        if self.ack_at is None:
            self.revision += 1
            self.ack_at = now
            self.last_valid_capture_at = None
            self.phase = "settling"
            self.issue = "settling"
            self.message = "Смотрите точно на точку. Не поворачивайте голову."
        return self.status()

    def fail(self, message, issue="failed"):
        self.revision += 1
        self.active = False
        self.phase = "failed"
        self.issue = issue
        self.profile = None
        self._refresh_partial_profile()
        self.message = message

    def cancel(self):
        # Closing the wizard keeps stable checkpoints. The Camera's no-ID reset
        # remains the separate participant/camera lifecycle invalidation path.
        if self.profile is not None:
            return
        self.revision += 1
        self.active = False
        self.phase = "cancelled"
        self.issue = "cancelled"
        self.samples = []
        self.inlier_count = 0
        self._refresh_partial_profile()
        self.message = "Калибровка остановлена. Сохранённые точки можно использовать для теста."

    def use_saved(self):
        """Freeze only completed stable points; never turn a partial fit into full."""
        if self.active:
            self.revision += 1
            self.active = False
            self.phase = "partial"
            self.issue = "partial_calibration"
            self.message = "Используем сохранённые точки. Можно начинать тест."
        self.samples = []
        self.inlier_count = 0
        self._refresh_partial_profile()
        return self.status()

    def _refresh_partial_profile(self):
        """Use a saved center for head pose and an optional non-enforcing gaze fit.

        Three or four distinct target observations can describe approximate gaze,
        but lack the full four-corner/held-out validation. A completed calibration
        that failed quality checks keeps neutral pose only, never a replacement
        fit that hides the failed validation.
        """
        self.partial_profile = None
        if self.profile is not None or not self.geometry_valid or not self.groups:
            return
        try:
            neutral = [float(value) for value in self.groups[0]]
            if len(neutral) != 6 or not all(math.isfinite(value) for value in neutral):
                return
        except (TypeError, ValueError, OverflowError):
            return
        profile = {"version": self.VERSION, "mode": "partial_5", "approximate": True,
                   "neutral": neutral[:5], "neutral_roll": neutral[5],
                   "head_limit_deg": self.HEAD_LIMIT_DEGREES, "saved_points": len(self.groups),
                   "gaze_available": False, "viewport": dict(self.viewport or {})}
        self.partial_profile = profile
        if not 3 <= len(self.groups) < len(TARGETS):
            return
        import numpy as np
        try:
            features = np.asarray([group[2:4] for group in self.groups], dtype=float)
            targets = np.asarray([target[1:] for target in TARGETS[:len(self.groups)]], dtype=float)
            normalized, _ = self._normalized_design(features, np)
            covariances = self._checked_covariances(np, len(self.groups))
            if normalized is None or covariances is None:
                return
            center, scale, design = normalized
            weights = np.linalg.lstsq(design, targets, rcond=None)[0]
            errors = np.abs(design @ weights - targets)
            rms, maximum = float(np.sqrt(np.mean(errors ** 2))), float(np.max(errors))
            jitter = self._projected_jitter(covariances, weights, scale, np)
            if (not np.all(np.isfinite(weights)) or not math.isfinite(rms) or not math.isfinite(maximum)
                    or rms > self.FIT_RMS_LIMIT or maximum > self.FIT_MAX_LIMIT or jitter is None
                    or rms + jitter > self.VALIDATION_LIMIT):
                return
            profile.update(gaze_available=True, center=center.tolist(), scale=scale.tolist(),
                           weights=weights.tolist(), uncertainty=max(.025, rms + jitter))
        except (ValueError, TypeError, IndexError, OverflowError, np.linalg.LinAlgError):
            return

    def _restart_point(self, message, issue):
        # Only discard the unfinished point, and expose why it happened. A
        # continuing failure must not count as a new restart on every frame.
        discarded = bool(self.samples)
        if discarded:
            self.point_restarts[self.step] += 1
            self.restart_count += 1
            self.last_restart_step = self.step
            self.last_restart_reason = issue
            self.last_restart_message = (
                "Начинаем текущую точку заново. " + message + " Пройденные точки сохранены.")
        self.samples = []
        self.inlier_count = 0
        self.last_valid_capture_at = None
        self.message = self.last_restart_message if discarded else message
        self.issue = issue
        return discarded

    def _pause_point(self, message, issue, captured_at, grace=None):
        # A transient tracker failure contributes no sample, but should not
        # destroy a whole point. Changing reasons cannot extend the grace.
        grace = self.PAUSE_GRACE_SECONDS if grace is None else grace
        if self.pause_at is None:
            self.pause_at, self.pause_grace = captured_at, grace
        else:
            self.pause_grace = min(self.pause_grace, grace)
        self.pause_issue, self.pause_message = issue, message
        self.phase = "paused"
        self.message, self.issue = message, issue
        if captured_at - self.pause_at > self.pause_grace:
            self._restart_point(message, issue)

    def _measured_samples(self):
        """Keep a bounded window, excluding only a small minority of outliers.

        The dispersion requirements are unchanged. Alternating/moving gaze is
        not turned into a stable average or a hand-picked cluster. A single
        tracking spike can be discarded after enough corroborating frames.
        """
        columns = list(zip(*(sample for _, sample in self.samples)))
        measured = [[column[0] + angle_delta(value, column[0]) for value in column]
                    if index in (0, 1, 5) else list(column)
                    for index, column in enumerate(columns)]
        medians = [statistics.median(column) for column in measured]
        inliers = [index for index in range(len(self.samples))
                   if all(abs(column[index] - median) <= 3 * limit
                          for column, median, limit in zip(measured, medians, self.STABILITY_LIMITS))]
        self.inlier_count = len(inliers)
        if len(inliers) < self.MIN_SAMPLES or len(inliers) / len(self.samples) < .8:
            return None
        # Sparse valid frames must still cover a real observation interval.
        if self.samples[inliers[-1]][0] - self.samples[inliers[0]][0] < self.SAMPLE_SECONDS:
            return None
        filtered = [[column[index] for index in inliers] for column in measured]
        if any(statistics.pstdev(column) > limit
               for column, limit in zip(filtered, self.STABILITY_LIMITS)):
            return None
        return filtered

    def _observe_valid_capture(self, captured_at):
        """Size the bounded window from capture cadence, not inference latency.

        Keep room for six inliers and a small minority of outliers without reducing any
        dispersion or validation requirement. Do not let an unobserved gap or
        time spent waiting for the next target inflate the cadence estimate.
        """
        if self.last_valid_capture_at is not None:
            interval = captured_at - self.last_valid_capture_at
            if 0 < interval <= self.MAX_SAMPLE_GAP_SECONDS:
                self.valid_frame_intervals.append(interval)
                self.valid_frame_intervals = self.valid_frame_intervals[-16:]
                cadence = statistics.median(self.valid_frame_intervals)
                self.observation_fps = 1. / cadence
                self.sample_window_seconds = min(self.MAX_SAMPLE_WINDOW_SECONDS,
                    max(self.SAMPLE_WINDOW_SECONDS, cadence * math.ceil(self.MIN_SAMPLES / .8)))
        self.last_valid_capture_at = captured_at

    def check_timeout(self, now=None):
        """Expire even if the camera produces no frames; True means just expired."""
        now = time.monotonic() if now is None else now
        if self.active and (now - self.started_at > self.TOTAL_TIMEOUT or (
                self.ack_at is not None and now - self.ack_at > self.TARGET_TIMEOUT)):
            detail = self.message if self.issue not in ("awaiting_target", "settling", "gathering") else (
                "Не удалось получить достаточно свежих устойчивых кадров.")
            self.fail("Время калибровки истекло. " + detail)
            return True
        return False

    def collect(self, signals, now=None):
        now = time.monotonic() if now is None else now
        if not self.active or self.check_timeout(now):
            return
        if self.ack_at is None:
            return
        try:
            sequence, captured_at = signals["analyzed_seq"], float(signals["analyzed_at"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        if (isinstance(sequence, bool) or not isinstance(sequence, int) or not math.isfinite(captured_at) or
                sequence <= self.last_seq or captured_at <= self.last_at or
                captured_at < self.ack_at + self.SETTLE_SECONDS or captured_at > now):
            return
        self.last_frame_age_ms = round((now - captured_at) * 1000, 1)
        if now - captured_at >= self.MAX_FRAME_AGE_SECONDS:
            self.revision += 1
            self.issue = "slow_analysis"
            self.message = "Кадры устарели: задержка анализа достигла трёх секунд. Закройте лишние программы и проверьте камеру."
            return
        self.last_seq, self.last_at = sequence, captured_at
        self.revision += 1
        self.phase = "collecting"
        try:
            head = [float(signals[key]) for key in ("yaw", "pitch", "roll")]
        except (KeyError, TypeError, ValueError, OverflowError):
            head = [math.nan] * 3
        reason = invalid_reason(signals)
        if signals.get("camera_ok") is not True or signals.get("analysis_ready") is not True:
            self._pause_point("Нет свежего изображения камеры. Проверьте подключение камеры.",
                              "camera_unavailable", captured_at)
            return
        messages = {"no_face": "Лицо не видно. Вернитесь в кадр и смотрите на точку.",
                    "multiple_faces": "В кадре должно быть только одно лицо.",
                    "low_light": "Слишком темно. Добавьте свет перед лицом."}
        if reason in ("low_light", "no_face"):
            self._pause_point(messages[reason], reason, captured_at)
            return
        if reason in messages:
            # Two simultaneous people create ambiguity immediately. Never mix
            # their features into one unfinished point, even for a short gap.
            self._pause_point(messages[reason], reason, captured_at, 0.)
            self._restart_point(messages[reason], reason)
            return
        if (not all(math.isfinite(value) for value in head) or
                not signals.get("pose_valid", False) or
                signals.get("landmark_faces", 1) != 1 or signals.get("landmarks_valid", True) is False):
            self._pause_point("Не удаётся определить положение головы. Лицо должно быть видно целиком.",
                              "pose_unavailable", captured_at)
            return
        if self.groups:
            reference = (self.initial_head_reference if self.initial_head_reference is not None else
                         tuple(self.groups[0][index] for index in (0, 1, 5)))
            if max(abs(angle_delta(value, neutral)) for value, neutral in zip(head, reference)) > 4.0:
                self._pause_point("Верните голову в исходное положение; переводите только взгляд.",
                                  "head_moved", captured_at)
                return
        # A normal blink pauses collection instead of erasing all good frames.
        # Head/face checks above still apply even when the eyes are closed.
        sample = calibration_sample(signals)
        if sample is None:
            issue = "eyes_closed" if reason == "eyes_closed" else "eyes_unavailable"
            message = ("Моргание — пауза. Продолжайте смотреть на точку." if reason == "eyes_closed" else
                       "Глаза плохо различимы. Смотрите на точку, уберите блики перед глазами.")
            self._pause_point(message, issue, captured_at, self.EYE_GAP_SECONDS)
            return
        if self.pause_at is not None and captured_at - self.pause_at > self.pause_grace:
            self._restart_point(self.pause_message, self.pause_issue)
        elif self.samples and captured_at - self.samples[-1][0] > self.MAX_SAMPLE_GAP_SECONDS:
            self._restart_point("Слишком долго не было подходящих кадров.", "sample_gap")
        self.pause_at = None
        self.pause_grace = None
        self.pause_issue = ""
        self.pause_message = ""
        self._observe_valid_capture(captured_at)
        roll = head[2]
        self.samples.append((captured_at, sample + [roll]))
        self.samples = [entry for entry in self.samples[-self.MAX_SAMPLES:]
                        if captured_at - entry[0] <= self.sample_window_seconds]
        self.issue = "gathering"
        self.message = "Удерживайте взгляд на точке."
        self.inlier_count = len(self.samples)
        if len(self.samples) < self.MIN_SAMPLES:
            return
        measured = self._measured_samples()
        if measured is None:
            self.issue = "unstable"
            self.message = "Измерения пока нестабильны. Смотрите на точку; подходящие кадры сохраняются."
            return
        group = [statistics.median(column) for column in measured]
        # Estimate variability of an individual analyzed frame, not the much
        # smaller standard error of the point median/mean. Keep both iris axes
        # together so their correlation survives projection to screen space.
        iris_x, iris_y = measured[2], measured[3]
        covariance = statistics.covariance(iris_x, iris_y)
        self.group_covariances[self.step] = [[statistics.variance(iris_x), covariance],
                                             [covariance, statistics.variance(iris_y)]]
        if self.step == 0 and self.initial_head_reference is None:
            self.initial_head_reference = tuple(group[index] for index in (0, 1, 5))
        self.groups.append(group)
        self._refresh_partial_profile()
        self.samples = []
        self.inlier_count = 0
        self.step += 1
        self.ack_at = None
        if self.step == len(TARGETS):
            self._finish()
        else:
            self.phase = "awaiting_target"
            self.issue = "awaiting_target"
            self.message = "Следующая точка — переведите только взгляд."

    def _finish(self):
        import numpy as np
        self.quality = {"fit_rms_limit": self.FIT_RMS_LIMIT, "fit_max_limit": self.FIT_MAX_LIMIT,
                        "validation_limit": self.VALIDATION_LIMIT,
                        "screen_jitter_limit": self.VALIDATION_LIMIT,
                        "head_limit_deg": self.HEAD_LIMIT_DEGREES,
                        "fit_points": len(FIT_INDICES), "validation_points": 1,
                        "samples_per_point": self.MIN_SAMPLES}
        training = np.asarray([self.groups[index] for index in FIT_INDICES], dtype=float)
        targets = np.asarray([(TARGETS[index][1], TARGETS[index][2]) for index in FIT_INDICES], dtype=float)
        irises = training[:, 2:4]
        span = np.ptp(irises, axis=0)
        for axis, name in enumerate(("eye_range_x", "eye_range_y")):
            if math.isfinite(float(span[axis])):
                self.quality[name] = float(span[axis])
        covariances = self._checked_covariances(np)
        if covariances is None:
            self.fail("Не удалось проверить устойчивость измерений на всех пяти точках.", "noise_error")
            return
        self.quality.update(eye_noise_x=float(np.sqrt(np.max(covariances[:, 0, 0]))),
                            eye_noise_y=float(np.sqrt(np.max(covariances[:, 1, 1]))))
        normalized, failure = self._normalized_design(irises, np)
        if normalized is None:
            messages = {
                "insufficient_eye_range": "По этим измерениям пока не удалось различить направления взгляда.",
                "degenerate_fit": "Не удалось разделить горизонтальное и вертикальное направления взгляда.",
            }
            self.fail(messages[failure], failure)
            return
        center, scale, design = normalized
        weights = np.linalg.lstsq(design, targets, rcond=None)[0]
        errors = np.abs(design @ weights - targets)
        rms = float(np.sqrt(np.mean(errors ** 2)))
        maximum = float(np.max(errors))
        # The center is NEVER used to fit the mapping. It independently checks
        # the four-corner fit, even though it was captured first for head pose.
        verify = np.asarray(self.groups[VALIDATION_INDEX][2:4], dtype=float)
        predicted = np.asarray([1., *((verify - center) / scale)]) @ weights
        validation_error = float(np.max(np.abs(predicted - np.asarray(TARGETS[VALIDATION_INDEX][1:]))))
        point_errors = [validation_error] + [float(value) for value in np.max(errors, axis=1)]
        self.quality.update(rms_error=round(rms, 6), max_fit_error=round(maximum, 6),
                            validation_error=round(validation_error, 6),
                            point_errors=[round(value, 6) for value in point_errors])
        if rms > self.FIT_RMS_LIMIT or maximum > self.FIT_MAX_LIMIT:
            self.fail("По пяти точкам не удалось получить достаточно точное соответствие взгляда экрану.", "fit_error")
            return
        jitter = self._projected_jitter(covariances, weights, scale, np)
        if jitter is None:
            self.fail("Не удалось проверить погрешность отдельных измерений на экране.", "noise_error")
            return
        self.quality["screen_jitter"] = jitter
        uncertainty = max(.025, validation_error + rms + jitter)
        self.quality["uncertainty"] = round(uncertainty, 6)
        if validation_error > self.VALIDATION_LIMIT or validation_error + rms > self.VALIDATION_LIMIT:
            self.fail("Контрольная точка в центре не подтвердила точность настройки.", "validation_error")
            return
        if uncertainty > self.VALIDATION_LIMIT:
            self.fail("Измерения взгляда пока слишком нестабильны для точной проверки.", "noise_error")
            return
        self.profile = {"version": self.VERSION, "mode": self.MODE, "neutral": self.groups[0][:5],
                        "neutral_roll": self.groups[0][5], "center": center.tolist(),
                        "scale": scale.tolist(), "weights": weights.tolist(),
                        "uncertainty": uncertainty, "head_limit_deg": self.HEAD_LIMIT_DEGREES,
                        "quality": dict(self.quality), "viewport": dict(self.viewport or {})}
        self.partial_profile = None
        self.active = False
        self.phase = "complete"
        self.issue = "complete"
        self.message = "Калибровка готова. Можно начинать тест."

    @staticmethod
    def _normalized_design(features, np):
        """Reject numerical constants or inseparable axes, not small motion.

        Feature amplitudes depend on eye geometry and the camera. Reliability
        comes from full-fit/held-out errors and projected frame variability,
        rather than a fixed minimum iris ratio. The span floor only protects
        double-precision normalization from effectively constant inputs.
        """
        if not np.all(np.isfinite(features)):
            return None, "degenerate_fit"
        floor = 64 * np.finfo(float).eps * np.maximum(1., np.max(np.abs(features), axis=0))
        if np.any(np.ptp(features, axis=0) <= floor):
            return None, "insufficient_eye_range"
        center, scale = np.mean(features, axis=0), np.std(features, axis=0)
        if not np.all(np.isfinite(scale)) or np.any(scale <= 0):
            return None, "degenerate_fit"
        design = np.column_stack((np.ones(len(features)), (features - center) / scale))
        if not np.all(np.isfinite(design)):
            return None, "degenerate_fit"
        condition = float(np.linalg.cond(design))
        if not math.isfinite(condition) or np.linalg.matrix_rank(design) < 3 or condition > 12:
            return None, "degenerate_fit"
        return (center, scale, design), ""

    def _checked_covariances(self, np, count=None):
        """Every saved target needs its own finite, positive-semidefinite data."""
        count = len(TARGETS) if count is None else count
        try:
            matrices = np.asarray([self.group_covariances[index] for index in range(count)], dtype=float)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if matrices.shape != (count, 2, 2) or not np.all(np.isfinite(matrices)):
            return None
        for matrix in matrices:
            tolerance = 64 * np.finfo(float).eps * float(np.max(np.abs(matrix)))
            if (np.any(np.diag(matrix) < 0) or np.max(np.abs(matrix - matrix.T)) > tolerance or
                    float(np.min(np.linalg.eigvalsh(matrix))) < -tolerance):
                return None
        return matrices

    @staticmethod
    def _projected_jitter(covariances, weights, scale, np):
        # Two sample standard deviations are a variability allowance, not a
        # claimed confidence interval or a guarantee for future webcam frames.
        jacobian = weights[1:, :] / scale[:, None]
        with np.errstate(over="ignore", invalid="ignore"):
            projected = [jacobian.T @ covariance @ jacobian for covariance in covariances]
        if not np.all(np.isfinite(projected)):
            return None
        variances = [max(0., float(value)) for matrix in projected for value in np.diag(matrix)]
        jitter = 2 * math.sqrt(max(variances))
        return jitter if math.isfinite(jitter) else None
