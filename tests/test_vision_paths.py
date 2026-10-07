"""Opt-in real-model checks for the Windows Unicode-path loading fix.

Run with QORGAU_TEST_VISION=1 after installing the full dependencies.
No webcam is required. These checks also run on Linux, but do not emulate Windows.
"""
import gc
import os
from pathlib import Path
import shutil

import pytest


@pytest.mark.skipif(os.environ.get("QORGAU_TEST_VISION") != "1",
                    reason="Set QORGAU_TEST_VISION=1 to load all real models")
@pytest.mark.parametrize("folder", ["ascii models", "Асет/Загрузки/Қорғау models"])
def test_real_models_from_paths_with_spaces_and_unicode(tmp_path, folder):
    import numpy as np
    from qorgau.vision import VisionModels

    source = Path(__file__).resolve().parents[1] / "models"
    model_dir = tmp_path / folder
    model_dir.mkdir(parents=True)
    for name in ("face_landmarker.task", "yolov8n.onnx", "yolov8n-face.onnx"):
        shutil.copyfile(source / name, model_dir / name)

    models = VisionModels(model_dir)
    try:
        # Inference must remain usable after temporary constructor objects die.
        gc.collect()
        result = models.analyze(np.full((480, 640, 3), 120, dtype=np.uint8))
        assert result["faces"] == 0
        assert result["phone"] is False
        assert result["phone_boxes"] == []
    finally:
        models.close()
