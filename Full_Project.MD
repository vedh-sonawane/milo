I want you to help me design and build a genuinely ambitious robotics + AI system. Before suggesting code, I want you to fully understand the project, its goals, constraints, hardware, and intended architecture.

## THE CORE IDEA

I want to build a small physical robot/system that acts like a persistent intelligence that exists in my environment.

This is NOT supposed to be:

* A generic smart-home system
* A camera that simply detects objects
* A dashboard with sensors
* A chatbot inside a robot
* A robot that only responds when I press a button
* A motivational assistant
* A gimmicky Arduino project
* Something that constantly talks for no reason

The goal is much more ambitious:

The system should continuously observe its environment, understand what is happening, build and maintain a memory of the world around it, answer questions about both the present and the past, notice changes and unfinished things, and eventually perform useful physical actions.

I want it to feel like a small persistent intelligence that lives with me.

For example, imagine I am working on a project.

The robot observes me working on it.

Twenty minutes later, I have moved on to something else.

The system could recognize that the project appears to have been started but not completed and remember that.

Later I could ask:

"What was I working on earlier?"

"What did I leave unfinished?"

"Where did I put the screwdriver?"

"What was on my desk this morning?"

"Did anything change on my desk?"

"What was that component I was using yesterday?"

"What projects have I started recently?"

"Where did I last see my calculator?"

"What am I currently working on?"

"Have I forgotten anything?"

And the system should combine its current visual understanding with its historical memory.

For example:

"Where is my screwdriver?"

It shouldn't just answer based on the current camera frame. It might reason:

"I don't currently see it, but the last time I saw it was on the desk beside the electronics kit about an hour ago."

The important part is that this should be a **world model**, not just a collection of disconnected images.

---

# THE BIG ARCHITECTURE

The basic concept is:

REAL WORLD
↓
ESP32-CAM
↓
OBSERVATION / PERCEPTION
↓
WORLD MODEL
↓
MEMORY + REASONING / AGENT
↓
PHYSICAL ACTION

There are several pieces of hardware involved, but I want them to function as ONE coherent system.

## 1. ESP32-CAM = EYES

I have an ESP32-CAM.

Its main job is visual observation.

It should be capable of continuously observing the environment, although "continuous" does NOT necessarily mean recording full-resolution video constantly.

We need to be smart about bandwidth, storage, processing power, and privacy.

Possible approaches include:

* Low-frequency image sampling
* Motion/change-triggered captures
* Low-resolution continuous observation
* Event-based snapshots
* Sending frames to another computer for heavier processing

The important thing is that the system should maintain awareness of what is happening rather than only taking a photo when I explicitly ask it to.

Potential visual capabilities:

* Object detection
* Object tracking
* OCR
* Reading labels/text
* Recognizing activities
* Detecting changes
* Recognizing projects/workspaces
* Understanding approximate object locations
* Detecting when something has appeared/disappeared
* Comparing current state with previous states

We should NOT assume the ESP32-CAM itself can perform sophisticated AI vision. It is primarily the camera/edge device.

---

# 2. RASPBERRY PI 5 = EDGE CONTROLLER

I have a Raspberry Pi 5 with only 1 GB of RAM.

This limitation matters.

I do NOT want to use the Pi as the long-term storage or heavy AI machine.

The Pi should instead act as the robot's local edge computer.

Its responsibilities could include:

* Receiving camera data
* Lightweight image processing
* Motion/change detection
* Temporary event processing
* Maintaining a small rolling buffer
* Communicating with the other hardware
* Controlling motors
* Reading sensors
* Handling local robot logic
* Sending useful observations to the main computer
* Detecting when the robot is near my home network
* Syncing information to my main computer

The Pi should NOT store months of raw video.

It should keep only a limited temporary buffer.

---

# 3. MY WINDOWS COMPUTER = LONG-TERM MEMORY + HEAVY AI BRAIN

My main computer has significantly more resources than the Pi.

This should be the long-term brain of the system.

It can handle:

