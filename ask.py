"""Ask Milo about what it has seen. Answers come only from memories in memory.db.

Run: python ask.py                       (interactive, type questions)
     python ask.py "what dates did I see today?"
     python ask.py --hours 72 "what was I working on?"
"""

import argparse
import json
import re
from datetime import datetime, timedelta

import requests

import world
from observe import ASKING, MODEL, OLLAMA_URL, connect

MAX_MEMORIES = 200      # memories loaded from the time window (newest kept)
RELEVANT_MEMORIES = 12  # most related to the question
RECENT_MEMORIES = 6     # always included, for "what am I doing now" questions
RELEVANT_THINGS = 8
MAX_SOURCES = 5         # shown under an answer

SYSTEM_PROMPT = """You are Milo, a quiet memory assistant worn on the user's shoulder. You see what they see.
Below are two things you know:
- THINGS: each object you have seen, how many times, and where you saw it most recently (newest sighting first).
- MEMORIES: numbered, timestamped observations from your camera, oldest first.
Answer the user's question using ONLY these.
- For where-is / where-did-I-leave questions, use THINGS: the first sighting listed is the last place you saw it.
- Always say when you saw the things you mention (e.g. "at 7:37 PM", "about 2 hours ago").
- If the memories do not contain the answer, say you did not see it. Never make things up.
- Observations can be wrong. If something was seen only once or looks doubtful, say you are not sure.
- Be short and direct, one to three sentences. Speak to the user as "you". Do not offer further help.
- Never write memory numbers in the answer text; put them only in "sources".
Reply with ONLY a JSON object: {"answer": "your answer", "sources": [ids of the memories you used, e.g. 4, 6]}
Example: {"answer": "Your calculator was on the desk next to your notebook at 4:10 PM, about an hour ago.", "sources": [12]}"""


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


def format_memory(m):
    when = datetime.fromisoformat(m["timestamp"]).strftime("%a %b %d %I:%M %p")
    parts = [f"#{m['id']} [{when}] {m['summary']}", f"place: {m['location']}", f"doing: {m['activity']}"]
    names = [o.get("name", "") for o in m["objects"] if isinstance(o, dict)]
    if names:
        parts.append("objects: " + ", ".join(names))
    if m["text_seen"]:
        parts.append("text: " + " / ".join(map(str, m["text_seen"])))
    dates = [f"{d.get('date')} ({d.get('about')})" for d in m["dates"] if isinstance(d, dict)]
    if dates:
        parts.append("dates: " + "; ".join(dates))
    return " | ".join(parts)


def format_thing(t):
    def at(s):
        when = datetime.fromisoformat(s["timestamp"]).strftime("%a %b %d %I:%M %p")
        where = f" ({s['position']})" if s["position"] else ""
        return f"{when} at {s['place']}{where} [memory #{s['memory_id']}]"
    latest, earlier = t["sightings"][0], t["sightings"][1:]
    line = f"{t['name']}: seen {t['times_seen']} time{'s' if t['times_seen'] != 1 else ''}. Last seen {at(latest)}."
    if earlier:
        line += " Before that: " + "; ".join(at(s) for s in earlier) + "."
    if t["times_seen"] == 1:
        line += " (Seen only once: may be misidentified.)"
    elif latest["match"] < world.SURE_MATCH:
        line += f" (Latest sighting was called '{latest['seen_as']}': not certain it is the same {t['name']}.)"
    return line


def answer(question, hours=24):
    """Returns {"answer": str, "sources": [memory dicts the answer was based on]}."""
    ASKING.set()  # the watcher holds off starting new photo descriptions so this answer isn't queued behind them
    try:
        return _answer(question, hours)
    finally:
        ASKING.clear()


def _answer(question, hours):
    all_memories = load_memories(hours)
    if not all_memories:
        return {"answer": f"I have no memories from the last {hours:g} hours.", "sources": []}
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    all_things = world.things(since)

    # Only send what's relevant (the model reads ~80 tokens/s on this PC), plus the latest memories for "now".
    try:
        world.catch_up()
        memory_ids, thing_ids = world.relevant(question, since, RELEVANT_MEMORIES, RELEVANT_THINGS)
    except Exception:  # embedding model unavailable: fall back to the most recent
        memory_ids, thing_ids = [], [t["id"] for t in all_things[:RELEVANT_THINGS]]
    keep = set(memory_ids) | {m["id"] for m in all_memories[-RECENT_MEMORIES:]}
    memories = [m for m in all_memories if m["id"] in keep]
    by_thing = {t["id"]: t for t in all_things}
    things = "\n".join(format_thing(by_thing[i]) for i in thing_ids if i in by_thing) or "(none)"
    context = "\n".join(format_memory(m) for m in memories)
    now = datetime.now().strftime("%a %b %d %Y %I:%M %p")

    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 16384, "temperature": 0.2},
            "messages": [
                {"role": "system", "content": f"{SYSTEM_PROMPT}\n\nTHINGS:\n{things}\n\nMEMORIES:\n{context}"
                                              f"\n\nCurrent time: {now}"},
                {"role": "user", "content": question},
            ],
        },
        timeout=300,
    )
    resp.raise_for_status()
    reply = json.loads(resp.json()["message"]["content"])

    by_id = {m["id"]: m for m in all_memories}
    ids = [int(n) for n in re.findall(r"\d+", json.dumps(reply.get("sources", [])))]
    sources = [by_id[i] for i in dict.fromkeys(ids) if i in by_id][:MAX_SOURCES]
    text = re.sub(r"\s*[\[(](?:memory|memories)?\s*#\d+(?:,\s*#?\d+)*[\])]", "", str(reply.get("answer", ""))).strip()
    text = text or "I'm not sure. I couldn't put an answer together from my memories."
    return {"answer": text, "sources": sources}


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
