"""Ask Milo about what it has seen. Answers come only from memories in memory.db.

Built to answer in a few seconds on a PC with integrated graphics (which reads new text at only
~100-150 tokens/s):
- Instant answers (no language model) for lookups such as "where is my X" or "is anything due".
  The kind of question is decided by meaning from examples (router.py, intents.json), not word rules.
- Everything else goes to a small text model whose context is the memory log, kept pre-read:
  the log only grows at the end, so the model's cache already holds it and only the question is new.

Run: python ask.py                       (interactive, type questions)
     python ask.py "what dates did I see today?"
     python ask.py --hours 72 "what was I working on?"
"""

import argparse
import json
import re
import threading
from datetime import datetime, timedelta

import requests

import deadlines
import router
import world
from observe import ASKING, GPU, OLLAMA_URL, connect

ANSWER_MODEL = "llama3.2:3b"  # small and fast; the vision model stays loaded separately for describing
NUM_CTX = 12288
KEEP_ALIVE = "24h"            # keep the model and its pre-read memory log in memory
LOG_CAPACITY = 120            # memories in the pre-read log (~70 tokens each)
LOG_STEP = 50                 # the log's first memory only moves in jumps of this, so the cache survives
MAX_MEMORIES = 200            # memories loaded for the timeline and instant answers
MAX_SOURCES = 5               # shown under an answer

INSTRUCTIONS = """You are Milo, a quiet memory assistant worn on the user's shoulder. You see what they see.
The MEMORY LOG below lists what your camera saw, oldest first: "#id time | place | what you saw | text | dates".
The user's message gives the current time, THINGS (objects you track, newest sighting first) and the question.
Answer using ONLY the memory log and THINGS.
- Say when you saw the things you mention, using the day and time as written in the log (e.g. "on Saturday
  at 9:07 PM"). Never calculate how long ago something was.
- If they do not contain the answer, say you did not see it. Never make things up.
- Observations can be wrong. If something was seen only once or looks doubtful, say you are not sure.
- Answer in one full sentence that says what and when. Never answer with just "yes", "no" or one word.
- Speak to the user as "you". Do not offer further help. Never write memory numbers in the answer text.
Reply with ONLY a JSON object: {"answer": "your answer", "sources": [at most 3 ids of the memories you used]}
Example: {"answer": "Yes, your laptop was open on the office desk on Saturday at 9:07 PM.", "sources": [51]}"""

# ---- memories ----

def load_memories(hours):
    """Memories from the last `hours` as dicts, oldest first (at most MAX_MEMORIES, newest kept)."""
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect() as db:
        rows = db.execute(
            "SELECT id, timestamp, summary, location, objects, text_seen, dates, activity"
            " FROM observations WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
            (since, MAX_MEMORIES),
        ).fetchall()
    return [to_dict(r) for r in reversed(rows)]


def to_dict(row):
    id_, timestamp, summary, location, objects, text_seen, dates, activity = row
    return {
        "id": id_, "timestamp": timestamp, "summary": summary, "location": location,
        "objects": json.loads(objects or "[]"), "text_seen": json.loads(text_seen or "[]"),
        "dates": json.loads(dates or "[]"), "activity": activity,
    }


def log_line(m):
    when = datetime.fromisoformat(m["timestamp"]).strftime("%a %b %d %I:%M %p")
    names = ", ".join(o.get("name", "") for o in m["objects"] if isinstance(o, dict))
    line = f"#{m['id']} {when} | {m['location']} | {m['summary'] or '(not described yet; text read instantly)'}"
    if names:
        line += f" Objects: {names}."
    if m["text_seen"]:
        line += " | text: " + " / ".join(map(str, m["text_seen"]))
    dates = [f"{d.get('date')} ({d.get('about')})" for d in m["dates"] if isinstance(d, dict)]
    if dates:
        line += " | dates: " + "; ".join(dates)
    return line


