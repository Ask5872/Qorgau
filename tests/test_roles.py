"""Local role handoff and API visibility; no identity/account system implied."""
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from qorgau.server import create_app


@pytest.fixture
def workspace(tmp_path):
    app = create_app(Path(__file__).resolve().parents[1], tmp_path,
                     demo=True, initial_pin="123456")
    with TestClient(app) as client:
        client.headers["X-Qorgau-Token"] = app.state.runtime.csrf
        yield client, app.state.runtime
    app.state.runtime.store.close()


def start(client):
    if client.get("/api/state").json().get("participant") is None:
        entered = client.post("/api/participant/login", json={"name": "Участник А", "group": "ИСУ"})
        assert entered.status_code == 200, entered.text
    response = client.post("/api/sessions", json={"name": "Участник А", "consent": True})
    assert response.status_code == 200, response.text
    return response.json()["session"]


def teacher_token(client):
    response = client.post("/api/login", json={"pin": "123456"})
    assert response.status_code == 200
    return response.json()["token"]


def teacher_headers(client):
    return {"Authorization": "Bearer " + teacher_token(client)}


def private_episode(rt, sid):
    episode = rt.store.add_event(sid, "phone", "Служебное описание",
                                  evidence="private-evidence.jpg",
                                  observations={"private_detail": "Для преподавателя"})
    rt.store.review(episode["id"], "confirmed", "Служебный комментарий")
    return episode


def test_public_state_keeps_exam_answers_but_hides_teacher_events_and_config(workspace):
    client, rt = workspace
    session = start(client)
    question = session["exam"][0]
    assert client.post("/api/answer", json={"question_id": question["id"], "option": 2}).status_code == 200
    episode = private_episode(rt, session["id"])
    config = rt.store.session(session["id"])["config"]
    config["teacher_notes"] = "Приватный комментарий к настройкам"
    with rt.store.db:
        rt.store.db.execute("UPDATE sessions SET config=? WHERE id=?", (json.dumps(config), session["id"]))

    response = client.get("/api/state")
    public = response.json()
    assert public["viewer_role"] == "participant"
    assert public["session"]["answers"] == {question["id"]: 2}
    assert public["session"]["exam"] == session["exam"]
    assert all("correct" not in q for q in public["session"]["exam"])
    assert "teacher_notes" not in public["session"]["config"]
    assert "guard" not in public["session"]["config"]
    assert "questions" not in public["exam_config"]
    assert public["events"] == []
    assert public["summary"]["pending"] == 0
    for private in ("private-evidence.jpg", "Служебный комментарий", "private_detail", episode["hash"]):
        assert private not in response.text

    privileged = client.get("/api/state", headers=teacher_headers(client)).json()
    assert privileged["viewer_role"] == "teacher"
    assert privileged["events"][0]["note"] == "Служебный комментарий"
    assert privileged["events"][0]["evidence"] == "private-evidence.jpg"
    assert privileged["session"]["config"]["teacher_notes"] == config["teacher_notes"]


@pytest.mark.parametrize("authorization", ["", "Bearer wrong", "Basic wrong", "wrong"])
def test_invalid_supplied_teacher_authorization_cannot_silently_become_participant(workspace, authorization):
    client, _rt = workspace
    assert client.get("/api/state", headers={"Authorization": authorization}).status_code == 401
    assert client.get("/api/state").status_code == 200


