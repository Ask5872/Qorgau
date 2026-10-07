"""Explainable, local phone-pose cues; never proof that a photograph was taken.

YOLO establishes the object class. Only its small crops are examined here. A
visible quadrilateral, weak perspective distortion, screen-area position and a
stable hold provide a *possible* screen-directed pose. Neither camera side nor
shutter activation is observable reliably from this geometry. The reported
quality is a geometric score, not a probability of cheating.
"""
from collections import deque
from dataclasses import dataclass, field
import math
import time


def _iou(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    overlap = max(0, right - x) * max(0, bottom - y)
    return float(overlap / max(a[2] * a[3] + b[2] * b[3] - overlap, 1))


def _valid_box(value):
    """Ignore malformed model output before it reaches OpenCV or JSON."""
    try:
        box = [float(v) for v in value]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(box) != 4 or not all(math.isfinite(v) for v in box) or min(box[2:]) <= 0:
        return None
    return box


def _face_context(box, face_boxes):
    """Geometric proximity to the largest visible face, not phone ownership.

    A phone may cover part of a face or be held beside it. This local relation
    reduces broad-plane alerts on objects far from the participant, but cannot
    reveal which phone side or lens is facing the computer.
    """
    faces = [valid for value in (face_boxes or []) if (valid := _valid_box(value))]
    if not faces:
        return False, None
    face = max(faces, key=lambda value: value[2] * value[3])
    x, y, width, height = box
    fx, fy, fw, fh = face
    cx, cy = x + width / 2, y + height / 2
    # Require placement around face height, with a modest allowance beside it.
    # A phone well below the face or across the image is not a photo cue.
    horizontal_gap = max(fx - (x + width), x - (fx + fw), 0.0)
    near = (horizontal_gap <= fw * 0.55 and
            fy - fh * 0.35 <= cy <= fy + fh * 1.35 and
            abs(cx - (fx + fw / 2)) <= fw * 1.20 + width / 2)
    return bool(near), face


def plane_features(bgr, box):
    """Return measurable plane cues from one detector crop, bounded to 256 px.

    A small or occluded object may have no usable outline. That is 'unknown',
    not evidence of a side-facing phone. Rectification checks that the detected
    edges delimit a real contrast boundary rather than a flat crop.
    """
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    x, y, bw, bh = map(float, box)
    empty = {"plane_visible": False, "plane_facing_camera": False, "quality": 0.0,
             "reason": "outline_not_visible", "quad": []}
    if min(bw, bh) < 22 or bw * bh < w * h * 0.0025:
        return dict(empty, reason="phone_too_small")
    margin = max(4, min(bw, bh) * 0.12)
    x1, y1 = max(0, int(x - margin)), max(0, int(y - margin))
    x2, y2 = min(w, math.ceil(x + bw + margin)), min(h, math.ceil(y + bh + margin))
    if x2 <= x1 or y2 <= y1:
        return dict(empty, reason="invalid_crop")
    crop = bgr[y1:y2, x1:x2]
    scale = min(1.0, 256 / max(crop.shape[:2]))
    if scale < 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if float(gray.std()) < 5:
        return dict(empty, reason="insufficient_contrast")
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, 35, 100)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = empty
    # Bounded contour work even when a phone screen contains many text glyphs.
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:24]:
        if cv2.contourArea(contour) < bw * bh * scale * scale * 0.30:
            continue
        poly = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
        if len(poly) != 4 or not cv2.isContourConvex(poly):
            continue
        quad = poly.reshape(4, 2).astype(np.float32)
        center = quad.mean(axis=0)
        quad = quad[np.argsort(np.arctan2(quad[:, 1] - center[1], quad[:, 0] - center[0]))]
        quad = np.roll(quad, -int(np.argmin(quad.sum(axis=1))), axis=0)
        world = quad / scale + np.array([x1, y1], dtype=np.float32)
        qx, qy = world.min(axis=0)
        qw, qh = world.max(axis=0) - world.min(axis=0)
        coverage = float(cv2.contourArea(quad)) / max(bw * bh * scale * scale, 1)
        agreement = _iou((qx, qy, qw, qh), box)
        # Reject outlines belonging to background objects outside the YOLO box.
        if agreement < 0.52 or not 0.30 <= coverage <= 1.25:
            continue
        edge_vectors = np.roll(quad, -1, axis=0) - quad
        lengths = np.linalg.norm(edge_vectors, axis=1)
        if float(lengths.min()) < 12:
            continue
        side_a, side_b = float((lengths[0] + lengths[2]) / 2), float((lengths[1] + lengths[3]) / 2)
        aspect = min(side_a, side_b) / max(side_a, side_b)
        # Includes portrait and landscape; very narrow edge-on objects fail.
        if not 0.30 <= aspect <= 0.78:
            continue
        opposite = min(float(min(lengths[i], lengths[i + 2]) / max(lengths[i], lengths[i + 2]))
                       for i in (0, 1))
        cosine = max(abs(float(np.dot(edge_vectors[i], edge_vectors[(i + 1) % 4]) /
                               (lengths[i] * lengths[(i + 1) % 4]))) for i in range(4))
        # Warp a slightly expanded plane so its boundary lies inside the patch.
        expanded = center + (quad - center) * 1.12
        transform = cv2.getPerspectiveTransform(expanded.astype(np.float32),
                      np.array([[0, 0], [95, 0], [95, 191], [0, 191]], dtype=np.float32))
        patch = cv2.warpPerspective(gray, transform, (96, 192))
        # Pair samples on either side of each edge; texture within the screen
        # alone cannot create this boundary contrast on all four sides.
        contrasts = [float(np.median(np.abs(patch[15:177, 2].astype(float) - patch[15:177, 9]))),
                     float(np.median(np.abs(patch[15:177, 93].astype(float) - patch[15:177, 86]))),
                     float(np.median(np.abs(patch[4, 12:84].astype(float) - patch[19, 12:84]))),
                     float(np.median(np.abs(patch[187, 12:84].astype(float) - patch[172, 12:84])))]
        border_contrast = sorted(contrasts)[1]  # Three of four edges must be visible.
        if border_contrast < 8:
            continue
        facing = opposite >= 0.67 and cosine <= 0.42 and aspect >= 0.36
        quality = min(1.0, 0.35 * agreement + 0.30 * opposite +
                      0.20 * max(0, 1 - cosine) + 0.15 * min(1, border_contrast / 40))
        feature = {"plane_visible": True, "plane_facing_camera": bool(facing),
                   "quality": round(quality, 3), "quad": world.round(1).tolist(),
                   "aspect_ratio": round(aspect, 3), "opposite_edge_ratio": round(opposite, 3),
                   "corner_skew": round(cosine, 3), "detector_agreement": round(agreement, 3),
                   "border_contrast": round(border_contrast, 1),
                   "reason": "broad_plane" if facing else "plane_oblique"}
        if quality > best["quality"]:
            best = feature
    return best


