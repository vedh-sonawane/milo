"""Learning where measurements naturally split into two groups, instead of hand-set thresholds.

Example: frame sharpness. Blurry frames and sharp frames form two groups; the split point between them
is found with Otsu's method (the cut that best separates the groups). A split is only trusted when the
data really has two groups: Ashman's D above 2, the standard test for two clearly separated groups.
Otherwise there is no cutoff (None) and the caller must not filter anything.

Values are learned on a log scale (they are skewed: many small, a few very large) and remembered in
memory.db, so what Milo learned survives restarts. The only fixed numbers are memory sizes: how many
recent values each learner keeps, not decisions about the values.
"""

import json
import math
import threading
from collections import deque

import numpy as np

from observe import connect

SAVE_EVERY = 60  # values between saves to memory.db
MAX_GROUPS = 4   # most groups considered (the data picks how many, 1 to 4)


class Split:
    def __init__(self, name, boundary, memory=2000):
        self.name, self.boundary = name, boundary  # "above_lowest" or "below_highest", see _split
        self.values = deque(_load(name), maxlen=memory)
        self._lock = threading.Lock()
        self._cut = None
        self._stale = True
        self._unsaved = 0

    def add(self, value):
        with self._lock:
            self.values.append(math.log1p(max(value, 0.0)))
            self._stale = True
            self._unsaved += 1
            if self._unsaved >= SAVE_EVERY:
                _save(self.name, list(self.values))
                self._unsaved = 0

    def cutoff(self):
        """The learned split point in the original units, or None if the data doesn't show two groups yet."""
        with self._lock:
            if self._stale:
                self._cut = _split(np.fromiter(self.values, float), self.boundary)
                self._stale = False
            return None if self._cut is None else math.expm1(self._cut)

    def describe(self):
        cut = self.cutoff()
        return {"learned_from": len(self.values), "cutoff": None if cut is None else round(cut, 2)}


def _split(values, boundary):
    """Model selection, not a guess: fit 1 to MAX_GROUPS Gaussian groups and let the Bayesian information
    criterion choose how many there are (e.g. dark, blurry and sharp frames are three). With one group
    there is no split. Otherwise the boundary is taken next to the group that matters:
      "above_lowest":  just above the lowest group (unusable frames; quick glances)
      "below_highest": just below the highest group (a genuinely new view)
    and only if those two neighbouring groups are clearly separated (Ashman's D > 2)."""
    n = len(values)
    if n < 20 or values.std() == 0:  # too few to see groups at all
        return None
    best = None
    for k in range(1, MAX_GROUPS + 1):
        fit = _mixture(values, k)
        if fit is None:
            break
        groups, loglik = fit
        bic = -2 * loglik + (3 * k - 1) * math.log(n)
        if best is None or bic < best[0]:
            best = (bic, groups)
    groups = best[1]
    if len(groups) < 2:
        return None
    (w1, m1, s1), (w2, m2, s2) = (groups[-2], groups[-1]) if boundary == "below_highest" else (groups[0], groups[1])
    if math.sqrt(2) * abs(m2 - m1) / math.sqrt(s1 ** 2 + s2 ** 2) <= 2:
        return None
    grid = np.linspace(m1, m2, 512)  # first point between the two means where the upper group is more likely
    upper = w2 * np.exp(_normal_logpdf(grid, m2, s2)) >= w1 * np.exp(_normal_logpdf(grid, m1, s1))
    return float(grid[np.argmax(upper)]) if upper.any() else None


def _normal_logpdf(x, mean, sd):
    return -0.5 * ((x - mean) / sd) ** 2 - math.log(sd * math.sqrt(2 * math.pi))


def _mixture(values, k):
    """Best k-group Gaussian mixture: ([(weight, mean, sd)] sorted by mean, log-likelihood), or None.
    Fitting starts from two different guesses (groups spread over the middle of the data, and spread over
    its full range, which finds small far-off groups like a few covered-lens frames) and keeps the better."""
    fits = [f for f in (_fit(values, np.percentile(values, np.linspace(0, 100, k + 2)[1:-1])),
                        _fit(values, np.linspace(values.min(), values.max(), k + 2)[1:-1] if k > 1
                             else np.array([values.mean()]))) if f]
    return max(fits, key=lambda f: f[1]) if fits else None


def _fit(values, means, iterations=300):
    """Expectation-maximisation from the given starting means; None if a group ends up (nearly) empty."""
    k = len(means)
    sd0 = values.std()
    floor = sd0 * 1e-3  # keeps a group from shrinking onto a single repeated value
    w, m, s = np.full(k, 1.0 / k), np.array(means, float), np.full(k, sd0 / k)
    for _ in range(iterations):
        dens = np.stack([w[j] * np.exp(_normal_logpdf(values, m[j], s[j])) for j in range(k)]) + 1e-300
        resp = dens / dens.sum(axis=0)
        nk = resp.sum(axis=1)
        if (nk < 2).any():  # a group needs at least 2 members to have a spread at all
            return None
        w = nk / len(values)
        m_new = (resp * values).sum(axis=1) / nk
        s = np.maximum(np.sqrt((resp * (values - m_new[:, None]) ** 2).sum(axis=1) / nk), floor)
        done = np.allclose(m_new, m)
        m = m_new
        if done:
            break
    dens = np.stack([w[j] * np.exp(_normal_logpdf(values, m[j], s[j])) for j in range(k)]) + 1e-300
    order = np.argsort(m)
    return [(float(w[j]), float(m[j]), float(s[j])) for j in order], float(np.log(dens.sum(axis=0)).sum())


def _load(name):
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        row = db.execute("SELECT value FROM settings WHERE key = ?", (f"learn:{name}",)).fetchone()
    return json.loads(row[0]) if row else []


def _save(name, values):
    with connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"learn:{name}", json.dumps(values)))
