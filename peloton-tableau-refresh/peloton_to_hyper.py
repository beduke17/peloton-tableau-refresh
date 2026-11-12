#!/usr/bin/env python3
"""
peloton_to_hyper.py
===================
Fetches Peloton data and writes a multi-table Tableau Hyper extract
ready to open in Tableau Desktop.

Tables
------
  workouts              — your personal ride history           (Peloton login required)
  earned_achievements   — badges you have personally earned   (Peloton login required)
  instructors           — all instructor profiles             (public)
  rides                 — full class catalog                  (public)
  achievement_templates — badge definitions                   (public)

Relationships (for Tableau data model)
---------------------------------------
  workouts.instructor_id  → instructors.instructor_id
  workouts.ride_id        → rides.ride_id
  earned_achievements.template_id → achievement_templates.template_id

Usage
-----
  pip install -r requirements.txt
  cp .env.example .env        # fill in credentials
  source .env                 # or set env vars manually
  python peloton_to_hyper.py

  Output: peloton_data.hyper  (open in Tableau Desktop)

Environment variables
---------------------
  PELOTON_TOKEN   Bearer token from browser (see below)
  PELOTON_USER_ID Your Peloton user ID (see below)
  HYPER_FILE      Output path  (default: peloton_data.hyper)
  MAX_WORKOUTS    Cap fetched workouts, 0 = all  (default: 0)
  MAX_RIDES       Cap fetched rides              (default: 1000)

How to get your token (see TOKEN_HELP below, or run: python peloton_to_hyper.py --token-help)
---------------------------------------------------------------------------------------------
  Short version: Chrome → members.onepeloton.com → DevTools → Network → Fetch/XHR
  → reload → click any api.onepeloton.com request → Request Headers → copy the
  value after "authorization: Bearer ".

  PELOTON_USER_ID is optional — the script decodes it out of the token for you.

  Tokens expire after ~48 hours. Grab a fresh one when the script says 401.
"""

import os
import sys
import time
import json
import base64
import getpass
from pathlib import Path
from typing import Optional

import httpx
from tableauhyperapi import (
    HyperProcess,
    Telemetry,
    Connection,
    CreateMode,
    TableDefinition,
    TableName,
    SqlType,
    Inserter,
)

# ── Config ────────────────────────────────────────────────────────────────────

PELOTON_BASE = "https://api.onepeloton.com"
PAGE_SIZE    = 100

TOKEN_HELP = """
╭──────────────────────────────────────────────────────────────────────────────╮
│  HOW TO GRAB YOUR PELOTON BEARER TOKEN FROM CHROME          (about a minute)  │
╰──────────────────────────────────────────────────────────────────────────────╯

  1.  In Chrome, go to  https://members.onepeloton.com  and log in.

  2.  Open DevTools:   Cmd+Option+I   (Mac)      F12   (Windows/Linux)
      …or right-click the page → Inspect.

  3.  Click the  Network  tab along the top of DevTools.

  4.  Click the  Fetch/XHR  filter button, and type   api   in the
      "Filter" box to the left of it. This hides images, CSS and fonts.

  5.  Reload the page:   Cmd+R  /  F5
      A list of requests to api.onepeloton.com appears.

  6.  Click any one of them — /api/me is a good choice.

  7.  In the pane that opens, stay on the  Headers  tab and scroll down to
      ▼ Request Headers.  Find the line starting:

            authorization: Bearer eyJhbGciOi....

      (If DevTools shows "Provisional headers are shown", click a different
       request — that one was served from cache.)

  8.  Copy EVERYTHING AFTER  "Bearer "  — the token itself, starting  eyJ.
      It is long (several hundred characters). Do not include the word Bearer.

  9.  Paste it at the prompt below. Your typing is hidden on purpose.

  ── Your user ID ────────────────────────────────────────────────────────────
  You don't need it. This script decodes it out of the token automatically.
  If that ever fails, click the /api/me request from step 6, open the
  Response tab, and copy the value of the  "id"  field.

  ── Keeping it safe ─────────────────────────────────────────────────────────
  This token is a live credential — it can read your Peloton account until it
  expires (~48 hours). Prefer passing it as an environment variable:

        export PELOTON_TOKEN='eyJ...'

  Don't paste it into this file, and don't commit it. If you think it leaked,
  log out of Peloton everywhere to invalidate it.

  ── If there is no "authorization" header ───────────────────────────────────
  Peloton sometimes authenticates the web app with a session cookie instead.
  Right-click the request → Copy → Copy as cURL, and check what it sends. This
  script only speaks Bearer tokens, so a cookie-only session needs a different
  approach.
"""


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def _prompt(label: str, secret: bool = False) -> str:
    if secret:
        return getpass.getpass(f"  {label}: ")
    return input(f"  {label}: ").strip()