def memory_log():
    """(system prompt, memories in it). Starts at a multiple of LOG_STEP, so it changes only at the end
    as memories are added, which lets the model reuse what it already read."""
    with connect() as db:
        newest = db.execute("SELECT MAX(id) FROM observations").fetchone()[0] or 0
        first = max(0, -(-(newest - LOG_CAPACITY + 1) // LOG_STEP) * LOG_STEP)
        rows = db.execute("SELECT id, timestamp, summary, location, objects, text_seen, dates, activity"
                          " FROM observations WHERE id >= ? ORDER BY id", (first,)).fetchall()
    memories = [to_dict(r) for r in rows]
    return INSTRUCTIONS + "\n\nMEMORY LOG:\n" + "\n".join(log_line(m) for m in memories), memories


# ---- instant answers ----

def when_text(iso):
    t = datetime.fromisoformat(iso)
    minutes = (datetime.now() - t).total_seconds() / 60
    clock = t.strftime("%I:%M %p").lstrip("0")
    if minutes < 2:
        return f"just now ({clock})"
    if minutes < 60:
        return f"at {clock}, about {minutes:.0f} minutes ago"
    if t.date() == datetime.now().date():
        return f"at {clock}, about {minutes / 60:.0f} hour{'s' if minutes >= 90 else ''} ago"
    if (datetime.now().date() - t.date()).days == 1:
        return f"yesterday at {clock}"
    return f"on {t.strftime('%a %b %d')} at {clock}"


PLACES = ["bedroom", "bathroom", "kitchen", "living room", "dining room", "office", "desk", "hallway", "classroom",
          "garage", "closet", "outside", "street", "car", "store"]


def clean_place(location):
    """The first known room named in a location ("Indoor, possibly a living room or bedroom" -> "living room"),
    or "" when it's too vague to say."""
    p = str(location or "").lower()
    hits = [(p.find(name), name) for name in PLACES if re.search(rf"\b{name}\b", p)]
    if not hits:
        return ""
    name = min(hits)[1]
    return "office" if name == "desk" else name


def find_thing(question, things):
    """The tracked thing the question names, if any: full name first ("pen holder"), then the last word."""
    q = question.lower()
    named = [t for t in things if re.search(rf"\b{re.escape(t['name'])}(e?s)?\b", q)]
    if not named:
        named = [t for t in things if re.search(rf"\b{re.escape(t['name'].split()[-1])}(e?s)?\b", q)]
    return max(named, key=lambda t: (len(t["name"]), t["last_seen"])) if named else None


def _hours_since(since):
    return max(1, (datetime.now() - datetime.fromisoformat(since)).total_seconds() / 3600)


def answer_thing_location(question, since):
    thing = find_thing(question, world.things(since, limit=500))
    if not thing:
        return None  # names something Milo doesn't track: the language model says it didn't see it
    s = thing["sightings"][0]
    spot = f"{s['place']}" + (f", {s['position']}" if s["position"] else "")
    text = f"I last saw your {thing['name']} {when_text(s['timestamp'])}, at the {spot}."
    if len(thing["sightings"]) > 1:
        p = thing["sightings"][1]
        text += f" Before that, {when_text(p['timestamp'])}, at the {p['place']}."
    if thing["times_seen"] == 1:
        text += " I only saw it once, so I'm not completely sure it was that."
    return text, [x["memory_id"] for x in thing["sightings"]]


def days_text(days):
    return "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"


def answer_deadlines(question, since):
    """Upcoming deadlines as real dates (deadlines.py), soonest first."""
    items = deadlines.upcoming()
    if not items:
        return "I haven't seen anything coming up.", []
    parts = [f"{d['title']} on {datetime.fromisoformat(d['due']):%a %b %d} ({days_text(d['days_left'])})" for d in items]
    return "Coming up: " + "; ".join(parts) + ".", [i for d in items for i in d["memory_ids"]]


def answer_places_visited(question, since):
    visits = []  # [place, first memory, last memory], in order, merging back-to-back memories in one place
    for m in load_memories(_hours_since(since)):
        place = clean_place(m["location"])
        if place and visits and visits[-1][0] == place:
            visits[-1][2] = m
        elif place:
            visits.append([place, m, m])
    if not visits:
        return "I don't have any memories of where you were in that time.", []
    parts = []
    for place, first, last in visits[-8:]:
        a = datetime.fromisoformat(first["timestamp"]).strftime("%I:%M %p").lstrip("0")
        b = datetime.fromisoformat(last["timestamp"]).strftime("%I:%M %p").lstrip("0")
        parts.append(f"{place} ({a}" + (f" to {b})" if b != a else ")"))
    return "You were in: " + ", then ".join(parts) + ".", [v[2]["id"] for v in visits[-8:]]


def answer_current_activity(question, since):
    latest = load_memories(24)
    if not latest:
        return None
    m = latest[-1]
    return f"Most recently ({when_text(m['timestamp'])}): {m['summary']}", [m["id"]]


# One handler per kind of question in intents.json ("open" has none: the language model answers).
INSTANT = {
    "thing_location": answer_thing_location,
    "deadlines": answer_deadlines,
    "places_visited": answer_places_visited,
    "current_activity": answer_current_activity,
}


def instant_answer(question, since):
    """Answer straight from memory when the question is a lookup (kind decided by router.py). None if not."""
    try:
        intent, _ = router.route(question)
    except Exception:
        return None  # embedding model unavailable: the language model answers everything
    handler = INSTANT.get(intent)
    return handler(question, since) if handler else None


# ---- language model ----

def _chat(system, user, **options):
    # num_predict caps the answer: small models in JSON mode occasionally never stop (seen: 5 minutes).
    resp = requests.post(OLLAMA_URL, json={
        "model": ANSWER_MODEL, "stream": False, "format": "json", "keep_alive": KEEP_ALIVE,
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2, "num_predict": 120, **options},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }, timeout=300)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def warm():
    """Pre-read the memory log so the next question only has to read itself."""
    if ASKING.is_set():
        return  # a question is being answered right now; it reads the log itself
    system, _ = memory_log()
    with GPU:
        _chat(system, "ok", num_predict=1)


_warm_wanted = threading.Event()


def warm_soon():
    """Ask for a background pre-read (after new memories). Several requests in a row become one."""
    if not getattr(warm_soon, "thread", None):
        def loop():
            while True:
                _warm_wanted.wait()
                _warm_wanted.clear()
                try:
                    warm()
                except Exception:
                    pass  # Ollama busy or down: the next question just reads more itself
        warm_soon.thread = threading.Thread(target=loop, daemon=True)
        warm_soon.thread.start()
    _warm_wanted.set()


def answer(question, hours=24):
    """Returns {"answer": str, "sources": [memory dicts the answer was based on]}."""
    for event in answer_stream(question, hours):
        if event.get("done"):
            return {"answer": event["answer"], "sources": event["sources"]}


def answer_stream(question, hours=24):
    """Yields {"text": more words} while the answer is written, then
    {"done": True, "answer": final text, "sources": [...]}. Lookups arrive as one piece."""
    ASKING.set()  # the watcher holds off starting new photo descriptions while answering
    try:
        yield from _answer_stream(question, hours)
    finally:
        ASKING.clear()


def _answer_stream(question, hours):
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        world.catch_up()
    except Exception:
        pass  # embedding model unavailable: answer from what's already in the world model

    quick = instant_answer(question, since)
    if quick:
        text, ids = quick
        yield {"done": True, "answer": text, "sources": _memories_by_id(ids)[:MAX_SOURCES]}
        return

    system, log = memory_log()
    if not log:
        yield {"done": True, "answer": "I don't have any memories yet.", "sources": []}
        return
    try:
        memory_ids, thing_ids = world.relevant(question, since, memories=3, things=3)
    except Exception:
        memory_ids, thing_ids = [], []
    # Everything here is new text the model must read (~100 tokens/s), so keep it small: the latest sighting
    # of 3 things, and up to 3 related memories that are too old to be in the pre-read log (without them
    # the model made things up about older memories).
    by_thing = {t["id"]: {**t, "sightings": t["sightings"][:1]} for t in world.things(since, limit=500)}
    things = "\n".join(format_thing(by_thing[i]) for i in thing_ids if i in by_thing) or "(none)"
    in_log = {m["id"] for m in log}
    older = "\n".join(log_line(m) for m in _memories_by_id([i for i in memory_ids if i not in in_log]))
    if older:
        things += f"\nOLDER MEMORIES:\n{older}"
    # Upcoming deadlines are always included (a line each), so any wording of "what's coming up" can use
    # them; without them the model invented a meeting.
    coming = "; ".join(f"{d['title']} on {datetime.fromisoformat(d['due']):%a %b %d} ({days_text(d['days_left'])})"
                       for d in deadlines.upcoming())
    things += f"\nCOMING UP (deadlines you have read): {coming or 'nothing you have seen'}"
    now = datetime.now().strftime("%a %b %d %Y %I:%M %p")

    content, shown = "", 0
    with GPU:
        resp = requests.post(OLLAMA_URL, json={
            "model": ANSWER_MODEL, "stream": True, "format": "json", "keep_alive": KEEP_ALIVE,
            # num_predict caps the answer: small models in JSON mode occasionally never stop (seen: 5 minutes).
            "options": {"num_ctx": NUM_CTX, "temperature": 0.2, "num_predict": 120},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": f"Current time: {now}\nTHINGS:\n{things}\n\nQuestion: {question}"}],
        }, stream=True, timeout=300)
        with resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                content += chunk.get("message", {}).get("content", "")
                # Pass on the answer text as it grows inside the JSON ({"answer": "...).
                partial = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)', content)
                if partial and len(partial.group(1)) > shown:
                    yield {"text": partial.group(1)[shown:].replace('\\"', '"')}
                    shown = len(partial.group(1))
                if chunk.get("done"):
                    break

    reply = _parse_reply(content)
    ids = [int(n) for n in re.findall(r"\d+", json.dumps(reply.get("sources", [])))]
    text = re.sub(r"\s*[\[(](?:memory|memories)?\s*#\d+(?:,\s*#?\d+)*[\])]", "", str(reply.get("answer", ""))).strip()
    yield {"done": True, "answer": text or "I'm not sure. I couldn't put an answer together from my memories.",
           "sources": _memories_by_id(ids)[:MAX_SOURCES]}


