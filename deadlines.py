"""Deadlines: dates Milo has seen, turned into real calendar dates, merged, and sorted by how soon.

No date rules are written here. For each date in a memory, the vision model (as a text model) is given
the date the photo was taken plus what was written, and returns the real calendar date (or none if the
date isn't complete), a short title, and whether it is one of the deadlines already known. Whether a
deadline is still coming up is plain arithmetic against today.

Runs in the background after new memories; a question always interrupts it (it is retried later).
"""

import calendar
import json
import re
from datetime import date, datetime, timedelta

from observe import Interrupted, background_json, connect

PROMPT = """A camera worn on a person's shoulder took a photo on {photo_day}, {photo_date}.
In the photo it read this date: "{written}" ({about}).
All text read in the photo: {text}

Known deadlines and events so far:
{known}

Read "{written}" and report only what it literally says. Do not calculate or guess anything:
- "year", "month" (1-12), "day" (1-31): only the parts actually written, otherwise null.
- "days_after_photo": only for words like "today" (0), "tomorrow" (1), "in 3 days" (3), otherwise null.
- "weekday": only if a day of the week is written (e.g. "friday"), otherwise null.
- "title": what is due or happening, in your own words from the text, at most 6 words.
- "same_as": if it is the same thing as one of the known deadlines (for example the same note read
  again, even partly), that deadline's id, otherwise null.
Reply with ONLY JSON: {{"year": ..., "month": ..., "day": ..., "days_after_photo": ..., "weekday": ...,
"title": "...", "same_as": ...}}"""



def to_date(photo_day, reading, written):
    """Calendar arithmetic on what the model read, after checking the reading against what is written.
    Principles, not tuned rules:
    - every part the model reports must actually appear in the written text (models add parts that aren't there);
    - a written date wins over a weekday, which wins over "days after the photo";
    - a date without a year is the occurrence closest to when the photo was taken;
    - a weekday is the next one on or after the photo date;
    - without a day of the month the date is incomplete (None): nothing is invented."""
    text = written.lower()
    digits = re.findall(r"\d+", text)

    def number(key):
        found = re.match(r"\s*(-?\d+)", str(reading.get(key)))  # "3rd" -> 3
        return int(found.group(1)) if found else None

    def written_number(key):
        value = number(key)
        return value if value is not None and any(int(d) == value for d in digits) else None

    def spelled(names):
        """Index of a calendar name written in the text (longest match), e.g. "oct" or "october"."""
        hits = [(len(name), i) for i, name in names if name and re.search(rf"(?<![a-z]){name}", text)]
        return max(hits)[1] if hits else None

    month_names = [(i, calendar.month_name[i].lower()) for i in range(1, 13)] + \
                  [(i, calendar.month_abbr[i].lower()) for i in range(1, 13)]
    year, day = written_number("year"), written_number("day")
    month = spelled(month_names) or written_number("month")
    if month and not day:
        return None  # a month without a day is incomplete
    if month and day:
        if year and year < 100:
            year += 2000
        candidates = []
        for y in [year] if year else [photo_day.year - 1, photo_day.year, photo_day.year + 1]:
            try:
                candidates.append(date(y, month, day))
            except ValueError:
                pass
        return min(candidates, key=lambda d: abs((d - photo_day).days)) if candidates else None
    weekday = spelled([(i, calendar.day_name[i].lower()) for i in range(7)])
    if weekday is not None:
        return photo_day + timedelta(days=(weekday - photo_day.weekday()) % 7)
    offset = number("days_after_photo")
    if offset is not None and (not digits or any(int(d) == offset for d in digits)):
        return photo_day + timedelta(days=offset)  # "tomorrow" has no digits; "in 3 days" must show the 3
    return None


def ensure_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS deadlines (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            due        TEXT,              -- YYYY-MM-DD, or NULL while only partly known
            title      TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen  TEXT NOT NULL,
            memory_ids TEXT NOT NULL      -- JSON list of memories it was seen in
        );
        CREATE TABLE IF NOT EXISTS deadline_meta (key TEXT PRIMARY KEY, value TEXT);
    """)


def resolve(photo_time, written, about, text_seen, known):
    """The model's reading of one date: {"date", "title", "same_as"}. known: [(id, due, title)]."""
    taken = datetime.fromisoformat(photo_time)
    listing = "\n".join(f"#{i}: {due or 'date not complete'}, {title}" for i, due, title in known) or "(none)"
    reply = background_json(PROMPT.format(
        photo_day=taken.strftime("%A"), photo_date=taken.strftime("%Y-%m-%d"), written=written, about=about,
        text=" / ".join(map(str, text_seen)) or "(none)", known=listing), num_predict=160)
    due = to_date(taken.date(), reply, written)
    due = due.isoformat() if due else None
    title = str(reply.get("title") or about or written).strip()
    same = reply.get("same_as")
    same = int(str(same).lstrip("#")) if str(same).lstrip("#").isdigit() else None  # "#1" or 1
    match = next((k for k in known if k[0] == same), None)
    if match and match[1] and due and match[1] != due:
        same = None  # two different complete dates can't be one deadline
    elif match and not (match[1] and due):
        same = same if confirm_same(written, about, text_seen, match, source_text(match[0])) else None
    elif not match:
        same = None
    return {"date": due, "title": title, "same_as": same}