def _need(key: str, label: str, secret: bool = False, help_text: str = "") -> str:
    v = _env(key)
    if not v:
        if help_text:
            print(help_text)
        print(f"\n  {key} not set:")
        v = _prompt(label, secret)
    return v


def _user_id_from_token(token: str) -> Optional[str]:
    """Decode the JWT payload and pull out the Peloton user id.

    A JWT is three base64url segments joined by dots; the middle one is the
    payload. No signature check here -- we're reading a claim, not trusting it.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)           # restore base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None

    for key in ("http://onepeloton.com/user_id", "user_id", "sub"):
        val = claims.get(key)
        if isinstance(val, str) and val:
            return val.split("|")[-1]                  # strip any "auth0|" prefix
    return None


def get_config() -> dict:
    print("\n── Peloton → Hyper Extract ───────────────────────────────")

    token = _need("PELOTON_TOKEN",
                  "Paste your Peloton Bearer token (hidden)",
                  secret=True, help_text=TOKEN_HELP)

    user_id = _env("PELOTON_USER_ID") or _user_id_from_token(token)
    if user_id:
        print(f"  ✓ User ID: {user_id}")
    else:
        print("\n  Couldn't read the user ID out of that token.")
        print("  Open the /api/me request in DevTools → Response tab → copy \"id\".")
        user_id = _prompt("Peloton user ID")

    return {
        "peloton_token":   token,
        "peloton_user_id": user_id,
        "hyper_file":      _env("HYPER_FILE", "peloton_data.hyper"),
        "max_workouts":    int(_env("MAX_WORKOUTS", "0") or 0),
        "max_rides":       int(_env("MAX_RIDES", "1000") or 0),
    }


# ── Peloton auth helpers ──────────────────────────────────────────────────────

def verify_token(token: str, user_id: str) -> None:
    """Quick check that the token is valid."""
    print("\n[1/6] Verifying Peloton token…")
    resp = httpx.get(
        f"{PELOTON_BASE}/api/user/{user_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    if resp.status_code == 401:
        print(TOKEN_HELP)
        sys.exit("✗ Token is expired or invalid (they last ~48h). "
                 "Grab a fresh one using the steps above.")
    if not resp.is_success:
        sys.exit(f"✗ Token check failed ({resp.status_code}): {resp.text}")
    name = resp.json().get("username") or resp.json().get("email") or user_id
    print(f"  ✓ Token valid  (user: {name})")

def _auth_headers(token: str) -> dict:
    return {
        "Authorization":    f"Bearer {token}",
        "Peloton-Platform": "web",
        "Accept":           "application/json, text/plain, */*",
        "Accept-Language":  "en-US",
        "Origin":           "https://members.onepeloton.com",
        "Referer":          "https://members.onepeloton.com/",
        "X-Requested-With": "XmlHttpRequest",
        "X-Token-Exchange": "1",
        "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    }

def _ts(unix: Optional[int]) -> Optional[str]:
    """Unix timestamp → ISO 8601 UTC string, or None."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(unix)) if unix else None


# ── Peloton data fetchers ─────────────────────────────────────────────────────

