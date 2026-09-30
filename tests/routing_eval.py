"""Compare question routing: the old word rules vs the learned router, on questions NOT in intents.json.

Run: python tests/routing_eval.py
"""

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import router  # noqa: E402

TESTS = [
    ("any idea where my calc went", "thing_location"),
    ("where did my glasses end up", "thing_location"),
    ("I lost my pencil case, seen it?", "thing_location"),
    ("where's the usb cable", "thing_location"),
    ("my wallet, where is it", "thing_location"),
    ("have you spotted my earbuds", "thing_location"),
    ("where did I set down my mug", "thing_location"),
    ("what've I got coming up", "deadlines"),
    ("anything I need to submit soon", "deadlines"),
    ("when's the science project due", "deadlines"),
    ("do I have exams soon", "deadlines"),
    ("remind me what deadlines you saw", "deadlines"),
    ("what's due this week", "deadlines"),
    ("which rooms did I go into", "places_visited"),
    ("where have I been walking around", "places_visited"),
    ("was I in the kitchen today", "places_visited"),
    ("what parts of the house was I in", "places_visited"),
    ("what am I looking at", "current_activity"),
    ("what's happening right now", "current_activity"),
    ("what can you see", "current_activity"),
    ("tell me what I'm doing", "current_activity"),
    ("what was I building last night", "open"),
    ("did I leave the light on", "open"),
    ("what's the name on the certificate", "open"),
    ("how long was I at my desk", "open"),
    ("was my room messy", "open"),
    ("did I use the microcontroller today", "open"),
    ("what did I eat", "open"),
    ("what was written on the whiteboard", "open"),
    ("have I worked on the robot this week", "open"),
]


def word_rules(q):
    """The old hardcoded routing from ask.py, for comparison."""
    q = q.lower()
    if re.search(r"\bwhere\b|\blast (see|saw|seen)\b|\b(find|leave|left|put)\b", q):
        return "thing_location"
    if re.search(r"\b(due|deadline|deadlines|dates?|assignment|homework)\b", q):
        return "deadlines"
    if re.search(r"\b(rooms?|places?)\b|where (have|had|was|were) i( been)?\b|where did i go", q):
        return "places_visited"
    if re.search(r"what (am i|are you) (doing|looking at|seeing)|what do you see|right now", q):
        return "current_activity"
    return "open"


def main():
    router.route("warm up")
    old = new = old_wrong = new_wrong = 0
    times = []
    for q, want in TESTS:
        t = time.time()
        got, conf = router.route(q)
        times.append(time.time() - t)
        rule = word_rules(q)
        old += rule == want
        new += got == want
        # The costly mistake: an instant answer of the wrong kind (sending to "open" is only slower).
        old_wrong += rule != want and rule != "open"
        new_wrong += got != want and got != "open"
        flag = "" if got == want else f"   <-- learned said {got} ({conf:.2f})"
        flag += "" if rule == want else f"   [rules said {rule}]"
        print(f"{want:<17} {q}{flag}")
    n = len(TESTS)
    print(f"\nword rules:     {old}/{n} correct, {old_wrong} wrong instant answers")
    print(f"learned router: {new}/{n} correct, {new_wrong} wrong instant answers   "
          f"({1000 * sum(times) / n:.0f} ms average)")


if __name__ == "__main__":
    main()