* Long-term memory
* Database
* Embeddings/vector search if useful
* Heavy computer vision
* LLM reasoning
* Speech-to-text
* Text-to-speech
* Historical analysis
* World-model maintenance
* Complex agent decisions
* Querying memory
* Processing larger amounts of data

I want the computer to become the system's permanent memory.

Potentially something like:

SQLite/PostgreSQL
+
structured world model
+
embeddings
+
local AI models
+
vision processing
+
agent/reasoning layer

I already have experience with Python, JavaScript/TypeScript, FastAPI, SQLite, AI APIs, local models, etc., so the software can be fairly sophisticated.

I also have local AI models available through Ollama.

However, I do NOT want to unnecessarily overcomplicate the first version.

---

# THE IMPORTANT STORAGE IDEA

One of the most important design decisions I have made is:

THE ROBOT SHOULD NOT NEED LARGE LOCAL STORAGE.

When the robot is away from home:

ESP32-CAM
↓
Raspberry Pi
↓
temporary rolling memory

When it comes back into range of my home Wi-Fi:

Raspberry Pi detects the home network
↓
automatically connects to the main computer
↓
uploads useful observations/events
↓
computer integrates them into long-term memory
↓
Pi clears its temporary buffer

So the robot can move around without needing to permanently store everything onboard.

The computer is the permanent memory.

The Pi is basically a temporary edge cache.

Do NOT assume we should upload/store raw video unless there is a specific reason to. Prefer semantic/event-based information when possible.

---

# VOICE INTERACTION

Eventually I want to be able to talk to the system naturally.

For example:

Me:
"Where did I leave my calculator?"

System:
"I last saw it on your desk beside your notebook around 40 minutes ago."

Or:

Me:
"What was I working on yesterday?"

System:
"You spent time working on the electronics project and later switched to your school science assignment."

I want actual spoken responses, not just text.

However, I currently do NOT want to buy new hardware.

I may have usable wired microphones/speakers from old computers.

Therefore, voice does NOT need to be solved immediately.

For the first versions, the main computer can provide:

* Microphone
* Speaker
* Speech-to-text
* Text-to-speech

Later we can investigate putting a microphone/speaker directly into the physical robot using old hardware if possible.

A Bluetooth speaker is NOT a requirement.

---

# THE WORLD MODEL

This is one of the most important parts.

I don't want the system to simply store:

"Image at 4:32 PM."

I want it to gradually build a representation of what is happening.

For example:

OBJECTS

* screwdriver
* calculator
* notebook
* electronics kit
* laptop
* components

LOCATIONS

* desk
* bedroom
* work area
* shelf
* floor

ACTIVITIES

* coding
* electronics
* homework
* assembling something
* moving objects
* leaving the room

PROJECTS

* project A
* project B
* school assignment

EVENTS

* object appeared
* object moved
* project started
* project stopped
* something changed

RELATIONSHIPS

* screwdriver was beside electronics kit
* calculator was on desk
* user was working on project
* project used specific components

TEMPORAL INFORMATION

* first seen
* last seen
* duration
* previous locations
* changes over time

CONFIDENCE
The system should understand that its observations aren't always certain.

For example:

"I am 72% confident the object is the same screwdriver I saw earlier."

This doesn't necessarily have to literally be a percentage in the UI. The important concept is uncertainty.

The system should be able to update its beliefs when new evidence arrives.

---

# MEMORY

I want memory to be more than a database dump.

There should be different levels of memory.

For example:

SHORT-TERM MEMORY
What is happening right now?

RECENT MEMORY
What happened over the last few hours?

LONG-TERM MEMORY
What happened over days/weeks/months?

SEMANTIC MEMORY
What does the system know about objects, projects, places, etc.?

EPISODIC MEMORY
What actually happened during a particular event?

The exact architecture is something I want you to help determine.

---

# PROACTIVE BEHAVIOUR

This is another major requirement.

The robot should not always wait for me to ask questions.

It should be capable of noticing useful things.

For example:

I start working on something.

The system sees the activity.

I leave.

Twenty minutes later it notices the project is still sitting there unfinished.

It remembers that.

Later it might determine that the information is useful enough to surface.

Another example:

