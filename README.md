# Milo

Milo is a personal AI memory device that sits on my shoulder, sees what I see, and builds a
long-term memory of my world: objects, places, text like due dates, activities, projects, and
how all of that changes over time. The goal is to be able to ask things like:

- "Where did I last see my calculator?"
- "What was I working on yesterday?"
- "What did I leave unfinished?"
- "Did I write down a due date this week?"

and get answers that combine what it sees right now with what it remembers.

It is not a smart-home gadget, a chatbot in a box, or an object detector. It is meant to be a
quiet, persistent, local intelligence that mostly stays silent and only speaks up when something
is actually worth saying.

The full vision, constraints, hardware inventory, and the step-by-step roadmap (section "ROADMAP") are in
[Full_Project.MD](Full_Project.MD).

## Status

The project is built bottom-up, one tested layer at a time.

| Layer | Status |
| --- | --- |
| Camera: USB webcam server, 30 fps live view + max-quality snapshot (ESP32 camera as fallback) | Done |
| One photo -> one structured memory in SQLite (local vision model) | Done |
| Watching on its own, built for walking: fast capture, scenes, background describing | Done |
| Raspberry Pi edge cache + sync to PC when home | Not started |
| World model: objects tracked across memories, last seen where, how sure | Done (places/projects/changes next) |
| Asking questions about memories (text, with sources) | Done (voice later) |
| Web app: ask, timeline, delete, pause/resume, camera view | Done |
| Proactive behaviour ("is this worth saying?") | Not started |
| Physical action | Not started (mechanism will be chosen after a real use case is found) |

## How it works today

```
Camera (webcam or ESP32)                        Windows PC
+-------------------------+   Wi-Fi / HTTP    +------------------------------------------+
| camera_server.py (USB)  |  GET /capture ->  | Capture (every 1 s)                      |
| or ESP32 camera.ino     |  <- 640x480 JPEG  |   blurry/dark? -> skip                   |
| /  = MJPEG live stream  |                   |   group frames into scenes, keep sharpest|
| /capture = one snapshot |                   |          |                               |
+-------------------------+                   |          v  queue (max 30)               |
                                              | Describe (~15 s each, GPU)               |
                                              |   Ollama qwen2.5vl:7b -> memory.db       |
                                              | Ask: question + memories -> answer       |
                                              | Web app: http://localhost:8000           |
                                              +------------------------------------------+
```

Milo is worn while you walk around, so it can't wait 20 seconds for a steady shot. The slow part is
the vision model (about 15 seconds per photo on the GPU), so capturing and describing run separately:

1. **Capture, every second.** Each frame gets a sharpness score; clearly unusable frames (covered lens, dark) are skipped, using thresholds learned from what the camera sees.
2. **Scenes.** Frames that look alike are grouped into a scene, and only the sharpest one is kept,
   so a clear frame wins over a blurry step. A scene is queued once it has been steady for 4 seconds,
   or when the view changes (a glance while walking still counts). Glancing back at a view queued in
   the last 2 minutes does not create a duplicate. A long stay in one place is re-remembered every
   10 minutes so Milo knows how long you were there.
3. **Queue.** Scenes wait for the model. If you move faster than it can keep up, the queue holds 30
   and drops the most redundant scene first, so a walk still ends up as a spread of distinct memories.
4. **Instant record.** The moment a scene is kept, it is saved as a memory with its time and any text in
   it, read by Windows' built-in OCR on the processor (about 20 ms, no waiting for the AI). Walking past a
   poster, its text is stored within a fraction of a second, and shows in the terminal and the page. If
   the AI never gets to a scene (too much going on), the memory still keeps its time and text.
5. **Describe, in the background.** The local vision model (Ollama, nothing leaves the PC) returns
   JSON: summary, location, objects, exact text seen, dates, activity. It is told not to guess blurry
   text or invent objects. Each memory is stored with the time the photo was **taken**.
6. **World model.** Every object in a new memory becomes a *sighting* of a *thing*. Names that mean the
   same (checked with the small local `nomic-embed-text` model) are the same thing: "black calculator"
   and "calculator" merge, "pen holder" and "pen" do not. Each sighting keeps when, where, and how sure
   the match was. People and body parts are never tracked as things.