def test_expired_teacher_session_is_rejected(workspace):
    client, rt = workspace
    token = teacher_token(client)
    rt.auth.sessions[token] = time.monotonic() - 1
    assert client.get("/api/state", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_logout_revokes_only_given_token_and_is_idempotent(workspace):
    client, rt = workspace
    sid = start(client)["id"]
    private_episode(rt, sid)
    first, second = teacher_headers(client), teacher_headers(client)
    response = client.post("/api/logout", headers=first, json={})
    assert response.status_code == 200
    assert response.json()["viewer_role"] == "participant"
    assert response.json()["events"] == []
    assert client.get("/api/state", headers=first).status_code == 401
    assert client.get("/api/exam-config", headers=first).status_code == 401
    assert client.get("/api/state", headers=second).json()["viewer_role"] == "teacher"
    assert client.post("/api/logout", headers=first, json={}).status_code == 200
    assert client.post("/api/logout", json={}).status_code == 200


def test_next_participant_reset_hides_result_but_teacher_keeps_report(workspace):
    client, rt = workspace
    sid = start(client)["id"]
    private_episode(rt, sid)
    assert client.post("/api/finish").status_code == 200
    assert client.get("/api/state").json()["session"]["id"] == sid
    reset = client.post("/api/participant/reset", json={})
    assert reset.status_code == 200
    assert reset.json()["session"] is None
    assert reset.json()["participant"] is None
    assert client.get("/api/state").json()["session"] is None
    headers = teacher_headers(client)
    assert client.get("/api/state", headers=headers).json()["session"]["id"] == sid
    report = client.get(f"/api/sessions/{sid}", headers=headers).json()
    assert report["events"][0]["note"] == "Служебный комментарий"
    assert report["session"]["status"] == "completed"
    assert rt.store.verify(sid)["ok"]
    assert start(client)["id"] != sid


def test_reset_cannot_hide_active_exam_or_change_its_answers(workspace):
    client, rt = workspace
    sid = start(client)["id"]
    response = client.post("/api/participant/reset", json={})
    assert response.status_code == 409
    assert rt.active_id == sid
    assert client.get("/api/state").json()["session"]["id"] == sid


def test_reset_hides_blocked_student_result_but_does_not_unlock_workstation(workspace):
    client, rt = workspace
    sid = start(client)["id"]
    with rt.lock:
        rt._finish_locked("disqualified", reason="phone")
    response = client.post("/api/participant/reset", json={})
    assert response.status_code == 200
    assert response.json()["session"] is None
    assert response.json()["participant"] is None
    assert response.json()["enforcement"]["blocked"]
    entered = client.post("/api/participant/login", json={"name": "Участник Б", "group": "ИСУ"})
    assert entered.status_code == 200, entered.text
    assert client.post("/api/sessions", json={"name": "Участник Б", "consent": True}).status_code == 400
    assert client.post("/api/unlock", json={}).status_code == 401
    assert rt.store.session(sid)["status"] == "disqualified"
    assert rt.store.get_setting("entry_block")["session_id"] == sid


def test_demo_mutation_response_cannot_bypass_participant_event_redaction(workspace):
    client, rt = workspace
    sid = start(client)["id"]
    response = client.post("/api/demo-event", json={"kind": "phone"})
    assert response.status_code == 200
    assert response.json()["events"] == []
    assert len(rt.store.events(sid)) == 1
    response = client.post("/api/demo-event", json={"kind": "look_down"}, headers=teacher_headers(client))
    assert len(response.json()["events"]) == 2


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/sessions", None),
    ("GET", "/api/exam-config", None),
    ("PUT", "/api/exam-config", {"title": "T", "questions": [{"text": "Q", "options": ["a", "b", "c", "d"], "correct": 0}]}),
    ("GET", "/api/sessions/{sid}", None),
    ("GET", "/api/sessions/{sid}/export/json", None),
    ("GET", "/api/sessions/{sid}/export/html", None),
    ("GET", "/api/sessions/{sid}/export/csv", None),
    ("GET", "/api/evidence/{eid}", None),
    ("POST", "/api/events/{eid}/review", {"decision": "dismissed", "note": "not allowed"}),
    ("DELETE", "/api/sessions/{sid}", None),
    ("POST", "/api/unlock", {}),
])
def test_participant_cannot_access_teacher_api_even_with_known_identifiers(workspace, method, path, payload):
    client, rt = workspace
    sid = start(client)["id"]
    episode = private_episode(rt, sid)
    response = client.request(method, path.format(sid=sid, eid=episode["id"]), json=payload)
    assert response.status_code == 401
    assert rt.store.events(sid)[0]["review"] == "confirmed"
    assert rt.store.session(sid)


@pytest.mark.parametrize("path", ["/api/logout", "/api/participant/reset"])
def test_role_handoff_routes_keep_csrf_protection(workspace, path):
    client, _rt = workspace
    assert client.post(path, json={}, headers={"X-Qorgau-Token": "wrong"}).status_code == 403