The system notices that an object that was normally on the desk is gone.

It doesn't immediately announce something useless.

Instead, it updates its world model.

If I later ask where the object is, it can use that information.

The system should be mostly silent.

It should NOT constantly interrupt me.

The agent needs some concept of:

"Is this actually worth doing/saying?"

---

# PHYSICAL ACTIONS

I also want this to eventually have a physical body/capability.

I have:

* SG90 servo
* 28BYJ-48 stepper motor + ULN2003 driver
* Small DC motor
* Arduino UNO
* ESP32
* Raspberry Pi
* Sensors from my ELEGOO starter kit
* 12V DC gearmotor with encoder
* Bench power supply
* Various mechanical/electronic components

I do NOT want to attach motors just because "robots need motors."

The physical action needs to solve a real problem.

Potential actuator capabilities include:

SG90:

* Small flap
* latch
* pointer
* lever
* switch
* small mechanism

28BYJ-48:

* Rotating mechanism
* Selector
* Carousel
* Indexed positioning
* Small positioning mechanism

DC motor:

* Roller
* Simple moving mechanism
* Retraction mechanism
* Other mechanical movement

The exact physical mechanism has NOT been decided yet.

I want us to first discover a genuinely useful behaviour that benefits from physical action.

Then design the mechanism around that.

---

# HARDWARE I CURRENTLY HAVE

Here is the complete relevant inventory.

## Computing

* Raspberry Pi 5, 1 GB RAM
* ESP32-WROOM-32 development board
* Freenove ESP32-CAM
* Windows computer with 32 GB RAM
* Old computers that can potentially be salvaged for parts

## Arduino/Electronics

ELEGOO UNO R3 starter kit including:

* Arduino UNO R3
* 28BYJ-48 stepper motor
* ULN2003 stepper driver
* SG90 servo
* Small DC motor
* L293D motor driver chip
* HC-SR04 ultrasonic sensor
* DHT11 temperature/humidity sensor
* Photoresistors
* Thermistor
* Tilt switch
* IR receiver + remote
* Joystick
* Rotary encoder
* Keypad
* LCD1602
* 4-digit 7-segment display
* 8x8 LED matrix
* Buzzers
* LEDs
* Resistors
* Capacitors
* Diodes
* Transistors
* Potentiometers
* Breadboard
* Jumper wires
* Battery holder

## Other hardware

* NICE-POWER 0-30V / 5A bench power supply
* 12V DC gearmotor with encoder
* Various old computers/components
* Hot glue
* Duct tape
* Cardboard
* Scissors

I also have a NEMA 17 stepper motor and TMC2209 driver, but they have not arrived yet, so DO NOT make the initial design depend on them.

---

# HARD CONSTRAINT

I am NOT buying new hardware right now.

Design the initial system around what I already own.

If something would be useful later, you can identify it as a future upgrade, but the MVP must work without purchasing anything.

---

# ONE PHYSICAL SYSTEM

I don't want this to become:

"Camera over here + Raspberry Pi over there + random Arduino project + separate smart speaker."

I want the end result to feel like ONE robot/system.

Conceptually:

┌─────────────────────────────┐
│       PHYSICAL ROBOT        │
│                             │
│  ESP32-CAM → EYES           │
│  Raspberry Pi → EDGE BRAIN  │
│  ESP32/UNO → BODY CONTROL   │
│  Motors → ACTION            │
│  Sensors → ENVIRONMENT      │
└──────────────┬──────────────┘
│
│ Wi-Fi
▼
┌─────────────────────────────┐
│       HOME COMPUTER         │
│                             │
│ Long-term memory            │
│ Heavy vision                │
│ LLM reasoning               │
│ World model                 │
│ Voice interface             │
│ Agent                       │
└─────────────────────────────┘

The physical robot should be small enough to actually exist as a compact device.

## FORM FACTOR (DECIDED)

The device is WEARABLE. It lives on my shoulder and goes wherever I go.

It is NOT a fixed device on a desk, because not much happens on a desk. The interesting context (projects, objects, places, people, what I leave behind) happens as I move around.

