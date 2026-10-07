from pathlib import Path
import time
import pytest
from fastapi.testclient import TestClient

from qorgau.rules import TemporalRules, Policy, attention_summary
from qorgau.store import Store
from qorgau.server import create_app
from qorgau.guard import blocked_shortcut


def signals(**kwargs):
    return {"camera_ok": True, "faces": 1, "brightness": 100, "calibrated": True, **kwargs}


def test_brief_glances_blinks_and_low_light_are_not_absence():
    r = TemporalRules()
    assert not r.evaluate(signals(looking_down=True), 0)
    assert not r.evaluate(signals(looking_down=True), 3.9)
    assert not r.evaluate(signals(), 4)
    assert not r.evaluate(signals(faces=0), 5)
    assert not r.evaluate(signals(), 5.3)
    r.evaluate(signals(faces=0, brightness=10), 6)
    events = r.evaluate(signals(faces=0, brightness=10), 11)
    assert [e["kind"] for e in events] == ["low_light"]


def test_sustained_event_once_per_episode_then_cooldown():
    r = TemporalRules()
    assert [e["kind"] for e in r.evaluate(signals(phone=True), 0)] == ["phone"]
    assert not r.evaluate(signals(phone=True), 1)
    assert not r.evaluate(signals(phone=True), 20)
    r.evaluate(signals(), 21)
    assert r.evaluate(signals(phone=True), 22)[0]["kind"] == "phone"
    assert not r.evaluate(signals(phone=True), 23)


def test_uncalibrated_or_disabled_gaze_does_not_accuse():
    for r, s in [(TemporalRules(), signals(calibrated=False, looking_down=True)),
                 (TemporalRules(Policy(gaze_enabled=False)), signals(looking_down=True))]:
        r.evaluate(s, 0)
        assert not r.evaluate(s, 100)


def test_multiple_faces_and_camera_failure_are_distinct():
    r = TemporalRules()
    r.evaluate(signals(faces=2), 0)
    assert r.evaluate(signals(faces=2), 2)[0]["kind"] == "multiple_faces"
    r.evaluate(signals(camera_ok=False, faces=0), 3)
    assert [e["kind"] for e in r.evaluate(signals(camera_ok=False, faces=0), 6)] == ["camera_lost"]


def test_chain_reviews_and_tampering(tmp_path):
    store = Store(tmp_path)
    sid = store.create("A", "G", "demo", {}, [])
    e = store.add_event(sid, "phone")
    store.add_event(sid, "look_down")
    assert store.verify(sid)["ok"]
    store.review(e["id"], "dismissed", "False positive")
    assert store.verify(sid)["ok"]
    assert attention_summary(store.events(sid))["pending"] == 1
    with store.db:
        store.db.execute("UPDATE events SET payload='{}' WHERE id=?", (e["id"],))
    assert not store.verify(sid)["ok"]
    store.close()


def test_crash_recovery_preserves_answers(tmp_path):
    store = Store(tmp_path)
    sid = store.create("A", "G", "live", {}, [])
    store.save_answer(sid, "q1", 2)
    store.close()
    reopened = Store(tmp_path)
    assert reopened.session(sid)["status"] == "interrupted"
    assert reopened.session(sid)["answers"] == {"q1": 2}
    reopened.close()


def test_guard_keeps_emergency_exit_available():
    assert blocked_shortcut(ord("Q"), {0x11, 0x10}) == "EMERGENCY"
    assert blocked_shortcut(0x09, {0x12})
    assert blocked_shortcut(ord("C"), {0x11})
    assert blocked_shortcut(ord("A"), set()) is None
    assert blocked_shortcut(0x2E, {0x11, 0x12}) is None  # Ctrl+Alt+Delete remains OS-owned.


@pytest.fixture
def client(tmp_path):
    root = Path(__file__).resolve().parents[1]
    app = create_app(root, tmp_path, demo=True, initial_pin="123456")
    with TestClient(app) as c:
        c.headers["X-Qorgau-Token"] = app.state.runtime.csrf
        yield c, app.state.runtime
    app.state.runtime.store.close()


