"""Face class decoding, cross-detector association, and optional real inference."""
import os
from pathlib import Path

import numpy as np
import pytest

from qorgau.face_detector import associate_faces, decode_faces


def output(*rows):
    arr = np.zeros((1, 300, 21), dtype=np.float32)
    for i, row in enumerate(rows):
        arr[0, i, :6] = row
    return arr


def detection(box, confidence=.9):
    return {"box": box, "confidence": confidence, "source": "yolov8n-face"}


def test_face_coordinates_restore_letterbox_on_widescreen():
    result = decode_faces(output([100, 170, 200, 270, .9, 0]), (720, 1280, 3), .5, 0, 140)
    assert result == [{"box": [200, 60, 200, 200], "confidence": .9, "source": "yolov8n-face"}]


def test_face_coordinates_clip_to_image_and_reject_padding():
    result = decode_faces(output([-20, 100, 200, 270, .9, 0], [0, 0, 40, 80, .9, 0]),
                          (720, 1280, 3), .5, 0, 140)
    assert len(result) == 1
    assert result[0]["box"] == [0, 0, 400, 260]


@pytest.mark.parametrize("row", [
    [1, 1, 10, 10, .49, 0], [1, 1, 10, 10, 1.1, 0], [1, 1, 10, 10, .9, 1],
    [1, 1, 0, 10, .9, 0], [1, 1, 10, 0, .9, 0], [float('nan'), 1, 10, 10, .9, 0],
    [1, 1, float('inf'), 10, .9, 0], [1, 1, 2, 2, .9, 0]])
def test_no_invalid_wrongclass_or_tiny_faces(row):
    assert decode_faces(output(row), (640, 640, 3), 1, 0, 0) == []


def test_duplicate_face_rows_do_not_count_twice():
    rows = output([10, 10, 90, 100, .9, 0], [11, 11, 91, 101, .8, 0], [200, 50, 300, 200, .7, 0])
    result = decode_faces(rows, (640, 640, 3), 1, 0, 0)
    assert len(result) == 2
    assert result[0]["confidence"] == .9


def test_unexpected_yolo_output_fails_openly():
    with pytest.raises(ValueError, match="pinned export"):
        decode_faces(np.zeros((1, 84, 8400)), (640, 640, 3), 1, 0, 0)


def test_same_face_from_yolo_and_mesh_not_double_counted():
    result = associate_faces([detection([10, 10, 100, 100])], [[20, 20, 80, 75]])
    assert len(result) == 1
    assert result[0]["source"] == "yolov8n-face+mediapipe"
    assert result[0]["landmark_index"] == 0


def test_second_face_without_mesh_remains_counted():
    result = associate_faces([detection([10, 10, 100, 100]), detection([250, 10, 80, 90])],
                             [[20, 20, 80, 75]])
    assert len(result) == 2
    assert result[1]["source"] == "yolov8n-face"


def test_unmatched_mesh_face_is_counted_and_labelled_honestly():
    result = associate_faces([detection([10, 10, 100, 100])], [[260, 20, 70, 75]])
    assert len(result) == 2
    assert result[1]["source"] == "mediapipe"
    assert result[1]["confidence"] is None


def test_association_is_one_to_one_not_many_meshes_per_face():
    result = associate_faces([detection([0, 0, 300, 300])], [[20, 20, 80, 75], [180, 20, 80, 75]])
    assert len(result) == 2
    assert sorted(v["landmark_index"] for v in result) == [0, 1]


def test_association_leaves_input_unchanged_and_handles_empty():
    items = [detection([10, 10, 100, 100])]
    result = associate_faces(items, [[20, 20, 80, 75]])
    result[0]["box"][0] = 50
    assert items[0]["box"][0] == 10
    assert items[0]["source"] == "yolov8n-face"
    assert associate_faces([], []) == []


@pytest.mark.skipif(os.environ.get("QORGAU_TEST_VISION") != "1", reason="Opt-in bundled real face ONNX")
def test_required_real_face_model_runs_locally_on_blank():
    from qorgau.face_detector import YoloFaceDetector
    detector = YoloFaceDetector(Path(__file__).resolve().parents[1] / "models")
    try:
        assert detector.detect(np.full((360, 640, 3), 120, np.uint8)) == []
    finally:
        detector.close()