7. **Ask, in a few seconds.** Lookups are answered instantly from the world model with no AI at all:
   "where is my X", "is anything due", "what rooms was I in", "what am I doing now". Which kind of
   question it is gets decided by *meaning*: the question is compared to example questions in
   `intents.json`, the closest examples vote, and a kind only wins with a majority (otherwise the AI
   answers). On 30 unseen wordings: 26 routed right and 0 wrong instant answers, vs 18 and 3 for the old
   word rules. To teach a new wording, add an example; no code change. Answers are **streamed**: the first
   words appear after about 3 seconds and the rest fills in. Related memories too old for the pre-read log
   are added to the question, so older things are answered instead of made up. Measured with
   `tests/answer_eval.py`: 33/33 correct for `llama3.2:3b` (qwen2.5:3b scored 63%, so it was not used).
8. **Deadlines.** Every date Milo reads becomes a real calendar date, shown in a **Coming up** panel
   ("Sat Oct 3: science project due, in 5 days"). No date rules are written in code: the vision model
   reports only what is literally written (month, day, weekday, "tomorrow"), each part is checked against
   the written text, and plain calendar arithmetic does the rest (a date without a year is the occurrence
   closest to when the photo was taken; without a day it is incomplete and never shown). Duplicate
   sightings are merged when the model says they are the same note (a focused yes/no check for partial
   dates; two different complete dates are never merged). `tests/deadline_eval.py`: 10/10. Everything else goes
   to a small text model (`llama3.2:3b`) whose memory log is **pre-read in the background** after each new
   memory, so only the question itself is new when you ask. Measured: lookups 0.0 s, other questions
   about 2.5-4.5 s. Only one AI job runs at a time on the GPU; a question interrupts a photo description,
   which is redone right after.
   If Milo never saw something, it says so instead of guessing. "Where is X" uses the latest sighting.

Example stored memory:

```json
{
  "summary": "A close-up of a notebook page with handwritten text, suggesting a science project due on October 3rd.",
  "location": "office desk",
  "objects": [{"name": "notebook", "where": "center of the image"}],
  "text_seen": ["SCIENCE", "PROJECT", "DUE OCT3"],
  "dates": [{"date": "OCT3", "about": "due date for the science project"}],
  "activity": "writing or reviewing a science project due date"
}
```

## Planned architecture

| Device | Role |
| --- | --- |
| ESP32-CAM (Freenove ESP32-WROVER CAM) | Eyes. Captures images only; no heavy AI on the board. |
| Raspberry Pi 5 (1 GB) | Edge controller worn with the camera: change detection, sensors, motors, a small temporary buffer while away from home, sync to the PC when back on home Wi-Fi. |
| ESP32 / Arduino UNO | Body control for any future actuators. |
| Windows PC | Permanent memory and heavy brain: database, world model, local vision and language models, speech, agent. |

Design principles:

- **Wearable.** It goes where I go, because that is where the interesting context is.
- **Local first.** Vision and reasoning run on my own machines through Ollama.
- **Memories, not footage.** Store structured observations and events, not raw video.
- **Mostly silent.** Update the world model quietly; only surface what is actually useful.
- **Use what I own.** No new hardware purchases for the MVP.

## Repository layout

```
camera_server.py       USB webcam as Milo's camera (same endpoints as the ESP32, 30 fps)
camera/
  camera.ino           ESP32 camera firmware (fallback camera)
  secrets.example.h    Template for Wi-Fi credentials
  secrets.h            Your real credentials (gitignored, you create it)
web/
  index.html           The web app page (plain HTML/JS, no build step)
server.py              Web app server: runs the watcher and serves the page and API
watch.py               Watcher: fast capture, scenes, queue, background describing
ask.py                 Answers questions from the relevant memories and things
world.py               World model: things, sightings, matching, search
learn.py               Learns thresholds from the data itself (no hand-set numbers)
router.py              Decides what kind of question was asked, by meaning, from examples
deadlines.py           Dates Milo read, as real calendar dates: merged, sorted, days left
episodes.py            Draft: memories grouped into episodes (not used yet, needs real wearing data)
intents.json           Example questions per kind (add examples to teach new wordings)
tests/routing_eval.py  Scores the router on questions it has never seen
tests/answer_eval.py   Scores answering models on questions with known answers (compare models)
tests/deadline_eval.py Checks dates are read into the right calendar dates
tests/watch_learning_eval.py  Checks the watcher's learned thresholds on simulated frames
tests/instant_record_eval.py  Checks scenes are recorded instantly with their text, then described
observe.py             One snapshot -> vision model -> memory; shared helpers
Full_Project.MD        Full vision, constraints, hardware inventory
AGENTS.md              Instructions for AI coding assistants working in this repo
```

