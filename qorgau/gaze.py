"""Calibrated, coarse gaze estimates from the local MediaPipe face mesh.

Directions describe the participant, not an observer looking at the preview.
Frames are not mirrored: image-right is participant-left. MediaPipe's metric
camera looks along -Z; positive RQ yaw turns the nose image-right and positive
RQ pitch turns it down. These are observable cues, not a screen gaze point.
"""
import math
import statistics


FEATURE_KEYS = ("yaw", "pitch", "iris_x", "iris_y", "head_vertical")


def angle_delta(value, baseline):
    return (float(value) - float(baseline) + 180.0) % 360.0 - 180.0


def _point(points, index, width, height):
    point = points[index]
    value = (float(point.x) * width, float(point.y) * height)
    if not all(math.isfinite(v) for v in value):
        raise ValueError("Non-finite face landmark")
    return value


def _subtract(a, b):
    return a[0] - b[0], a[1] - b[1]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def extract_eye_features(points, width, height):
    """Measure irises in each eye's pixel axes, independent of head roll.

    Both eyes must be open and provide compatible ratios. A blink, an occluded
    eye or contradictory iris fits yields no gaze estimate; head pose remains
    available separately. No probability is assigned to these geometric cues.
    """
    result = {"iris_x": 0.5, "iris_y": 0.5, "eye_open": False,
              "eye_features_valid": False, "eye_visible_count": 0,
              "head_vertical": 0.0}
    try:
        if width <= 0 or height <= 0:
            return result
        left = _point(points, 33, width, height)
        right = _point(points, 263, width, height)
        nose = _point(points, 1, width, height)
        span = _subtract(right, left)
        distance = math.hypot(*span)
        if distance < 8:
            return result
        down = (-span[1] / distance, span[0] / distance)
        center = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
        result["head_vertical"] = _dot(_subtract(nose, center), down) / distance
        values = []
        for iris, a, b, upper, lower in ((468, 33, 133, 159, 145),
                                         (473, 362, 263, 386, 374)):
            start, end = _point(points, a, width, height), _point(points, b, width, height)
            top, bottom = _point(points, upper, width, height), _point(points, lower, width, height)
            pupil = _point(points, iris, width, height)
            axis = _subtract(end, start)
            eye_width = math.hypot(*axis)
            if eye_width < 4:
                continue
            horizontal = (axis[0] / eye_width, axis[1] / eye_width)
            vertical = (-horizontal[1], horizontal[0])
            aperture = _dot(_subtract(bottom, top), vertical)
            # Pixel axes avoid the old aspect-ratio dependent openness test.
            if aperture / eye_width <= 0.10:
                continue
            x = _dot(_subtract(pupil, start), horizontal) / eye_width
            y = _dot(_subtract(pupil, top), vertical) / aperture
            if -0.15 <= x <= 1.15 and -0.3 <= y <= 1.3:
                values.append((x, y))
        result["eye_visible_count"] = len(values)
        if len(values) != 2:
            return result
        # Do not turn one badly fitted/occluded iris into a side accusation.
        if abs(values[0][0] - values[1][0]) > 0.22 or abs(values[0][1] - values[1][1]) > 0.4:
            return result
        result.update(iris_x=statistics.mean(v[0] for v in values),
                      iris_y=statistics.mean(v[1] for v in values),
                      eye_open=True, eye_features_valid=True)
    except (IndexError, AttributeError, TypeError, ValueError, OverflowError):
        return result
    return result


def _features(signals):
    try:
        values = [float(signals[key]) for key in FEATURE_KEYS]
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return values if all(math.isfinite(value) for value in values) else None


def invalid_reason(signals):
    faces = signals.get("faces", 0)
    if faces == 0:
        return "no_face"
    if faces != 1:
        return "multiple_faces"
    if signals.get("landmark_faces", 1) != 1 or signals.get("landmarks_valid", True) is False:
        return "invalid_landmarks"
    try:
        brightness = float(signals.get("brightness", 0))
    except (TypeError, ValueError, OverflowError):
        return "low_light"
    if not math.isfinite(brightness) or brightness < 30:
        return "low_light"
    if not signals.get("eye_open", False):
        return "eyes_closed"
    if signals.get("eye_features_valid", True) is False or _features(signals) is None:
        return "invalid_landmarks"
    return ""


