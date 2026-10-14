#!/usr/bin/env python3
"""Together: live classroom questions with a QR code, no external services needed.

Usage:
    python together.py                      # uses presentation.json
    python together.py other.json --port 8080

Students join from their phones (same WiFi network) by scanning the QR code,
or from anywhere with --public.
"""
import argparse
import csv
import io
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import segno
except ImportError:
    segno = None

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
LANGUAGES = ("en", "es", "pt")  # interface languages, one file each in static/i18n/
TYPES = {"content", "scale", "wordcloud", "choice"}
MAX_VOTERS = 1000  # per slide, to limit abuse through the public URL

# Free tunnels used to get a public URL: (program, command, URL pattern)
TUNNELS = [
    ("cloudflared",
     lambda p: ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{p}"],
     r"https://[a-z0-9-]+\.trycloudflare\.com"),
    ("ssh",
     lambda p: ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
                "-o", "ExitOnForwardFailure=yes", "-R", f"80:localhost:{p}", "nokey@localhost.run"],
     r"https://[a-z0-9-]+\.lhr\.life"),
]


def local_ip():
    """This computer's IP on the local network (sends no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def load_messages(lang):
    """Interface texts for a language, falling back to English for any missing key."""
    messages = json.loads((STATIC / "i18n" / "en.json").read_text(encoding="utf-8"))
    if lang != "en":
        messages.update(json.loads((STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8")))
    return messages


def normalize(word):
    w = re.sub(r"[^\w\s.+#-]", "", word.strip().lower())
    return re.sub(r"\s+", " ", w).strip()[:30]


class State:
    def __init__(self, presentation, results_file, reset=False, time_limit=None):
        self.lock = threading.Lock()
        self.title = presentation.get("title", "Presentation")
        self.slides = presentation["slides"]
        # Seconds to answer: --time > the presentation's "time" > 30. 0 = no limit.
        self.time_limit = time_limit if time_limit is not None else presentation.get("time", 30)
        self.index = 0
        self._start_slide()
        self.file = results_file
        self.answers = {s["id"]: {} for s in self.slides}  # id -> voter -> answer
        self.seen = {}  # voter -> last request (to count who is connected)
        self.names = {}  # voter -> name they typed
        if self.file.exists() and not reset:
            saved = json.loads(self.file.read_text(encoding="utf-8"))
            self.names = saved.get("names", {})
            for sid, answers in saved.get("answers", {}).items():
                if sid in self.answers:
                    self.answers[sid] = answers

    @property
    def current(self):
        return self.slides[self.index]

    def _start_slide(self):
        s = self.current
        self.started = time.time()
        self.duration = 0 if s["type"] == "content" else s.get("time", self.time_limit)
        self.revealed = False  # the presenter can show results before time is up

    def _numbering(self):
        """(number of this question, total questions), not counting content slides."""
        questions = [i for i, x in enumerate(self.slides) if x["type"] != "content"]
        number = sum(1 for i in questions if i <= self.index)
        return number, len(questions)

    def _remaining(self):
        """Seconds left to answer (None = no limit)."""
        if not self.duration:
            return None
        return max(0.0, self.started + self.duration - time.time())

    def _participation(self):
        """(answered, expected, names of who answered) on the current slide."""
        now = time.time()
        answered = self.answers[self.current["id"]]
        present = {v for v, t in self.seen.items() if now - t < 8 and v in self.names}
        expected = present | set(answered)
        names = [self.names.get(v, "") for v in answered]
        return len(answered), len(expected), names

    def _save(self):
        data = {
            "title": self.title,
            "saved": datetime.now().isoformat(timespec="seconds"),
            "summary": {s["id"]: self._results(s) for s in self.slides if s["type"] != "content"},
            "names": self.names,
            "answers": self.answers,
        }
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.file)
        self._save_csv()

    def _save_csv(self):
        """One row per student, one column per statement (to open in Excel or Sheets)."""
        columns = []  # (header, id, statement index | None for word cloud | list of options)
        for s in self.slides:
            if s["type"] == "scale":
                for i, st in enumerate(s["statements"]):
                    columns.append((f"{s['id']} · {st['text'][:60]}", s["id"], i))
            elif s["type"] == "wordcloud":
                columns.append((s["title"], s["id"], None))
            elif s["type"] == "choice":
                columns.append((s["title"], s["id"], s["options"]))
        voters = sorted({v for a in self.answers.values() for v in a},
                        key=lambda v: self.names.get(v, "").lower())
        tmp = self.file.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Name"] + [c[0] for c in columns])
            for v in voters:
                row = [self.names.get(v, "")]
                for _, sid, i in columns:
                    a = self.answers[sid].get(v)
                    if a is None:
                        row.append("")
                    elif i is None:
                        row.append(", ".join(a))
                    elif isinstance(i, list):
                        row.append(i[a])
                    else:
                        row.append(a[i])
                w.writerow(row)
        os.replace(tmp, self.file.with_suffix(".csv"))

    def _distribution(self, s, i):
        lo, hi = s.get("min", 1), s.get("max", 5)
        vals = [a[i] for a in self.answers[s["id"]].values() if i < len(a)]
        return vals, [vals.count(x) for x in range(lo, hi + 1)]

    def _results(self, s):
        answers = list(self.answers[s["id"]].values())
        if s["type"] == "scale":
            statements = []
            for i, st in enumerate(s["statements"]):
                vals, dist = self._distribution(s, i)
                item = {
                    "n": len(vals),
                    "mean": round(sum(vals) / len(vals), 2) if vals else None,
                    "distribution": dist,
                }
                ref = st.get("compare_with")
                other = next((x for x in self.slides if x["id"] == (ref or {}).get("id")), None)
                if other:
                    item["before_distribution"] = self._distribution(other, ref["statement"])[1]
                statements.append(item)
            return {"responses": len(answers), "statements": statements}
        if s["type"] == "wordcloud":
            counts = Counter(w for a in answers for w in a)
            return {"responses": len(answers), "words": counts.most_common(100)}
        if s["type"] == "choice":
            return {"responses": len(answers), "counts": [answers.count(i) for i in range(len(s["options"]))]}
        return {"responses": 0}

    def public(self, voter, name=""):
        with self.lock:
            if voter and len(voter) <= 64:
                self.seen[voter] = time.time()
                name = re.sub(r"\s+", " ", name).strip()[:60]
                if name:
                    self.names[voter] = name
            s = self.current
            remaining = self._remaining()
            number, questions = self._numbering()
            return {
                "index": self.index,
                "total": len(self.slides),
                "question": number,
                "questions": questions,
                "slide": s,
                "my_answer": self.answers[s["id"]].get(voter),
                "remaining": remaining,
                "duration": self.duration,
                "closed": remaining == 0,
            }

    def presenter(self, url):
        with self.lock:
            now = time.time()
            answered, expected, names = self._participation()
            remaining = self._remaining()
            number, questions = self._numbering()
            return {
                "title": self.title,
                "question": number,
                "questions": questions,
                "index": self.index,
                "total": len(self.slides),
                "slide": self.current,
                "results": self._results(self.current),
                "remaining": remaining,
                "duration": self.duration,
                "closed": remaining == 0,
                "revealed": self.revealed,
                "answered": answered,
                "expected": expected,
                "who": names,
                "connected": sum(1 for t in self.seen.values() if now - t < 8),
                "url": url,
                "qr": segno is not None,
            }

    def answer(self, voter, name, sid, answer):
        with self.lock:
            s = self.current
            if not voter or not isinstance(voter, str) or len(voter) > 64:
                raise ValueError("error.invalid_id")
            name = re.sub(r"\s+", " ", name).strip() if isinstance(name, str) else ""
            if not name or len(name) > 60:
                raise ValueError("error.name")
            if sid != s["id"]:
                raise ValueError("error.moved")
            if self.duration and time.time() > self.started + self.duration + 1.5:
                raise ValueError("error.time_up")
            if voter not in self.answers[sid] and len(self.answers[sid]) >= MAX_VOTERS:
                raise ValueError("error.max_voters")
            if s["type"] == "scale":
                lo, hi = s.get("min", 1), s.get("max", 5)
                if (not isinstance(answer, list) or len(answer) != len(s["statements"])
                        or not all(type(x) is int and lo <= x <= hi for x in answer)):
                    raise ValueError("error.scale")
                value = answer
            elif s["type"] == "wordcloud":
                if not isinstance(answer, list):
                    raise ValueError("error.invalid_answer")
                value = []
                for w in answer:
                    w = normalize(w) if isinstance(w, str) else ""
                    if w and w not in value:
                        value.append(w)
                value = value[: s.get("max_words", 3)]
                if not value:
                    raise ValueError("error.min_one_word")
            elif s["type"] == "choice":
                if type(answer) is not int or not 0 <= answer < len(s["options"]):
                    raise ValueError("error.choice")
                value = answer
            else:
                raise ValueError("error.no_question")
            self.answers[sid][voter] = value
            self.names[voter] = name
            self._save()

    def control(self, action, index=None):
        with self.lock:
            previous = self.index
            if action == "next":
                self.index = min(self.index + 1, len(self.slides) - 1)
            elif action == "prev":
                self.index = max(self.index - 1, 0)
            elif action == "go" and isinstance(index, int) and 0 <= index < len(self.slides):
                self.index = index
            elif action == "reveal":
                self.revealed = not self.revealed
            elif action == "restart_timer":
                self.started = time.time()
                self.duration = self.current.get("time", self.time_limit) if self.current["type"] != "content" else 0
                self.revealed = False
            elif action == "add_time":
                if self.duration:
                    self.duration = (time.time() - self.started) + self._remaining() + 15
            elif action == "clear":
                self.answers[self.current["id"]] = {}
                self._save()
            else:
                raise ValueError("error.unknown_action")
            if self.index != previous:
                self._start_slide()


class Handler(BaseHTTPRequestHandler):
    state = None
    key = ""
    vote_url = ""
    messages = {}

    def log_message(self, *args):
        pass

    def _send(self, body, content_type, code=200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code_key, status):
        self._json({"error": self.messages.get(code_key, code_key)}, status)

    def _json(self, data, code=200):
        self._send(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

    def _html(self, name):
        self._send((STATIC / name).read_bytes(), "text/html; charset=utf-8")

    def _authorized(self, q):
        return secrets.compare_digest(q.get("k", [""])[0], self.key)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/vote"):
            return self._html("vote.html")
        if u.path == "/presenter":
            return self._html("presenter.html")
        if u.path == "/i18n.json":
            return self._json(self.messages)
        if u.path == "/qr.svg" and segno:
            buf = io.BytesIO()
            segno.make(self.vote_url, error="m").save(buf, kind="svg", scale=10, border=2)
            return self._send(buf.getvalue(), "image/svg+xml")
        if u.path == "/api/state":
            return self._json(self.state.public(q.get("v", [""])[0], q.get("n", [""])[0]))
        if u.path == "/api/presenter":
            if not self._authorized(q):
                return self._error("error.invalid_key", 403)
            return self._json(self.state.presenter(self.vote_url))
        self._error("error.not_found", 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 10_000:
                raise ValueError("error.too_large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if u.path == "/api/answer":
                self.state.answer(data.get("v"), data.get("name"), data.get("id"), data.get("answer"))
                return self._json({"ok": True})
            if u.path == "/api/control":
                if not self._authorized(q):
                    return self._error("error.invalid_key", 403)
                self.state.control(data.get("action"), data.get("index"))
                return self._json({"ok": True})
            self._error("error.not_found", 404)
        except json.JSONDecodeError:
            self._error("error.bad_request", 400)
        except ValueError as e:  # the message is a key from static/i18n/*.json
            self._error(str(e) if str(e).startswith("error.") else "error.bad_request", 400)


def open_tunnel(port, preferred=None, wait=40):
    """Open a free tunnel and return (process, public_url), or (None, None)."""
    for name, command, pattern in TUNNELS:
        if (preferred and name != preferred) or not shutil.which(name):
            continue
        print(f"  Opening public tunnel with {name}…")
        # stdin stays open (PIPE) so the ssh session does not close.
        proc = subprocess.Popen(command(port), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        found = queue.Queue()

        def read():
            for line in proc.stdout:  # keep reading so the output pipe never fills up
                m = re.search(pattern, line)
                if m:
                    found.put(m.group(0) + "/")

        threading.Thread(target=read, daemon=True).start()
        try:
            return proc, found.get(timeout=wait)
        except queue.Empty:
            proc.terminate()
            print(f"  Could not open the tunnel with {name}.")
    return None, None


def keep_tunnel(proc, port, preferred):
    """If the tunnel drops, reopen it and update the URL (and the QR code)."""
    while True:
        proc.wait()
        print("\n  ⚠ The public tunnel dropped. Retrying…")
        while True:
            proc, url = open_tunnel(port, preferred)
            if url:
                Handler.vote_url = url
                print(f"  New URL for students: {url}  (the presenter's QR code is already updated)")
                break
            time.sleep(10)


def load_presentation(path):
    pres = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = set()
    for n, s in enumerate(pres.get("slides", []), 1):
        if s.get("type") not in TYPES:
            raise SystemExit(f"Slide {n}: type must be one of {sorted(TYPES)}")
        if not s.get("id") or s["id"] in ids:
            raise SystemExit(f"Slide {n}: 'id' is missing or repeated")
        ids.add(s["id"])
        if s["type"] == "scale" and not s.get("statements"):
            raise SystemExit(f"Slide {n}: a scale slide needs 'statements'")
        if s["type"] == "choice" and not s.get("options"):
            raise SystemExit(f"Slide {n}: a choice slide needs 'options'")
    if not ids:
        raise SystemExit("The presentation has no slides.")
    return pres


def main():
    ap = argparse.ArgumentParser(description="Live classroom questions with a QR code.")
    ap.add_argument("presentation", nargs="?", default=str(BASE / "presentation.json"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lang", choices=LANGUAGES,
                    help="Interface language for presenter and students (default: the presentation's "
                         "\"language\", or en)")
    ap.add_argument("--time", type=int, help="Seconds to answer each question (0 = no limit)")
    ap.add_argument("--public", action="store_true",
                    help="Create a public internet URL (online classes) through a free tunnel")
    ap.add_argument("--tunnel", choices=[t[0] for t in TUNNELS], help="Force which tunnel to use with --public")
    ap.add_argument("--ip", help="IP to show in the QR code (auto-detected by default)")
    ap.add_argument("--key", help="Presenter key (random by default)")
    ap.add_argument("--results", help="File where answers are saved")
    ap.add_argument("--reset", action="store_true", help="Ignore previously saved answers")
    ap.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = ap.parse_args()

    path = Path(args.presentation).resolve()
    pres = load_presentation(path)
    results = Path(args.results) if args.results else path.with_name(f"results-{path.stem}.json")

    lang = args.lang or pres.get("language", "en")
    if lang not in LANGUAGES:
        raise SystemExit(f"Unsupported language {lang!r}. Choose one of: {', '.join(LANGUAGES)}")
    Handler.messages = load_messages(lang)
    Handler.state = State(pres, results, args.reset, args.time)
    Handler.key = args.key or secrets.token_urlsafe(6)
    Handler.vote_url = f"http://{args.ip or local_ip()}:{args.port}/"
    presenter_url = f"http://localhost:{args.port}/presenter?k={Handler.key}"

    # With a public tunnel we only listen on this machine: all outside traffic comes through the tunnel.
    server = ThreadingHTTPServer(("127.0.0.1" if args.public else "0.0.0.0", args.port), Handler)
    print(f"\n  {pres.get('title', '')}")
    if args.public:
        proc, url = open_tunnel(args.port, args.tunnel)
        if not url:
            server.server_close()
            raise SystemExit("\n  Could not create the public URL. Install cloudflared (see README.md) "
                             "or check that the ssh command works.")
        Handler.vote_url = url
        threading.Thread(target=keep_tunnel, args=(proc, args.port, args.tunnel), daemon=True).start()
    print(f"  Language:   {lang}")
    print(f"  Students:   {Handler.vote_url}")
    print(f"  Presenter:  {presenter_url}   (do not share this link)")
    print(f"  Answers saved to:      {results}")
    print(f"  Spreadsheet per student: {results.with_suffix('.csv')}")
    if segno:
        segno.make(Handler.vote_url, error="m").terminal(compact=True)
    else:
        print("\n  (To show the QR code, install segno:  pip install segno)")
    print("\n  Ctrl+C to quit.\n")
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, [presenter_url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Done. Have a great class!")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