def _parse_workout_row(w: dict) -> dict:
    ride = w.get("ride") or {}
    instructor = ride.get("instructor") or {}
    return {
        "workout_id":           w.get("id") or "",
        "workout_date":         _ts(w.get("start_time")),
        "end_time":             _ts(w.get("end_time")),
        "class_title":          ride.get("title") or w.get("name") or "",
        "ride_id":              ride.get("id") or "",
        "instructor_id":        ride.get("instructor_id") or "",
        "instructor_name":      instructor.get("name") or "",
        "fitness_discipline":   w.get("fitness_discipline") or "",
        "duration_min":         round((w.get("length") or 0) / 60, 1),
        "total_output_kj":      round((w.get("total_work") or 0) / 1000, 2),
        "avg_output_watts":     float(w.get("avg_power") or 0),
        "max_output_watts":     float(w.get("max_power") or 0),
        "avg_cadence_rpm":      float(w.get("avg_cadence") or 0),
        "max_cadence_rpm":      float(w.get("max_cadence") or 0),
        "avg_resistance_pct":   float(w.get("avg_resistance") or 0),
        "max_resistance_pct":   float(w.get("max_resistance") or 0),
        "avg_speed_mph":        float(w.get("avg_speed") or 0),
        "max_speed_mph":        float(w.get("max_speed") or 0),
        "distance_miles":       float(w.get("distance") or 0),
        "calories_kcal":        float(w.get("calories") or 0),
        "heart_rate_avg":       float(w.get("avg_heart_rate") or 0),
        "heart_rate_max":       float(w.get("max_heart_rate") or 0),
        "difficulty_rating":    round(float(ride.get("difficulty_rating_avg") or 0), 2),
        "device_type":          w.get("device_type") or "",
        "is_outdoor":           bool(w.get("is_outdoor", False)),
        "status":               w.get("status") or "",
        "timezone":             w.get("timezone") or "",
        "leaderboard_rank":     int(w.get("leaderboard_rank") or 0) or None,
        "total_leaderboard":    int(w.get("total_leaderboard_users") or 0) or None,
        "has_pedaling_metrics": bool(w.get("has_pedaling_metrics", False)),
    }

def fetch_workouts(token: str, user_id: str, max_workouts: int) -> list[dict]:
    print("\n[2/6] Fetching workout history…")
    rows = []

    page = 0
    while True:
        limit = min(PAGE_SIZE, max_workouts - len(rows)) if max_workouts else PAGE_SIZE
        url = (
            f"{PELOTON_BASE}/api/user/{user_id}/workouts"
            f"?joins=ride&limit={limit}&page={page}"
        )
        data = httpx.get(url, headers=_auth_headers(token), timeout=30).json()

        batch = data.get("data") or []
        if not batch:
            break

        if len(rows) == 0:
            total = data.get("total") or data.get("count")
            if total:
                print(f"  Total available: {total:,}")

        rows.extend(_parse_workout_row(w) for w in batch)
        print(f"  Fetched {len(rows):,} workouts…", end="\r")

        if max_workouts and len(rows) >= max_workouts:
            break

        # Stop if we've fetched everything
        total = data.get("total") or 0
        if total and len(rows) >= total:
            break
        # Also stop if this page was short (last page)
        if len(batch) < limit:
            break
        page += 1

    print(f"  ✓ {len(rows):,} workouts fetched.          ")
    return rows


def fetch_earned_achievements(token: str, user_id: str) -> list[dict]:
    print("\n[3/6] Fetching your earned achievements…")
    # Try v2 first, fall back to v1
    for endpoint in [
        f"{PELOTON_BASE}/api/v2/user/{user_id}/achievements",
        f"{PELOTON_BASE}/api/user/{user_id}/achievements",
    ]:
        resp = httpx.get(endpoint, headers=_auth_headers(token), timeout=20)
        if resp.is_success:
            break
    else:
        print(f"  ⚠ Could not fetch earned achievements. Skipping.")
        return []
    data = resp.json()
    records = data.get("data") or (data if isinstance(data, list) else [])
    rows = []
    for a in records:
        template = a.get("template") or {}
        rows.append({
            "user_achievement_id": a.get("id") or "",
            "template_id":         a.get("achievement_template_id") or template.get("id") or "",
            "slug":                template.get("slug") or a.get("slug") or "",
            "name":                template.get("name") or a.get("name") or "",
            "achievement_type":    template.get("achievement_type") or "",
            "earned_at":           _ts(a.get("earned_at") or a.get("created_at")),
            "earn_count":          int(a.get("earn_count") or 1),
            "image_url":           template.get("image_url") or a.get("image_url") or "",
        })
    print(f"  ✓ {len(rows)} earned achievements fetched.")
    return rows