Runtime files that stay on the PC and are gitignored: `memory.db`, `last_capture.jpg`.

## Hardware

- **Camera (recommended):** a USB webcam on the **Raspberry Pi 5**, powered by a USB-C power bank, so
  Milo is wireless and wearable. Tested with the Ubisoft Wii camera (640x480) and a 5V/2A power bank:
  about 25-30 fps over Wi-Fi, no low-voltage warnings, Pi about 50 C and mostly idle.
- The same webcam also works plugged straight into the PC (for testing).
- **Camera (fallback):** Freenove ESP32-WROVER CAM (GC0308 sensor, 640x480, no hardware JPEG, so only
  a few frames per second). Wireless on its own, but not smooth.
- A Windows PC running [Ollama](https://ollama.com) with the `qwen2.5vl:7b` model

## Setup

```powershell
pip install requests pillow fastapi uvicorn opencv-python numpy winocr rapidocr-onnxruntime
ollama pull qwen2.5vl:7b
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

### Raspberry Pi 5 camera (wearable)

One-time setup (already done for `milo`):

1. Raspberry Pi Imager: Raspberry Pi OS Lite (64-bit), hostname `milo`, user `milo`, your Wi-Fi,
   SSH with **public-key only** (key: `~/.ssh/milo_pi.pub` on the PC). Raspberry Pi Connect off.
2. Without admin rights: OpenCV goes into a private environment and cron starts the camera at boot.

   ```bash
   python3 -m venv ~/milo-env && ~/milo-env/bin/pip install opencv-python-headless
   # copy camera_server.py to ~/ and create ~/start_camera.sh (a loop that runs it and restarts it)
   (crontab -l; echo "@reboot sleep 10 && $HOME/start_camera.sh") | crontab -
   ```

After that it is plug and play: plug the webcam and power bank into the Pi, wait about a minute, and
the camera is at `http://milo.local:8081/`. Milo's camera address is `milo.local:8081`.

Update the camera code on the Pi after changing `camera_server.py`:

```powershell
scp -i $env:USERPROFILE\.ssh\milo_pi camera_server.py milo@milo.local:~/
ssh -i $env:USERPROFILE\.ssh\milo_pi milo@milo.local "pkill -f [c]amera_server.py"   # restarts itself
```

Check power and temperature: `ssh -i $env:USERPROFILE\.ssh\milo_pi milo@milo.local "vcgencmd get_throttled; vcgencmd measure_temp"`
(`throttled=0x0` means no low-voltage problems).

### USB webcam on the PC (testing)

```powershell
python camera_server.py --list         # find the webcam's number
python camera_server.py --device 1     # leave running
```

Live view: http://localhost:8081/ (smooth, and any number of viewers can watch while Milo runs).
Milo's camera address is then `localhost:8081`.

### ESP32 camera (fallback)

1. Credentials are never stored in tracked files: `copy camera\secrets.example.h camera\secrets.h`,
   then fill in your 2.4 GHz network name and password (the file is gitignored).
2. Flash it (requires [arduino-cli](https://arduino.github.io/arduino-cli/) with the `esp32:esp32` core):

   ```powershell
   arduino-cli core install esp32:esp32
   arduino-cli compile --fqbn esp32:esp32:esp32wrover:PartitionScheme=huge_app camera
   arduino-cli upload  --fqbn esp32:esp32:esp32wrover:PartitionScheme=huge_app -p COM4 camera
   ```

3. Open a serial monitor at 115200 baud and press EN/RST; it prints its address, e.g. `192.168.2.22`.
   The ESP32 serves one client at a time: close its live view before running Milo.

### Tell Milo which camera to use

```powershell
setx CAMERA_IP milo.local:8081    # Pi; localhost:8081 for a webcam on the PC; or the ESP32's address
```

Open a new terminal afterwards so `CAMERA_IP` is picked up. (You can also pass the address as the first
argument to `server.py`, `watch.py` or `observe.py` instead.)

## Usage

### The web app (normal use)

```powershell
python server.py
```

Then open **http://localhost:8000**. Milo starts watching right away (use `--paused` to start paused).

- **Ask Milo:** type a question; the answer shows the memories it was based on, with their times.
- **Memories:** a timeline of what Milo remembered, newest first, with text and dates highlighted.
  Delete any memory for good with one click.
- **Pause / Start watching:** stop the camera at any time. Scenes already captured still get remembered.
- **What Milo sees:** smooth live video with the USB webcam (the latest snapshot with the ESP32, which
  cannot stream while Milo watches), plus counts of frames checked, skipped and saved.

To open it from your phone on the same Wi-Fi, run `python server.py --lan` and browse to
`http://<your-pc-ip>:8000`. Anyone on your Wi-Fi can then see your memories, so only use it at home.

### Terminal tools

```powershell
python watch.py                                 # watch without the web app (Ctrl+C to stop)
python ask.py                                   # interactive questions
python ask.py --hours 72 "what was I working on?"
python observe.py                               # one manual snapshot, for testing
```

Don't run `watch.py` and `server.py` at the same time; both would use the camera.

## Camera endpoints

Both cameras serve the same two endpoints, so the rest of Milo does not care which one is used.

| Endpoint | USB webcam (`camera_server.py`) | ESP32 (`camera.ino`) |
| --- | --- | --- |
| `/` live view (MJPEG) | 30 fps, any number of viewers | a few fps, blocks the camera while open |
| `/capture` one JPEG | 640x480, quality 100, about 0.2 s | 640x480, quality 100, about 0.5 s + Wi-Fi |

Both send an `X-Timing` header on `/capture` with the camera-side time.

## Memory database

`memory.db` (SQLite), table `observations`:

| Column | Type | Content |
| --- | --- | --- |
| `id` | INTEGER | Auto-incrementing primary key |
| `timestamp` | TEXT | Local ISO time, e.g. `2026-09-25T18:14:16` |
| `summary` | TEXT | One or two sentences describing the scene |
| `location` | TEXT | Kind of place (kitchen, desk, street, ...) |
| `objects` | TEXT | JSON list of `{"name", "where"}` |
| `text_seen` | TEXT | JSON list of exact text strings |
| `dates` | TEXT | JSON list of `{"date", "about"}` |
| `activity` | TEXT | What I appear to be doing |

World model tables (all rebuilt from `observations` with `python -c "import world; world.rebuild()"`,
so they are safe to delete):

| Table | Content |
| --- | --- |
| `things` | One row per thing: name, first/last seen, times seen, name embedding |
| `sightings` | Each time a thing was seen: memory id, time, place, position, the name used, match score |
| `memory_vectors` | One search embedding per memory, used to pick relevant memories for a question |

Quick look at stored memories:

```powershell
python -c "import sqlite3; [print(r) for r in sqlite3.connect('memory.db').execute('SELECT id, timestamp, summary, text_seen, dates FROM observations')]"
```

## Testing

There is no automated test suite yet. Manual checks, in order:

1. **Stream:** open `http://<camera-ip>/` in a browser and see live video. Then close the tab.
2. **Snapshot:** `curl.exe -o test.jpg http://<camera-ip>/capture` gives a sharp 640x480 JPEG.
3. **Pipeline:** hold up a handwritten note with a date and run `python observe.py`.
   Pass if `text_seen` matches the note and `dates` picks up the date.
4. **Storage:** run the query above; each run adds one row.
5. **Watching:** run `python server.py`, open the web app, and walk around. New memories appear in
   the timeline (each about 15 seconds after the scene). Covering the lens raises "Skipped as blurry".
6. **Asking:** ask "what did I do in the last 10 minutes?" in the web app. The answer should match what
   you did and list its sources. Asking about something it never saw should get "I did not see it".

For Python changes, at minimum run `python -m py_compile observe.py watch.py ask.py server.py camera_server.py`.

## Known issues and gotchas

- **ESP32: why everything runs at 640x480.** The GC0308 has no JPEG encoder, so frames arrive as raw RGB565
  and are converted in software. The camera driver fixes the frame size at startup and drops any frame
  of a different size (`cam_hal: FB-SIZE: 153600 != 614400`), so switching resolution needs a slow,
  flaky camera restart (about 3 seconds). Fast captures matter more for a wearable than a smooth
  preview, so the camera stays at 640x480 and the stream is just choppier.
- **ESP32: the camera clock is 10 MHz, not the usual 20 MHz.** At 20 MHz the raw 640x480 frames arrive faster
  than the board can store them, so frames are silently dropped and a capture could wait up to 3 seconds
  for a complete one. At 10 MHz frames arrive steadily at 5 per second and captures never wait.
- **ESP32: Wi-Fi signal decides the rest.** The board side takes about 0.5 s; sending the ~60 KB photo takes
  0.2-4 s depending on signal (measured -65 to -70 dBm at the desk). Closer to the router is faster.
- **Compiler temp-file error on Windows.** If compiling fails with
  `cc1plus.exe: fatal error: @C:\Windows\TEMP\...: Invalid argument`, point TEMP/TMP at a
  user-writable folder for that shell, e.g. `$env:TEMP="$env:LOCALAPPDATA\arduino-tmp"; $env:TMP=$env:TEMP`.
- **The vision model can invent objects and places.** Exact text (`text_seen`) has been reliable in
  testing, but `objects` and `location` sometimes include things that are not there. Treat them as
  low-confidence. The world model flags things seen only once ("may be wrong").
- **Dates have no year.** "OCT3" is stored as written; resolving it to a full date is future world-model work.
- **Darkness looks like blur.** In a dark room frames come back nearly black and are skipped as
  blurry.
- **The model is the bottleneck.** About 4 memories per minute at most. On a busy walk the queue fills
  and the most redundant scenes are dropped; the timeline catches up once you slow down.
- **This PC has Intel Arc integrated graphics**, which reads new text at only ~100-150 tokens/s and
  cannot run two models at once reliably (the vision model crashed). Hence: one AI job at a time,
  questions first, a small pre-read answering model, and instant lookups. Big open-ended questions
  ("what was I doing around 9") can still take ~8 s.
- **Use `127.0.0.1`, not `localhost`, for Ollama on Windows.** `localhost` tries IPv6 first and cost
  about 2 s on every request (embeddings went from 2.1 s to 0.06 s after the fix).
- **Matching names is by meaning, with measured thresholds** (top of `world.py`). Same things scored
  0.885-0.98 and different things up to 0.873, so the grey zone 0.88-0.92 also requires the last word
  to match. Rare wrong merges or splits are possible; `world.rebuild()` re-runs everything after tuning.
- **The watcher learns its thresholds from what the camera sees** (`learn.py`). Until it has seen enough
  to be sure (a fresh start), it filters nothing, so the first minutes queue more scenes than later on.
  What it learned so far is shown in `/api/status` under `learned`.
- **Power bank life is about 3 hours** with the 5000 mAh (2750 mAh at 5V) bank.
- **Home Wi-Fi only.** Away from home the camera has no network. Buffering on the Raspberry Pi and
  syncing later is a future layer.
- **Finding the camera.** Windows looks up `milo.local` unreliably (it can fail with the Pi online), so
  Milo tries the name, then the last address that worked (saved in `memory.db`), then searches the local
  network for a camera (about 2 s, backing off while none is found). For your own commands (ssh), use the
  Pi's address, e.g. `192.168.2.23`, if `milo.local` fails.

## Privacy

This device points a camera at the world, and other people will sometimes be in view.

- All processing is local (camera -> my PC -> Ollama). No cloud services.
- `camera_server.py` listens on the network so the Raspberry Pi setup can work later; anyone on your
  home Wi-Fi who knows the address could open the live view.
- Only the most recent photo is kept on disk (`last_capture.jpg`, overwritten on every capture).
  Scenes waiting for the model are held in memory only. Memories are stored as structured text,
  not images or video.
- The web app listens only on this PC unless you start it with `--lan`. The Pause button stops the
  camera, and any memory can be deleted.
- Photos, the memory database, and Wi-Fi credentials are all gitignored and never leave the PC.
- Making it obvious when the camera is active, and handling consent for other people, are
  requirements for the continuous-observation phase.
