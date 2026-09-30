"""Watch continuously, built for walking around, not holding still.

Two things run side by side:
- Capture (fast, every ~2 s): grab a frame, drop blurry/dark ones, and group frames into scenes.
  A scene is a run of frames that look alike; only its sharpest frame is kept.
- Describe (slow, ~15 s per photo on the GPU): the vision model works through queued scenes at its
  own pace. Each memory keeps the time its photo was taken, not the time it was described.

If you move faster than the model can keep up, the queue fills and the most redundant scenes are
dropped first, so a walk through the house still ends up as a spread of distinct memories.

Run: python watch.py <camera-ip>   (or set CAMERA_IP). Stop with Ctrl+C.
"""

import io
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageChops, ImageFilter, ImageStat

import ask
import deadlines
import learn
import world
from observe import ASKING, Interrupted, camera_url_from_args, capture, complete, describe, read_text, record

CAPTURE_INTERVAL = 1.0   # seconds between captures (a USB webcam capture takes ~0.2 s)
# What counts as blurry, as a new scene, and as a real stay (vs a glance) is learned from what the camera
# actually sees (learn.Split): no hand-set thresholds. Until a learner has seen two clear groups it
# decides nothing, so nothing is skipped while it learns.
MAX_QUIET = 600          # remember an unchanged scene again after this long, to record time spent there
RECENT_SECONDS = 120     # a scene matching one queued this recently is a look-back, not a new scene
QUEUE_LIMIT = 30         # scenes waiting for the model; beyond this the most redundant is dropped


@dataclass
class Frame:
    jpeg: bytes
    taken_at: datetime
    sharpness: float
    thumb: Image.Image
    row_id: int = 0  # its memory, recorded instantly before the vision model describes it


@dataclass
class Scene:
    anchor: Image.Image     # thumbnail the scene is compared against
    best: Frame             # sharpest frame so far
    started: float
    queued_at: float = 0.0  # 0 until the scene has been queued once


def sharpness(img):
    """Edge variance: high for crisp detail, low for blur, darkness or a blank wall."""
    gray = img.convert("L").resize((320, 240))
    return ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]


def thumbnail(img):
    return img.convert("L").resize((32, 24))


