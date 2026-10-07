"""Material/photo observations across the live API and persistent audit log.

Numbered injected analysis frames verify policy and persistence, not physical
camera accuracy. Other objects remain review-only under strict enforcement.
"""
from copy import deepcopy
import json

import pytest

from test_enforcement import frame, instructor, live_app, start


MATERIALS = [
    ({"kind": "book", "confidence": .55, "box": [10, 20, 200, 280],
      "method": "yolo_coco", "class_id": 73}, "book", 1.2),
    ({"kind": "laptop", "confidence": .78, "box": [20, 80, 300, 180],
      "method": "yolo_coco", "class_id": 63}, "additional_device", 1.2),
    ({"kind": "monitor", "confidence": .91, "box": [80, 60, 240, 180],
      "method": "yolo_coco", "class_id": 62}, "additional_device", 1.2),
    ({"kind": "paper_candidate", "confidence": None, "box": [50, 90, 120, 180],
      "method": "paper_geometry", "text_rows": 8, "geometry_score": .86},
     "paper_candidate", 2.0),
]
MATERIAL_KINDS = {"book", "additional_device", "paper_candidate"}


def begin(client, **kwargs):
    response = start(client, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["session"]["id"]


def materials_frame(rt, clock, at, seq, item, **changes):
    frame(rt, clock, at, seq, phone=False, objects=[deepcopy(item)], **changes)


@pytest.mark.parametrize("item,event_kind,delay", MATERIALS,
                         ids=["book", "laptop", "monitor", "paper"])
@pytest.mark.parametrize("snapshots", [False, True])
def test_sustained_material_is_one_review_event_with_persisted_metadata(
        live_app, item, event_kind, delay, snapshots):
    with live_app() as (client, rt, clock):
        sid = begin(client, snapshots=snapshots)
        for seq, at in enumerate((100, 100.5, 101, 101.5, 102, 102.5, 103), 1):
            materials_frame(rt, clock, at, seq, item)
            if at - 100 < delay:
                assert not MATERIAL_KINDS.intersection(e["kind"] for e in rt.store.events(sid))

        events = rt.store.events(sid)
        assert [e["kind"] for e in events] == [event_kind]
        event = events[0]
        assert event["confidence"] == item["confidence"]
        assert event["duration"] >= delay
        assert event["observations"] == {"objects": [item], "review_required": True}
        assert event["simulated"] is False
        evidence_files = list((rt.store.folder / "evidence").glob("*.jpg"))
        if snapshots:
            assert len(evidence_files) == 1
            assert evidence_files[0].name == event["evidence"]
            assert evidence_files[0].read_bytes() == b"test-jpeg"
        else:
            assert event["evidence"] is None
            assert evidence_files == []
        assert rt.active_id == sid
        assert rt.store.session(sid)["status"] == "running"
        assert not rt.entry_block
        assert client.get("/api/state").json()["enforcement"]["blocked"] is False
        assert rt.store.verify(sid)["ok"]

        instructor(client)
        response = client.get(f"/api/sessions/{sid}/export/json")
        assert response.status_code == 200, response.text
        assert response.json()["events"] == events
        assert response.json()["integrity"]["ok"]


@pytest.mark.parametrize("item,event_kind,delay", [MATERIALS[0], MATERIALS[3]],
                         ids=["book", "paper"])
@pytest.mark.parametrize("scenario", ["brief", "stale", "not_ready", "repeated_sequence",
                                      "long_gap", "low_light", "camera_lost"])
def test_insufficient_material_evidence_neither_emits_nor_ends_attempt(
        live_app, item, event_kind, delay, scenario):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        if scenario == "brief":
            materials_frame(rt, clock, 100, 1, item)
            materials_frame(rt, clock, 100.5, 2, item)
            frame(rt, clock, 100.8, 3, phone=False, objects=[])
            # Two short episodes must not be concatenated into one long one.
            materials_frame(rt, clock, 101, 4, item)
            materials_frame(rt, clock, 101.5, 5, item)
            materials_frame(rt, clock, 102, 6, item)
        elif scenario == "long_gap":
            for seq, at in enumerate((100, 100.5, 102, 102.5, 103), 1):
                materials_frame(rt, clock, at, seq, item)
        else:
            for seq, at in enumerate((100, 100.5, 101, 101.5, 102, 102.5), 1):
                changes = {
                    "stale": {"analyzed_at": at - 2},
                    "not_ready": {"analysis_ready": False},
                    "repeated_sequence": {},
                    "low_light": {"brightness": 20},
                    "camera_lost": {"camera_ok": False},
                }[scenario]
                materials_frame(rt, clock, at, 1 if scenario == "repeated_sequence" else seq,
                                item, **changes)
        kinds = {e["kind"] for e in rt.store.events(sid)}
        assert not kinds.intersection(MATERIAL_KINDS | {"auto_disqualified"})
        assert rt.store.session(sid)["status"] == "running"
        assert rt.active_id == sid
        assert not rt.entry_block


@pytest.mark.parametrize("confidence", [.54, None, "0.9", float("nan"), float("inf")])
def test_unusable_model_confidence_never_emits_a_material_event(live_app, confidence):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        item = {**MATERIALS[0][0], "confidence": confidence}
        for seq, at in enumerate((100, 100.5, 101, 101.5, 102), 1):
            materials_frame(rt, clock, at, seq, item)
        assert not rt.store.events(sid)
        assert rt.active_id == sid and not rt.entry_block


def test_paper_requires_geometry_method_and_never_receives_a_probability(live_app):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        item = {**MATERIALS[3][0], "method": "unverified", "confidence": .99}
        for seq, at in enumerate((100, 100.5, 101, 101.5, 102, 102.5), 1):
            materials_frame(rt, clock, at, seq, item)
        assert not rt.store.events(sid)
        item["method"] = "paper_geometry"
        for seq, at in enumerate((103, 103.5, 104, 104.5, 105), 7):
            materials_frame(rt, clock, at, seq, item)
        event, = rt.store.events(sid)
        assert event["kind"] == "paper_candidate"
        # A geometric score is never a calibrated object probability.
        assert event["confidence"] is None


def test_observation_metadata_is_inside_hash_chain(live_app):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        for seq, at in enumerate((100, 100.6, 101.3), 1):
            materials_frame(rt, clock, at, seq, MATERIALS[0][0])
        event, = rt.store.events(sid)
        assert rt.store.verify(sid)["ok"]
        row = rt.store.db.execute("SELECT payload FROM events WHERE id=?", (event["id"],)).fetchone()
        payload = json.loads(row["payload"])
        payload["observations"]["objects"][0]["box"][0] += 1
        with rt.store.db:
            rt.store.db.execute("UPDATE events SET payload=? WHERE id=?",
                                (json.dumps(payload), event["id"]))
        result = rt.store.verify(sid)
        assert not result["ok"]
        assert result["broken_event"] == event["id"]


PHOTO_DETAILS = {
    "track_id": 9,
    "stage": "possible_photo",
    "near_face": True,
    "observed_raise_age_seconds": .4,
    "photo_hold_seconds": .5,
    "reasons": ["observed_raise_then_face_level_hold"],
    "camera_side_known": False,
    "photo_proven": False,
}


def test_possible_photo_is_persisted_before_dismissal_without_claiming_a_photo(live_app):
    with live_app() as (client, rt, clock):
        sid = begin(client, snapshots=True)
        for seq, at in enumerate((100, 100.5, 101, 101.6), 1):
            frame(rt, clock, at, seq, phone_photo_attempt=True,
                  photo_attempt_details=deepcopy(PHOTO_DETAILS))
        events = rt.store.events(sid)
        kinds = [e["kind"] for e in events]
        assert kinds.count("phone_photo_attempt") == 1
        assert kinds.count("auto_disqualified") == 1
        assert kinds.index("phone_photo_attempt") < kinds.index("auto_disqualified")
        observed = next(e for e in events if e["kind"] == "phone_photo_attempt")
        assert observed["observations"] == PHOTO_DETAILS
        assert observed["observations"]["photo_proven"] is False
        assert observed["observations"]["camera_side_known"] is False
        assert "не установлены" in observed["detail"]
        assert (rt.store.folder / "evidence" / observed["evidence"]).read_bytes() == b"test-jpeg"
        assert rt.store.session(sid)["status"] == "disqualified"
        assert rt.store.verify(sid)["ok"]
        instructor(client)
        report = client.get(f"/api/sessions/{sid}/export/json")
        assert report.status_code == 200, report.text
        assert report.json()["events"] == events
        for seq, at in enumerate((102, 102.5, 113, 114, 115), 5):
            frame(rt, clock, at, seq, phone_photo_attempt=True,
                  photo_attempt_details=deepcopy(PHOTO_DETAILS), objects=[MATERIALS[0][0]])
        assert rt.store.events(sid) == events


@pytest.mark.parametrize("scenario", ["stale", "not_ready", "no_phone"])
def test_unusable_photo_signal_is_not_recorded(live_app, scenario):
    with live_app() as (client, rt, clock):
        sid = begin(client)
        for seq, at in enumerate((100, 100.5, 101, 101.5, 102, 102.5), 1):
            changes = {"stale": {"analyzed_at": at - 2},
                       "not_ready": {"analysis_ready": False},
                       "no_phone": {"phone": False}}[scenario]
            frame(rt, clock, at, seq, phone_photo_attempt=True,
                  photo_attempt_details=deepcopy(PHOTO_DETAILS), **changes)
        assert "phone_photo_attempt" not in {e["kind"] for e in rt.store.events(sid)}
        assert rt.active_id == sid and not rt.entry_block
