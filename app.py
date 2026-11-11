#!/usr/bin/env python3
"""
Workout Tracker — a single-file Python + SQLite web app.

Modelled on the SiteLead CRM pattern: one production entrypoint (app.py),
SQLite for storage, no third-party dependencies (Python standard library
only), Railway-ready.

Run locally:
    python3 app.py                # http://localhost:8000

Environment variables:
    PORT        Port to bind (default 8000). Railway sets this.
    WORKOUT_DB  Full path to the SQLite file (wins if set).
    DATA_DIR    Directory for the SQLite file (default: ./data).
                On Railway, point this at the mounted volume, e.g. /data.

Features:
    * Movements with a current max (1RM) and a full max history.
    * Percentage tables (50-100% in 5% steps, rounded to nearest 5 lb).
    * One-tap weekly generator: 4 training days built from your maxes,
      driven by a TRAINING STYLE (heavy strength vs endurance) and a
      simple 4-week percentage wave.
    * Set logging with prescribed vs actual weight/reps, persisted
      server-side on every change (tab switches never lose a workout).
    * Progress: max history per movement + completed workout history.
    * Reminders (gym / prayer / water) stored as settings and surfaced
      on the Today tab. See README for the honest iOS push limitation.
    * PWA: manifest + icons + service worker, so Safari's
      "Add to Home Screen" installs it like a native app.
"""

import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# --------------------------------------------------------------------------
# Database setup
# --------------------------------------------------------------------------

def db_path():
    if os.environ.get("WORKOUT_DB"):
        return os.environ["WORKOUT_DB"]
    data_dir = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
    return os.path.join(data_dir, "workout.db")