def fetch_instructors() -> list[dict]:
    print("\n[4/6] Fetching instructors…")
    rows, page = [], 0
    while True:
        data = httpx.get(
            f"{PELOTON_BASE}/api/instructor?page={page}&limit={PAGE_SIZE}",
            timeout=20,
        ).json()
        batch = data.get("data") or []
        if not batch:
            break
        for i in batch:
            rows.append({
                "instructor_id":        i.get("id") or "",
                "name":                 i.get("name") or "",
                "bio":                  (i.get("bio") or "")[:500],
                "short_bio":            (i.get("short_bio") or "")[:200],
                "quote":                (i.get("quote") or "")[:300],
                "background":           (i.get("background") or "")[:500],
                "fitness_disciplines":  ", ".join(i.get("fitness_disciplines") or []),
                "image_url":            i.get("image_url") or "",
                "is_instructor_visible": bool(i.get("is_instructor_visible", True)),
            })
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    print(f"  ✓ {len(rows)} instructors fetched.")
    return rows


def _get_with_retry(url: str, token: str, retries: int = 3, backoff: float = 5.0):
    """GET with retry on transient connection errors."""
    for attempt in range(retries):
        try:
            resp = httpx.get(url, headers=_auth_headers(token), timeout=30)
            return resp
        except Exception as e:
            if attempt < retries - 1:
                wait = backoff * (attempt + 1)
                print(f"\n  ⚠ Connection error ({e}), retrying in {wait:.0f}s…")
                time.sleep(wait)
            else:
                raise


def fetch_rides(token: str, max_rides: int) -> list[dict]:
    print("\n[5/6] Fetching class catalog (rides)…")
    rows, page, total = [], 0, None
    while True:
        if max_rides and len(rows) >= max_rides:
            break
        resp = _get_with_retry(
            f"{PELOTON_BASE}/api/v2/ride/archived"
            f"?page={page}&limit={PAGE_SIZE}&sort_by=original_air_time"
            f"&desc=true&content_format=audio,video",
            token,
        )
        if not resp.is_success:
            print(f"  ⚠ Rides returned {resp.status_code}, stopping.")
            break
        data  = resp.json()
        batch = data.get("data") or []
        if not batch:
            break
        # Small delay every 50 pages to avoid rate limiting
        if page > 0 and page % 50 == 0:
            time.sleep(2)
        for r in batch:
            rows.append({
                "ride_id":               r.get("id") or "",
                "title":                 r.get("title") or "",
                "description":           (r.get("description") or "")[:400],
                "instructor_id":         r.get("instructor_id") or "",
                "fitness_discipline":    r.get("fitness_discipline") or "",
                "duration_min":          round((r.get("duration") or 0) / 60, 1),
                "difficulty_avg":        round(float(r.get("difficulty_rating_avg") or 0), 2),
                "difficulty_estimate":   str(r.get("difficulty_estimate") or ""),
                "original_air_date":     _ts(r.get("original_air_time")),
                "ride_type_id":          r.get("ride_type_id") or "",
                "ride_type_name":        r.get("ride_type") or "",
                "image_url":             r.get("image_url") or "",
                "is_explicit":           bool(r.get("is_explicit", False)),
                "content_format":        r.get("content_format") or "",
                "total_ratings":         int(r.get("total_ratings") or 0),
                "avg_difficulty_rating": round(float(r.get("difficulty_rating_avg") or 0), 2),
                "total_in_progress":     int(r.get("total_in_progress_workouts") or 0),
            })
        if total is None:
            total = data.get("total", 0)
            print(f"  Total available: {total:,}")
        print(f"  Fetched {len(rows):,} rides…", end="\r")
        if total and len(rows) >= total:
            break
        if not data.get("show_next"):
            break
        page += 1
    print(f"  ✓ {len(rows):,} rides fetched.          ")
    return rows


