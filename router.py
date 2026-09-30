"""Decides what kind of question was asked, by meaning, from the examples in intents.json.

No word rules and no cutoff number: the question is compared to every example, the closest examples
vote (weighted by similarity), and "open" (let the language model answer) is just another kind.
"""

import json
import os
import threading
from pathlib import Path

import world

INTENTS_PATH = Path(__file__).parent / "intents.json"
NEIGHBOURS = 5  # examples that vote

_lock = threading.Lock()
_cache = {"mtime": None, "examples": []}  # [(intent, vector)], reloaded when intents.json changes


def _examples():
    mtime = os.path.getmtime(INTENTS_PATH)
    with _lock:
        if _cache["mtime"] != mtime:
            data = json.loads(INTENTS_PATH.read_text(encoding="utf-8"))
            pairs = [(intent, text) for intent, texts in data.items() if not intent.startswith("_") for text in texts]
            vectors = world.embed([f"classification: {text}" for _, text in pairs])
            _cache.update(mtime=mtime, examples=[(intent, v) for (intent, _), v in zip(pairs, vectors)])
        return _cache["examples"]


def route(question):
    """(intent, confidence 0-1). Confidence is the winning intent's share of the neighbours' votes.
    A kind only wins with a majority; a split vote goes to "open", so an unsure question gets a slower
    but correct answer from the language model instead of a fast wrong one."""
    intent, confidence = _vote(question)
    return (intent, confidence) if intent == "open" or confidence > 0.5 else ("open", confidence)


def _vote(question):
    examples = _examples()
    vector = world.embed([f"classification: {question}"])[0]
    nearest = sorted(((world.similarity(vector, v), intent) for intent, v in examples), reverse=True)[:NEIGHBOURS]
    votes = {}
    for score, intent in nearest:
        votes[intent] = votes.get(intent, 0.0) + score
    intent = max(votes, key=votes.get)
    return intent, votes[intent] / sum(votes.values())