DB_PATH = db_path()
_db_lock = threading.Lock()


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS movements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    current_max REAL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS max_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_id INTEGER NOT NULL REFERENCES movements(id) ON DELETE CASCADE,
    max_value   REAL NOT NULL,
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS weeks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,               -- Monday, YYYY-MM-DD
    style      TEXT NOT NULL DEFAULT 'strength',
    wave_index INTEGER NOT NULL DEFAULT 0,  -- 0..3 position in the 4-week wave
    wave_label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS workouts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    week_id           INTEGER REFERENCES weeks(id) ON DELETE CASCADE,
    date              TEXT NOT NULL,        -- YYYY-MM-DD
    title             TEXT NOT NULL,
    focus_movement_id INTEGER REFERENCES movements(id) ON DELETE SET NULL,
    status            TEXT NOT NULL DEFAULT 'planned',  -- planned|in_progress|completed
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS workout_exercises (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id  INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    movement_id INTEGER REFERENCES movements(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    is_main     INTEGER NOT NULL DEFAULT 0,
    is_circuit  INTEGER NOT NULL DEFAULT 0,
    rest_seconds INTEGER,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise_id       INTEGER NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
    set_number        INTEGER NOT NULL,
    pct               REAL,                 -- % of max this set was prescribed at
    prescribed_weight REAL,
    prescribed_reps   INTEGER,
    actual_weight     REAL,
    actual_reps       INTEGER,
    done              INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS water_log (
    date    TEXT PRIMARY KEY,               -- YYYY-MM-DD
    glasses INTEGER NOT NULL DEFAULT 0
);
"""


def init_db():
    with _db_lock, get_db() as conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def round5(weight):
    """Round a weight to the nearest 5 lb (gym-plate friendly)."""
    if weight is None:
        return None
    return int(round(weight / 5.0) * 5)


def pct_table(max_value):
    """50-100% in 5% steps, rounded to nearest 5 lb."""
    if not max_value:
        return []
    return [
        {"pct": p, "weight": round5(max_value * p / 100.0)}
        for p in range(50, 101, 5)
    ]


def row_to_dict(row):
    return dict(row) if row is not None else None


DEFAULT_SETTINGS = {
    "theme": "dark",
    "training_style": "strength",
    "reminders": {
        "gym_enabled": True,
        "gym_time": "16:00",
        "prayer_enabled": True,
        "prayer_time": "07:00",
        "water_enabled": True,
        "water_mode": "interval",      # interval | fixed
        "water_start": "08:00",
        "water_end": "21:00",
        "water_interval_hours": 2,
        "water_times": ["09:00", "12:00", "15:00", "18:00"],
        "water_goal": 8,               # glasses per day
    },
}


def get_settings(conn):
    settings = dict(DEFAULT_SETTINGS)
    settings["reminders"] = dict(DEFAULT_SETTINGS["reminders"])
    for row in conn.execute("SELECT key, value FROM settings"):
        try:
            settings[row["key"]] = json.loads(row["value"])
        except (ValueError, TypeError):
            settings[row["key"]] = row["value"]
    return settings


def save_settings(conn, updates):
    for key, value in updates.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


# --------------------------------------------------------------------------
# Training styles + the weekly generator
# --------------------------------------------------------------------------
# The scheme stays deliberately simple and explainable in one line:
#   Strength:  "Main sets wave 75-92% of max for 1-5 reps; rest 3-5 min."
#   Endurance: "Main sets wave 50-70% of max for 12-20 reps, accessories
#               run as a circuit; rest about 60 seconds."
# Each style has a 4-week wave (Build / Build+ / Peak-or-Push / Deload).

STYLES = {
    "strength": {
        "label": "Heavy Strength",
        "summary": "Main sets at 75–92% of max, 1–5 reps, rest 3–5 minutes.",
        "main_rest_seconds": 240,
        "accessory_sets": 3,
        "accessory_reps": 8,
        "accessory_circuit": False,
        "waves": [
            {"label": "Build",  "sets": [(0.75, 5), (0.80, 4), (0.82, 3), (0.80, 4)]},
            {"label": "Build+", "sets": [(0.80, 4), (0.85, 3), (0.87, 2), (0.85, 3)]},
            {"label": "Peak",   "sets": [(0.82, 3), (0.87, 2), (0.90, 1), (0.92, 1)]},
            {"label": "Deload", "sets": [(0.65, 5), (0.70, 5), (0.75, 3)]},
        ],
    },
    "endurance": {
        "label": "Endurance",
        "summary": "Main sets at 50–70% of max, 12–20 reps, accessories as a circuit, rest ~60 seconds.",
        "main_rest_seconds": 60,
        "accessory_sets": 3,      # circuit rounds
        "accessory_reps": 15,
        "accessory_circuit": True,
        "waves": [
            {"label": "Build",  "sets": [(0.50, 15), (0.55, 15), (0.55, 12), (0.50, 15)]},
            {"label": "Build+", "sets": [(0.55, 15), (0.60, 12), (0.60, 12), (0.55, 15), (0.50, 20)]},
            {"label": "Push",   "sets": [(0.65, 12), (0.65, 12), (0.60, 15), (0.60, 15), (0.55, 20)]},
            {"label": "Deload", "sets": [(0.50, 12), (0.50, 12), (0.50, 12)]},
        ],
    },
}

# The split Harry trains: Push / Pull / Legs / Arms+Shoulders / Push+Pull,
# Monday-Friday. (day offset from Monday, focus label, movement-name
# keywords for the day's main lift, accessories)
DAY_PLAN = [
    (0, "Push Day",           ("bench",),
     ["Incline press", "Lateral raises", "Triceps pushdown"]),
    (1, "Pull Day",           ("deadlift", "row"),
     ["Lat pulldown", "Seated cable row", "Biceps curl"]),
    (2, "Legs Day",           ("squat",),
     ["Leg press", "Romanian deadlift", "Calf raises"]),
    (3, "Arms/Shoulders Day", ("overhead", "shoulder", "ohp"),
     ["Lateral raises", "Biceps curl", "Triceps extension"]),
    (4, "Push/Pull Day",      ("row", "bench"),
     ["Chest fly", "Lat pulldown", "Face pull"]),
]


def find_movement(conn, keywords):
    rows = conn.execute("SELECT * FROM movements ORDER BY id").fetchall()
    for kw in keywords:
        for row in rows:
            if kw in row["name"].lower():
                return row
    return None


def generate_week(conn, style_key, week_start=None):
    """Create a week of 5 workouts (Push / Pull / Legs / Arms+Shoulders /
    Push+Pull) from current maxes. Replaces any existing week that starts
    on the same Monday."""
    style = STYLES[style_key]
    if week_start is None:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
    else:
        monday = datetime.strptime(week_start, "%Y-%m-%d").date()
        monday = monday - timedelta(days=monday.weekday())

    # Wave position advances from the most recently generated week.
    last = conn.execute(
        "SELECT wave_index FROM weeks ORDER BY id DESC LIMIT 1").fetchone()
    wave_index = (last["wave_index"] + 1) % 4 if last else 0
    wave = style["waves"][wave_index]

    # Regenerating a week replaces its unfinished workouts, but completed
    # workouts are history: they stay attached to their original week row
    # so Progress never loses them.
    for old in conn.execute(
            "SELECT id FROM weeks WHERE week_start = ?", (monday.isoformat(),)).fetchall():
        conn.execute(
            "DELETE FROM workouts WHERE week_id = ? AND status != 'completed'",
            (old["id"],))
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM workouts WHERE week_id = ?",
            (old["id"],)).fetchone()["c"]
        if remaining == 0:
            conn.execute("DELETE FROM weeks WHERE id = ?", (old["id"],))
    cur = conn.execute(
        "INSERT INTO weeks (week_start, style, wave_index, wave_label) VALUES (?, ?, ?, ?)",
        (monday.isoformat(), style_key, wave_index, wave["label"]),
    )
    week_id = cur.lastrowid

    for offset, title, keywords, accessories in DAY_PLAN:
        day = monday + timedelta(days=offset)
        movement = find_movement(conn, keywords)
        cur = conn.execute(
            "INSERT INTO workouts (week_id, date, title, focus_movement_id) VALUES (?, ?, ?, ?)",
            (week_id, day.isoformat(), title, movement["id"] if movement else None),
        )
        workout_id = cur.lastrowid

        # Main lift
        focus_name = title.replace(" Day", "")
        if movement and movement["current_max"]:
            cur = conn.execute(
                "INSERT INTO workout_exercises (workout_id, movement_id, name, is_main, rest_seconds, sort_order) "
                "VALUES (?, ?, ?, 1, ?, 0)",
                (workout_id, movement["id"], movement["name"], style["main_rest_seconds"]),
            )
            ex_id = cur.lastrowid
            for i, (pct, reps) in enumerate(wave["sets"], start=1):
                weight = round5(movement["current_max"] * pct)
                conn.execute(
                    "INSERT INTO sets (exercise_id, set_number, pct, prescribed_weight, prescribed_reps) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ex_id, i, int(pct * 100), weight, reps),
                )
        else:
            # No matching movement/max yet — leave an editable placeholder.
            conn.execute(
                "INSERT INTO workout_exercises (workout_id, name, is_main, rest_seconds, sort_order) "
                "VALUES (?, ?, 1, ?, 0)",
                (workout_id, f"{focus_name} (add a max in Movements)", style["main_rest_seconds"]),
            )

        # Accessory slots (editable names; circuits in endurance mode)
        for j, acc_name in enumerate(accessories, start=1):
            cur = conn.execute(
                "INSERT INTO workout_exercises (workout_id, name, is_circuit, rest_seconds, sort_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (workout_id, acc_name, 1 if style["accessory_circuit"] else 0,
                 style["main_rest_seconds"], j),
            )
            ex_id = cur.lastrowid
            for i in range(1, style["accessory_sets"] + 1):
                conn.execute(
                    "INSERT INTO sets (exercise_id, set_number, prescribed_reps) VALUES (?, ?, ?)",
                    (ex_id, i, style["accessory_reps"]),
                )
    return week_id


# --------------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------------

def movement_json(conn, row):
    data = row_to_dict(row)
    data["pct_table"] = pct_table(row["current_max"])
    data["history"] = [
        row_to_dict(r)
        for r in conn.execute(
            "SELECT * FROM max_history WHERE movement_id = ? ORDER BY created_at DESC, id DESC",
            (row["id"],),
        )
    ]
    return data


def workout_json(conn, row):
    data = row_to_dict(row)
    exercises = []
    for ex in conn.execute(
        "SELECT * FROM workout_exercises WHERE workout_id = ? ORDER BY sort_order, id",
        (row["id"],),
    ):
        ex_data = row_to_dict(ex)
        ex_data["sets"] = [
            row_to_dict(s)
            for s in conn.execute(
                "SELECT * FROM sets WHERE exercise_id = ? ORDER BY set_number, id",
                (ex["id"],),
            )
        ]
        exercises.append(ex_data)
    data["exercises"] = exercises
    return data


def week_json(conn, week_row):
    data = row_to_dict(week_row)
    style = STYLES.get(week_row["style"], STYLES["strength"])
    data["style_label"] = style["label"]
    data["style_summary"] = style["summary"]
    data["workouts"] = [
        workout_json(conn, w)
        for w in conn.execute(
            "SELECT * FROM workouts WHERE week_id = ? ORDER BY date", (week_row["id"],)
        )
    ]
    return data


def reminder_status(conn, settings):
    """Compute what is due today, honestly from stored settings."""
    rem = settings["reminders"]
    now = datetime.now()
    today_str = date.today().isoformat()
    items = []

    # Gym: due on training days of the latest generated week.
    if rem.get("gym_enabled"):
        row = conn.execute(
            "SELECT date FROM workouts WHERE date = ? ORDER BY id LIMIT 1", (today_str,)
        ).fetchone()
        items.append({
            "type": "gym",
            "label": "Get to the gym",
            "time": rem.get("gym_time", "16:00"),
            "today": row is not None,
            "due": row is not None and now.strftime("%H:%M") >= rem.get("gym_time", "16:00"),
            "detail": "Training day" if row else "Rest day — no workout scheduled today",
        })

    # Prayer: daily.
    if rem.get("prayer_enabled"):
        t = rem.get("prayer_time", "07:00")
        items.append({
            "type": "prayer",
            "label": "Pray",
            "time": t,
            "today": True,
            "due": now.strftime("%H:%M") >= t,
            "detail": f"Daily at {t}",
        })

    # Water: scheduled times for today + progress toward the goal.
    if rem.get("water_enabled"):
        times = []
        if rem.get("water_mode") == "fixed":
            times = sorted(rem.get("water_times", []))
        else:
            try:
                start = datetime.strptime(rem.get("water_start", "08:00"), "%H:%M")
                end = datetime.strptime(rem.get("water_end", "21:00"), "%H:%M")
                step = max(1, int(rem.get("water_interval_hours", 2)))
                t = start
                while t <= end:
                    times.append(t.strftime("%H:%M"))
                    t += timedelta(hours=step)
            except ValueError:
                times = []
        water_row = conn.execute(
            "SELECT glasses FROM water_log WHERE date = ?", (today_str,)
        ).fetchone()
        glasses = water_row["glasses"] if water_row else 0
        due_count = sum(1 for t in times if t <= now.strftime("%H:%M"))
        items.append({
            "type": "water",
            "label": "Drink water",
            "time": times[0] if times else "",
            "today": True,
            "due": due_count > glasses,
            "detail": f"{glasses}/{rem.get('water_goal', 8)} glasses — next "
                      + next((t for t in times if t > now.strftime("%H:%M")), "done for today"),
            "times": times,
            "glasses": glasses,
            "goal": rem.get("water_goal", 8),
        })
    return items


# --------------------------------------------------------------------------
# HTTP handler / JSON API
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "WorkoutApp/1.0"

    # -- plumbing ------------------------------------------------------
    def log_message(self, fmt, *args):  # keep logs concise
        print(f"{self.address_string()} {fmt % args}")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def _send_static(self, rel_path):
        path = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
            self.send_error(404)
            return
        types = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", types.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        # Shell files: revalidate so app updates are never stuck in cache.
        self.send_header("Cache-Control", "no-cache" if ext != ".png" else "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    # -- routing -------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self._send_static("index.html")
        if path.startswith("/static/"):
            return self._send_static(path[len("/static/"):])
        if path in ("/manifest.json", "/sw.js"):
            return self._send_static(path.lstrip("/"))
        if path.startswith("/api/"):
            return self._api_get(path, parse_qs(parsed.query))
        self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path.startswith("/api/"):
            return self._api_write("POST", urlparse(self.path).path)
        self.send_error(404)

    def do_PUT(self):
        if urlparse(self.path).path.startswith("/api/"):
            return self._api_write("PUT", urlparse(self.path).path)
        self.send_error(404)

    def do_DELETE(self):
        if urlparse(self.path).path.startswith("/api/"):
            return self._api_write("DELETE", urlparse(self.path).path)
        self.send_error(404)

    # -- GET endpoints ---------------------------------------------------
    def _api_get(self, path, query):
        with _db_lock, get_db() as conn:
            if path == "/api/movements":
                rows = conn.execute("SELECT * FROM movements ORDER BY id").fetchall()
                return self._send_json({"movements": [movement_json(conn, r) for r in rows]})

            if path == "/api/week/current":
                row = conn.execute("SELECT * FROM weeks ORDER BY id DESC LIMIT 1").fetchone()
                if not row:
                    return self._send_json({"week": None})
                return self._send_json({"week": week_json(conn, row)})

            if path.startswith("/api/workouts/"):
                wid = path.split("/")[-1]
                row = conn.execute("SELECT * FROM workouts WHERE id = ?", (wid,)).fetchone()
                if not row:
                    return self._send_json({"error": "workout not found"}, 404)
                return self._send_json({"workout": workout_json(conn, row)})

            if path == "/api/today":
                today_str = date.today().isoformat()
                row = conn.execute(
                    "SELECT * FROM workouts WHERE date = ? ORDER BY id LIMIT 1", (today_str,)
                ).fetchone()
                settings = get_settings(conn)
                water = conn.execute(
                    "SELECT glasses FROM water_log WHERE date = ?", (today_str,)
                ).fetchone()
                return self._send_json({
                    "date": today_str,
                    "workout": workout_json(conn, row) if row else None,
                    "reminders": reminder_status(conn, settings),
                    "water_glasses": water["glasses"] if water else 0,
                })

            if path == "/api/progress":
                movements = []
                for r in conn.execute("SELECT * FROM movements ORDER BY id"):
                    movements.append({
                        "id": r["id"],
                        "name": r["name"],
                        "current_max": r["current_max"],
                        "history": [row_to_dict(h) for h in conn.execute(
                            "SELECT * FROM max_history WHERE movement_id = ? ORDER BY created_at, id",
                            (r["id"],))],
                    })
                completed = [
                    row_to_dict(w) for w in conn.execute(
                        "SELECT * FROM workouts WHERE status = 'completed' ORDER BY date DESC, id DESC")
                ]
                return self._send_json({"movements": movements, "completed_workouts": completed})

            if path == "/api/settings":
                return self._send_json({"settings": get_settings(conn)})

            if path == "/api/styles":
                return self._send_json({"styles": {
                    k: {"label": v["label"], "summary": v["summary"]} for k, v in STYLES.items()
                }})

        return self._send_json({"error": "not found"}, 404)

    # -- write endpoints -------------------------------------------------
    def _api_write(self, method, path):
        data = self._read_json() if method in ("POST", "PUT") else {}
        parts = [p for p in path.split("/") if p]  # e.g. ['api','movements','3','max']

        with _db_lock, get_db() as conn:
            # Movements -------------------------------------------------
            if parts == ["api", "movements"] and method == "POST":
                name = (data.get("name") or "").strip()
                if not name:
                    return self._send_json({"error": "name required"}, 400)
                cur = conn.execute("INSERT INTO movements (name, current_max) VALUES (?, ?)",
                                   (name, data.get("max")))
                mid = cur.lastrowid
                if data.get("max"):
                    conn.execute("INSERT INTO max_history (movement_id, max_value, note) VALUES (?, ?, ?)",
                                 (mid, data["max"], "Starting max"))
                conn.commit()
                row = conn.execute("SELECT * FROM movements WHERE id = ?", (mid,)).fetchone()
                return self._send_json({"movement": movement_json(conn, row)}, 201)

            if len(parts) == 3 and parts[:2] == ["api", "movements"]:
                mid = parts[2]
                if method == "PUT":
                    name = (data.get("name") or "").strip()
                    if name:
                        conn.execute("UPDATE movements SET name = ? WHERE id = ?", (name, mid))
                    conn.commit()
                    row = conn.execute("SELECT * FROM movements WHERE id = ?", (mid,)).fetchone()
                    return self._send_json({"movement": movement_json(conn, row)})
                if method == "DELETE":
                    conn.execute("DELETE FROM movements WHERE id = ?", (mid,))
                    conn.commit()
                    return self._send_json({"ok": True})

            if len(parts) == 4 and parts[:2] == ["api", "movements"] and parts[3] == "max" and method == "POST":
                mid = parts[2]
                new_max = data.get("max")
                if new_max is None:
                    return self._send_json({"error": "max required"}, 400)
                conn.execute("UPDATE movements SET current_max = ? WHERE id = ?", (new_max, mid))
                conn.execute("INSERT INTO max_history (movement_id, max_value, note) VALUES (?, ?, ?)",
                             (mid, new_max, data.get("note", "")))
                conn.commit()
                row = conn.execute("SELECT * FROM movements WHERE id = ?", (mid,)).fetchone()
                return self._send_json({"movement": movement_json(conn, row)})

            # Weekly generator ------------------------------------------
            if parts == ["api", "weeks", "generate"] and method == "POST":
                settings = get_settings(conn)
                style_key = data.get("style") or settings.get("training_style", "strength")
                if style_key not in STYLES:
                    return self._send_json({"error": "unknown style"}, 400)
                if data.get("style"):
                    save_settings(conn, {"training_style": style_key})
                week_id = generate_week(conn, style_key, data.get("week_start"))
                conn.commit()
                row = conn.execute("SELECT * FROM weeks WHERE id = ?", (week_id,)).fetchone()
                return self._send_json({"week": week_json(conn, row)}, 201)

            # Workouts ----------------------------------------------------
            if len(parts) == 3 and parts[:2] == ["api", "workouts"] and method == "PUT":
                wid = parts[2]
                if "title" in data:
                    conn.execute("UPDATE workouts SET title = ? WHERE id = ?", (data["title"], wid))
                if "date" in data:
                    conn.execute("UPDATE workouts SET date = ? WHERE id = ?", (data["date"], wid))
                if "status" in data and data["status"] in ("planned", "in_progress", "completed"):
                    conn.execute("UPDATE workouts SET status = ? WHERE id = ?", (data["status"], wid))
                conn.commit()
                row = conn.execute("SELECT * FROM workouts WHERE id = ?", (wid,)).fetchone()
                return self._send_json({"workout": workout_json(conn, row)})

            if len(parts) == 4 and parts[:2] == ["api", "workouts"] and parts[3] == "complete" and method == "POST":
                wid = parts[2]
                conn.execute("UPDATE workouts SET status = 'completed' WHERE id = ?", (wid,))
                conn.commit()
                row = conn.execute("SELECT * FROM workouts WHERE id = ?", (wid,)).fetchone()
                return self._send_json({"workout": workout_json(conn, row)})

            if len(parts) == 4 and parts[:2] == ["api", "workouts"] and parts[3] == "exercises" and method == "POST":
                wid = parts[2]
                name = (data.get("name") or "").strip()
                if not name:
                    return self._send_json({"error": "name required"}, 400)
                order = conn.execute(
                    "SELECT COALESCE(MAX(sort_order), 0) + 1 AS o FROM workout_exercises WHERE workout_id = ?",
                    (wid,)).fetchone()["o"]
                cur = conn.execute(
                    "INSERT INTO workout_exercises (workout_id, movement_id, name, sort_order) VALUES (?, ?, ?, ?)",
                    (wid, data.get("movement_id"), name, order))
                conn.commit()
                return self._send_json({"exercise_id": cur.lastrowid}, 201)

            # Exercises ---------------------------------------------------
            if len(parts) == 3 and parts[:2] == ["api", "exercises"]:
                exid = parts[2]
                if method == "PUT":
                    if "name" in data:
                        conn.execute("UPDATE workout_exercises SET name = ? WHERE id = ?",
                                     (data["name"], exid))
                    conn.commit()
                    return self._send_json({"ok": True})
                if method == "DELETE":
                    conn.execute("DELETE FROM workout_exercises WHERE id = ?", (exid,))
                    conn.commit()
                    return self._send_json({"ok": True})

            if len(parts) == 4 and parts[:2] == ["api", "exercises"] and parts[3] == "sets" and method == "POST":
                exid = parts[2]
                nxt = conn.execute(
                    "SELECT COALESCE(MAX(set_number), 0) + 1 AS n FROM sets WHERE exercise_id = ?",
                    (exid,)).fetchone()["n"]
                cur = conn.execute(
                    "INSERT INTO sets (exercise_id, set_number, prescribed_weight, prescribed_reps) VALUES (?, ?, ?, ?)",
                    (exid, nxt, data.get("weight"), data.get("reps")))
                conn.commit()
                return self._send_json({"set_id": cur.lastrowid}, 201)

            # Sets ----------------------------------------------------------
            if len(parts) == 3 and parts[:2] == ["api", "sets"]:
                sid = parts[2]
                if method == "PUT":
                    allowed = ("prescribed_weight", "prescribed_reps",
                               "actual_weight", "actual_reps", "done")
                    for field in allowed:
                        if field in data:
                            conn.execute(f"UPDATE sets SET {field} = ? WHERE id = ?",
                                         (data[field], sid))
                    conn.commit()
                    row = conn.execute("SELECT * FROM sets WHERE id = ?", (sid,)).fetchone()
                    return self._send_json({"set": row_to_dict(row)})
                if method == "DELETE":
                    conn.execute("DELETE FROM sets WHERE id = ?", (sid,))
                    conn.commit()
                    return self._send_json({"ok": True})

            # Settings ------------------------------------------------------
            if parts == ["api", "settings"] and method == "PUT":
                updates = {}
                if "theme" in data and data["theme"] in ("dark", "light"):
                    updates["theme"] = data["theme"]
                if "training_style" in data and data["training_style"] in STYLES:
                    updates["training_style"] = data["training_style"]
                if "reminders" in data and isinstance(data["reminders"], dict):
                    current = get_settings(conn)["reminders"]
                    current.update(data["reminders"])
                    updates["reminders"] = current
                save_settings(conn, updates)
                conn.commit()
                return self._send_json({"settings": get_settings(conn)})

            # Water log -----------------------------------------------------
            if parts == ["api", "water"] and method == "POST":
                today_str = date.today().isoformat()
                delta = int(data.get("delta", 1))
                conn.execute(
                    "INSERT INTO water_log (date, glasses) VALUES (?, MAX(0, ?)) "
                    "ON CONFLICT(date) DO UPDATE SET glasses = MAX(0, glasses + ?)",
                    (today_str, delta, delta))
                conn.commit()
                row = conn.execute("SELECT glasses FROM water_log WHERE date = ?",
                                   (today_str,)).fetchone()
                return self._send_json({"date": today_str, "glasses": row["glasses"]})

        return self._send_json({"error": "not found"}, 404)


def main():
    init_db()
    try:  # icons are generated, not committed (text-only git push)
        import icons as _icons
        _icons.ensure_icons(os.path.join(STATIC_DIR, "icons"))
    except Exception as exc:
        print(f"icon generation skipped: {exc}")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Workout Tracker listening on 0.0.0.0:{port} (db: {DB_PATH})")
    server.serve_forever()


if __name__ == "__main__":
    main()