def fetch_achievement_templates(token: str) -> list[dict]:
    print("\n[6/6] Fetching achievement templates…")
    for endpoint in [
        f"{PELOTON_BASE}/api/v2/achievement/template",
        f"{PELOTON_BASE}/api/achievement/template",
    ]:
        resp = httpx.get(endpoint, headers=_auth_headers(token), timeout=20)
        if resp.is_success:
            break
    else:
        print(f"  ⚠ Could not fetch achievement templates. Skipping.")
        return []
    data = resp.json()
    records = data.get("data") or (data if isinstance(data, list) else [])
    rows = []
    for a in records:
        rows.append({
            "template_id":      a.get("id") or "",
            "slug":             a.get("slug") or "",
            "name":             a.get("name") or "",
            "description":      (a.get("description") or "")[:400],
            "achievement_type": a.get("achievement_type") or "",
            "image_url":        a.get("image_url") or "",
        })
    print(f"  ✓ {len(rows)} achievement templates fetched.")
    return rows


# ── Hyper schema ──────────────────────────────────────────────────────────────

def _col(name: str, sql_type, nullable: bool = True) -> TableDefinition.Column:
    return TableDefinition.Column(name, sql_type)

# Plain string key order for each table — must match SCHEMA column order exactly
SCHEMA_KEYS: dict[str, list[str]] = {
    "workouts": [
        "workout_id", "workout_date", "end_time", "class_title", "ride_id",
        "instructor_id", "instructor_name", "fitness_discipline", "duration_min",
        "total_output_kj", "avg_output_watts", "max_output_watts", "avg_cadence_rpm",
        "max_cadence_rpm", "avg_resistance_pct", "max_resistance_pct", "avg_speed_mph",
        "max_speed_mph", "distance_miles", "calories_kcal", "heart_rate_avg",
        "heart_rate_max", "difficulty_rating", "device_type", "is_outdoor", "status",
        "timezone", "leaderboard_rank", "total_leaderboard", "has_pedaling_metrics",
    ],
    "earned_achievements": [
        "user_achievement_id", "template_id", "slug", "name", "achievement_type",
        "earned_at", "earn_count", "image_url",
    ],
    "instructors": [
        "instructor_id", "name", "bio", "short_bio", "quote", "background",
        "fitness_disciplines", "image_url", "is_instructor_visible",
    ],
    "rides": [
        "ride_id", "title", "description", "instructor_id", "fitness_discipline",
        "duration_min", "difficulty_avg", "difficulty_estimate", "original_air_date",
        "ride_type_id", "ride_type_name", "image_url", "is_explicit", "content_format",
        "total_ratings", "avg_difficulty_rating", "total_in_progress",
    ],
    "achievement_templates": [
        "template_id", "slug", "name", "description", "achievement_type", "image_url",
    ],
}

# Text columns per table — any value in these columns will be coerced to str
SCHEMA_TEXT_COLS: dict[str, set[str]] = {
    "workouts": {
        "workout_id", "workout_date", "end_time", "class_title", "ride_id",
        "instructor_id", "instructor_name", "fitness_discipline", "device_type",
        "status", "timezone",
    },
    "earned_achievements": {
        "user_achievement_id", "template_id", "slug", "name", "achievement_type",
        "earned_at", "image_url",
    },
    "instructors": {
        "instructor_id", "name", "bio", "short_bio", "quote", "background",
        "fitness_disciplines", "image_url",
    },
    "rides": {
        "ride_id", "title", "description", "instructor_id", "fitness_discipline",
        "difficulty_estimate", "original_air_date", "ride_type_id", "ride_type_name",
        "image_url", "content_format",
    },
    "achievement_templates": {
        "template_id", "slug", "name", "description", "achievement_type", "image_url",
    },
}

