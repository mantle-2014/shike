"""Local SQLite storage for TaskTimeDesk. No network code or dependencies."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse(value):
    return datetime.fromisoformat(value)


def seconds_between(start, end):
    return max(0, int((parse(end) - parse(start)).total_seconds()))


def config_directory():
    override = os.environ.get("TASKTIME_CONFIG_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TaskTimeDesk"
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_directory():
    override = os.environ.get("TASKTIME_DATA_DIR")
    if override:
        base = Path(override)
    else:
        root = config_directory()
        config = root / "storage.json"
        if config.exists():
            configured = json.loads(config.read_text(encoding="utf-8")).get("data_dir")
            if not configured or not Path(configured).is_absolute():
                raise ValueError("数据存储位置配置无效")
            base = Path(configured)
        else:
            base = root
    base.mkdir(parents=True, exist_ok=True)
    return base


def set_data_directory(folder):
    target = Path(folder).resolve()
    if not target.is_absolute():
        raise ValueError("请选择完整的数据目录路径")
    root = config_directory()
    temporary = root / f".storage-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps({"data_dir": str(target)}, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, root / "storage.json")


def validate_data_file(path):
    with closing(sqlite3.connect(str(path))) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("目标数据库校验失败")
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
            raise ValueError("目标目录中的 tasktime.db 不是时刻数据库")


def prepare_data_directory(store, folder, use_existing=False):
    """Copy a consistent SQLite snapshot to a new folder; keep the source intact."""
    target_dir = Path(folder).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / "tasktime.db"
    if target_dir == store.path.parent.resolve():
        raise ValueError("新位置与当前数据目录相同")
    if destination.exists() and use_existing:
        validate_data_file(destination)
        return destination
    if destination.exists():
        validate_data_file(destination)
        backup_path = target_dir / f"tasktime-before-replace-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.db"
        with closing(sqlite3.connect(str(destination))) as original, closing(sqlite3.connect(str(backup_path))) as saved:
            original.backup(saved)
    temporary = target_dir / f".tasktime-{uuid.uuid4().hex}.db"
    try:
        store.backup(temporary)
        if destination.exists():
            for suffix in ("-wal", "-shm"):
                Path(str(destination) + suffix).unlink(missing_ok=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_directory() / "tasktime.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#4f8bd6',
            archived INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 1,
            folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
            system INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY, day TEXT NOT NULL, title TEXT NOT NULL,
            task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
            done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL,
            goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
            done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY, body TEXT NOT NULL,
            goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
            done INTEGER NOT NULL DEFAULT 0,
            todo_id INTEGER REFERENCES todos(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS note_items (
            id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
            todo_id INTEGER REFERENCES todos(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
            goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
            start_at TEXT NOT NULL, end_at TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'normal', created_at TEXT NOT NULL,
            CHECK(end_at >= start_at)
        );
        CREATE TABLE IF NOT EXISTS breaks (
            id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id),
            start_at TEXT NOT NULL, end_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_timer (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            task_id INTEGER REFERENCES tasks(id), goal_id INTEGER REFERENCES goals(id),
            start_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
            mode TEXT NOT NULL, focus_minutes INTEGER NOT NULL DEFAULT 25,
            break_minutes INTEGER NOT NULL DEFAULT 5, alerted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pomodoro (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            phase TEXT NOT NULL CHECK(phase IN ('focus','break','long_break')),
            phase_start TEXT NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 0,
            focus_minutes INTEGER NOT NULL,
            break_minutes INTEGER NOT NULL,
            long_minutes INTEGER NOT NULL,
            long_every INTEGER NOT NULL DEFAULT 4,
            started_other INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS checkins (
            day TEXT PRIMARY KEY, start_at TEXT, end_at TEXT
        );
        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS entries_start ON entries(start_at);
        CREATE INDEX IF NOT EXISTS goals_day ON goals(day);
        """)
        existing = {r["name"] for r in self.db.execute("PRAGMA table_info(tasks)")}
        if "pinned" not in existing:
            self.db.execute("ALTER TABLE tasks ADD COLUMN pinned INTEGER NOT NULL DEFAULT 1")
        if "folder_id" not in existing:
            self.db.execute("ALTER TABLE tasks ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")
        if "system" not in existing:
            self.db.execute("ALTER TABLE tasks ADD COLUMN system INTEGER NOT NULL DEFAULT 0")
        note_columns = {r["name"] for r in self.db.execute("PRAGMA table_info(notes)")}
        pomo_columns = {r["name"] for r in self.db.execute("PRAGMA table_info(pomodoro)")}
        if "long_every" not in pomo_columns:
            self.db.execute("ALTER TABLE pomodoro ADD COLUMN long_every INTEGER NOT NULL DEFAULT 4")
        if "archived" not in note_columns:
            self.db.execute("ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        if not self.db.execute("SELECT 1 FROM preferences WHERE key='legacy_breaks_migrated'").fetchone():
            self.db.execute("INSERT INTO entries(task_id,start_at,end_at,mode,created_at) SELECT task_id,start_at,end_at,'legacy_break',? FROM breaks WHERE task_id IS NOT NULL AND end_at>start_at", (now(),))
            self.db.execute("INSERT INTO preferences(key,value) VALUES('legacy_breaks_migrated','1')")
            self.db.execute("UPDATE active_timer SET mode='normal' WHERE mode IN ('break','pomodoro')")
        self.db.commit()

    def rows(self, sql, params=()):
        return self.db.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.db.execute(sql, params).fetchone()

    def execute(self, sql, params=()):
        with self.db:
            return self.db.execute(sql, params).lastrowid

    def tasks(self, include_archived=False):
        return self.rows("SELECT * FROM tasks " + ("" if include_archived else "WHERE archived=0 ") + "ORDER BY archived,pinned DESC,name")

    def folders(self):
        return self.rows("SELECT * FROM folders ORDER BY name")

    def add_folder(self, name):
        return self.execute("INSERT INTO folders(name,created_at) VALUES(?,?)", (name.strip(), now()))

    def assign_folder(self, task_id, folder_id):
        self.execute("UPDATE tasks SET folder_id=? WHERE id=?", (folder_id, task_id))

    def delete_task(self, task_id):
        task = self.one("SELECT system FROM tasks WHERE id=?", (task_id,))
        if not task:
            return False
        if task["system"]:
            with self.db:
                self.db.execute("DELETE FROM entries WHERE task_id=?", (task_id,))
                self.db.execute("DELETE FROM breaks WHERE task_id=?", (task_id,))
                stamp = now()
                self.db.execute("UPDATE active_timer SET start_at=?,heartbeat_at=? WHERE task_id=?", (stamp, stamp, task_id))
            return True
        with self.db:
            self.db.execute("DELETE FROM entries WHERE task_id=?", (task_id,))
            self.db.execute("DELETE FROM breaks WHERE task_id=?", (task_id,))
            self.db.execute("DELETE FROM active_timer WHERE task_id=?", (task_id,))
            self.db.execute("UPDATE goals SET task_id=NULL WHERE task_id=?", (task_id,))
            self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return True

    def delete_folder(self, folder_id):
        with self.db:
            self.db.execute("UPDATE tasks SET folder_id=NULL WHERE folder_id=?", (folder_id,))
            self.db.execute("DELETE FROM folders WHERE id=?", (folder_id,))

    def delete_note(self, note_id):
        self.execute("DELETE FROM notes WHERE id=?", (note_id,))

    def archive_note(self, note_id, archived=True):
        self.execute("UPDATE notes SET archived=? WHERE id=?", (int(archived), note_id))

    def other_task(self):
        row = self.one("SELECT id FROM tasks WHERE system=1 LIMIT 1")
        if row:
            return row["id"]
        with self.db:
            cur = self.db.execute("INSERT INTO tasks(name,color,archived,pinned,system,created_at) VALUES('其他','#8b91a3',0,0,1,?)", (now(),))
            return cur.lastrowid

    def add_task(self, name):
        return self.execute("INSERT INTO tasks(name,created_at) VALUES(?,?)", (name.strip(), now()))

    def goals(self, day=None):
        return self.rows("SELECT g.*,t.name task_name FROM goals g LEFT JOIN tasks t ON t.id=g.task_id WHERE (? IS NULL OR g.day=?) ORDER BY g.done,g.id", (day, day))

    def add_goal(self, day, title, task_id=None):
        return self.execute("INSERT INTO goals(day,title,task_id,created_at) VALUES(?,?,?,?)", (day, title.strip(), task_id, now()))

    def delete_goal(self, goal_id):
        with self.db:
            self.db.execute("UPDATE entries SET goal_id=NULL WHERE goal_id=?", (goal_id,))
            self.db.execute("UPDATE active_timer SET goal_id=NULL WHERE goal_id=?", (goal_id,))
            self.db.execute("UPDATE notes SET goal_id=NULL WHERE goal_id=?", (goal_id,))
            self.db.execute("DELETE FROM todos WHERE goal_id=?", (goal_id,))
            self.db.execute("DELETE FROM goals WHERE id=?", (goal_id,))

    def todos(self, goal_id=None, day=None):
        if day:
            return self.rows("SELECT x.*,g.title goal_title FROM todos x LEFT JOIN goals g ON g.id=x.goal_id WHERE g.day=? OR x.goal_id IS NULL ORDER BY x.done,x.id", (day,))
        if goal_id is None:
            return self.rows("SELECT x.*,g.title goal_title FROM todos x LEFT JOIN goals g ON g.id=x.goal_id ORDER BY x.done,x.id")
        return self.rows("SELECT x.*,g.title goal_title FROM todos x LEFT JOIN goals g ON g.id=x.goal_id WHERE x.goal_id=? ORDER BY x.done,x.id", (goal_id,))

    def add_todo(self, title, goal_id=None):
        return self.execute("INSERT INTO todos(title,goal_id,created_at) VALUES(?,?,?)", (title.strip(), goal_id, now()))

    def notes(self, archived=False):
        return self.rows("SELECT n.*,g.title goal_title,x.done todo_done FROM notes n LEFT JOIN goals g ON g.id=n.goal_id LEFT JOIN todos x ON x.id=n.todo_id WHERE n.archived=? ORDER BY n.id DESC", (int(archived),))

    def add_note(self, body, goal_id=None):
        return self.execute("INSERT INTO notes(body,goal_id,created_at) VALUES(?,?,?)", (body.strip(), goal_id, now()))

    def add_note_with_items(self, body, items, goal_id=None):
        with self.db:
            cur = self.db.execute("INSERT INTO notes(body,goal_id,created_at) VALUES(?,?,?)", (body.strip(), goal_id, now()))
            for item in items:
                if item.strip():
                    self.db.execute("INSERT INTO note_items(note_id,title) VALUES(?,?)", (cur.lastrowid, item.strip()))
            return cur.lastrowid

    def update_note(self, note_id, body, goal_id, items):
        """Replace editable note content while preserving surviving checklist item ids."""
        with self.db:
            self.db.execute("UPDATE notes SET body=?,goal_id=? WHERE id=?", (body.strip(), goal_id, note_id))
            previous = {row["id"] for row in self.note_items(note_id)}
            retained = set()
            for item in items:
                title = item["title"].strip()
                if not title:
                    continue
                item_id = item.get("id")
                if item_id in previous:
                    self.db.execute("UPDATE note_items SET title=?,done=? WHERE id=? AND note_id=?", (title, int(item["done"]), item_id, note_id))
                    retained.add(item_id)
                else:
                    self.db.execute("INSERT INTO note_items(note_id,title,done) VALUES(?,?,?)", (note_id, title, int(item["done"])))
            for item_id in previous - retained:
                self.db.execute("DELETE FROM note_items WHERE id=?", (item_id,))

    def note_items(self, note_id):
        return self.rows("SELECT * FROM note_items WHERE note_id=? ORDER BY id", (note_id,))

    def set_note_item_done(self, item_id, done):
        with self.db:
            self.db.execute("UPDATE note_items SET done=? WHERE id=?", (int(done), item_id))
            row = self.one("SELECT todo_id FROM note_items WHERE id=?", (item_id,))
            if row and row["todo_id"]:
                self.db.execute("UPDATE todos SET done=? WHERE id=?", (int(done), row["todo_id"]))

    def set_done(self, table, item_id, done):
        if table not in ("goals", "todos", "notes"):
            raise ValueError(table)
        with self.db:
            self.db.execute(f"UPDATE {table} SET done=? WHERE id=?", (int(done), item_id))
            if table == "todos":
                self.db.execute("UPDATE notes SET done=? WHERE todo_id=?", (int(done), item_id))
                self.db.execute("UPDATE note_items SET done=? WHERE todo_id=?", (int(done), item_id))
            if table == "notes":
                note = self.one("SELECT todo_id FROM notes WHERE id=?", (item_id,))
                if note and note["todo_id"]:
                    self.db.execute("UPDATE todos SET done=? WHERE id=?", (int(done), note["todo_id"]))

    def active(self):
        return self.one("SELECT a.*,t.name task_name FROM active_timer a LEFT JOIN tasks t ON t.id=a.task_id WHERE singleton=1")

    def _finish_active(self, end_at):
        active = self.active()
        if not active:
            return
        if end_at < active["start_at"]:
            end_at = active["start_at"]
        if seconds_between(active["start_at"], end_at) == 0:
            self.db.execute("DELETE FROM active_timer WHERE singleton=1")
            return
        if active["mode"] == "break":
            self.db.execute("INSERT INTO breaks(task_id,start_at,end_at) VALUES(?,?,?)", (active["task_id"], active["start_at"], end_at))
        else:
            self.db.execute("INSERT INTO entries(task_id,goal_id,start_at,end_at,mode,created_at) VALUES(?,?,?,?,?,?)", (active["task_id"], active["goal_id"], active["start_at"], end_at, active["mode"], now()))
        self.db.execute("DELETE FROM active_timer WHERE singleton=1")

    def stop(self, end_at=None):
        with self.db:
            self._finish_active(end_at or now())

    def start(self, task_id, goal_id=None, mode="normal", focus_minutes=25, break_minutes=5):
        stamp = now()
        with self.db:
            self._finish_active(stamp)
            self.db.execute("INSERT INTO active_timer VALUES(1,?,?,?,?,?,?,?,0)", (task_id, goal_id, stamp, stamp, mode, focus_minutes, break_minutes))

    def start_break(self):
        active = self.active()
        if not active or active["mode"] != "pomodoro":
            return
        stamp = now()
        with self.db:
            self._finish_active(stamp)
            self.db.execute("INSERT INTO active_timer VALUES(1,?,?,?,?,?,?,?,0)", (active["task_id"], active["goal_id"], stamp, stamp, "break", active["focus_minutes"], active["break_minutes"]))

    def heartbeat(self):
        self.execute("UPDATE active_timer SET heartbeat_at=? WHERE singleton=1", (now(),))

    def alert(self):
        self.execute("UPDATE active_timer SET alerted=1 WHERE singleton=1")

    def checkin(self, day, kind):
        if kind not in ("start_at", "end_at"):
            raise ValueError(kind)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO checkins(day) VALUES(?)", (day,))
            self.db.execute(f"UPDATE checkins SET {kind}=? WHERE day=?", (now(), day))

    def checkin_once(self, day, kind):
        if kind not in ("start_at", "end_at"):
            raise ValueError(kind)
        with self.db:
            if kind == "end_at":
                open_shift = self.one("SELECT day FROM checkins WHERE start_at IS NOT NULL AND end_at IS NULL AND start_at<=? ORDER BY start_at DESC LIMIT 1", (now(),))
                if open_shift:
                    return self.db.execute("UPDATE checkins SET end_at=? WHERE day=? AND end_at IS NULL", (now(), open_shift["day"])).rowcount == 1
            self.db.execute("INSERT OR IGNORE INTO checkins(day) VALUES(?)", (day,))
            return self.db.execute(f"UPDATE checkins SET {kind}=? WHERE day=? AND {kind} IS NULL", (now(), day)).rowcount == 1

    def checkins_on(self, day):
        start = datetime.fromisoformat(day).astimezone()
        end = start + timedelta(days=1)
        return self.rows("SELECT * FROM checkins WHERE (start_at>=? AND start_at<?) OR (end_at>=? AND end_at<?) OR (start_at<? AND (end_at>? OR end_at IS NULL)) ORDER BY start_at", (start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat(), start.isoformat(), end.isoformat()))

    def pomo(self):
        return self.one("SELECT * FROM pomodoro WHERE singleton=1")

    def start_pomodoro(self, focus=25, rest=5, long_rest=15, long_every=4):
        if self.pomo():
            return
        started_other = 0
        if not self.active():
            self.start(self.other_task())
            started_other = 1
        with self.db:
            self.db.execute("INSERT INTO pomodoro(singleton,phase,phase_start,rounds,focus_minutes,break_minutes,long_minutes,long_every,started_other) VALUES(1,'focus',?,0,?,?,?,?,?)", (now(), focus, rest, long_rest, long_every, started_other))

    def ensure_pomo_other(self):
        if self.pomo() and not self.active():
            self.start(self.other_task())
            self.execute("UPDATE pomodoro SET started_other=1 WHERE singleton=1")

    def advance_pomodoro(self):
        state = self.pomo()
        if not state:
            return None
        rounds = state["rounds"]
        if state["phase"] == "focus":
            rounds += 1
            phase = "break"
            message = "专注结束，开始短休息"
        elif state["phase"] == "break" and rounds % max(1, state["long_every"]) == 0:
            phase = "long_break"
            message = "短休结束，开始长休息"
        else:
            phase = "focus"
            message = "休息结束，开始专注"
        with self.db:
            self.db.execute("UPDATE pomodoro SET phase=?,phase_start=?,rounds=? WHERE singleton=1", (phase, now(), rounds))
        return message

    def stop_pomodoro(self):
        state = self.pomo()
        if not state:
            return
        active = self.active()
        with self.db:
            self.db.execute("DELETE FROM pomodoro WHERE singleton=1")
        if state["started_other"] and active and active["task_id"] == self.other_task():
            self.stop()

    def preference(self, key, default=None):
        row = self.one("SELECT value FROM preferences WHERE key=?", (key,))
        return row["value"] if row else default

    def set_preference(self, key, value):
        self.execute("INSERT INTO preferences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def set_checkin(self, day, kind, value):
        if kind not in ("start_at", "end_at"):
            raise ValueError(kind)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO checkins(day) VALUES(?)", (day,))
            self.db.execute(f"UPDATE checkins SET {kind}=? WHERE day=?", (value, day))

    def entries_between(self, start, end):
        return self.rows("SELECT e.*,t.name task_name,t.color color FROM entries e JOIN tasks t ON t.id=e.task_id WHERE e.start_at<? AND e.end_at>? ORDER BY e.start_at", (end, start))

    def breaks_between(self, start, end):
        return self.rows("SELECT b.*,t.name task_name FROM breaks b LEFT JOIN tasks t ON t.id=b.task_id WHERE b.start_at<? AND b.end_at>? ORDER BY b.start_at", (end, start))

    def add_entry(self, task_id, start_at, end_at, mode="manual"):
        if parse(end_at) <= parse(start_at):
            raise ValueError("结束时间必须晚于开始时间")
        return self.execute("INSERT INTO entries(task_id,start_at,end_at,mode,created_at) VALUES(?,?,?,?,?)", (task_id, start_at, end_at, mode, now()))

    def update_entry(self, entry_id, start_at, end_at):
        if parse(end_at) <= parse(start_at):
            raise ValueError("结束时间必须晚于开始时间")
        self.execute("UPDATE entries SET start_at=?,end_at=? WHERE id=?", (start_at, end_at, entry_id))

    def totals(self, start, end):
        a, b = parse(start), parse(end)
        sums = {}
        for row in self.entries_between(start, end):
            s, e = max(parse(row["start_at"]), a), min(parse(row["end_at"]), b)
            if e > s:
                key = (row["task_id"], row["task_name"])
                sums[key] = sums.get(key, 0) + int((e - s).total_seconds())
        active = self.active()
        if active and active["mode"] != "break" and active["task_id"]:
            s, e = max(parse(active["start_at"]), a), min(parse(now()), b)
            if e > s:
                key = (active["task_id"], active["task_name"])
                sums[key] = sums.get(key, 0) + int((e - s).total_seconds())
        return sums

    def backup(self, destination):
        target = sqlite3.connect(destination)
        try:
            with target:
                self.db.backup(target)
        finally:
            target.close()

    def export_csv(self, destination):
        with open(destination, "w", newline="", encoding="utf-8-sig") as out:
            writer = csv.writer(out)
            writer.writerow(["任务", "开始", "结束", "秒数", "方式"])
            for e in self.rows("SELECT e.*,t.name task_name FROM entries e JOIN tasks t ON t.id=e.task_id ORDER BY e.start_at"):
                writer.writerow([e["task_name"], e["start_at"], e["end_at"], seconds_between(e["start_at"], e["end_at"]), e["mode"]])

    def close(self):
        self.db.close()