def change(a, b):
    """Mean pixel difference (0-255) between two thumbnails."""
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def print_log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Watcher:
    def __init__(self, camera_url, log=print_log):
        self.camera_url = camera_url
        self.log = log
        self.latest_jpeg = None
        self._queue = []              # Frames waiting for the model, oldest first
        self._recent = []             # (thumb, monotonic time) of recently queued scenes
        self._scene = None
        self._prev_thumb = None
        self.blur = learn.Split("sharpness", "above_lowest")      # unusable frames (covered, dark, smeared) are the lowest group
        self.motion = learn.Split("change", "below_highest")         # a new view is the highest group of frame-to-frame change
        self.stay = learn.Split("scene_seconds", "above_lowest")   # quick glances are the lowest group of scene lengths
        self._cond = threading.Condition()
        self._recorder = ThreadPoolExecutor(max_workers=1)  # instant records, in order, off the capture thread
        self._stop = threading.Event()
        self._capture_thread = None
        self._describe_thread = None
        self._describing = False
        self.camera_online = None
        self.last_capture_at = None
        self.last_error = None
        self.last_memory = None
        self.counts = {"frames": 0, "blurry": 0, "queued": 0, "dropped": 0, "saved": 0}

    # ---- control ----

    @property
    def running(self):
        return bool(self._capture_thread and self._capture_thread.is_alive())

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        if not (self._describe_thread and self._describe_thread.is_alive()):
            self._describe_thread = threading.Thread(target=self._describe_loop, daemon=True)
            self._describe_thread.start()
        self.log(f"Watching {self.camera_url}")

    def stop(self):
        """Stop capturing. Scenes already captured are still described."""
        self._stop.set()
        if self._capture_thread:
            self._capture_thread.join()
        self.log("Paused")

    def pending(self):
        with self._cond:
            return len(self._queue) + (1 if self._describing else 0)

    def status(self):
        return {
            "running": self.running,
            "camera_online": self.camera_online,
            "last_capture_at": self.last_capture_at,
            "last_error": self.last_error,
            "pending": self.pending(),
            "last_memory": self.last_memory,
            "counts": dict(self.counts),
            "learned": {"blurry_below": self.blur.describe(), "new_scene_above": self.motion.describe(),
                        "stay_after_seconds": self.stay.describe()},
        }

    # ---- capture side ----

    def _capture_loop(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                jpeg = capture(self.camera_url)
                if self.camera_online is not True:
                    self.log("Camera online")
                self.camera_online, self.last_error = True, None
                self.latest_jpeg = jpeg
                self.last_capture_at = datetime.now().isoformat(timespec="seconds")
                self._handle_frame(jpeg, started)
            except Exception as e:
                if self.camera_online is not False:
                    self.log(f"Camera offline: {e.__class__.__name__}: {e}")
                self.camera_online, self.last_error = False, f"{e.__class__.__name__}: {e}"
            self._stop.wait(max(0.0, CAPTURE_INTERVAL - (time.monotonic() - started)))

        # Don't lose the scene we were in when paused.
        if self._scene and not self._scene.queued_at:
            self._enqueue(self._scene.best, "last scene before pause")
        self._scene = None

    def _handle_frame(self, jpeg, now):
        self.counts["frames"] += 1
        img = Image.open(io.BytesIO(jpeg))
        frame = Frame(jpeg, datetime.now(), sharpness(img), thumbnail(img))
        self.blur.add(frame.sharpness)
        if self._prev_thumb is not None:
            self.motion.add(change(frame.thumb, self._prev_thumb))
        self._prev_thumb = frame.thumb

        blurry_below = self.blur.cutoff()
        if blurry_below is not None and frame.sharpness < blurry_below:
            self.counts["blurry"] += 1
            return

        scene, new_scene_above = self._scene, self.motion.cutoff()
        if scene and new_scene_above is not None and change(frame.thumb, scene.anchor) < new_scene_above:
            if frame.sharpness > scene.best.sharpness:
                scene.best = frame
            stay_after = self.stay.cutoff()
            if not scene.queued_at and stay_after is not None and now - scene.started >= stay_after:
                self._enqueue(scene.best, "new scene", check_recent=True)
                scene.queued_at, scene.best = now, frame
            elif scene.queued_at and now - scene.queued_at >= MAX_QUIET:
                self._enqueue(scene.best, "still here")
                scene.queued_at, scene.best = now, frame
            return

        # The view changed (or scenes aren't learned yet: then every frame is its own scene and the
        # queue's redundancy check thins them out). A scene that ended before it counted as a stay
        # (a glance while walking) still counts.
        if scene:
            if new_scene_above is not None:
                self.stay.add(now - scene.started)
            if not scene.queued_at:
                self._enqueue(scene.best, "passing view", check_recent=True)
        self._scene = Scene(anchor=frame.thumb, best=frame, started=now)

    def _enqueue(self, frame, reason, check_recent=False):
        now = time.monotonic()
        with self._cond:
            self._recent = [(t, at) for t, at in self._recent if now - at < RECENT_SECONDS]
            same_view = self.motion.cutoff()
            if check_recent and same_view is not None and any(change(frame.thumb, t) < same_view for t, _ in self._recent):
                return  # looked away and back; already have this view
            self._recent.append((frame.thumb, now))
        self._recorder.submit(self._record, frame)

    def _record(self, frame):
        """Instant record: read the scene's text on the CPU (~0.2 s) and store the memory right away, then
        hand the photo to the vision model. If the model never gets to it, the time and text are still kept."""
        try:
            text = read_text(frame.jpeg)
        except Exception as e:
            text = []
            self.log(f"Text reading failed: {e.__class__.__name__}: {e}")
        frame.row_id, timestamp = record(frame.taken_at, text)
        self.last_memory = {"id": frame.row_id, "timestamp": timestamp, "summary": ""}
        self.log(f"#{frame.row_id} recorded (taken {frame.taken_at:%H:%M:%S})"
                 + (f" | text: {', '.join(text)}" if text else ""))
        with self._cond:
            self._queue.append(frame)
            self.counts["queued"] += 1
            if len(self._queue) > QUEUE_LIMIT:
                self._drop_most_redundant()
            self._cond.notify()

    def _drop_most_redundant(self):
        """Stop waiting to describe the queued frame most similar to another queued one (ties: the blurrier).
        Its memory stays, with its time and text; it just won't get a full description."""
        def redundancy(i):
            others = (change(self._queue[i].thumb, f.thumb) for j, f in enumerate(self._queue) if j != i)
            return (min(others), self._queue[i].sharpness)
        victim = min(range(len(self._queue)), key=redundancy)
        del self._queue[victim]
        self.counts["dropped"] += 1

    # ---- describe side ----

    def _describe_loop(self):
        while True:
            with self._cond:
                while not self._queue:
                    self._cond.wait()
                frame = self._queue.pop(0)
                self._describing = True
            try:
                while ASKING.is_set():  # a question is being answered: let it use the model first
                    time.sleep(0.2)
                try:
                    obs = describe(frame.jpeg, interruptible=True)
                except Interrupted:  # a question came in mid-description: redo this photo afterwards
                    with self._cond:
                        self._queue.insert(0, frame)
                    continue
                complete(frame.row_id, obs)
                self.counts["saved"] += 1
                self.last_memory = {"id": frame.row_id, "timestamp": frame.taken_at.isoformat(timespec="seconds"),
                                    "summary": obs.get("summary", "")}
                self.log(f"#{frame.row_id} described ({self.pending() - 1} waiting): {obs.get('summary', '')}")
                world.catch_up()  # add this memory's objects to the world model
                ask.warm_soon()   # pre-read it so questions stay fast
                if obs.get("dates"):
                    deadlines.safe_catch_up(self.log)  # read its dates into real deadlines
            except Exception as e:  # bad model output or Ollama down: the memory keeps its time and text
                self.log(f"#{frame.row_id} not described (kept its time and text): {e.__class__.__name__}: {e}")
            finally:
                with self._cond:
                    self._describing = False


def main():
    watcher = Watcher(camera_url_from_args())
    watcher.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    watcher.stop()
    try:
        while watcher.pending():
            print_log(f"Finishing {watcher.pending()} captured scene(s). Ctrl+C again to quit now.")
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    print_log(f"Stopped. {watcher.counts}")


if __name__ == "__main__":
    main()