def calibration_sample(signals):
    return None if invalid_reason(signals) else _features(signals)


def fit_neutral(samples):
    """Reject unstable calibration and handle Euler angles crossing +/-180."""
    if len(samples) < 30:
        return None
    columns = list(zip(*samples))
    angular = [[column[0] + angle_delta(value, column[0]) for value in column]
               for column in columns[:2]]
    measured = angular + [list(column) for column in columns[2:]]
    limits = (8.0, 8.0, 0.08, 0.18, 0.07)
    if any(statistics.pstdev(column) > limit for column, limit in zip(measured, limits)):
        return None
    neutral = [statistics.median(column) for column in measured]
    neutral[:2] = [angle_delta(value, 0) for value in neutral[:2]]
    return neutral


def estimate_gaze(signals, baseline, policy):
    if isinstance(baseline, dict):
        return estimate_screen_gaze(signals, baseline)
    result = {"gaze_direction": "unknown", "gaze_horizontal": "unknown",
              "gaze_vertical": "unknown", "gaze_valid": False,
              "gaze_source": "unavailable", "gaze_reason": "uncalibrated",
              "looking_side": False, "looking_left": False, "looking_right": False,
              "looking_down": False, "looking_up": False,
              "head_yaw": None, "head_pitch": None, "yaw_delta": None, "pitch_delta": None}
    if baseline is None:
        return result
    values = _features(signals)
    if values is not None and signals.get("faces") == 1 and signals.get("landmark_faces", 1) == 1:
        yaw, pitch = angle_delta(values[0], baseline[0]), angle_delta(values[1], baseline[1])
        result.update(head_yaw=round(yaw, 1), head_pitch=round(pitch, 1),
                      yaw_delta=round(yaw, 1), pitch_delta=round(pitch, 1))
    reason = invalid_reason(signals)
    if reason:
        result["gaze_reason"] = reason
        return result
    yaw, pitch = angle_delta(values[0], baseline[0]), angle_delta(values[1], baseline[1])
    iris_x, iris_y = values[2] - baseline[2], values[3] - baseline[3]
    nose = values[4] - baseline[4]
    head_h = (1 if yaw > policy.yaw_degrees else -1 if yaw < -policy.yaw_degrees else 0)
    iris_h = (1 if iris_x > policy.iris_delta else -1 if iris_x < -policy.iris_delta else 0)
    # Pitch sign and nose geometry must agree; abs(pitch) confused up/down.
    head_v = (1 if pitch > policy.pitch_degrees and nose > 0.08 else
              -1 if pitch < -policy.pitch_degrees and nose < -0.08 else 0)
    iris_v = (1 if iris_y > 0.28 else -1 if iris_y < -0.28 else 0)
    if (head_h and iris_h and head_h != iris_h) or (head_v and iris_v and head_v != iris_v):
        result["gaze_reason"] = "conflicting_cues"
        return result
    horizontal, vertical = head_h or iris_h, head_v or iris_v
    from_head, from_iris = bool(head_h or head_v), bool(iris_h or iris_v)
    h = "left" if horizontal > 0 else "right" if horizontal < 0 else "center"
    v = "down" if vertical > 0 else "up" if vertical < 0 else "center"
    result.update(gaze_valid=True, gaze_reason="", gaze_horizontal=h, gaze_vertical=v,
                  gaze_direction=v if v != "center" else h,
                  gaze_source="head+iris" if from_head and from_iris else
                              "head" if from_head else "iris" if from_iris else "neutral",
                  looking_left=h == "left", looking_right=h == "right", looking_side=h != "center",
                  looking_down=v == "down", looking_up=v == "up")
    return result