@dataclass
class _Track:
    number: int
    box: list
    seen: float
    samples: deque = field(default_factory=lambda: deque(maxlen=40))
    held: deque = field(default_factory=lambda: deque(maxlen=20))
    photo_held: deque = field(default_factory=lambda: deque(maxlen=20))
    raised_until: float = 0.0
    observed_raise_at: float | None = None
    rise_qualified: bool = False


class PhonePoseTracker:
    """Match phones by location/scale; never reuse pose history across a long gap."""
    MAX_GAP = 0.65
    HOLD_SECONDS = 0.35
    PHOTO_SEQUENCE_SECONDS = 2.0

    def __init__(self):
        self.tracks = {}
        self.next_id = 1
        self.last_time = None
        self.shape = None

    def reset(self):
        self.tracks.clear()
        self.last_time = None
        self.shape = None

    @staticmethod
    def _empty(reason="no_phone"):
        details = {"reason": reason, "reasons": [reason], "stage": "none", "near_face": False,
                   "method": "tracked_plane_geometry", "photo_proven": False,
                   "camera_side_known": False}
        return {"phone_raised": False, "phone_aimed": False, "phone_photo_attempt": False,
                "phone_track_id": None, "phone_aim_details": details,
                "photo_attempt_details": dict(details), "phone_pose_tracks": []}

    def update(self, bgr, detections, now=None, face_boxes=None):
        now = time.monotonic() if now is None else float(now)
        if not math.isfinite(now):
            return self._empty("invalid_frame_time")
        shape = bgr.shape[:2]
        if self.last_time is not None and now <= self.last_time:
            return self._empty("duplicate_or_old_frame")
        if self.shape != shape or (self.last_time is not None and now - self.last_time > self.MAX_GAP):
            self.reset()
        self.last_time, self.shape = now, shape
        h, w = shape
        self.tracks = {k: t for k, t in self.tracks.items() if now - t.seen <= self.MAX_GAP}
        unmatched = set(self.tracks)
        poses = []
        # Cap crop processing. YOLO detections still report every visible phone.
        detections = sorted(detections, key=lambda d: d["confidence"], reverse=True)[:4]
        for detection in detections:
            box = _valid_box(detection.get("box"))
            if box is None:
                continue
            x, y, bw, bh = box
            if x + bw <= 0 or y + bh <= 0 or x >= w or y >= h:
                continue
            cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
            match, best_cost = None, float("inf")
            for number in unmatched:
                old = self.tracks[number].box
                distance = math.hypot(cx - (old[0] + old[2] / 2) / w, cy - (old[1] + old[3] / 2) / h)
                size_ratio = (bw * bh) / max(old[2] * old[3], 1)
                overlap = _iou(box, old)
                if 0.45 < size_ratio < 2.2 and (overlap > 0.15 or distance < 0.12):
                    cost = distance + (1 - overlap) * 0.12
                    if cost < best_cost:
                        match, best_cost = number, cost
            if match is None:
                match = self.next_id
                self.next_id += 1
                self.tracks[match] = _Track(match, box, now)
            else:
                unmatched.remove(match)
            track = self.tracks[match]
            track.box, track.seen = box, now
            previous_cy = track.samples[-1][2] if track.samples else cy
            track.samples.append((now, cx, cy, bw, bh))
            while track.samples and now - track.samples[0][0] > 1.5:
                track.samples.popleft()
            previous = [s for s in track.samples if now - s[0] >= 0.15]
            rise = max((s[2] - cy for s in previous), default=0.0)
            in_zone = 0.12 <= cx <= 0.88 and 0.10 <= cy <= 0.66
            rise_qualified = rise >= max(0.10, bh / h * 0.35) and in_zone
            # Do not refresh the event forever while an old low sample remains
            # in the deque. A later timestamp requires actual upward movement.
            if rise_qualified and (not track.rise_qualified or previous_cy - cy >= 0.004):
                track.raised_until = now + 1.2
                track.observed_raise_at = now
                track.photo_held.clear()
            track.rise_qualified = bool(rise_qualified)
            if not in_zone:
                track.observed_raise_at = None
                track.photo_held.clear()
            raised = bool(now < track.raised_until and in_zone)
            plane = plane_features(bgr, box)
            near_face, primary_face_box = _face_context(box, face_boxes)
            candidate = bool(in_zone and plane["plane_facing_camera"] and plane["quality"] >= 0.72)
            if candidate:
                # A new movement begins a new hold. Use phone-size-relative
                # displacement, scale and aspect, rather than raw pixel jitter.
                if track.held:
                    anchor = track.held[0]
                    drift = math.hypot((cx - anchor[1]) * w, (cy - anchor[2]) * h)
                    size_change = abs(math.log((bw * bh) / max(anchor[3] * anchor[4], 1)))
                    aspect_change = abs(plane["aspect_ratio"] - anchor[5])
                    perspective_change = abs(plane["opposite_edge_ratio"] - anchor[6])
                    if (drift > max(bw, bh) * 0.12 or size_change > 0.22 or
                            aspect_change > 0.10 or perspective_change > 0.15):
                        track.held.clear()
                        track.photo_held.clear()
                track.held.append((now, cx, cy, bw, bh, plane["aspect_ratio"], plane["opposite_edge_ratio"]))
            else:
                track.held.clear()
            hold_seconds = now - track.held[0][0] if track.held else 0.0
            aimed = bool(candidate and len(track.held) >= 3 and hold_seconds >= self.HOLD_SECONDS)
            raise_age = now - track.observed_raise_at if track.observed_raise_at is not None else None
            recent_raise = raise_age is not None and 0 <= raise_age <= self.PHOTO_SEQUENCE_SECONDS
            if candidate and near_face and recent_raise:
                track.photo_held.append(now)
            else:
                track.photo_held.clear()
            photo_hold_seconds = now - track.photo_held[0] if track.photo_held else 0.0
            photo_attempt = bool(aimed and len(track.photo_held) >= 3 and
                                 photo_hold_seconds >= self.HOLD_SECONDS)
            reasons = []
            if raised:
                reasons.append("tracked_upward_motion")
            if plane["plane_facing_camera"]:
                reasons.append("broad_plane_toward_webcam")
            else:
                reasons.append(plane["reason"])
            reasons.append("screen_area" if in_zone else "outside_screen_area")
            if aimed:
                reasons.append("stable_hold")
            elif candidate:
                reasons.append("waiting_for_stable_hold")
            reasons.append("near_primary_face" if near_face else
                           "no_visible_face" if primary_face_box is None else "away_from_primary_face")
            if photo_attempt:
                reasons.append("observed_raise_then_face_level_hold")
            elif not recent_raise:
                reasons.append("no_recent_observed_raise")
            stage = ("possible_photo" if photo_attempt else "holding" if recent_raise and candidate and near_face
                     else "raised" if raised else "detected")
            pose = {"track_id": match, "raised": raised, "aimed": aimed, "box": box,
                    "photo_attempt": photo_attempt, "stage": stage, "near_face": near_face,
                    "primary_face_box": primary_face_box,
                    "observed_raise_age_seconds": round(raise_age, 2) if raise_age is not None else None,
                    "photo_hold_seconds": round(photo_hold_seconds, 2),
                    "rise_frame_fraction": round(rise, 3), "hold_seconds": round(hold_seconds, 2),
                    "in_screen_area": in_zone, "plane": plane, "reasons": reasons,
                    "method": "tracked_plane_geometry", "photo_proven": False,
                    "camera_side_known": False}
            detection["track_id"], detection["pose"] = match, pose
            poses.append(pose)
        # A single missed detection interrupts the hold and upward-motion chain.
        for number in unmatched:
            self.tracks[number].held.clear()
            self.tracks[number].photo_held.clear()
            self.tracks[number].samples.clear()
            self.tracks[number].raised_until = 0.0
            self.tracks[number].observed_raise_at = None
            self.tracks[number].rise_qualified = False
        if not poses:
            return self._empty()
        primary = max(poses, key=lambda p: (p["photo_attempt"], p["aimed"], p["raised"], p["plane"]["quality"]))
        return {"phone_raised": any(p["raised"] for p in poses),
                "phone_aimed": any(p["aimed"] for p in poses),
                "phone_photo_attempt": any(p["photo_attempt"] for p in poses),
                "photo_attempt_details": primary,
                "phone_track_id": primary["track_id"], "phone_aim_details": primary,
                "phone_pose_tracks": poses}
