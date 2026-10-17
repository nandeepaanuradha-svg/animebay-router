"""SQLite state: input cursors, routes, episode pairs and durable send operations."""

import json
import secrets
import sqlite3
import time
from pathlib import Path

from .parser import normalize, parse_release


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (chat_id INTEGER PRIMARY KEY, cursor INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS routes (
            key TEXT PRIMARY KEY, title TEXT NOT NULL, destination INTEGER NOT NULL,
            slug TEXT NOT NULL DEFAULT '', sticker_chat INTEGER, sticker_id INTEGER,
            enabled INTEGER NOT NULL DEFAULT 0, finished INTEGER NOT NULL DEFAULT 0,
            override_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS aliases (
            normalized TEXT PRIMARY KEY, title TEXT NOT NULL,
            route TEXT NOT NULL REFERENCES routes(key)
        );
        CREATE TABLE IF NOT EXISTS inbox (
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            filename TEXT NOT NULL, caption TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new', reason TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(chat_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, route TEXT NOT NULL REFERENCES routes(key),
            episode TEXT NOT NULL, destination INTEGER NOT NULL, title TEXT NOT NULL,
            created_at INTEGER NOT NULL, anchor INTEGER, done INTEGER NOT NULL DEFAULT 0,
            retry_at INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '', notified_at INTEGER NOT NULL DEFAULT 0,
            refresh_at INTEGER NOT NULL DEFAULT 0, UNIQUE(route, episode)
        );
        CREATE TABLE IF NOT EXISTS media (
            job INTEGER NOT NULL REFERENCES jobs(id), quality TEXT NOT NULL,
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, filename TEXT NOT NULL,
            PRIMARY KEY(job, quality)
        );
        CREATE TABLE IF NOT EXISTS outbox (
            job INTEGER NOT NULL REFERENCES jobs(id), stage TEXT NOT NULL,
            random_id INTEGER NOT NULL UNIQUE, payload TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0, message_id INTEGER,
            PRIMARY KEY(job, stage)
        );
        """)

    def close(self):
        self.db.close()

    def rows(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def execute(self, sql, params=()):
        with self.db:
            return self.db.execute(sql, params)

    def get(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key, value):
        self.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def route(self, key):
        return self.one("SELECT * FROM routes WHERE key=?", (key,))

    def add_route(self, key, title, destination):
        with self.db:
            self.db.execute("INSERT INTO routes(key,title,destination) VALUES (?,?,?)",
                            (key, title, destination))
            self.db.execute("INSERT INTO aliases VALUES (?,?,?)", (normalize(title), title, key))

    def add_alias(self, key, title):
        normalized = normalize(title)
        if not normalized:
            raise ValueError("Alias cannot be empty")
        with self.db:
            old = self.one("SELECT * FROM aliases WHERE normalized=?", (normalized,))
            if old and old["route"] != key:
                raise ValueError(f"That alias already belongs to {old['route']}")
            self.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?,?)", (normalized, title, key))

    def match(self, title):
        return self.one("SELECT r.* FROM routes r JOIN aliases a ON a.route=r.key "
                        "WHERE a.normalized=?", (normalize(title),))

    def capture(self, chat_id, message_id, filename, caption=""):
        """Called before a polling cursor advances; safe to repeat after a crash."""
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO inbox(chat_id,message_id,filename,caption) "
                            "VALUES (?,?,?,?)", (chat_id, message_id, filename, caption))
        return self.assign(chat_id, message_id)

    def assign(self, chat_id, message_id):
        row = self.one("SELECT * FROM inbox WHERE chat_id=? AND message_id=?", (chat_id, message_id))
        if row["status"] not in ("new", "unmatched"):
            return row["status"]
        try:
            release = parse_release(row["filename"], row["caption"])
        except ValueError as exc:
            self.execute("UPDATE inbox SET status='ignored', reason=? WHERE chat_id=? AND message_id=?",
                         (str(exc), chat_id, message_id))
            return "ignored"
        route = self.match(release.title)
        if not route:
            self.execute("UPDATE inbox SET status='unmatched', reason=? WHERE chat_id=? AND message_id=?",
                         (release.title, chat_id, message_id))
            return "unmatched"
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs(route,episode,destination,title,created_at) "
                            "VALUES (?,?,?,?,?)", (route["key"], release.episode,
                            route["destination"], route["title"], int(time.time())))
            job = self.one("SELECT id FROM jobs WHERE route=? AND episode=?", (route["key"], release.episode))
            result = self.db.execute("INSERT OR IGNORE INTO media VALUES (?,?,?,?,?)",
                                    (job["id"], release.quality, chat_id, message_id, row["filename"]))
            status = "queued" if result.rowcount else "duplicate"
            self.db.execute("UPDATE inbox SET status=?,reason='' WHERE chat_id=? AND message_id=?",
                            (status, chat_id, message_id))
        return status

    def rematch(self):
        count = 0
        for row in self.rows("SELECT chat_id,message_id FROM inbox WHERE status='unmatched'"):
            count += self.assign(row["chat_id"], row["message_id"]) == "queued"
        return count

    def cursor(self, chat_id, message_id):
        self.execute("UPDATE sources SET cursor=MAX(cursor,?) WHERE chat_id=?", (message_id, chat_id))

    def pending(self):
        return self.rows("SELECT j.*,r.enabled FROM jobs j JOIN routes r ON j.route=r.key "
                         "WHERE j.done=0 ORDER BY j.id")

    def qualities(self, job):
        return {row["quality"]: row for row in self.rows("SELECT * FROM media WHERE job=?", (job,))}

    def operation(self, job, stage):
        row = self.one("SELECT * FROM outbox WHERE job=? AND stage=?", (job, stage))
        if row:
            row["payload"] = json.loads(row["payload"])
        return row

    def prepare(self, job, stage, payload):
        self.execute("INSERT OR IGNORE INTO outbox(job,stage,random_id,payload) VALUES (?,?,?,?)",
                     (job, stage, secrets.randbits(63) or 1, json.dumps(payload, ensure_ascii=False)))
        return self.operation(job, stage)

    def sent(self, op, message_id):
        with self.db:
            self.db.execute("UPDATE outbox SET done=1,message_id=? WHERE job=? AND stage=?",
                            (message_id, op["job"], op["stage"]))
            self.db.execute("UPDATE jobs SET retry_at=0,attempts=0,error='' WHERE id=?", (op["job"],))
            if op["stage"] == "schedule":
                self.db.execute("UPDATE jobs SET refresh_at=0 WHERE route=(SELECT route FROM jobs WHERE id=?) AND id<>?",
                                (op["job"], op["job"]))
                self.db.execute("UPDATE jobs SET done=1,refresh_at=? WHERE id=?",
                                (int(time.time()) + 900, op["job"]))
