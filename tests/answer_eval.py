"""Score answering models on questions whose answers are known from the memories in memory.db.

Each check lists words the answer must contain (any one of each group) and words it must not contain.
The facts are specific to this memory.db, so update CASES when the memories change.

Run: python tests/answer_eval.py qwen2.5:3b llama3.2:3b
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402
import router  # noqa: E402

RUNS = 3
CASES = [
    # (question, [groups of acceptable words, each group must be matched], [words that must not appear])
    ("Have I been working on electronics?", [["yes"], ["microcontroller", "circuit", "electronic"]], ["no,", "did not"]),
    ("Did you see my keys?", [["not", "no ", "didn't", "haven't"]], ["desk", "bedroom", "kitchen"]),
    ("Did you see a laptop?", [["yes"], ["laptop"], ["desk"]], ["did not", "no,"]),
    ("Did you see a dog?", [["yes"], ["dog"]], ["did not", "no,"]),
    ("Did you see a stapler?", [["yes"], ["stapler"]], ["did not", "no,"]),
    ("Did you see a guitar?", [["not", "no ", "no,", "didn't", "haven't"]], ["yes"]),
    ("What did the note about the project say?", [["science"], ["oct"]], []),
    ("Was there a certificate somewhere?", [["certificate"], ["wall", "desk", "office"]], ["did not"]),
    ("Did I have a microcontroller?", [["yes"], ["microcontroller"]], ["did not", "no,"]),
    ("what've I got coming up", [["science"], ["oct", "5 days", "five days"]], ["meeting"]),
    ("Do I have anything to hand in soon?", [["science"]], []),
]


def check(answer, need, avoid):
    a = answer.lower()
    return all(any(w in a for w in group) for group in need) and not any(w in a for w in avoid)


def evaluate(model):
    ask.ANSWER_MODEL = model
    ask.warm()  # pre-read the memory log for this model, like the running app does
    passed, times = 0, []
    for question, need, avoid in CASES:
        intent, _ = router.route(question)
        results = []
        for _ in range(RUNS):
            t = time.time()
            answer = ask.answer(question, hours=24 * 30)["answer"]
            times.append(time.time() - t)
            results.append(check(answer, need, avoid))
        passed += sum(results)
        mark = "".join("+" if r else "-" for r in results)
        print(f"  {mark}  {question}  [{intent}]  last: {answer[:90]}")
    total = len(CASES) * RUNS
    print(f"{model}: {passed}/{total} correct ({100 * passed / total:.0f}%), "
          f"average {sum(times) / len(times):.1f}s, slowest {max(times):.1f}s\n")


if __name__ == "__main__":
    for model in sys.argv[1:] or [ask.ANSWER_MODEL]:
        print(model)
        evaluate(model)
