"""World model: turns separate memories into things Milo knows about, and where it saw them.

Every object in every memory becomes a sighting of a thing. Two names are the same thing when
their meanings are close enough (measured with the local nomic-embed-text model):

  similarity >= 0.92                  same thing
  0.88 <= similarity < 0.92           same thing only if the last words match ("black calculator" yes,
                                      "pen holder" vs "pen" no)
  below 0.88                          a new thing

Measured on real pairs: same things scored 0.885-0.98, different things 0.73-0.873.
Each sighting stores its match score, so answers can say when Milo is not sure.
"""

import json
import math
import re
import threading

import requests

from observe import OLLAMA_URL, connect as connect_memories

EMBED_URL = OLLAMA_URL.replace("/api/chat", "/api/embed")
EMBED_MODEL = "nomic-embed-text"
SURE_MATCH = 0.92
MAYBE_MATCH = 0.88
# Not tracked as things: people (the user or anyone else) and generic filler names.
PEOPLE = {"person", "persons", "people", "man", "mans", "woman", "womans", "boy", "girl", "child", "user", "selfie"}
IGNORE = {"hand", "hands", "finger", "fingers", "face", "head", "arm", "arms",
          "object", "objects", "item", "items", "thing", "things"}

_lock = threading.Lock()  # one catch-up at a time (watcher, web server and ask may all call it)


def connect():
    db = connect_memories()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS things (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            embedding  TEXT NOT NULL,
            first_seen TEXT,
            last_seen  TEXT,
            times_seen INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sightings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            thing_id       INTEGER NOT NULL,
            observation_id INTEGER NOT NULL,
            timestamp      TEXT NOT NULL,
            place          TEXT,
            position       TEXT,
            seen_as        TEXT,
            match          REAL
        );
        CREATE INDEX IF NOT EXISTS sightings_thing ON sightings(thing_id, timestamp);
        CREATE TABLE IF NOT EXISTS memory_vectors (observation_id INTEGER PRIMARY KEY, embedding TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS world_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    return db


def normalize(name):
    name = re.sub(r"[^a-z0-9 \-]", " ", str(name).lower().replace("'", ""))  # "person's head" -> "persons head"
    name = re.sub(r"^(a|an|the|some|two|three|several|many)\s+", "", " ".join(name.split()))
    # Drop an owner: "womans bag" -> "bag", "persons head" -> "head" (then judged by what's left).
    name = re.sub(r"^(persons|mans|womans|boys|girls|childs|users|my|your|his|her|their)\s+", "", name)
    return name.strip()


def embed(texts):
    """nomic-embed-text wants a task prefix on each text: "clustering: ", "search_query: " or "search_document: "."""
    resp = requests.post(EMBED_URL, json={"model": EMBED_MODEL, "input": texts}, timeout=60)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def same_head(a, b):
    """The last word names the thing: "black calculator" is a calculator, "pen holder" is not a pen.
    Heads match if equal or one ends with the other ("smartphone" / "phone", "doors" / "door")."""
    ha, hb = a.split()[-1], b.split()[-1]
    ha, hb = ha[:-1] if ha.endswith("s") and len(ha) > 3 else ha, hb[:-1] if hb.endswith("s") and len(hb) > 3 else hb
    return ha.endswith(hb) or hb.endswith(ha)


def best_match(name, vector, things):
    """(thing_id, score) of the thing this name refers to, or (None, best score)."""
    best_id, best = None, 0.0
    for thing_id, thing_name, thing_vec in things:
        score = 1.0 if thing_name == name else similarity(vector, thing_vec)
        if score > best:
            best_id, best = thing_id, score
    if best_id is None:
        return None, 0.0
    thing_name = next(n for i, n, _ in things if i == best_id)
    if best >= SURE_MATCH or (best >= MAYBE_MATCH and same_head(name, thing_name)):
        return best_id, best
    return None, best


def memory_text(summary, place, activity, objects, text_seen, dates):
    """What a memory is about, as one line of text for search."""
    parts = [summary or "", f"place: {place}", f"doing: {activity}"]
    parts.append("objects: " + ", ".join(str(o.get("name", "")) for o in objects if isinstance(o, dict)))
    parts.append("text: " + " / ".join(map(str, text_seen)))
    parts.append("dates: " + "; ".join(f"{d.get('date')} {d.get('about')}" for d in dates if isinstance(d, dict)))
    return " | ".join(parts)


def _ingest(db, obs_id, timestamp, place, objects, document):
    names = {}
    for o in objects:
        if not isinstance(o, dict):
            continue
        name = normalize(o.get("name", ""))
        words = name.split()
        if words and words[-1] not in IGNORE | PEOPLE and name not in names:
            names[name] = str(o.get("where", ""))
    # One embedding call per memory: the memory itself (for search) plus each object name (for matching).
    vectors = embed([f"search_document: {document}"] + [f"clustering: {n}" for n in names])
    db.execute("INSERT OR REPLACE INTO memory_vectors (observation_id, embedding) VALUES (?, ?)",
               (obs_id, json.dumps(vectors[0])))
    things = [(i, n, json.loads(e)) for i, n, e in db.execute("SELECT id, name, embedding FROM things")]
    seen_here = set()
    for (name, position), vector in zip(names.items(), vectors[1:]):
        thing_id, score = best_match(name, vector, things)
        if thing_id is None:
            thing_id = db.execute("INSERT INTO things (name, embedding, first_seen) VALUES (?, ?, ?)",
                                  (name, json.dumps(vector), timestamp)).lastrowid
            things.append((thing_id, name, vector))
            score = 1.0
        if thing_id in seen_here:
            continue  # two names in one photo for the same thing: one sighting is enough
        seen_here.add(thing_id)
        db.execute("INSERT INTO sightings (thing_id, observation_id, timestamp, place, position, seen_as, match)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?)", (thing_id, obs_id, timestamp, place, position, name, round(score, 3)))
        _refresh_thing(db, thing_id)


def _refresh_thing(db, thing_id):
    first, last, count = db.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM sightings WHERE thing_id = ?",
                                    (thing_id,)).fetchone()
    if count:
        db.execute("UPDATE things SET first_seen = ?, last_seen = ?, times_seen = ? WHERE id = ?",
                   (first, last, count, thing_id))
    else:
        db.execute("DELETE FROM things WHERE id = ?", (thing_id,))