This means:

* The camera view is egocentric and constantly moving, so locations must be inferred from the scene, not from a fixed camera position.
* Motion blur and bad angles are normal, so sampling and quality filtering matter.
* It runs on portable power (battery / power bank), so power budget matters.
* It is often away from home Wi-Fi, so the Pi's temporary buffer and home sync are central, not optional.
* Other people will regularly be in view, so privacy and consent matter even more.
* Any physical action must be small, light, and safe to wear.

---

# PRIVACY / DATA HANDLING

Because this system involves continuous visual observation, privacy needs to be designed into the architecture.

I don't want months of raw video sitting around.

Prefer:

camera
→ perception
→ useful events/structured observations
→ memory

rather than:

camera
→ permanently record everything

The system should also be designed so that it is obvious when the camera is active.

If other people could be captured, we need to think about appropriate consent/privacy rather than treating continuous recording as automatically acceptable.

This is primarily a local system. I want as much processing as reasonably possible to stay local.

---

# WHAT I DON'T WANT

Do NOT turn this into a generic IoT project.

Do NOT give me a list of 30 unrelated features.

Do NOT immediately tell me to buy:

* microphones
* Bluetooth speakers
* cameras
* sensors
* cloud subscriptions
* expensive AI hardware

Do NOT start by writing hundreds of lines of code.

Do NOT build the physical robot first and figure out its purpose later.

Do NOT make motors exist purely for demonstration.

Do NOT make the system dependent on constantly pressing buttons.

Do NOT make it a glorified object detector.

Do NOT assume the Raspberry Pi 5 with 1 GB RAM can run huge vision models locally.

Do NOT treat "continuous observation" as synonymous with "store full-resolution video forever."

---

# THE BIGGER VISION

The end goal is something closer to:

A persistent local intelligence with eyes, memory, reasoning, and physical capabilities.

It observes the environment.

It understands what is happening.

It remembers.

It connects events over time.

It maintains a model of objects, places, activities, projects, and changes.

It can answer questions about the present and past.

It can notice things without being asked.

It can eventually decide when a physical action is useful.

And it should improve as more observations accumulate.

The important part is that the system becomes more useful because it has CONTEXT.

It doesn't just know:

"There is a screwdriver."

It knows:

"There is a screwdriver."

Then:

"The screwdriver was previously on the desk."

Then:

"It was being used during an electronics project."

Then:

"It disappeared from the desk."

Then:

"The user later asked where it was."

That is the type of intelligence/context I am trying to build.

---

# HOW I WANT YOU TO HELP

Do NOT jump straight into implementation.

First analyze the entire system and tell me:

1. What should the actual architecture be?

2. Which device should be responsible for each task?

3. What should run on the ESP32-CAM?

4. What should run on the Raspberry Pi?

5. What should run on the Windows computer?

6. How should the devices communicate?

7. How should the temporary Pi memory and permanent computer memory work?

8. What should the world-model/database schema conceptually look like?

9. How should observations become memories?

10. How should the system determine that two observations refer to the same object/event?

11. How should uncertainty/confidence work?

12. How should current perception and historical memory be combined when answering questions?

13. How should the proactive agent decide when something is worth mentioning or acting on?

14. What should the first physical capability eventually be, and what useful problem should it solve?

15. What are the biggest technical limitations of this architecture?

16. What parts of my idea are unrealistic or need to be changed?

17. What is the smallest version that would already feel like the core idea?

MOST IMPORTANTLY:

Tell me how you would break this enormous project into concrete phases.

I want an actual development roadmap.

For example, think in terms of:

PHASE 0 - architecture/proof of concept
PHASE 1 - basic camera observation
PHASE 2 - event extraction
PHASE 3 - temporary Pi memory
PHASE 4 - Wi-Fi synchronization
PHASE 5 - long-term memory
PHASE 6 - world model
PHASE 7 - querying
PHASE 8 - proactive intelligence
PHASE 9 - physical actions
etc.

But do NOT blindly use those phases. Create the phases you actually think make technical sense.

For each phase, explain:

