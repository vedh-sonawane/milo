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
| Camera firmware: live stream + high-quality snapshot over Wi-Fi | Done |
| One photo -> one structured memory in SQLite (local vision model) | Done |
| Continuous / event-based observation | Not started |
| Raspberry Pi edge cache + sync to PC when home | Not started |
| World model (objects, places, projects, changes over time, confidence) | Not started |
| Querying memory (text, then voice) | Not started |
| Proactive behaviour ("is this worth saying?") | Not started |
| Physical action | Not started (mechanism will be chosen after a real use case is found) |

## How it works today

```
ESP32 camera (shoulder)                          Windows PC
+-------------------------+    Wi-Fi / HTTP     +--------------------------------------+
| GC0308 sensor           |  GET /capture  ->   | observe.py                           |
| RGB565 -> JPEG (sw)     |  <- 640x480 JPEG    |   -> Ollama qwen2.5vl:7b (local)     |
| /  = MJPEG live stream  |                     |   -> structured JSON                 |
| /capture = one snapshot |                     |   -> SQLite memory.db                |
+-------------------------+                     +--------------------------------------+
```

1. `observe.py` asks the camera for one high-quality snapshot.
2. The photo is sent to a local vision model through Ollama. Nothing leaves the PC.
3. The model returns JSON: summary, location, objects, exact text seen, dates, activity.
   It is told not to guess blurry text.
4. The result is stored as one row in `memory.db` with a timestamp.

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
camera/
  camera.ino           ESP32 camera firmware (stream + snapshot web server)
  secrets.example.h    Template for Wi-Fi credentials
  secrets.h            Your real credentials (gitignored, you create it)
observe.py             Snapshot -> local vision model -> SQLite memory
Full_Project.MD        Full vision, constraints, hardware inventory
AGENTS.md              Instructions for AI coding assistants working in this repo
```

Runtime files that stay on the PC and are gitignored: `memory.db`, `last_capture.jpg`.

## Hardware

- Freenove ESP32-WROVER CAM with built-in CH340 USB (shows up as a COM port)
- Camera sensor: GC0308, max 640x480, **no hardware JPEG**
- A 2.4 GHz Wi-Fi network
- A Windows PC running [Ollama](https://ollama.com) with the `qwen2.5vl:7b` model

## Setup

### 1. Wi-Fi credentials

Credentials are never stored in tracked files.

```powershell
copy camera\secrets.example.h camera\secrets.h
```

Edit `camera/secrets.h` and fill in your network name and password. The file is gitignored.

### 2. Flash the camera

Requires [arduino-cli](https://arduino.github.io/arduino-cli/) with the `esp32:esp32` core.

```powershell
arduino-cli core install esp32:esp32
arduino-cli compile --fqbn esp32:esp32:esp32wrover:PartitionScheme=huge_app camera
arduino-cli upload  --fqbn esp32:esp32:esp32wrover:PartitionScheme=huge_app -p COM4 camera
```

Replace `COM4` with your board's port. Open a serial monitor at 115200 baud and press EN/RST;
the board prints its stream and capture URLs.

### 3. Run an observation

```powershell
pip install requests
ollama pull qwen2.5vl:7b
python observe.py <camera-ip>
```

To avoid typing the IP every time, set it once:

```powershell
setx CAMERA_IP <camera-ip>
```

Then open a new terminal and just run `python observe.py`.

## Camera endpoints

| Endpoint | Returns | Notes |
| --- | --- | --- |
| `/` | MJPEG live stream | 320x240 (QVGA), JPEG quality 50. Good for aiming the camera. |
| `/capture` | One `image/jpeg` | 640x480 (VGA), JPEG quality 90. Takes about 3 seconds. |

**The camera serves one client at a time.** Close the live stream tab before calling `/capture`
or running `observe.py`, otherwise the request will hang.

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

Quick look at stored memories:

```powershell
python -c "import sqlite3; [print(r) for r in sqlite3.connect('memory.db').execute('SELECT id, timestamp, summary, text_seen, dates FROM observations')]"
```

## Testing

There is no automated test suite yet. Manual checks, in order:

1. **Stream:** open `http://<camera-ip>/` in a browser and see live video. Then close the tab.
2. **Snapshot:** `curl.exe -o test.jpg http://<camera-ip>/capture` gives a sharp 640x480 JPEG (about 45 KB).
3. **Pipeline:** hold up a handwritten note with a date and run `python observe.py`.
   Pass if `text_seen` matches the note and `dates` picks up the date.
4. **Storage:** run the query above; each run adds one row.

For Python changes, at minimum run `python -m py_compile observe.py`.

## Known issues and gotchas

- **Why `/capture` restarts the camera.** The GC0308 has no JPEG encoder, so frames arrive as raw
  RGB565 and are converted in software. The camera driver fixes the frame size at startup and drops
  any frame of a different size (`cam_hal: FB-SIZE: 153600 != 614400`), so the resolution cannot be
  changed at runtime. `/capture` shuts the camera down, restarts it at VGA, takes the shot, then
  restarts it at QVGA for streaming.
- **Compiler temp-file error on Windows.** If compiling fails with
  `cc1plus.exe: fatal error: @C:\Windows\TEMP\...: Invalid argument`, point TEMP/TMP at a
  user-writable folder for that shell, e.g. `$env:TEMP="$env:LOCALAPPDATA\arduino-tmp"; $env:TMP=$env:TEMP`.
- **The vision model can invent objects and places.** Exact text (`text_seen`) has been reliable in
  testing, but `objects` and `location` sometimes include things that are not there. Treat them as
  low-confidence until the world-model layer adds cross-checking over time.
- **Dates have no year.** "OCT3" is stored as written; resolving it to a full date is future world-model work.
- **The camera IP can change** if the router hands out a new address. Check the serial monitor.

## Privacy

This device points a camera at the world, and other people will sometimes be in view.

- All processing is local (ESP32 -> my PC -> Ollama). No cloud services.
- Only the most recent photo is kept on disk (`last_capture.jpg`, overwritten every run). Memories are
  stored as structured text, not images or video.
- Photos, the memory database, and Wi-Fi credentials are all gitignored and never leave the PC.
- Making it obvious when the camera is active, and handling consent for other people, are
  requirements for the continuous-observation phase.