def start(client, **kw):
    payload = {"name": "Участник", "group": "ИСУ", "consent": True, **kw}
    if client.get("/api/state").json().get("participant") is None:
        entered = client.post("/api/participant/login", json={
            "name": payload["name"], "group": payload["group"],
        })
        assert entered.status_code == 200, entered.text
    return client.post("/api/sessions", json=payload)


def login(client):
    response = client.post("/api/login", json={"pin": "123456"})
    assert response.status_code == 200
    client.headers["Authorization"] = "Bearer " + response.json()["token"]


def test_full_exam_review_export_delete(client):
    c, rt = client
    assert start(c, consent=False).status_code == 400
    response = start(c)
    assert response.status_code == 200
    state = response.json()
    sid = state["session"]["id"]
    q = state["session"]["exam"][0]
    assert "correct" not in q
    assert start(c).status_code == 400
    assert c.post("/api/answer", json={"question_id": "bogus", "option": 1}).status_code == 400
    assert c.post("/api/answer", json={"question_id": q["id"], "option": 2}).status_code == 200
    assert c.post("/api/demo-event", json={"kind": "phone"}).json()["events"] == []
    assert c.get("/api/sessions").status_code == 401
    assert c.get(f"/api/sessions/{sid}/export/json").status_code == 401
    login(c)
    e = c.get("/api/state").json()["events"][0]
    assert e["simulated"]
    assert c.delete(f"/api/sessions/{sid}").status_code == 409
    assert c.post(f"/api/events/{e['id']}/review", json={"decision": "dismissed", "note": "=SUM(A1:A2)"}).status_code == 200
    result = c.post("/api/finish").json()
    assert result["total"] == 8
    assert c.post("/api/answer", json={"question_id": q["id"], "option": 1}).status_code == 409
    report = c.get(f"/api/sessions/{sid}/export/json").json()
    assert report["events"][0]["review"] == "dismissed"
    assert report["integrity"]["ok"]
    assert report["review_history"][0]["note"] == "=SUM(A1:A2)"
    assert "'=SUM" in c.get(f"/api/sessions/{sid}/export/csv").text
    assert "ДЕМОНСТРАЦИЯ" in c.get(f"/api/sessions/{sid}/export/html").text
    assert c.delete(f"/api/sessions/{sid}").status_code == 200
    assert c.get("/api/sessions").json() == []


def test_csrf_origin_and_hostname(client):
    c, rt = client
    assert c.post("/api/finish", headers={"X-Qorgau-Token": "wrong"}).status_code == 403
    assert c.post("/api/finish", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/state", headers={"Host": "evil.example"}).status_code == 400
    assert c.get("/").headers["x-frame-options"] == "DENY"


def test_pin_throttles_and_report_xss_is_escaped(client):
    c, rt = client
    entered = c.post("/api/participant/login", json={"name": "<script>alert(1)</script>", "group": "ИСУ"})
    assert entered.status_code == 200, entered.text
    r = c.post("/api/sessions", json={"name": "<script>alert(1)</script>", "consent": True})
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    login(c)
    assert "<script>alert(1)</script>" not in c.get(f"/api/sessions/{sid}/export/html").text
    for _ in range(5):
        assert c.post("/api/login", json={"pin": "wrong"}).status_code == 401
    assert c.post("/api/login", json={"pin": "123456"}).status_code == 429


def test_server_deadline_not_browser_clock(client):
    c, rt = client
    s = start(c).json()["session"]
    with rt.store.db:
        rt.store.db.execute("UPDATE sessions SET started=? WHERE id=?", (time.time() - 99999, s["id"]))
    assert c.post("/api/answer", json={"question_id": s["exam"][0]["id"], "option": 1}).status_code == 409
    assert rt.store.session(s["id"])["status"] == "completed"


def test_demo_events_impossible_in_live_mode(tmp_path):
    root = Path(__file__).resolve().parents[1]
    app = create_app(root, tmp_path, demo=False, initial_pin="123456")
    with TestClient(app) as c:
        c.headers["X-Qorgau-Token"] = app.state.runtime.csrf
        assert c.post("/api/demo-event", json={"kind": "phone"}).status_code == 403
        assert start(c).status_code == 400  # Cannot silently start without real camera.
    app.state.runtime.store.close()