* What we build
* Which hardware/software is involved
* What the input is
* What the output is
* What "done" means
* How we test it
* What we should NOT build yet

I want the roadmap to let us build this incrementally so that after every phase we have a working system rather than waiting months for the complete robot.

Finally, tell me:

**WHAT SHOULD WE BUILD FIRST?**

Give me the first concrete milestone that we can start implementing immediately with the hardware I already own.

Do not give me code yet unless it is genuinely necessary to explain the architecture.

Think like a senior robotics engineer + AI systems architect helping me turn a very ambitious idea into a real, buildable system.

---

# ROADMAP (AGREED 2026-09-28)

This is the working plan. Each step ends with something that works and has been tested.

## END GOAL

A shoulder-worn companion that sees what I see, remembers it, understands it (things, places, projects, deadlines, and how they change), answers by voice within a couple of seconds, notices what matters and otherwise stays quiet, keeps working away from home, and does a few genuinely useful physical things. Everything runs locally on hardware I already own.

## RULES FOR EVERY STEP

* No hardcoding behaviour. Prefer things Milo learns from its own memories and from my feedback over fixed word lists, regex rules, or magic numbers. Measured calibration values are acceptable only if they are documented and re-tunable.
* Image quality stays at maximum (640x480, JPEG quality 100). Speed has to come from somewhere else.
* Everything must be fast: answers within about 5 seconds, a smooth live view, and quick recovery after power or Wi-Fi loss.
* Local only. No cloud services. Store memories, not footage.
* Never commit or push for me; I do that myself.
* Every feature, physical ones included, must actually help me. Each step states the helpful outcome up front ("I stop forgetting my calculator on test days") and is tested against that outcome, not just "it runs". Anything that doesn't measurably help gets changed or removed.

## DONE

1. Photo to memory: camera, local vision model (qwen2.5vl:7b), SQLite.
2. Watching on its own: fast capture, frames grouped into scenes, blur and duplicate skipping, background describing. Built for walking, not holding still.
3. Asking plus a web app: answers with sources, memory timeline, pause, delete, live view.
4. World model v1: things, sightings, "last seen where", and how sure each match is.
5. Wearable camera: Raspberry Pi 5 with a USB webcam (Ubisoft Wii camera) on a power bank. Starts automatically, streams about 30 fps over Wi-Fi, recovers by itself after reboots. Wi-Fi power saving is off.
6. Speed pass: lookups answer instantly, other questions in about 2.5 to 4.5 seconds. One AI job runs at a time, and questions go first.

## NEXT

7. Remove the hardcoding (laptop only)
   * DONE: question types learned from example questions instead of regex rules (router.py, intents.json). Helpful outcome: my own wordings get instant answers. Measured on 30 unseen questions: 26 right, 0 wrong instant answers (old rules: 18 right, 3 wrong).
   * Places learned by grouping memories that look alike; I can name a place once in the UI.
     Finding (2026-09-28): the memories' text alone cannot separate places (memories a minute apart
     scored 0.875 similar, memories hours apart 0.826, with heavy overlap), so this needs a visual
     fingerprint of each photo (an image-embedding model; the photo itself is still not stored).
     Needs the camera (Pi or laptop webcam) to test.
   * What is worth tracking decided by the model, not a word list.
   * Match thresholds that retune themselves when I correct a merge.
   * DONE: the watcher's thresholds (unusable frame, new scene, real stay vs glance) are learned from what
     the camera sees (learn.py: the data decides how many groups there are; a split is only used when the
     groups are clearly separate, otherwise nothing is filtered). Finding: one global blur cutoff can't work
     because sharpness depends on the scene, so ordinary blur is handled by keeping each scene's sharpest
     frame and only clearly unusable frames (covered lens, darkness) are skipped.

8. Memory levels (laptop only)
   * Episodes: stretches of activity ("9:00 to 9:40 PM, electronics at the desk").
     Finding (2026-09-28): on the current memories (mostly camera testing) no approach gave useful
     episodes; the big reasoning model (gpt-oss:20b) did best but evicts the fast models. Needs 1-2 hours
     of real wearing data first; then build it to run only while Milo is idle.
   * Projects: recurring episodes with the same things become a project with a history.
   * Nightly consolidation: old raw memories are summarised into episodes and project notes, so memory stays small and fast over months.
   * This covers short-term, recent, long-term, episodic and semantic memory.

