import json
import sqlite3
from pathlib import Path
from .models import Member, Problem, now
from .security import restrict


class Store:
    def __init__(self, home: Path):
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(home / "state.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS accounts(label TEXT PRIMARY KEY, id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS refs(account_id INTEGER, peer_id INTEGER, kind TEXT, hash INTEGER,
          PRIMARY KEY(account_id,peer_id));
        CREATE TABLE IF NOT EXISTS mappings(id INTEGER PRIMARY KEY, source INTEGER, destination INTEGER,
          source_account TEXT, destination_account TEXT);
        CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY, username TEXT, display_name TEXT,
          bot INTEGER, deleted INTEGER);
        CREATE TABLE IF NOT EXISTS memberships(user_id INTEGER REFERENCES members(id), source INTEGER,
          collected_at INTEGER, PRIMARY KEY(user_id,source));
        CREATE TABLE IF NOT EXISTS collections(source INTEGER, account_id INTEGER, status TEXT,
          collected_at INTEGER, retrieved INTEGER, reported_count INTEGER, explanation TEXT,
          PRIMARY KEY(source,account_id));
        CREATE TABLE IF NOT EXISTS consent(user_id INTEGER, destination INTEGER, state TEXT,
          timestamp INTEGER, evidence TEXT, PRIMARY KEY(user_id,destination));
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, body TEXT, fingerprint TEXT);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, plan_id TEXT UNIQUE, status TEXT,
          not_before INTEGER DEFAULT 0, link TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS outcomes(run_id TEXT REFERENCES runs(id), user_id INTEGER,
          state TEXT, category TEXT, explanation TEXT, PRIMARY KEY(run_id,user_id));
        CREATE TABLE IF NOT EXISTS restrictions(account_id INTEGER PRIMARY KEY,
          not_before INTEGER DEFAULT 0, halted INTEGER DEFAULT 0);
        ''')
        restrict(home / "state.sqlite3")

    def execute(self, sql, args=()):
        with self.db:
            return self.db.execute(sql, args)

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        if not rows:
            raise Problem("not_found", "No matching local record")
        return rows[0]

    def mapping(self, mapping_id):
        return self.one("SELECT * FROM mappings WHERE id=?", (mapping_id,))

    def remember(self, account, peer, kind, access_hash):
        self.execute("INSERT OR REPLACE INTO refs VALUES (?,?,?,?)", (account, peer, kind, access_hash))

    def put_member(self, member: Member, source: int):
        self.execute("INSERT INTO members VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                     "username=excluded.username,display_name=excluded.display_name,"
                     "bot=excluded.bot,deleted=excluded.deleted",
                     (member.id, member.username, member.display_name, member.bot, member.deleted))
        self.execute("INSERT OR REPLACE INTO memberships VALUES (?,?,?)", (member.id, source, now()))

    def consent(self, user, destination):
        rows = self.rows("SELECT * FROM consent WHERE user_id=? AND destination=?", (user, destination))
        return rows[0] if rows else {"state": "unknown", "timestamp": None, "evidence": ""}

    def set_consent(self, user, destination, state, timestamp, evidence):
        if state not in {"unknown", "opted_in", "declined", "revoked"}:
            raise Problem("validation", "Invalid consent state")
        if state == "opted_in" and (not timestamp or not evidence.strip()):
            raise Problem("validation", "Opt-in requires timestamp and evidence reference")
        self.execute("INSERT OR REPLACE INTO consent VALUES (?,?,?,?,?)",
                     (user, destination, state, timestamp, evidence))

    def outcome(self, run, user, state, category="", explanation=""):
        self.execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?)", (run, user, state, category, explanation))

    def save_plan(self, plan, fingerprint):
        self.execute("INSERT INTO plans VALUES (?,?,?)", (plan["id"], json.dumps(plan), fingerprint))

    def plan(self, plan_id):
        row = self.one("SELECT * FROM plans WHERE id=?", (plan_id,))
        return json.loads(row["body"]), row["fingerprint"]

    def delete_source(self, source):
        # Plans contain personal IDs; invalidate/remove associated runs and plans as well.
        affected = [r["user_id"] for r in self.rows("SELECT user_id FROM memberships WHERE source=?", (source,))]
        for row in self.rows("SELECT id,body FROM plans"):
            if json.loads(row["body"])["mapping"]["source"] == source:
                runs = self.rows("SELECT id FROM runs WHERE plan_id=?", (row["id"],))
                for run in runs:
                    self.execute("DELETE FROM outcomes WHERE run_id=?", (run["id"],))
                self.execute("DELETE FROM runs WHERE plan_id=?", (row["id"],))
                self.execute("DELETE FROM plans WHERE id=?", (row["id"],))
        self.execute("DELETE FROM collections WHERE source=?", (source,))
        self.execute("DELETE FROM memberships WHERE source=?", (source,))
        for user in affected:
            if not self.rows("SELECT 1 FROM memberships WHERE user_id=?", (user,)):
                self.execute("DELETE FROM consent WHERE user_id=?", (user,))
                self.execute("DELETE FROM refs WHERE kind='user' AND peer_id=?", (user,))
                self.execute("DELETE FROM members WHERE id=?", (user,))
        self.db.execute("VACUUM")

    def close(self):
        self.db.close()
