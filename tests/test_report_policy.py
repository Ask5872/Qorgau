"""Reports describe the saved attempt, not whatever policy is current now."""
from types import SimpleNamespace

import pytest

from qorgau.server import report_data, html_report


@pytest.mark.parametrize("mode,config,expected,absent", [
    ("live", {"strict": True, "gaze_mode": "confirmed", "gaze_min_frames": 4,
              "gaze_seconds": .4}, "минимум 4 последовательными", "прежнее строгое правило"),
    ("live", {"strict": True}, "прежнее строгое правило", "подтверждается минимум"),
    ("demo", {"strict": True, "gaze_mode": "confirmed"}, "события искусственные", "прекращают попытку"),
    ("live", {"gaze_seconds": 4}, "Историческая попытка", "прежнее строгое правило"),
])
def test_report_notice_uses_saved_policy_and_preserves_historical_meaning(mode, config, expected, absent):
    session = {"id": "saved", "mode": mode, "config": config, "name": "Участник",
               "group_name": "Группа", "started": 100, "status": "completed"}
    store = SimpleNamespace(session=lambda sid: session, events=lambda sid: [],
                            verify=lambda sid: {"ok": True}, review_history=lambda sid: [])
    data = report_data(SimpleNamespace(store=store), "saved")
    assert expected in data["notice"] and absent not in data["notice"]
    assert expected in html_report(data)
    if config.get("gaze_min_frames") == 4:
        assert "не менее 0,4 с" in data["notice"]
