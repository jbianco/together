# Together

Live classroom questions, Mentimeter-style, running on your own computer.
Students join by scanning a QR code or opening a link. Nobody needs to create an account.

- **Online class** (`--public`): a free public `https://…` URL is created and students can join
  from anywhere.
- **In-person class**: without `--public`, students must be on the same WiFi network.

## Interface language

The presenter screen and the students' pages (browser and phone) can be shown in **English**,
**Spanish** or **Portuguese**. Set it in the presentation file:

```json
{"title": "…", "language": "es", "time": 30, "slides": […]}
```

or when starting the program, which takes priority over the file:

```bash
.venv/bin/python together.py --lang pt
```

Supported values: `en` (default), `es`, `pt`. This only changes the interface (buttons, hints,
messages, errors). The slide texts are written in the presentation file in whatever language you
like.

All interface texts live in `static/i18n/<lang>.json`. To fix a translation, edit that file. To add
another language, copy `en.json` to e.g. `fr.json`, translate the values (keep the keys and the
`{placeholders}`) and add the code to `LANGUAGES` in `together.py`. Any key missing from a
translation falls back to English.

## Online class (public URL)

```bash
.venv/bin/python together.py --public         # Windows: .venv\Scripts\python together.py --public
```

The terminal shows the URL for students. The presenter's **Copy link** button copies it too, so
you can paste it into the Zoom, Meet or Teams chat. The QR code on the shared screen also points
to that URL.

The public URL comes from a **free tunnel**: your computer keeps running the program and the
service forwards visits to it. The program tries two options, in this order:

1. **Cloudflare Tunnel** (recommended, more stable). No account needed, but you have to install `cloudflared` once:
   - Windows: `winget install --id Cloudflare.cloudflared`
   - Mac: `brew install cloudflared`
   - Linux: download the `.deb` or the binary from https://github.com/cloudflare/cloudflared/releases
2. **localhost.run**: nothing to install, it only uses the `ssh` command, which ships with Linux,
   Mac and Windows 10/11. It drops more often.

To pick one by hand: `--public --tunnel cloudflared` or `--public --tunnel ssh`.

Keep in mind:
- **The URL changes every time you start the program.** Share it in the chat when class starts.
- If the tunnel drops, the program reopens it on its own and updates the QR code, but the new
  URL is different. You'll need to share it in the chat again. No answers are lost.
- Your computer has to stay on and connected for the whole class.
- Anyone with the URL can answer, but only you can change slides (the presenter link has a key
  and does not go through the tunnel).

## Installation (once)

You need Python 3.8 or newer.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
```

`segno` is the only dependency and draws the QR code. Without segno the program still works, but
only shows the address as text.

## In-person class

```bash
.venv/bin/python together.py                  # Windows: .venv\Scripts\python together.py
```

1. The browser opens the **presenter view**: project it and press `F` for full screen.
2. Students scan the QR code, or type the address shown below it.
3. Move forward with `→`, the space bar or `Page Down`. A slide clicker works too.
   Go back with `←` or `Page Up`.
4. Each question has a **countdown** (30 s by default). While it runs, the screen shows the
   statements, how many have answered ("12 of 17 answered") and the names of those who already
   answered. Results stay hidden so nobody is influenced.
5. Results are revealed with an animation when **everyone has answered** (with confetti and an
   "Everyone answered!" toast) or when **time is up**. No more answers are accepted after that.
6. Shortcuts: `H` shows or hides results early, `T` restarts the countdown and `+` adds 15 seconds.

"Everyone" means the people connected at that moment who have already entered their name.

Before answering, each student enters their **full name**. It is saved in their browser and they
can change it with "change".

Answers are saved automatically to two files:
- `results-presentation.csv`: one row per student, with their name and one column per question.
  Opens in Excel or Google Sheets.
- `results-presentation.json`: all the data, which the program uses to resume. If you close the
  program and start it again, answers are kept. To start from scratch, use `--reset`.

The presenter link includes a key (`?k=...`) that changes every time you start the program.
**Don't share it**: that link controls the presentation.

### Options

| Option | What it does |
|---|---|
| `other.json` | Use another slides file, e.g. for class 2 |
| `--lang es` | Interface language: `en`, `es` or `pt` (overrides `"language"` in the file) |
| `--public` | Create a public URL for online classes |
| `--time 45` | Seconds to answer each question (`0` = no limit) |
| `--port 8080` | Change the port (8000 by default) |
| `--ip 192.168.0.10` | Force the IP shown in the QR code, if auto-detection picked the wrong network |
| `--reset` | Ignore saved answers |
| `--key abc` | Use a fixed presenter key |

## Class 1 flow

| # | Slide | When |
|---|---|---|
| 1 | Cover with a large QR code | While students join |
| 2 | «¿Hasta dónde llegaste con los temas de la clase?» (single choice) | At the start |
| 3 | «¿Qué tema te generó más dudas?» (single choice) | At the start |
| 4, 6, 8, 10 | Tema N: «¿qué sabés hoy?» (seen it? / understand it?) | **Before** explaining each topic |
| 5, 7, 9, 11 | Tema N: «¿y ahora?» (understand it? / improved?) | **After** explaining it |
| 12 | Word cloud for the class | At the end |

Each statement shows the answer **distribution**: bars with the % of students who chose each
value from 1 to 5, with a smoothed curve on top. Hovering over a bar shows the exact count. On the
«¿y ahora?» slides, the «Entiendo…» statement adds the "before the explanation" curve as a dashed
gray line, so you can see how far it moved to the right.

## Editing or creating presentations

Copy `presentation.json` and edit it. There are four slide types: `content`, `scale` (1 to 5),
`wordcloud` and `choice` (pick one; results show as horizontal bars with the % and number of
people):

```json
{"id": "cover", "type": "content", "title": "…", "text": "…", "show_qr": true}

{"id": "t1-before", "type": "scale", "title": "…",
 "statements": [{"text": "I've seen it"}, {"text": "I understand it"}],
 "min": 1, "max": 5, "min_label": "Not at all", "max_label": "A lot"}

{"id": "t1-after", "type": "scale", "title": "…",
 "statements": [{"text": "I understand it", "compare_with": {"id": "t1-before", "statement": 1}}]}

{"id": "cloud", "type": "wordcloud", "title": "…", "max_words": 3}

{"id": "doubts", "type": "choice", "title": "Which topic raised the most questions?",
 "hint": "Choose just one.", "options": ["Topic 1", "Topic 2", "Topic 3"]}
```

Set the time with `"time": 30` at the top of the file, for every question, or inside a slide, for
that slide only. In Class 1, the word cloud has 60 s. The `--time` option overrides the general
value.

Each `id` must be unique. In `compare_with`, `statement` is the position within the referenced
slide, counting from 0.

## If phones can't connect (in-person class)

- **Windows Firewall:** the first time, Windows asks whether to allow Python on the network.
  Allow it on *private networks*. If the prompt didn't appear, allow Python under
  "Windows Firewall → Allow an app".
- **University WiFi:** many institutional networks don't let devices see each other. The simplest
  fix is to share internet from your phone (hotspot), connect the computer and the students to
  that network, and start the program again.
- **Wrong IP in the QR code** (for example with a VPN or several network adapters): find your IP
  with `ipconfig` (Windows) or `ip addr` (Linux) and use `--ip`.
