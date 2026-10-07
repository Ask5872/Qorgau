"""Separate local participant entry; teacher authentication stays independent."""
from pathlib import Path

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


def participant(client, name="Асет Балташев", group="ИСУ-23-2"):
    return client.post("/api/participant/login", json={"name": name, "group": group})


def start(client, **changes):
    return client.post("/api/sessions", json={"name": "Подставное имя", "group": "Другая группа",
                                              "consent": True, **changes})


@pytest.mark.parametrize("demo", [True, False])
def test_entry_is_required_before_start_in_both_modes(workspace, demo):
    client, rt = workspace
    rt.demo = demo
    assert client.get("/api/state").json()["participant"] is None
    response = start(client)
    assert response.status_code == 400
    assert "войдите как участник" in response.json()["detail"]
    assert rt.store.sessions() == []


def test_unicode_entry_is_trimmed_and_binds_attempt_instead_of_client_identity(workspace):
    client, rt = workspace
    response = participant(client, "  Балташев Асет Жанболатович\t", "\nИСУ-23-2 (KZ-US)  ")
    assert response.status_code == 200, response.text
    profile = {"name": "Балташев Асет Жанболатович", "group": "ИСУ-23-2 (KZ-US)"}
    assert response.json()["participant"] == profile
    assert response.json()["viewer_role"] == "participant"
    assert client.get("/api/state").json()["participant"] == profile
    session = start(client).json()["session"]
    assert session["name"] == profile["name"]
    assert session["group_name"] == profile["group"]
    stored = rt.store.session(session["id"])
    assert stored["name"] == profile["name"]
    assert stored["group_name"] == profile["group"]
    client.post("/api/finish")
    state = client.get("/api/state").json()
    assert state["participant"] == profile
    assert state["session"]["id"] == session["id"]


@pytest.mark.parametrize("payload", [
    {}, {"name": "Асет"}, {"group": "ИСУ"},
    {"name": "", "group": "ИСУ"}, {"name": " \t\n", "group": "ИСУ"},
    {"name": "Асет", "group": ""}, {"name": "Асет", "group": " \t"},
    {"name": "Я" * 101, "group": "ИСУ"}, {"name": "Асет", "group": "А" * 61},
    {"name": None, "group": "ИСУ"}, {"name": 123, "group": "ИСУ"},
    {"name": "Асет", "group": "ИСУ", "role": "teacher"},
    {"name": "Асет", "group": "ИСУ", "pin": "123456"},
])
def test_invalid_or_privilege_shaped_entry_does_not_create_profile(workspace, payload):
    client, rt = workspace
    response = client.post("/api/participant/login", json=payload)
    assert response.status_code == 422
    assert rt.participant_profile is None
    assert rt.store.sessions() == []


def test_maximum_lengths_apply_after_trim(workspace):
    client, _rt = workspace
    response = participant(client, "  " + "А" * 100 + "  ", " " + "Б" * 60 + " ")
    assert response.status_code == 200
    assert response.json()["participant"] == {"name": "А" * 100, "group": "Б" * 60}


@pytest.mark.parametrize("path", ["/api/sessions", "/api/exam-config"])
def test_participant_entry_never_grants_teacher_access(workspace, path):
    client, rt = workspace
    assert participant(client).status_code == 200
    assert client.get(path).status_code == 401
    assert client.post("/api/unlock", json={}).status_code == 401
    assert rt.auth.sessions == {}
    teacher = client.post("/api/login", json={"pin": "123456"})
    assert teacher.status_code == 200
    token = teacher.json()["token"]
    assert client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 200


@pytest.mark.parametrize("path", ["/api/participant/login", "/api/participant/reset"])
def test_active_attempt_blocks_identity_change_and_reset(workspace, path):
    client, rt = workspace
    participant(client)
    session = start(client).json()["session"]
    response = client.post(path, json={"name": "Другой человек", "group": "Другая группа"})
    assert response.status_code == 409
    assert rt.active_id == session["id"]
    assert rt.participant_profile == {"name": "Асет Балташев", "group": "ИСУ-23-2"}
    assert rt.participant_visible_id == session["id"]


@pytest.mark.parametrize("path", ["/api/participant/login", "/api/participant/reset"])
def test_cleanup_blocks_identity_change_and_reset(workspace, path):
    client, rt = workspace
    participant(client)
    rt.cleanup_in_progress = True
    response = client.post(path, json={"name": "Другой человек", "group": "Другая группа"})
    assert response.status_code == 409
    assert rt.participant_profile == {"name": "Асет Балташев", "group": "ИСУ-23-2"}
    rt.cleanup_in_progress = False


def test_reset_clears_entry_and_result_but_preserves_teacher_report(workspace):
    client, rt = workspace
    participant(client)
    sid = start(client).json()["session"]["id"]
    client.post("/api/finish")
    response = client.post("/api/participant/reset", json={})
    assert response.status_code == 200
    assert response.json()["participant"] is None
    assert response.json()["session"] is None
    assert rt.state(instructor=True)["session"]["id"] == sid
    assert rt.store.session(sid)["status"] == "completed"
    assert start(client).status_code == 400


def test_new_entry_hides_previous_result_and_cannot_clear_phone_block(workspace):
    client, rt = workspace
    participant(client)
    sid = start(client).json()["session"]["id"]
    with rt.lock:
        rt._finish_locked("disqualified", reason="phone")
    response = participant(client, "Другой участник", "ИС-23-1")
    assert response.status_code == 200
    assert response.json()["participant"]["name"] == "Другой участник"
    assert response.json()["session"] is None
    assert response.json()["enforcement"]["blocked"] is True
    assert start(client).status_code == 400
    assert rt.store.get_setting("entry_block")["session_id"] == sid
    assert rt.store.session(sid)["status"] == "disqualified"
    assert rt.state(instructor=True)["session"]["id"] == sid


@pytest.mark.parametrize("headers", [{"X-Qorgau-Token": "wrong"}, {"Origin": "https://other.example"}])
def test_participant_login_keeps_request_origin_and_csrf_checks(workspace, headers):
    client, rt = workspace
    response = client.post("/api/participant/login", json={"name": "Асет", "group": "ИСУ"}, headers=headers)
    assert response.status_code == 403
    assert rt.participant_profile is None


def test_state_profile_is_a_copy(workspace):
    client, rt = workspace
    participant(client)
    state = rt.state(instructor=False)
    state["participant"]["name"] = "Изменено"
    assert rt.participant_profile["name"] == "Асет Балташев"
