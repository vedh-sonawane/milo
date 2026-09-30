"""Check that dates are read into the right calendar dates (deadlines.resolve), with known answers.

Run: python tests/deadline_eval.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deadlines  # noqa: E402

# (photo taken, date as written, what it's about, expected YYYY-MM-DD or None for "incomplete / not a date")
CASES = [
    ("2026-09-25T18:14:16", "OCT3", "due date for the science project", "2026-10-03"),
    ("2026-09-25T18:14:16", "Oct 3rd", "science project due", "2026-10-03"),
    ("2026-09-25T18:14:16", "10/3", "math homework due", "2026-10-03"),
    ("2026-09-28T10:00:00", "tomorrow", "quiz tomorrow", "2026-09-29"),
    ("2026-09-28T10:00:00", "next Friday", "essay due next Friday", "2026-10-02"),
    ("2026-12-20T15:00:00", "JAN 5", "exam on Jan 5", "2027-01-05"),
    ("2026-09-26T21:01:06", "OCT", "due date", None),
    ("2026-09-26T21:05:26", "2018", "the year of the graduation", None),
    ("2026-09-27T22:42:07", "2024-09-27", "date on a screen", "2024-09-27"),
    ("2026-09-30T08:00:00", "Friday", "field trip form due Friday", "2026-10-02"),
]


def main():
    right, times = 0, []
    for photo, written, about, want in CASES:
        t = time.time()
        got = deadlines.resolve(photo, written, about, [], [])["date"]
        times.append(time.time() - t)
        ok = got == want
        right += ok
        print(f"{'ok ' if ok else 'BAD'} photo {photo[:10]}  '{written}' -> {got}  (expected {want})")
    print(f"\n{right}/{len(CASES)} correct, {sum(times) / len(times):.1f}s average")


if __name__ == "__main__":
    main()
