# Grounded Strength — Workout Tracker

A personal, percentage-based workout tracker. Built for one user, on an
iPhone, mid-workout: big numbers, big tap targets, no accounts, no fluff.

**The core mechanic:** you enter your max (1RM) for each big lift. The app
turns those into percentage tables and generates your weekly workouts from
them — every main-lift set is prescribed with an actual weight **and** the
percentage it came from (e.g. `185 lb · 75%`), rounded to the nearest 5 lb.
Update a max and the next generated week uses the new numbers.

## Training styles

Pick a style in Settings (or when generating). The generated week is
labelled with the active style.

| Style | Scheme (one line) |
|---|---|
| **Heavy Strength** | Main sets at 75–92% of max, 1–5 reps, rest 3–5 minutes. |
| **Endurance** | Main sets at 50–70% of max, 12–20 reps, accessories run as a circuit, rest ~60 seconds. |

Each style runs a simple 4-week wave (Build → Build+ → Peak/Push → Deload)
so percentages move week to week without you doing math.

Accessories are prescribed by name and reps only (no weight), unless you
edit them — they're slots, not max-based lifts.

## Features

- **Movements & maxes** — CRUD for lifts, current max, full max history,
  and a 50–100% (5% steps) percentage table per movement.
- **Weekly generator** — one tap builds 4 days (Squat / Bench / Deadlift /
  Overhead) from your maxes; regenerate anytime, edit any day, exercise,
  or set afterwards.
- **Set logging** — sets prefilled from the prescription; steppers adjust
  actual weight/reps; check sets off; everything saves to SQLite the
  moment you tap, so switching tabs never loses an in-progress workout.
- **Progress** — max history per movement (list + sparkline) and a history
  of completed workouts. Update a max from a workout or the Maxes tab.
- **Dashboard tabs** — Today / Week / Maxes / Progress / Settings on a
  bottom tab bar, one-thumb friendly.
- **Settings** — light/dark mode (dark by default, saved), training style,
  and reminders.
- **Reminders** — gym (on your training days, at your time), a daily
  prayer reminder, and water reminders (every N hours or fixed times)
  with a daily glass counter. What's due today shows on the Today tab.
- **PWA** — installable via Safari's *Add to Home Screen* with its own
  icon; service worker caches the app shell for fast opens.

### An honest note on notifications

Web apps on iOS Safari **cannot reliably push notifications in the
background** — Apple doesn't give installed web apps the same push
treatment as native apps, and `Notification` support from a home-screen
web app is limited. This app does what's honestly possible: reminder
settings are stored, due items are surfaced on the Today tab, and while
the app is open it checks every minute and fires a notification for
anything that just became due (after you grant permission in Settings).
It does **not** pretend to push while closed. If you need guaranteed
phone buzzes at set times, mirror the same times in Apple Reminders.

## Run it locally

No dependencies — Python 3 standard library only.

```bash
python3 app.py            # http://localhost:8000
```

Data lives in `data/workout.db` by default (gitignored). Override with:

```bash
WORKOUT_DB=/path/to/workout.db python3 app.py
# or
DATA_DIR=/path/to/dir PORT=8000 python3 app.py
```

## Deploy on Railway (same pattern as SiteLead CRM)

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, pick this repo.
   The `Procfile` start command is `python3 app.py`; Railway's `PORT` is
   read automatically.
3. Add a **Volume** mounted at `/data` so the SQLite database survives
   deploys, and set the env var `DATA_DIR=/data` (or
   `WORKOUT_DB=/data/workout.db`).
4. Open the generated Railway URL on the iPhone, then
   **Share → Add to Home Screen** to install it.

## Development

- `app.py` — the whole backend (server, SQLite schema, generator, API).
- `static/` — the frontend (`index.html`, `app.js`, `style.css`,
  `manifest.json`, `sw.js`, `icons/`).
- `icons.py` — generates the PWA icons (stdlib-only). Icons are not
  committed; `app.py` generates them at startup when missing.
  `tools/generate_icons.py` forces a regeneration.
- Seed data: **none.** First run is an empty state that walks you through
  adding your movements and maxes.
