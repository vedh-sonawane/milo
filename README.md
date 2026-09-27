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

The full vision, constraints, and hardware inventory are in [Full_Project.MD](Full_Project.MD).

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

1. **Capture, every second.** Each frame gets a sharpness score. Blurry or dark frames are skipped.
2. **Scenes.** Frames that look alike are grouped into a scene, and only the sharpest one is kept,
   so a clear frame wins over a blurry step. A scene is queued once it has been steady for 4 seconds,
   or when the view changes (a glance while walking still counts). Glancing back at a view queued in
   the last 2 minutes does not create a duplicate. A long stay in one place is re-remembered every
   10 minutes so Milo knows how long you were there.
3. **Queue.** Scenes wait for the model. If you move faster than it can keep up, the queue holds 30
   and drops the most redundant scene first, so a walk still ends up as a spread of distinct memories.
4. **Describe, in the background.** The local vision model (Ollama, nothing leaves the PC) returns
   JSON: summary, location, objects, exact text seen, dates, activity. It is told not to guess blurry
   text or invent objects. Each memory is stored with the time the photo was **taken**.
5. **World model.** Every object in a new memory becomes a *sighting* of a *thing*. Names that mean the
   same (checked with the small local `nomic-embed-text` model) are the same thing: "black calculator"
   and "calculator" merge, "pen holder" and "pen" do not. Each sighting keeps when, where, and how sure
   the match was. People and body parts are never tracked as things.
6. **Ask.** Questions are answered only from stored memories and things, with the memories used as sources.
   Only the memories and things most related to the question (plus the latest few) are sent to the
   model, so answers stay fast however many memories pile up.
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
observe.py             One snapshot -> vision model -> memory; shared helpers
Full_Project.MD        Full vision, constraints, hardware inventory
AGENTS.md              Instructions for AI coding assistants working in this repo
```

Runtime files that stay on the PC and are gitignored: `memory.db`, `last_capture.jpg`.

## Hardware

- **Camera (recommended):** any USB webcam. Tested with the Ubisoft Wii camera (640x480, 30 fps).
  It plugs into this PC for now and into the Raspberry Pi later, when Milo is worn.
- **Camera (fallback):** Freenove ESP32-WROVER CAM (GC0308 sensor, 640x480, no hardware JPEG, so only
  a few frames per second). Wireless on its own, but not smooth.
- A Windows PC running [Ollama](https://ollama.com) with the `qwen2.5vl:7b` model

## Setup

```powershell
pip install requests pillow fastapi uvicorn opencv-python
ollama pull qwen2.5vl:7b
```

### USB webcam

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
setx CAMERA_IP localhost:8081     # webcam; or the ESP32's address
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
- **Answers take about 20-25 seconds** on this PC (Intel Arc integrated graphics reads about 80 tokens
  per second). Describing new photos pauses while a question is answered, so questions go first.
- **Matching names is by meaning, with measured thresholds** (top of `world.py`). Same things scored
  0.885-0.98 and different things up to 0.873, so the grey zone 0.88-0.92 also requires the last word
  to match. Rare wrong merges or splits are possible; `world.rebuild()` re-runs everything after tuning.
- **Tuning values are first guesses** (top of `watch.py`), measured on a few photos. Tune them after
  real wearing.
- **Home Wi-Fi only.** Away from home the camera has no network. Buffering on the Raspberry Pi and
  syncing later is a future layer.
- **The camera IP can change** if the router hands out a new address. Check the serial monitor.

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
