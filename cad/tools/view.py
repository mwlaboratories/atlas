"""Web viewer for the case: serves cad/tools/viewer + cad/out/view on localhost and rebuilds
whenever a case source file or a board changes.

    cad view                # http://localhost:8737, fast rebuilds (build.py --fast) on change
    cad view --full         # rebuild with the full checks and the reference compare
    cad view --port 9000

A rebuild starts once the sources and boards have been quiet for DEBOUNCE seconds (an
editor's or KiCad's save is often several writes). A change during a build aborts it and
starts over once things are quiet again: parts finished before the abort stay cached.
Each rebuild runs build.py in a fresh process (so edited code is picked up), with the half
the page shows first. The page polls view/view.json for meshes and check results, and
/status for the build state ("rebuilding…", the last build's duration, or the first error
line of a failed build, while the last good meshes stay up).
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEBOUNCE = 2.0          # s of quiet before a rebuild
POLL = 0.5              # s between looks at the files

# shared with the HTTP handler threads
STATE = {"state": "idle", "started": None, "duration": None, "finished": None, "error": None,
         "pending": False, "half": "left", "mode": "fast"}
LOCK = threading.Lock()


def handler(out: Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        """/view/* from <out>/view, /status the build state, the rest from cad/tools/viewer."""

        def translate_path(self, path):
            path = path.split("?", 1)[0]
            if path.startswith("/view/"):
                return str(out / "view" / path[len("/view/"):])
            return str(HERE / "viewer" / (path.lstrip("/") or "index.html"))

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/status":
                q = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
                with LOCK:
                    if q.get("half") in ("left", "right"):
                        STATE["half"] = q["half"]           # build the shown half first
                    s = dict(STATE, now=time.time())
                body = json.dumps(s).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass
    return Handler


def sources(pcb_dir: Path):
    yield from (HERE / "atlas_case").glob("*.py")
    yield HERE / "build.py"
    yield from pcb_dir.glob("atlas-*.kicad_pcb")


def stamp(pcb_dir: Path):
    """(path, mtime, size) of every source. Files come and go during saves (KiCad, editors'
    atomic renames, emacs lock links), so a file that vanishes mid-look is just left out."""
    out = []
    for f in sources(pcb_dir):
        try:
            st = f.stat()
        except OSError:
            continue
        out.append((str(f), st.st_mtime_ns, st.st_size))
    return tuple(sorted(out))


class Build:
    """One build.py run in its own process group, its output echoed and kept for errors."""

    def __init__(self, cmd: list[str]):
        self.started = time.time()
        self.tail = deque(maxlen=400)
        self.proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, start_new_session=True)
        self.reader = threading.Thread(target=self._echo, daemon=True)
        self.reader.start()

    def _echo(self):
        for line in self.proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            self.tail.append(line.rstrip("\n"))

    def done(self) -> bool:
        return self.proc.poll() is not None

    def abort(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)    # build.py and its worker processes
        except ProcessLookupError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        self.reader.join(timeout=2)

    def error(self) -> str | None:
        """None if the build ran (checks may still fail), else its first error line."""
        self.reader.join(timeout=2)
        rc, lines = self.proc.returncode, list(self.tail)
        # exit 1 is failing checks, but also any uncaught Python error: a run that got
        # through prints build.py's closing "done in" line
        if rc in (0, 1) and any(l.startswith("done in ") for l in lines):
            return None
        for l in lines:                              # build.py's own summary of a job's error
            if l.startswith("ERROR "):
                return l
        tb = [l for l in lines if l and not l.startswith(" ")]
        return (tb[-1] if tb else f"build.py exited with {rc}")[:300]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8737)
    ap.add_argument("--pcb-dir", default=str(REPO / "pcb"))
    ap.add_argument("--out", default=str(REPO / "cad" / "out"))
    ap.add_argument("--full", action="store_true", help="full rebuilds: every check, reference compare")
    ap.add_argument("--no-watch", action="store_true", help="serve the last build only")
    a = ap.parse_args()
    pcb_dir, out = Path(a.pcb_dir).resolve(), Path(a.out).resolve()
    (out / "view").mkdir(parents=True, exist_ok=True)
    STATE["mode"] = "full" if a.full else "fast"

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler(out))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"viewer: http://localhost:{a.port}", flush=True)
    if a.no_watch:
        threading.Event().wait()

    build, built_stamp = None, None          # built_stamp: the sources the last build started from
    last, changed_at = stamp(pcb_dir), 0.0   # first build right away
    try:
        while True:
            now = stamp(pcb_dir)
            if now != last:
                last, changed_at = now, time.time()
                if build is not None:
                    print(time.strftime("%H:%M:%S"), "change during the build -> aborting it", flush=True)
                    build.abort()
                    build = None
                with LOCK:
                    STATE.update(state="waiting", pending=True)
            if build is not None and build.done():
                err = build.error()
                with LOCK:
                    STATE.update(state="failed" if err else "ok", error=err, finished=time.time(),
                                 duration=round(time.time() - build.started, 1))
                print(time.strftime("%H:%M:%S"), f"build {'failed: ' + err if err else 'done'} "
                      f"({STATE['duration']} s)", flush=True)
                build = None
            if build is None and last != built_stamp and time.time() - changed_at >= DEBOUNCE:
                built_stamp = last
                with LOCK:
                    half = STATE["half"]
                    STATE.update(state="building", started=time.time(), pending=False, error=None)
                print(time.strftime("%H:%M:%S"), f"rebuilding ({STATE['mode']}, {half} first)", flush=True)
                cmd = [sys.executable, str(HERE / "build.py"), "--pcb-dir", str(pcb_dir), "--out", str(out),
                       "--first", half] + ([] if a.full else ["--fast"])
                build = Build(cmd)
            time.sleep(POLL)
    except KeyboardInterrupt:
        if build is not None:
            build.abort()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