SCHEMA: dict[str, list[TableDefinition.Column]] = {
    "workouts": [
        _col("workout_id",            SqlType.text(),   nullable=False),
        _col("workout_date",          SqlType.text()),
        _col("end_time",              SqlType.text()),
        _col("class_title",           SqlType.text()),
        _col("ride_id",               SqlType.text()),
        _col("instructor_id",         SqlType.text()),
        _col("instructor_name",       SqlType.text()),
        _col("fitness_discipline",    SqlType.text()),
        _col("duration_min",          SqlType.double()),
        _col("total_output_kj",       SqlType.double()),
        _col("avg_output_watts",      SqlType.double()),
        _col("max_output_watts",      SqlType.double()),
        _col("avg_cadence_rpm",       SqlType.double()),
        _col("max_cadence_rpm",       SqlType.double()),
        _col("avg_resistance_pct",    SqlType.double()),
        _col("max_resistance_pct",    SqlType.double()),
        _col("avg_speed_mph",         SqlType.double()),
        _col("max_speed_mph",         SqlType.double()),
        _col("distance_miles",        SqlType.double()),
        _col("calories_kcal",         SqlType.double()),
        _col("heart_rate_avg",        SqlType.double()),
        _col("heart_rate_max",        SqlType.double()),
        _col("difficulty_rating",     SqlType.double()),
        _col("device_type",           SqlType.text()),
        _col("is_outdoor",            SqlType.bool()),
        _col("status",                SqlType.text()),
        _col("timezone",              SqlType.text()),
        _col("leaderboard_rank",      SqlType.int()),
        _col("total_leaderboard",     SqlType.int()),
        _col("has_pedaling_metrics",  SqlType.bool()),
    ],
    "earned_achievements": [
        _col("user_achievement_id", SqlType.text(), nullable=False),
        _col("template_id",         SqlType.text()),
        _col("slug",                SqlType.text()),
        _col("name",                SqlType.text()),
        _col("achievement_type",    SqlType.text()),
        _col("earned_at",           SqlType.text()),
        _col("earn_count",          SqlType.int()),
        _col("image_url",           SqlType.text()),
    ],
    "instructors": [
        _col("instructor_id",         SqlType.text(), nullable=False),
        _col("name",                  SqlType.text()),
        _col("bio",                   SqlType.text()),
        _col("short_bio",             SqlType.text()),
        _col("quote",                 SqlType.text()),
        _col("background",            SqlType.text()),
        _col("fitness_disciplines",   SqlType.text()),
        _col("image_url",             SqlType.text()),
        _col("is_instructor_visible", SqlType.bool()),
    ],
    "rides": [
        _col("ride_id",               SqlType.text(), nullable=False),
        _col("title",                 SqlType.text()),
        _col("description",           SqlType.text()),
        _col("instructor_id",         SqlType.text()),
        _col("fitness_discipline",    SqlType.text()),
        _col("duration_min",          SqlType.double()),
        _col("difficulty_avg",        SqlType.double()),
        _col("difficulty_estimate",   SqlType.text()),
        _col("original_air_date",     SqlType.text()),
        _col("ride_type_id",          SqlType.text()),
        _col("ride_type_name",        SqlType.text()),
        _col("image_url",             SqlType.text()),
        _col("is_explicit",           SqlType.bool()),
        _col("content_format",        SqlType.text()),
        _col("total_ratings",         SqlType.int()),
        _col("avg_difficulty_rating", SqlType.double()),
        _col("total_in_progress",     SqlType.int()),
    ],
    "achievement_templates": [
        _col("template_id",      SqlType.text(), nullable=False),
        _col("slug",             SqlType.text()),
        _col("name",             SqlType.text()),
        _col("description",      SqlType.text()),
        _col("achievement_type", SqlType.text()),
        _col("image_url",        SqlType.text()),
    ],
}


# ── Hyper writer ─────────────────────────────────────────────────────────────

