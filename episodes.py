"""Episodes: memories grouped into stretches of one activity ("9:00-9:08 PM: microcontroller work at the desk").

Nothing is hardcoded:
- Sessions are cut where the time between memories is a long break. What counts as long is learned from
  the memories themselves: time gaps split into two groups (moments seconds apart vs real breaks), and the
  split point between the groups is found with Otsu's method.
- Each finished session is read by the vision model (the stronger of Milo's models, used here as a text
  model) and split into episodes with short titles. Single-photo activity guesses are too noisy to
  compare with numbers ("taking a selfie" / "working" flip within seconds); a model reading the whole
  session can tell what was actually going on.

Runs in the background; a question always interrupts it (it is retried later).
"""

import json
import math
import statistics
from datetime import datetime

import requests

from observe import ASKING, GPU, MODEL, NUM_CTX, OLLAMA_URL, Interrupted, connect

CHUNK = 40  # memories read per model call, so prompt + answer fit the model's NUM_CTX (shared with describing)

PROMPT = """Below are timestamped observations from a camera worn on a person's shoulder, in order.
Split them into episodes: continuous stretches where the person was doing one thing.
Merge brief or noisy moments into the surrounding episode; fewer, longer episodes are better than many tiny ones.
Every observation belongs to exactly one episode, in order.
Reply with ONLY a JSON object:
{"episodes": [{"first": first observation id, "last": last observation id,
  "title": "what the person was doing, concrete, at most 8 words",
  "place": "where, in a few words", "things": ["main objects involved"]}]}

OBSERVATIONS:
"""


def ensure_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS episodes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            start           TEXT NOT NULL,
            end             TEXT NOT NULL,
            first_memory_id INTEGER NOT NULL,
            last_memory_id  INTEGER NOT NULL,
            title           TEXT,
            place           TEXT,
            things          TEXT
        );
        CREATE TABLE IF NOT EXISTS episode_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def otsu(values, bins=64):
    """The split point that best separates values into two groups (maximum between-group variance)."""
    lo, hi = min(values), max(values)
    best, cut = -1.0, hi
    for k in range(1, bins):
        t = lo + (hi - lo) * k / bins
        low, high = [v for v in values if v < t], [v for v in values if v >= t]
        if low and high:
            between = len(low) * len(high) * (statistics.mean(low) - statistics.mean(high)) ** 2
            if between > best:
                best, cut = between, t
    return cut


def break_seconds(rows):
    """Learned: how long a gap between memories has to be to count as a break between sessions."""
    gaps = [(datetime.fromisoformat(b[1]) - datetime.fromisoformat(a[1])).total_seconds() for a, b in zip(rows, rows[1:])]
    gaps = [math.log10(max(g, 1.0)) for g in gaps]
    return 10 ** otsu(gaps) if len(gaps) >= 10 else None


def sessions(rows, gap):
    """Split memories (sorted by time) wherever the gap is a break."""
    out, current = [], []
    for row in rows:
        if current and (datetime.fromisoformat(row[1]) - datetime.fromisoformat(current[-1][1])).total_seconds() > gap:
            out.append(current)
            current = []
        current.append(row)
    if current:
        out.append(current)
    return out


def line(row):
    id_, timestamp, summary, activity, location = row
    return f"#{id_} {datetime.fromisoformat(timestamp):%a %I:%M:%S %p} | {location} | {activity} | {summary}"


def split_into_episodes(chunk):
    """Ask the model to split one chunk of a session into episodes. Returns [(first, last, title, place, things)]."""
    with GPU:
        if ASKING.is_set():
            raise Interrupted()
        resp = requests.post(OLLAMA_URL, json={
            "model": MODEL, "stream": True, "format": "json",
            "options": {"num_ctx": NUM_CTX, "temperature": 0.1, "num_predict": 600},
            "messages": [{"role": "user", "content": PROMPT + "\n".join(line(r) for r in chunk)}],
        }, stream=True, timeout=600)
        content = ""
        with resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if ASKING.is_set():
                    raise Interrupted()  # a question needs the model: stop and redo this later
                if raw:
                    part = json.loads(raw)
                    content += part.get("message", {}).get("content", "")
                    if part.get("done"):
                        break
    episodes = json.loads(content).get("episodes", [])

    # Keep the model honest: episodes must cover the chunk in order, with no gaps or overlaps.
    order = [r[0] for r in chunk]
    position = {id_: i for i, id_ in enumerate(order)}
    result, next_index = [], 0
    for e in episodes:
        first, last = position.get(e.get("first")), position.get(e.get("last"))
        if first is None or last is None or last < first or last < next_index:
            continue
        first = next_index  # anything skipped before this episode joins it
        result.append([first, last, str(e.get("title", "")).strip(), str(e.get("place", "")).strip(),
                       [str(t) for t in e.get("things", []) if t]])
        next_index = last + 1
    if not result:
        raise ValueError("model returned no usable episodes")
    result[-1][1] = len(order) - 1  # anything left at the end joins the last episode
    return [(order[a], order[b], title, place, things) for a, b, title, place, things in result]


def consolidate(log=print):
    """Turn every finished session that hasn't been processed yet into episodes. Returns how many were added."""
    with connect() as db:
        ensure_tables(db)
        rows = db.execute("SELECT id, timestamp, summary, activity, location FROM observations ORDER BY timestamp").fetchall()
        row = db.execute("SELECT value FROM episode_meta WHERE key = 'last_memory_id'").fetchone()
        done_up_to = int(row[0]) if row else 0
    gap = break_seconds(rows)
    if gap is None:
        return 0
    added = 0
    all_sessions = sessions(rows, gap)
    for session in all_sessions:
        still_going = session is all_sessions[-1] and \
            (datetime.now() - datetime.fromisoformat(session[-1][1])).total_seconds() <= gap
        if still_going or max(r[0] for r in session) <= done_up_to:
            continue
        for i in range(0, len(session), CHUNK):
            chunk = session[i:i + CHUNK]
            if max(r[0] for r in chunk) <= done_up_to:
                continue  # done before an interruption
            by_id = {r[0]: r for r in chunk}
            found = split_into_episodes(chunk)
            with connect() as db:  # the chunk's episodes and the progress marker are saved together
                for first, last, title, place, things in found:
                    db.execute("INSERT INTO episodes (start, end, first_memory_id, last_memory_id, title, place, things)"
                               " VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (by_id[first][1], by_id[last][1], first, last, title, place, json.dumps(things)))
                    log(f"episode {by_id[first][1][5:16]} to {by_id[last][1][11:16]}: {title}")
                done_up_to = max(r[0] for r in chunk)
                db.execute("INSERT OR REPLACE INTO episode_meta (key, value) VALUES ('last_memory_id', ?)", (done_up_to,))
            added += len(found)
    return added


def recent(limit=60):
    """Episodes, oldest first (the newest `limit`)."""
    with connect() as db:
        ensure_tables(db)
        rows = db.execute("SELECT id, start, end, title, place, things, first_memory_id, last_memory_id FROM episodes"
                          " ORDER BY start DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r[0], "start": r[1], "end": r[2], "title": r[3], "place": r[4], "things": json.loads(r[5] or "[]"),
             "first_memory_id": r[6], "last_memory_id": r[7]} for r in reversed(rows)]


def rebuild(log=print):
    """Throw episodes away and make them again from all memories (they are derived data)."""
    with connect() as db:
        ensure_tables(db)
        db.executescript("DELETE FROM episodes; DELETE FROM episode_meta;")
    return consolidate(log)