def catch_up():
    """Add every memory the world model hasn't seen yet. Safe to call often; returns how many were added."""
    with _lock, connect() as db:
        row = db.execute("SELECT value FROM world_meta WHERE key = 'last_observation_id'").fetchone()
        last_id = int(row[0]) if row else 0
        rows = db.execute("SELECT id, timestamp, location, objects, summary, text_seen, dates, activity"
                          " FROM observations WHERE id > ? ORDER BY id", (last_id,)).fetchall()
        for obs_id, timestamp, location, objects, summary, text_seen, dates, activity in rows:
            objects = json.loads(objects or "[]")
            document = memory_text(summary, location, activity, objects,
                                   json.loads(text_seen or "[]"), json.loads(dates or "[]"))
            _ingest(db, obs_id, timestamp, location or "", objects, document)
            db.execute("INSERT OR REPLACE INTO world_meta (key, value) VALUES ('last_observation_id', ?)", (obs_id,))
            db.commit()  # keep progress if a later memory fails (e.g. Ollama stopped)
        return len(rows)


def rebuild():
    """Throw the world model away and rebuild it from all memories (it is derived data)."""
    with _lock, connect() as db:
        db.executescript("DROP TABLE things; DROP TABLE sightings; DROP TABLE memory_vectors; DROP TABLE world_meta;")
    connect().close()
    return catch_up()


def forget(observation_id):
    """Remove a deleted memory's sightings; things no longer seen anywhere disappear."""
    with _lock, connect() as db:
        thing_ids = [r[0] for r in db.execute("SELECT DISTINCT thing_id FROM sightings WHERE observation_id = ?",
                                              (observation_id,))]
        db.execute("DELETE FROM sightings WHERE observation_id = ?", (observation_id,))
        db.execute("DELETE FROM memory_vectors WHERE observation_id = ?", (observation_id,))
        for thing_id in thing_ids:
            _refresh_thing(db, thing_id)


def relevant(question, since, memories=12, things=8):
    """Ids of the memories and things most related to a question, best first.
    Sending only these to the language model keeps answers fast however many memories exist."""
    q_search, q_cluster = embed([f"search_query: {question}", f"clustering: {question}"])
    with connect() as db:
        mem_rows = db.execute("SELECT v.observation_id, v.embedding FROM memory_vectors v"
                              " JOIN observations o ON o.id = v.observation_id WHERE o.timestamp >= ?", (since,))
        mem_scores = sorted(((similarity(q_search, json.loads(e)), i) for i, e in mem_rows), reverse=True)
        thing_rows = db.execute("SELECT id, name, embedding FROM things WHERE last_seen >= ?", (since,))
        thing_scores = []
        for thing_id, name, e in thing_rows:
            named = any(len(w) >= 3 and w in question.lower() for w in name.split())  # "where is my pen" -> pen
            thing_scores.append((similarity(q_cluster, json.loads(e)) + (1.0 if named else 0.0), thing_id))
        thing_scores.sort(reverse=True)
    return [i for _, i in mem_scores[:memories]], [i for _, i in thing_scores[:things]]


def things(since=None, limit=80, history=3):
    """Things seen since `since` (ISO time), most recently seen first, each with its latest sightings."""
    with connect() as db:
        rows = db.execute("SELECT id, name, first_seen, last_seen, times_seen FROM things"
                          " WHERE last_seen >= ? ORDER BY last_seen DESC LIMIT ?", (since or "", limit)).fetchall()
        result = []
        for thing_id, name, first_seen, last_seen, times_seen in rows:
            sightings = db.execute("SELECT timestamp, place, position, observation_id, seen_as, match FROM sightings"
                                   " WHERE thing_id = ? ORDER BY timestamp DESC LIMIT ?", (thing_id, history)).fetchall()
            result.append({
                "id": thing_id, "name": name, "first_seen": first_seen, "last_seen": last_seen,
                "times_seen": times_seen,
                "sightings": [{"timestamp": t, "place": p, "position": pos, "memory_id": m, "seen_as": a, "match": s}
                              for t, p, pos, m, a, s in sightings],
            })
        return result