9. Milo notices things (laptop only)
   * DONE: real deadlines ("Science project due Sat Oct 3, in 5 days") in a Coming up panel and in answers. Dates are read by the model, checked against the written text, and computed with calendar math (no date rules). tests/deadline_eval.py 10/10; answers 33/33.
   * Changes ("calculator moved"), unfinished projects, and things that are usually around but now missing.
   * Importance learned from my reactions: it starts cautious, and every dismissal or "useful" teaches it what is worth saying. No fixed scoring rules.
   * Shown quietly in a "Milo noticed" panel. Nothing interrupts me.

10. Voice (laptop microphone and speaker first)
    * Local speech-to-text and text-to-speech: "Milo, where is my calculator?" answered out loud.
    * Later: a microphone and speaker on the Pi, salvaged from old laptops.

11. Away from home (Pi)
    * Without home Wi-Fi, the Pi keeps only sharp, distinct scenes in a size-capped buffer on its SD card (oldest dropped first).
    * Back home it uploads them, the PC turns them into memories, and the Pi deletes its copies.

12. Privacy and consent (Pi plus kit parts)
    * An LED that is on whenever the camera is recording, and a physical pause button.
    * Blur other people's faces before anything is stored, retention limits, one-tap delete.

13. Physical actions (each one checked against weeks of real memories before it is built)
    On the shoulder (must stay light):
    * Silent shoulder tap (small DC motor or SG90): different patterns for "deadline soon", "you're leaving something behind", "camera blocked". Gets my attention without speaking.
    * Privacy shutter (SG90): a flap physically covers the lens on pause, in bathrooms, or when someone asks. Other people can see it is covered.
    * "Look at that" camera aim (SG90 pan): turns toward what I'm looking at or pointing at, or toward something half-seen at the edge of the frame. Enables "Milo, read that" and gives better memories.
    At home (on a dock, where heavier motors are fine):
    * Watch-station dock (28BYJ-48 turntable): when I take Milo off, it slowly scans the desk or room to keep the world model current, and charges there.
    * Glance display (LCD1602 or 8x8 LED matrix): silently shows the one thing that matters most right now ("Science proj: FRI").
    * Grab-and-go door tray (SG90 latch or 12V gearmotor): pops open with a light when there is something I need to take today.
    Suggested order: tap and shutter first, then camera aim, then the dock and display, then the door tray.

## WORKING ORDER (FROM 2026-09-28, HARDWARE AVAILABLE AGAIN)

1. Quick hardware wins: Pi desktop turned off (faster start), check camera, Wi-Fi fix and auto-start after a power cycle.
2. A real wearing session (1-2 hours of a normal evening, a couple of notes with dates). The most important step: everything after it is tuned on real data instead of camera tests.
3. Things that need that real data: episodes, projects, learned places (visual fingerprints), faster photo descriptions (only if text reading stays as good).
4. Milo learns from me: corrections on the page (removes the last hardcoded lists and numbers), and the "Milo noticed" panel that learns from what I dismiss.
5. Voice on the laptop's microphone and speaker.
6. Away from home: the Pi buffers scenes without home Wi-Fi and uploads them later.
7. Privacy: recording LED and pause button on the Pi (kit parts).
8. Physical actions: shoulder tap, lens shutter, camera aim, then the home dock and door tray.

Done (2026-09-29): instant record. Every kept scene is stored at once with its text (Windows OCR, ~20 ms), so nothing waits for the vision model; the full description fills in later. From the first wearing session: descriptions of real scenes take 25-40 s each, so this was needed.

Ongoing: speed (a smaller vision model only if it reads text equally well), tests, reliable restarts, and backups of memory.db.
Done along the way: answers stream (first words ~3 s); older related memories are included so they are answered, not made up; answer accuracy measured at 27/27 (tests/answer_eval.py).