def source_text(deadline_id):
    """The text read in the first memory a deadline was seen in."""
    with connect() as db:
        ids = json.loads(db.execute("SELECT memory_ids FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()[0])
        row = db.execute("SELECT text_seen FROM observations WHERE id = ?", (ids[0],)).fetchone()
    return " / ".join(map(str, json.loads(row[0] or "[]"))) if row else ""


def confirm_same(written, about, text_seen, known, known_text):
    """A focused yes/no check before merging a date that isn't complete (a bigger prompt merged too eagerly).
    Both notes' full text is shown, so a partly read copy of the same note can be recognised."""
    reply = background_json(
        f'Note A (just read): the date "{written}" ({about}). All text on it: {" / ".join(map(str, text_seen)) or "(none)"}\n'
        f'Note B (seen before): "{known[2]}", date {known[1] or "not complete"}. All text on it: {known_text or "(none)"}\n'
        'Are A and B about the same deadline or event (for example the same note, possibly read only partly)?\n'
        'Reply with ONLY JSON: {"same": true or false}', num_predict=20)
    return reply.get("same") is True


def catch_up(log=print):
    """Read the dates in every memory not processed yet. Returns how many dates were added."""
    with connect() as db:
        ensure_tables(db)
        row = db.execute("SELECT value FROM deadline_meta WHERE key = 'last_memory_id'").fetchone()
        rows = db.execute("SELECT id, timestamp, dates, text_seen FROM observations WHERE id > ? AND dates != '[]'"
                          " ORDER BY id", (int(row[0]) if row else 0,)).fetchall()
    added = 0
    for memory_id, timestamp, dates, text_seen in rows:
        for d in json.loads(dates or "[]"):
            if not isinstance(d, dict) or not d.get("date"):
                continue
            with connect() as db:
                known = db.execute("SELECT id, due, title FROM deadlines ORDER BY id").fetchall()
            r = resolve(timestamp, str(d["date"]), str(d.get("about", "")), json.loads(text_seen or "[]"), known)
            with connect() as db:
                match = db.execute("SELECT id, due, memory_ids FROM deadlines WHERE id = ?",
                                   (r["same_as"],)).fetchone() if r["same_as"] else None
                if match:
                    ids = json.loads(match[2]) + [memory_id]
                    db.execute("UPDATE deadlines SET due = COALESCE(due, ?), last_seen = ?, memory_ids = ? WHERE id = ?",
                               (r["date"], timestamp, json.dumps(ids), match[0]))
                    log(f"#{memory_id} '{d['date']}' -> same as deadline #{match[0]}")
                else:
                    db.execute("INSERT INTO deadlines (due, title, first_seen, last_seen, memory_ids) VALUES (?, ?, ?, ?, ?)",
                               (r["date"], r["title"], timestamp, timestamp, json.dumps([memory_id])))
                    log(f"#{memory_id} '{d['date']}' -> {r['date'] or 'incomplete date'}: {r['title']}")
                added += 1
        with connect() as db:  # progress is saved per memory, so an interruption only redoes the current one
            db.execute("INSERT OR REPLACE INTO deadline_meta (key, value) VALUES ('last_memory_id', ?)", (memory_id,))
    return added


def upcoming(today=None):
    """Deadlines from today on, soonest first, with days left."""
    today = today or date.today()
    with connect() as db:
        ensure_tables(db)
        rows = db.execute("SELECT id, due, title, first_seen, last_seen, memory_ids FROM deadlines"
                          " WHERE due >= ? ORDER BY due", (today.isoformat(),)).fetchall()
    return [{"id": i, "due": due, "title": title, "first_seen": first, "last_seen": last,
             "memory_ids": json.loads(ids), "days_left": (date.fromisoformat(due) - today).days}
            for i, due, title, first, last, ids in rows]


def rebuild(log=print):
    """Throw deadlines away and read all dates again (they are derived data)."""
    with connect() as db:
        ensure_tables(db)
        db.executescript("DELETE FROM deadlines; DELETE FROM deadline_meta;")
    return catch_up(log)


def safe_catch_up(log=print):
    """catch_up for background use: an interruption by a question just means "try again later"."""
    try:
        return catch_up(log)
    except Interrupted:
        return 0
