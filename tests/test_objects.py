"""Synthetic/post-processing tests; these do not measure real-world accuracy."""
from types import SimpleNamespace

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from qorgau.objects import decode_coco, paper_candidates
from qorgau.vision import VisionModels


def row(class_id, score=0.9, box=(200, 200, 160, 120)):
    value = np.zeros(84, dtype=np.float32)
    value[:4] = box
    value[class_id + 4] = score
    return value


def test_same_class_suppressed_but_overlapping_other_class_kept():
    raw = np.asarray([row(67), row(67, 0.8), row(73, 0.85), row(63, 0.75), row(62, 0.8), row(0)])
    results = decode_coco(raw.T[None], (480, 640, 3), 1, 0, 0)
    assert {item["kind"] for item in results} == {"phone", "book", "laptop", "monitor"}
    assert len(results) == 4
    assert {item["class_id"] for item in results} == {67, 73, 63, 62}
    assert all(item["method"] == "yolo_coco" for item in results)


def test_thresholds_invalid_predictions_and_clipping():
    nonfinite = row(73)
    nonfinite[0] = np.nan
    raw = np.asarray([row(67, 0.39), row(73, 0.49), row(63, 1.2), nonfinite,
                      row(62, box=(30, 20, 100, 60)), row(73, box=(30, 20, -100, 60)),
                      row(67, box=(-200, 20, 100, 60))])
    results = decode_coco(raw, (100, 100, 3), 1, 0, 0)
    assert len(results) == 1
    assert results[0]["box"] == [0, 0, 80, 50]


def test_letterbox_coordinates_and_raw_output_validation():
    # 1280x720 is resized to 640x360 with 140 pixels of vertical padding.
    result = decode_coco(np.asarray([row(73, box=(320, 320, 100, 160))]), (720, 1280, 3), 0.5, 0, 140)
    assert result[0]["box"] == [540, 200, 200, 320]
    with pytest.raises(RuntimeError):
        decode_coco(np.zeros((85, 5)), (480, 640, 3), 1, 0, 0)


def paper_scene(text=True, background=75, paper=235):
    frame = np.full((480, 640, 3), background, np.uint8)
    cv2.rectangle(frame, (230, 100), (410, 360), (paper,) * 3, -1)
    if text:
        for y in range(132, 321, 25):
            for x, width in [(248, 35), (291, 51), (350, 34)]:
                cv2.rectangle(frame, (x, y), (x + width, y + 4), (35, 35, 35), -1)
    return frame


def test_text_sheet_is_uncertain_candidate_without_fake_probability():
    results = paper_candidates(paper_scene())
    assert len(results) == 1
    item = results[0]
    assert item["kind"] == "paper_candidate"
    assert item["method"] == "paper_geometry"
    assert item["confidence"] is None and item["class_id"] is None
    assert item["label"] == "Возможный лист с текстом"
    assert item["observations"]["text_like_lines"] >= 3
    assert item["observations"]["confirmed_content"] is False


@pytest.mark.parametrize("frame", [paper_scene(False), paper_scene(background=220),
                                    np.full((480, 640, 3), 235, np.uint8)])
def test_blank_white_rectangles_and_low_boundary_contrast_rejected(frame):
    assert paper_candidates(frame) == []


@pytest.mark.parametrize("exclusion", [[225, 95, 190, 270], [250, 120, 120, 190]])
def test_known_phone_screen_book_or_face_excludes_paper_candidate(exclusion):
    assert paper_candidates(paper_scene(), [exclusion]) == []


def test_separate_object_does_not_hide_sheet_and_coordinates_scale_back():
    frame = cv2.resize(paper_scene(), (1280, 960))
    results = paper_candidates(frame, [[20, 20, 150, 150]])
    assert len(results) == 1
    x, y, w, h = results[0]["box"]
    assert abs(x - 460) <= 2 and abs(y - 200) <= 2
    assert abs(w - 362) <= 4 and abs(h - 522) <= 4


def test_analyze_reuses_one_inference_for_phones_and_other_objects():
    models = VisionModels.__new__(VisionModels)
    models.cv2, models.np = cv2, np
    models.size, models.input_name, models.ts = 640, "images", 0
    calls = []
    raw = np.asarray([row(67), row(73, box=(400, 350, 120, 170))]).T[None]

    def inference(*args):
        calls.append(args)
        return [raw]

    models.phone = SimpleNamespace(run=inference)
    models.face = SimpleNamespace(detect_for_video=lambda *_: SimpleNamespace(face_landmarks=[]))
    models.face_detector = SimpleNamespace(detect=lambda *_: [])
    models.mp = SimpleNamespace(Image=lambda **kw: kw, ImageFormat=SimpleNamespace(SRGB="rgb"))
    models.phone_pose = SimpleNamespace(update=lambda *_args, **_kw: {})
    result = models.analyze(np.full((480, 640, 3), 100, np.uint8))
    assert len(calls) == 1
    assert result["phone"] is True and result["phone_confidence"] == pytest.approx(0.9)
    assert set(result["phone_boxes"][0]) == {"box", "confidence"}
    assert [item["kind"] for item in result["objects"]] == ["book"]


def test_phones_helper_preserves_public_shape():
    models = VisionModels.__new__(VisionModels)
    models.detect_objects = lambda _: [{"kind": "book", "box": [0, 0, 10, 10], "confidence": 0.8},
                                      {"kind": "phone", "box": [4, 5, 20, 40], "confidence": 0.9}]
    assert models.phones(None) == [{"box": [4, 5, 20, 40], "confidence": 0.9}]


@pytest.mark.parametrize("box", [(0, 20, 1, 4), (100, 20, 1, 4), (20, 0, 4, 1), (20, 100, 4, 1)])
def test_partial_phone_at_each_frame_edge_and_exact_threshold_survives(box):
    results = decode_coco(np.asarray([row(67, .40, box)]), (100, 100, 3), 1, 0, 0)
    assert len(results) == 1 and results[0]["kind"] == "phone"
    assert results[0]["confidence"] == pytest.approx(.40)
    assert results[0]["box"][2] > 0 and results[0]["box"][3] > 0