def _parse_reply(content):
    """The model's JSON reply; if it was cut off mid-way, recover the answer text that was written."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        found = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)', content)
        ids = re.search(r'"sources"\s*:\s*\[([^\]]*)', content)
        return {"answer": found.group(1) if found else "", "sources": ids.group(1) if ids else []}


def _memories_by_id(ids):
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    with connect() as db:
        rows = db.execute("SELECT id, timestamp, summary, location, objects, text_seen, dates, activity"
                          f" FROM observations WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    by_id = {r[0]: to_dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def format_thing(t):
    def at(s):
        when = datetime.fromisoformat(s["timestamp"]).strftime("%a %b %d %I:%M %p")
        where = f" ({s['position']})" if s["position"] else ""
        return f"{when} at {s['place']}{where} [#{s['memory_id']}]"
    latest, earlier = t["sightings"][0], t["sightings"][1:]
    line = f"{t['name']}: seen {t['times_seen']} time{'s' if t['times_seen'] != 1 else ''}. Last seen {at(latest)}."
    if earlier:
        line += " Before that: " + "; ".join(at(s) for s in earlier) + "."
    if t["times_seen"] == 1:
        line += " (Seen only once: may be misidentified.)"
    return line


def main():
    parser = argparse.ArgumentParser(description="Ask Milo about what it has seen.")
    parser.add_argument("question", nargs="*", help="question to ask; leave empty for interactive mode")
    parser.add_argument("--hours", type=float, default=24, help="how far back to look (default 24)")
    args = parser.parse_args()

    if args.question:
        print(answer(" ".join(args.question), args.hours)["answer"])
        return

    print(f"Ask Milo about the last {args.hours:g} hours. Empty line or Ctrl+C to quit.")
    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        print(f"Milo: {answer(question, args.hours)['answer']}")


if __name__ == "__main__":
    main()