def estimate_screen_gaze(signals, profile):
    """Screen bounds learned from validated targets; pose has its own validity.

    Eye closure/iris disagreement must not hide an independently measured head
    rotation. They also must not manufacture a gaze violation. A position near
    an edge is inconclusive when its calibrated uncertainty crosses that edge.
    The enforcement gate separately requires repeated resolvable observations;
    this uncertainty is a measured allowance, not a probability guarantee.
    """
    result = {"gaze_direction": "unknown", "gaze_horizontal": "unknown",
              "gaze_vertical": "unknown", "gaze_valid": False,
              "gaze_source": "unavailable", "gaze_reason": "uncalibrated",
              "looking_side": False, "looking_left": False, "looking_right": False,
              "looking_down": False, "looking_up": False,
              "head_yaw": None, "head_pitch": None, "head_roll": None,
              "yaw_delta": None, "pitch_delta": None, "roll_delta": None,
              "head_pose_valid": False, "head_outside": False, "head_deviation": False,
              "screen_outside": False, "gaze_screen_x": None, "gaze_screen_y": None,
              "gaze_uncertainty": None, "gaze_boundary_uncertain": False,
              "gaze_outside_confirmable": False}
    if not profile or profile.get("version") != 2:
        return result
    partial = profile.get("approximate") is True
    try:
        baseline = profile["neutral"]
        head = [angle_delta(signals["yaw"], baseline[0]), angle_delta(signals["pitch"], baseline[1]),
                angle_delta(signals["roll"], profile["neutral_roll"])]
        lit = math.isfinite(float(signals.get("brightness", 0))) and float(signals.get("brightness", 0)) >= 30
        head_valid = (all(math.isfinite(value) for value in head) and lit and
                      signals.get("faces") == 1 and signals.get("landmark_faces") == 1 and
                      signals.get("landmarks_valid") is True and signals.get("pose_valid") is True)
        if head_valid:
            outside = max(abs(value) for value in head) > float(profile["head_limit_deg"])
            result.update(head_pose_valid=True, head_outside=outside, head_deviation=outside,
                          head_yaw=round(head[0], 2), head_pitch=round(head[1], 2), head_roll=round(head[2], 2),
                          yaw_delta=head[0], pitch_delta=head[1], roll_delta=head[2])
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        pass
    if partial and profile.get("gaze_available") is not True:
        result["gaze_reason"] = "partial_calibration"
        return result
    reason = invalid_reason(signals)
    if reason:
        result["gaze_reason"] = reason
        return result
    try:
        features = [1., (float(signals["iris_x"]) - profile["center"][0]) / profile["scale"][0],
                    (float(signals["iris_y"]) - profile["center"][1]) / profile["scale"][1]]
        x, y = [sum(features[row] * profile["weights"][row][axis] for row in range(3)) for axis in range(2)]
        margin = float(profile["uncertainty"])
        if not all(math.isfinite(value) for value in (x, y, margin)) or not 0 <= margin <= .10:
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        result["gaze_reason"] = "invalid_profile"
        return result
    # Remove only arithmetic residue at exact boundaries, not observed motion.
    x = 0. if abs(x) < 1e-12 else 1. if abs(x - 1.) < 1e-12 else x
    y = 0. if abs(y) < 1e-12 else 1. if abs(y - 1.) < 1e-12 else y
    horizontal = "left" if x < 0 else "right" if x > 1 else "center"
    vertical = "up" if y < 0 else "down" if y > 1 else "center"
    outside = horizontal != "center" or vertical != "center"
    uncertain_boundary = (-margin <= x <= 1 + margin and -margin <= y <= 1 + margin and
                          (x <= margin or x >= 1 - margin or y <= margin or y >= 1 - margin))
    confirmable = x + margin < 0 or x - margin > 1 or y + margin < 0 or y - margin > 1
    result.update(gaze_valid=True,
                  gaze_reason="partial_calibration" if partial else "boundary_uncertain" if uncertain_boundary else "",
                  gaze_source="approximate_screen" if partial else "calibrated_screen",
                  gaze_direction=vertical if vertical != "center" else horizontal,
                  gaze_horizontal=horizontal, gaze_vertical=vertical,
                  gaze_screen_x=x, gaze_screen_y=y, gaze_uncertainty=margin,
                  screen_outside=outside, gaze_boundary_uncertain=uncertain_boundary,
                  gaze_outside_confirmable=confirmable and not partial,
                  looking_left=horizontal == "left", looking_right=horizontal == "right",
                  looking_side=horizontal != "center", looking_down=vertical == "down", looking_up=vertical == "up")
    return result