def write_hyper(path: str, data: dict[str, list[dict]]) -> None:
    """Write the extract atomically.

    Builds into a temp file next to the target, sanity-checks it, then swaps it
    in with os.replace() -- atomic on POSIX, and on Windows for same-volume
    moves. An API failure, a bad row, or Ctrl-C part way through can no longer
    destroy a working extract: the original stays untouched until a complete,
    non-empty replacement exists.

    The temp file lives in the target's own directory so the swap never has to
    cross a filesystem boundary, which would make it a non-atomic copy.
    """
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")

    existing_kb = target.stat().st_size / 1024 if target.exists() else None

    print(f"\nWriting {target}…")
    if existing_kb is not None:
        print(f"  (existing extract is {existing_kb:,.1f} KB — kept until the new one is verified)")

    written: dict[str, int] = {}
    try:
        _build_hyper(str(tmp), data, written)

        # ── verify before touching the real file ─────────────────────────────
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("the new extract is missing or zero bytes")
        if not written.get("workouts"):
            raise RuntimeError(
                "no workout rows were written. That usually means the token is "
                "valid but PELOTON_USER_ID points at the wrong account. "
                "Refusing to replace the existing extract."
            )

        os.replace(tmp, target)

    except BaseException:                       # includes KeyboardInterrupt
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if existing_kb is not None:
            print(f"\n  ✗ Write failed. {target.name} is unchanged "
                  f"({existing_kb:,.1f} KB).")
        else:
            print(f"\n  ✗ Write failed. No extract was created.")
        raise

    size_kb = target.stat().st_size / 1024
    total = sum(written.values())
    print(f"\n  ✓ Written: {target}  ({size_kb:,.1f} KB, {total:,} rows)")


def _build_hyper(path: str, data: dict[str, list[dict]],
                 written: dict[str, int]) -> None:
    """Populate a fresh .hyper at `path`. Records rows-per-table into `written`."""
    with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(hyper.endpoint, path, CreateMode.CREATE_AND_REPLACE) as conn:
            conn.catalog.create_schema_if_not_exists("Extract")

            for table_name, columns in SCHEMA.items():
                rows = data.get(table_name) or []
                if not rows:
                    print(f"  (skipping {table_name} — no data)")
                    continue

                tdef = TableDefinition(TableName("Extract", table_name), columns)
                conn.catalog.create_table(tdef)

                col_names = SCHEMA_KEYS[table_name]
                # Which columns are text — coerce any non-string value to str
                text_cols = SCHEMA_TEXT_COLS[table_name]

                with Inserter(conn, tdef) as ins:
                    for row in rows:
                        vals = []
                        for c in col_names:
                            v = row.get(c)
                            if v is not None and c in text_cols and not isinstance(v, (str, bytes, bytearray)):
                                v = str(v)
                            vals.append(v)
                        ins.add_row(vals)
                    ins.execute()

                written[table_name] = len(rows)
                print(f"  ✓ {table_name:<25} {len(rows):>6,} rows")


# ── Next steps hint ───────────────────────────────────────────────────────────

def print_next_steps(hyper_file: str) -> None:
    print(f"""
── Next steps ────────────────────────────────────────────────
  1. Open Tableau Desktop
  2. Connect → To a File → Hyper Extract → select {hyper_file}
  3. In the data source pane, drag tables to the canvas and
     set up relationships:
       workouts.instructor_id  ↔  instructors.instructor_id
       workouts.ride_id        ↔  rides.ride_id
       earned_achievements.template_id ↔ achievement_templates.template_id
  4. Build your viz, then:
       Server → Tableau Public → Save to Tableau Public
  5. Re-run this script any time to refresh the extract,
     then re-publish from Desktop.
──────────────────────────────────────────────────────────────
""")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if any(a in ("--token-help", "--help-token", "-t") for a in sys.argv[1:]):
        print(TOKEN_HELP)
        return
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return

    cfg = get_config()

    token   = cfg["peloton_token"]
    user_id = cfg["peloton_user_id"]

    verify_token(token, user_id)

    workouts             = fetch_workouts(token, user_id, cfg["max_workouts"])
    earned_achievements  = fetch_earned_achievements(token, user_id)
    instructors           = fetch_instructors()
    rides                 = fetch_rides(token, cfg["max_rides"])
    achievement_templates = fetch_achievement_templates(token)

    write_hyper(cfg["hyper_file"], {
        "workouts":              workouts,
        "earned_achievements":   earned_achievements,
        "instructors":           instructors,
        "rides":                 rides,
        "achievement_templates": achievement_templates,
    })

    print_next_steps(cfg["hyper_file"])


if __name__ == "__main__":
    main()
