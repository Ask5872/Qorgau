import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from .rules import LABELS, attention_summary


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "evidence").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.folder / "qorgau.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, group_name TEXT, mode TEXT,
          started REAL, ended REAL, status TEXT, config TEXT, exam TEXT, answers TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
          payload TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
          decision TEXT NOT NULL, note TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS event_session ON events(session_id);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.execute("UPDATE sessions SET status='interrupted', ended=? WHERE status='running'", (time.time(),))
        self.db.commit()

    def create(self, name, group, mode, config, exam):
        sid = secrets.token_hex(8)
        with self.lock, self.db:
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?, ?,?,?,?)",
                            (sid, name, group, mode, time.time(), None, "running",
                             canonical(config), canonical(exam), "{}", None))
        return sid

    def arm_session(self, sid, protection):
        """Start the clock only after the protected window has acknowledged readiness."""
        with self.lock, self.db:
            session = self.session(sid)
            if not session or session["status"] != "running":
                raise ValueError("Попытка уже завершена")
            config = {**session["config"], "guard": protection}
            self.db.execute("UPDATE sessions SET started=?, config=? WHERE id=? AND status='running'",
                            (time.time(), canonical(config), sid))

    def session(self, sid, include_exam=False):
        with self.lock:
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        out = dict(row)
        for key in ("config", "exam", "answers", "result"):
            out[key] = json.loads(out[key]) if out[key] else None
        if not include_exam:
            out.pop("exam")
        return out

    def sessions(self):
        with self.lock:
            ids = self.db.execute("SELECT id FROM sessions ORDER BY started DESC LIMIT 200").fetchall()
        result = []
        for row in ids:
            s = self.session(row[0])
            events = self.events(row[0])
            s["event_count"] = len(events)
            s["summary"] = attention_summary(events)
            result.append(s)
        return result

    def save_answer(self, sid, qid, value):
        with self.lock, self.db:
            session = self.session(sid)
            answers = session["answers"]
            answers[qid] = value
            self.db.execute("UPDATE sessions SET answers=? WHERE id=?", (canonical(answers), sid))
        return answers

    def finish(self, sid, result, status="completed", block=None):
        with self.lock, self.db:
            self.db.execute("UPDATE sessions SET status=?, ended=?, result=? WHERE id=?",
                            (status, time.time(), canonical(result), sid))
            if block is not None:
                self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('entry_block',?)", (canonical(block),))

    def get_setting(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, canonical(value)))

    def add_event(self, sid, kind, detail="", duration=0, confidence=None, evidence=None, simulated=False, observations=None):
        if kind not in LABELS:
            raise ValueError("Unknown event kind")
        label, severity = LABELS[kind]
        with self.lock, self.db:
            prev = self.db.execute("SELECT event_hash FROM events WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            prev_hash = prev[0] if prev else "0" * 64
            session = self.session(sid)
            payload = {"session_id": sid, "kind": kind, "label": label, "severity": severity,
                       "detail": detail[:1000], "duration": duration, "confidence": confidence,
                       "evidence": evidence, "simulated": simulated, "at": time.time(),
                       "offset": round(time.time() - session["started"], 1)}
            if observations is not None:
                payload["observations"] = observations
            raw = canonical(payload)
            digest = hashlib.sha256((prev_hash + raw).encode()).hexdigest()
            cur = self.db.execute("INSERT INTO events(session_id,payload,prev_hash,event_hash) VALUES(?,?,?,?)",
                                  (sid, raw, prev_hash, digest))
            payload.update(id=cur.lastrowid, hash=digest, review="pending", note="")
        return payload

    def events(self, sid):
        with self.lock:
            rows = self.db.execute("SELECT * FROM events WHERE session_id=? ORDER BY id", (sid,)).fetchall()
            reviews = self.db.execute("SELECT * FROM reviews WHERE event_id IN (SELECT id FROM events WHERE session_id=?) ORDER BY id", (sid,)).fetchall()
        latest = {r["event_id"]: dict(r) for r in reviews}
        result = []
        for r in rows:
            e = json.loads(r["payload"])
            review = latest.get(r["id"], {})
            e.update(id=r["id"], hash=r["event_hash"], review=review.get("decision", "pending"), note=review.get("note", ""))
            result.append(e)
        return result

    def review(self, event_id, decision, note):
        if decision not in {"pending", "confirmed", "dismissed"}:
            raise ValueError("Invalid decision")
        with self.lock, self.db:
            if not self.db.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
                raise KeyError(event_id)
            self.db.execute("INSERT INTO reviews(event_id,decision,note,at) VALUES(?,?,?,?)", (event_id, decision, note[:1000], time.time()))

    def verify(self, sid):
        with self.lock:
            rows = self.db.execute("SELECT * FROM events WHERE session_id=? ORDER BY id", (sid,)).fetchall()
        expected = "0" * 64
        for row in rows:
            digest = hashlib.sha256((expected + row["payload"]).encode()).hexdigest()
            if row["prev_hash"] != expected or digest != row["event_hash"]:
                return {"ok": False, "events": len(rows), "broken_event": row["id"]}
            expected = digest
        return {"ok": True, "events": len(rows), "root": expected}

    def review_history(self, sid):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM reviews WHERE event_id IN (SELECT id FROM events WHERE session_id=?) ORDER BY id", (sid,))]

    def delete(self, sid):
        with self.lock, self.db:
            self.db.execute("DELETE FROM reviews WHERE event_id IN (SELECT id FROM events WHERE session_id=?)", (sid,))
            self.db.execute("DELETE FROM events WHERE session_id=?", (sid,))
            self.db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        # IDs are generated by us; do not accept arbitrary paths from a browser.
        if len(sid) == 16 and all(c in "0123456789abcdef" for c in sid):
            for p in (self.folder / "evidence").glob(sid + "_*.jpg"):
                p.unlink(missing_ok=True)

    def close(self):
        self.db.close()
