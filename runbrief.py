#!/usr/bin/env python3
"""runbrief - run noisy commands, hand your AI agent a brief.

Runs a build / test / simulator command, stores the full output in a log
file, and prints a compact, root-cause-first summary. The agent reads the
log only when the summary is not enough, and only the part it needs.

  runbrief run   [-n NAME] [-T SEC] [-e N] [-c N] [-w] [--json] -- <command ...>
  runbrief grep  REGEX [-n NAME | --log PATH] [-C 2] [-m 30]
  runbrief show  -L LINE [-n NAME | --log PATH] [-C 20]
  runbrief stats [--json] [--reset]
  runbrief clean
  runbrief install [DIR]      # put a `runbrief` command on PATH

A single string after `--` runs through the shell (pipes, &&, env vars work).
`runbrief -n build -- make` (no subcommand) is the same as `runbrief run ...`.

Zero dependencies, Python 3.9+, Linux / macOS / Windows. MIT licence.
Configuration (environment variables) is documented in README.md.
"""
import argparse
import glob
import gzip
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque

__version__ = "1.0.0"

# --------------------------------------------------------------------------- config

def env_num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)

def base_dir():
    return os.environ.get("RUNBRIEF_DIR") or os.path.join(os.getcwd(), ".runbrief")

def log_dir():
    d = os.path.join(base_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d

MB = 1024 * 1024

# --------------------------------------------------------------------------- patterns
# Root-cause error lines (compilers, linkers, configure steps, runtimes, tests).
ERR_PATTERNS = [
    r":\d+(:\d+)?:\s*(fatal )?error\b",                 # gcc / clang  file:line:col: error
    r"\(\d+(,\d+)?\)\s*:\s*(fatal )?error\b",           # MSVC / C#    file(line,col): error
    r"\berror\s+(C|CS|LNK|MSB|NU|BC|FS|CA)\d{3,5}\b",   # MSVC / C# / MSBuild / NuGet codes
    r"\bundefined reference to\b|\bmultiple definition of\b",
    r"\bunresolved external symbol\b",
    r"\bld(\.lld|\.gold)?: .*error\b|^ld: ",
    r"^CMake Error\b",
    r"^\s*(E\s{2,}(?!\s*\+)|FAILED\s+\S+::)",           # pytest assertion (not '+ where' notes) / summary
    r"^ERROR:? ",                                       # pytest collection errors, many tools
    r"Segmentation fault|core dumped|Assertion .*failed|\bpanic:|\bAborted\b",
    r"\b(FATAL|PANIC|FAULT)\b",
    r"^error(\[\w+\])?: ",                              # rustc, generic tools
]
WARN_PATTERNS = [
    r":\d+(:\d+)?:\s*warning\b",
    r"\(\d+(,\d+)?\)\s*:\s*warning\b",
    r"\bwarning\s+(C|CS|LNK|MSB|NU|CA)\d{3,5}\b",
    r"^CMake Warning\b",
    r"\b\w*Warning: ",
]
# Consequence lines: true failures, but never the root cause. Counted, not listed.
CASCADE_PATTERNS = [
    r"^g?make(\[\d+\])?: \*\*\*", r"^ninja: build stopped", r"^FAILED: ",
    r"collect2: error: ld returned", r"^Build FAILED\.?$", r"^\s*\d+ Error\(s\)",
    r"^error: command .* failed", r"^Error: Process completed with exit code",
    r"^gmake(\[\d+\])?: \*\*\*",
]
RESULT_RE = re.compile(
    r"^=+ .*\b(passed|failed|errors?|skipped|xfailed|deselected)\b.* in [\d.]+s.*=+$"
    r"|^Tests? (run|summary)|^\s*(Passed|Failed)!\s+-|^\d+% tests passed")
TB_START = "Traceback (most recent call last):"
TB_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+), in (.+)$')
STDLIB_HINT = re.compile(r"site-packages|dist-packages|[\\/]lib[\\/]python\d|<frozen|[\\/]Lib[\\/]")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\r")
NUM_RE = re.compile(r"0[xX][0-9a-fA-F]+|\d+")
SYS_HDR = re.compile(r"^(/usr/|/opt/|/Library/|[A-Za-z]:\\Program Files)|[\\/](include[\\/]c\+\+|lib[\\/]gcc|MSVC)[\\/]")
LOC_RE = re.compile(r"^(.+?):(\d+)(?::\d+)?:")
USER_CTX = re.compile(r"^(.+?):(\d+)(?::\d+)?:\s+(required from here|note: in instantiation of .* requested here"
                      r"|note: in instantiation of .*|required from )")
# cheap prefilter: a line is only regex-checked if it contains one of these (lower-case)
KEYWORDS = ("error", "warn", "fail", "fatal", "fault", "panic", "abort", "assert", "undefined",
            "unresolved", "segmentation", "dumped", "ld:", "e  ", "passed", "***", "build stopped",
            "required from", "requested here", "tests ", "test run", "% tests")
TEMPLATE_RE = re.compile(r"0[xX][0-9a-fA-F]+|\d+|\S*[\\/]\S*")
TRIM_RE = re.compile(r"^\.\.\. \[runbrief\] TRIMMED stream lines (\d+)-(\d+)")

def load_custom():
    """User patterns: env RUNBRIEF_ERR / RUNBRIEF_WARN + .runbrief/patterns.txt
    (one regex per line; prefix 'W:' for warnings, '#' comments)."""
    err, warn = [], []
    if os.environ.get("RUNBRIEF_ERR"):
        err.append(os.environ["RUNBRIEF_ERR"])
    if os.environ.get("RUNBRIEF_WARN"):
        warn.append(os.environ["RUNBRIEF_WARN"])
    pf = os.path.join(base_dir(), "patterns.txt")
    if os.path.exists(pf):
        for ln in open(pf, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.startswith("W:"):
                warn.append(ln[2:].strip())
            else:
                err.append(ln)
    mk = lambda xs: re.compile("|".join(xs), re.IGNORECASE) if xs else None
    return mk(err), mk(warn)

def load_patterns():
    """Built-ins + env RUNBRIEF_ERR / RUNBRIEF_WARN + .runbrief/patterns.txt
    (one regex per line; prefix 'W:' for warnings, '#' comments)."""
    err, warn = list(ERR_PATTERNS), list(WARN_PATTERNS)
    if os.environ.get("RUNBRIEF_ERR"):
        err.append(os.environ["RUNBRIEF_ERR"])
    if os.environ.get("RUNBRIEF_WARN"):
        warn.append(os.environ["RUNBRIEF_WARN"])
    pf = os.path.join(base_dir(), "patterns.txt")
    if os.path.exists(pf):
        for ln in open(pf, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            (warn if ln.startswith("W:") else err).append(ln[2:].strip() if ln.startswith("W:") else ln)
    return (re.compile("|".join(err), re.IGNORECASE),
            re.compile("|".join(warn), re.IGNORECASE),
            re.compile("|".join(CASCADE_PATTERNS)))

def short(s, width=240):
    s = s.rstrip()
    return s if len(s) <= width else s[:width] + " ..."

# --------------------------------------------------------------------------- analyzer

class Analyzer:
    """Streaming classifier. Keeps unique errors/warnings in first-seen order
    with hit counts, compacts Python tracebacks, counts cascade lines."""

    def __init__(self, ctx_after=0):
        self.err_re, self.warn_re, self.cascade_re = load_patterns()
        self.cust_err, self.cust_warn = load_custom()
        self.pending_user = None      # gcc: 'file:line: required from here' precedes the error
        self.need_user = None         # clang: '... requested here' follows the error
        self.templates = {}           # rare-line detection: template -> [count, line, text]
        self.errors, self.warnings = OrderedDict(), OrderedDict()
        self.cascade, self.cascade_first = 0, None
        self.results = []
        self.tb = None
        self.ctx_after, self._ctx_for, self._ctx_left = ctx_after, None, 0
        self.flagged = set()           # stream line numbers already shown

    def _add(self, table, n, text, key=None):
        key = key or NUM_RE.sub("#", text.strip())
        m = re.search(r"  \(from [^)]+\)$", text)
        if m and len(text) > 240:
            text = text[:230 - len(m.group(0))] + " ..." + m.group(0)
        if key in table:
            table[key][2] += 1
        elif len(table) < 5000:
            table[key] = [n, short(text), 1, []]
            return key
        return None

    def _template(self, n, st):
        if len(self.templates) < 20000:
            key = TEMPLATE_RE.sub("#", st.lower())[:120]
            t = self.templates.get(key)
            if t: t[0] += 1
            else: self.templates[key] = [1, n, short(st, 200)]
        else:
            key = TEMPLATE_RE.sub("#", st.lower())[:120]
            t = self.templates.get(key)
            if t: t[0] += 1

    def rare_lines(self, total, k=8, skip_first=30):
        """Lines whose shape occurs once or twice in a large, otherwise repetitive output."""
        if total < 1000:
            return []
        rare = [v for v in self.templates.values() if v[0] <= 2 and v[1] > skip_first]
        return [] if len(rare) > 60 else rare[:k]   # many unique lines -> heuristic is noise

    def _user_ref(self, s):
        m = USER_CTX.match(s)
        if m and not SYS_HDR.search(m.group(1)):
            return f"{os.path.basename(m.group(1))}:{m.group(2)}"
        return None

    def feed(self, n, s):
        if self._ctx_left and self._ctx_for is not None:
            self.errors[self._ctx_for][3].append(short(s, 160))
            self._ctx_left -= 1
        st = s.strip()
        if not st:
            return
        low = st.lower()
        hot = any(k in low for k in KEYWORDS) or st == TB_START or self.tb is not None
        if not hot:
            if self.cust_err and self.cust_err.search(s):
                self._add(self.errors, n, s); self.flagged.add(n)
            elif self.cust_warn and self.cust_warn.search(s):
                self._add(self.warnings, n, s); self.flagged.add(n)
            else:
                self._template(n, st)
            return
        # ---- C++ template instantiation context -> user source line
        ref = self._user_ref(s) if ("required from" in low or "requested here" in low or "in instantiation" in low) else None
        if ref:
            if self.need_user and self.need_user in self.errors and not self.errors[self.need_user][1].endswith(")"):
                self.errors[self.need_user][1] += f"  (from {ref})"
                self.need_user = None
            else:
                self.pending_user = ref
            return
        # ---- python traceback: one line = exception + last frame in user code
        if st == TB_START:
            self.tb = {"start": n, "frames": []}
            return
        if self.tb is not None:
            m = TB_FRAME.match(s)
            if m:
                self.tb["frames"].append(m.groups())
                return
            if s[:1] in (" ", "\t") or not st or st.startswith(("During handling", "The above exception")):
                return
            fr = self.tb["frames"]
            user = [f for f in fr if not STDLIB_HINT.search(f[0])] or fr
            where = f"  <- {user[-1][0]}:{user[-1][1]} in {user[-1][2]}" if user else ""
            self.tb = None
            self._add(self.errors, n, st + where, key="TB|" + NUM_RE.sub("#", st))
            self.flagged.add(n)
            return
        # ---- test-runner result lines
        if RESULT_RE.search(st):
            self.results.append(short(st))
            self.flagged.add(n)
            return
        if self.cascade_re.search(s):
            self.cascade += 1
            if self.cascade_first is None:
                self.cascade_first = (n, short(s, 160))
            self.flagged.add(n)
            return
        if self.err_re.search(s) or (self.cust_err and self.cust_err.search(s)):
            m = LOC_RE.match(st)
            in_sys = bool(m and SYS_HDR.search(m.group(1)))
            text = s
            if in_sys and self.pending_user:
                text = s.rstrip() + f"  (from {self.pending_user})"
            k = self._add(self.errors, n, text)
            if in_sys and not self.pending_user and k:
                self.need_user = k
            self.pending_user = None
            self.flagged.add(n)
            if k and self.ctx_after:
                self._ctx_for, self._ctx_left = k, self.ctx_after
            return
        if self.warn_re.search(s) or (self.cust_warn and self.cust_warn.search(s)):
            self._add(self.warnings, n, s)
            self.flagged.add(n)
            return
        self._template(n, st)

    def finish(self, n):
        if self.tb is not None:       # traceback cut off (e.g. killed) - still report it
            fr = self.tb["frames"]
            if fr:
                self._add(self.errors, self.tb["start"],
                          f"Traceback (incomplete) <- {fr[-1][0]}:{fr[-1][1]} in {fr[-1][2]}")

# --------------------------------------------------------------------------- log files

def trim_file(path, keep_head, keep_tail, stream_lines):
    """Keep first keep_head + last keep_tail bytes (cut on line breaks); replace
    the middle with a marker that records which stream lines were removed."""
    with open(path, "rb") as f:
        data = f.read()
    head = data[:keep_head]
    head = head[:head.rfind(b"\n") + 1] or head
    tail = data[-keep_tail:]
    tail = tail[tail.find(b"\n") + 1:]
    head_lines = head.count(b"\n") - 1            # minus header line
    first_cut, last_cut = head_lines + 1, stream_lines - tail.count(b"\n")
    removed = len(data) - len(head) - len(tail)
    marker = (f"... [runbrief] TRIMMED stream lines {first_cut}-{last_cut} "
              f"({removed / MB:.1f} MB, log cap reached). Errors were collected before trimming.\n").encode()
    with open(path, "wb") as f:
        f.write(head + marker + tail)
    return len(head) + len(marker) + len(tail)

def open_log(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")

def iter_stream(path):
    """Yield (stream_line_no or None, text) - None for header / trim markers."""
    with open_log(path) as f:
        n = 0
        for i, ln in enumerate(f):
            ln = ln.rstrip("\n")
            if i == 0 and ln.startswith("# runbrief"):
                yield None, ln
                continue
            m = TRIM_RE.match(ln)
            if m:
                n = int(m.group(2))
                yield None, ln
                continue
            n += 1
            yield n, ln

def logs_for(name):
    files = glob.glob(os.path.join(log_dir(), f"{name}-*.log*"))
    return sorted(files, key=lambda f: (os.path.getmtime(f), f))

def latest_log(name=None):
    files = logs_for(name) if name else sorted(glob.glob(os.path.join(log_dir(), "*.log*")),
                                               key=lambda f: (os.path.getmtime(f), f))
    return files[-1] if files else None

def retention(name, current):
    """Per name: keep newest KEEP_OK successful and KEEP_FAIL failed logs; gzip all
    but the newest; then cap the whole folder (oldest first, never `current`)."""
    keep_ok, keep_fail = int(env_num("RUNBRIEF_KEEP_OK", 1)), int(env_num("RUNBRIEF_KEEP_FAIL", 5))
    files = logs_for(name)
    oks = [f for f in files if "-ok.log" in f]
    fails = [f for f in files if "-ok.log" not in f]
    doomed = oks[:-keep_ok] if keep_ok > 0 else oks
    doomed += fails[:-keep_fail] if keep_fail > 0 else fails
    for f in doomed:
        if f != current:
            try: os.remove(f)
            except OSError: pass
    if env_num("RUNBRIEF_GZIP", 1):
        for f in logs_for(name):
            if f != current and f.endswith(".log"):
                try:
                    with open(f, "rb") as src, gzip.open(f + ".gz", "wb", compresslevel=6) as dst:
                        while True:
                            b = src.read(MB)
                            if not b: break
                            dst.write(b)
                    st = os.stat(f)
                    os.utime(f + ".gz", (st.st_atime, st.st_mtime))
                    os.remove(f)
                except OSError:
                    pass
    cap = env_num("RUNBRIEF_DIR_MAX_MB", 300) * MB
    every = sorted(glob.glob(os.path.join(log_dir(), "*.log*")), key=os.path.getmtime)
    total = sum(os.path.getsize(f) for f in every)
    for f in every:
        if total <= cap: break
        if f == current: continue
        total -= os.path.getsize(f)
        try: os.remove(f)
        except OSError: pass

# --------------------------------------------------------------------------- stats

def record(kind, **kw):
    if not env_num("RUNBRIEF_STATS", 1):
        return
    try:
        os.makedirs(base_dir(), exist_ok=True)
        with open(os.path.join(base_dir(), "stats.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(ts=round(time.time()), kind=kind, **kw)) + "\n")
    except OSError:
        pass

def tok(nbytes):
    """Rough token estimate (chars/4). Ratios between raw and brief do not depend on it."""
    return int(round(nbytes / 4))

class Out:
    """Collects printed output so its size can be recorded."""
    def __init__(self): self.parts = []
    def __call__(self, s=""):
        self.parts.append(s)
        try:
            print(s)
        except BrokenPipeError:        # reader (e.g. `| head`) went away - keep going quietly
            try: sys.stdout = open(os.devnull, "w")
            except OSError: pass
    def size(self): return len("\n".join(self.parts).encode("utf-8")) + 1

# --------------------------------------------------------------------------- run

def compact(rows):
    """Collapse runs of lines that differ only in numbers: 'L10-34: tick 1  [x25 similar]'."""
    out, i = [], 0
    while i < len(rows):
        k0, s0 = rows[i]
        key, j = NUM_RE.sub("#", s0.strip()), i + 1
        while j < len(rows) and NUM_RE.sub("#", rows[j][1].strip()) == key:
            j += 1
        if j - i > 2:
            out.append(f"L{k0}-{rows[j - 1][0]}: {short(rows[j - 1][1], 200)}  [x{j - i} similar]")
        else:
            out.extend(f"L{k}: {short(t, 200)}" for k, t in rows[i:j])
        i = j
    return out

def kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        try: os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            try: p.kill()
            except OSError: pass

def cmd_run(a):
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        sys.exit("runbrief: no command given (usage: runbrief run -n NAME -- <command>)")
    shell = len(cmd) == 1
    cmd_str = cmd[0] if shell else " ".join(shlex.quote(c) for c in cmd)
    try:
        first = shlex.split(cmd_str)[0]
    except (ValueError, IndexError):
        first = "cmd"
    name = a.name or re.sub(r"[^\w.-]", "_", os.path.basename(first))[:30] or "cmd"
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    tmp = os.path.join(log_dir(), f"{name}-{stamp}-run.log")
    cap = int(env_num("RUNBRIEF_MAX_MB", 100) * MB)
    keep_head = int(cap * env_num("RUNBRIEF_HEAD_PCT", 10) / 100)
    keep_tail = int(cap * env_num("RUNBRIEF_TAIL_PCT", 40) / 100)

    an = Analyzer(ctx_after=a.context)
    tail = deque(maxlen=40)
    n = raw_bytes = size = trims = 0
    timed_out = interrupted = False
    t0 = time.time()
    f = open(tmp, "w", encoding="utf-8", errors="replace", newline="\n")
    one_line = cmd_str.replace("\n", " \\n ")
    header = f"# runbrief {__version__} | {time.strftime('%Y-%m-%d %H:%M:%S')} | cwd={os.getcwd()} | $ {one_line}\n"
    f.write(header); size = len(header)
    popen_kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    encoding="utf-8", errors="replace", bufsize=1, shell=shell)
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    try:
        p = subprocess.Popen(cmd_str if shell else cmd, **popen_kw)
    except OSError as e:
        f.close(); os.remove(tmp)
        print(f"[runbrief] {name}: could not start: {e}")
        return 127
    timer = None
    if a.timeout:
        def on_timeout():
            nonlocal timed_out
            timed_out = True
            kill_tree(p)
        timer = threading.Timer(a.timeout, on_timeout)
        timer.daemon = True
        timer.start()
    try:
        for ln in p.stdout:
            raw_bytes += len(ln.encode("utf-8", "replace"))
            ln = ANSI_RE.sub("", ln)
            if not ln.endswith("\n"):
                ln += "\n"
            n += 1
            f.write(ln); size += len(ln)
            s = ln[:-1]
            an.feed(n, s)
            tail.append((n, s))
            if size >= cap:
                f.close()
                size = trim_file(tmp, keep_head, keep_tail, n)
                trims += 1
                f = open(tmp, "a", encoding="utf-8", errors="replace", newline="\n")
    except KeyboardInterrupt:
        interrupted = True
        kill_tree(p)
    rc = p.wait()
    if timer:
        timer.cancel()
    an.finish(n)
    if timed_out:
        f.write(f"[runbrief] TIMEOUT after {a.timeout:g}s - process tree killed\n")
        rc = 124
    if interrupted:
        rc = 130
    f.close()
    dt = time.time() - t0
    outcome = "ok" if rc == 0 else ("timeout" if timed_out else "fail")
    path = tmp.replace("-run.log", f"-{outcome}.log")
    os.replace(tmp, path)
    retention(name, path)

    # ------------------------------------------------------------- brief
    out = Out()
    n_err_hits = sum(v[2] for v in an.errors.values())
    n_warn_hits = sum(v[2] for v in an.warnings.values())
    status = ("INTERRUPTED" if interrupted else "OK" if outcome == "ok"
              else f"TIMEOUT after {a.timeout:g}s" if outcome == "timeout" else f"FAIL exit={rc}")
    trim_note = f" | log trimmed {trims}x (cap {cap // MB} MB)" if trims else ""
    rel = os.path.relpath(path)
    if a.json:
        doc = dict(name=name, status=outcome, exit=rc, seconds=round(dt, 2), lines=n,
                   errors=[dict(line=v[0], text=v[1], count=v[2], context=v[3]) for v in an.errors.values()][:a.max_errors],
                   error_unique=len(an.errors), error_hits=n_err_hits, warning_unique=len(an.warnings),
                   cascade=an.cascade, results=an.results, log=rel,
                   tail=[s for k, s in tail if k not in an.flagged][-(a.tail or 12):] if rc else [])
        out(json.dumps(doc, indent=1))
    elif (n <= env_num("RUNBRIEF_PASSTHROUGH_LINES", 40)
          and raw_bytes <= env_num("RUNBRIEF_PASSTHROUGH_BYTES", 4000) and not trims):
        # tiny output: the raw text is cheaper than a summary, and lossless
        out(f"[runbrief] {name}: {status} | {n} line(s), shown in full:")
        for k, s in iter_stream(path):
            if k is not None:
                out(short(s, 300))
    else:
        out(f"[runbrief] {name}: {status} | {dt:.1f}s | {n:,} lines | errors {len(an.errors)}"
            + (f" ({n_err_hits} hits)" if n_err_hits != len(an.errors) else "")
            + f" | warnings {len(an.warnings)}" + trim_note + f" | log {rel}")
        for r in an.results[-2:]:
            out(f"tests: {r}")
        show_err = rc != 0 or an.errors
        if show_err and an.errors:
            items = list(an.errors.values())
            out(f"errors (first-seen order, showing {min(len(items), a.max_errors)}/{len(items)}):")
            for line, text, cnt, ctx in items[:a.max_errors]:
                out(f"  L{line}: {text}" + (f"  [x{cnt}]" if cnt > 1 else ""))
                for c in ctx:
                    out(f"        | {c}")
        if (a.warnings or (rc != 0 and not an.errors)) and an.warnings:
            items = list(an.warnings.values())
            k = a.max_warnings
            out(f"warnings (showing {min(len(items), k)}/{len(items)}):")
            for line, text, cnt, _ in items[:k]:
                out(f"  L{line}: {text}" + (f"  [x{cnt}]" if cnt > 1 else ""))
        if an.cascade and rc != 0:
            ln_, txt = an.cascade_first
            out(f"build-system: {an.cascade} failure line(s), first L{ln_}: {txt}")
        if rc != 0:
            rare = an.rare_lines(n) if not an.errors else []
            if rare:
                out("rare lines (shape seen <=2x in an otherwise repetitive output; heuristic):")
                for cnt, ln_, txt in rare:
                    out(f"  L{ln_}: {txt}")
            want = a.tail if a.tail is not None else (0 if an.errors else 5 if rare else 20)
            rest = compact([(k, s) for k, s in tail if k not in an.flagged and s.strip()])[-want:] if want else []
            if rest:
                out("tail (last lines without a known pattern):")
                for row in rest:
                    out("  " + row)
            if not an.errors:
                out("note: no known error pattern matched. Add one to .runbrief/patterns.txt "
                    "(e.g. \\[EMU\\] FAULT) or inspect with grep/show.")
            out(f"more: runbrief grep 'REGEX' -n {name}  |  runbrief show -n {name} -L <line> -C 20")
    record("run", name=name, status=outcome, exit=rc, seconds=round(dt, 2), lines=n,
           raw_bytes=raw_bytes, brief_bytes=out.size(), errors=len(an.errors), trims=trims)
    return rc

# --------------------------------------------------------------------------- grep / show

def pick_log(a):
    path = a.log or latest_log(a.name)
    if not path or not os.path.exists(path):
        sys.exit("runbrief: no log found" + (f" for name '{a.name}'" if a.name else ""))
    return path

def cmd_grep(a):
    path, out = pick_log(a), Out()
    rx = re.compile(a.regex, re.IGNORECASE)
    rows = list(iter_stream(path))
    hits = [i for i, (k, s) in enumerate(rows) if k is not None and rx.search(s)]
    out(f"[runbrief] {os.path.relpath(path)}: {len(hits)} match(es) for /{a.regex}/"
        + (f", showing {a.max}" if len(hits) > a.max else ""))
    last = -1
    for i in hits[:a.max]:
        lo, hi = max(0, i - a.context), min(len(rows), i + a.context + 1)
        if lo > last + 1 and last >= 0:
            out("  --")
        for j in range(max(lo, last + 1), hi):
            k, s = rows[j]
            out(f"  {'L' + str(k) if k else '  '}{':' if j == i else '-'} {short(s, 300)}")
        last = hi - 1
    record("grep", name=a.name, brief_bytes=out.size())
    return 0

def cmd_show(a):
    path, out = pick_log(a), Out()
    lo, hi = a.line - a.context, a.line + a.context
    out(f"[runbrief] {os.path.relpath(path)}: lines {max(1, lo)}-{hi}")
    for k, s in iter_stream(path):
        if k is None:
            if TRIM_RE.match(s):
                out(f"  {s}")
            continue
        if k > hi:
            break
        if k >= lo:
            out(f"  L{k}{':' if k == a.line else '-'} {short(s, 300)}")
    record("show", name=a.name, brief_bytes=out.size())
    return 0

# --------------------------------------------------------------------------- stats / clean

def cmd_stats(a):
    sp = os.path.join(base_dir(), "stats.jsonl")
    if a.reset:
        if os.path.exists(sp): os.remove(sp)
        print("[runbrief] stats reset"); return 0
    rows = []
    if os.path.exists(sp):
        for ln in open(sp, encoding="utf-8"):
            try: rows.append(json.loads(ln))
            except ValueError: pass
    runs = [r for r in rows if r.get("kind") == "run"]
    follow = [r for r in rows if r.get("kind") in ("grep", "show")]
    raw = sum(r.get("raw_bytes", 0) for r in runs)
    brief = sum(r.get("brief_bytes", 0) for r in runs)
    fol = sum(r.get("brief_bytes", 0) for r in follow)
    seen = raw and (brief + fol)
    res = dict(runs=len(runs), failed=sum(1 for r in runs if r.get("status") != "ok"),
               followups=len(follow), raw_tokens=tok(raw), brief_tokens=tok(brief),
               followup_tokens=tok(fol), net_saved_tokens=tok(raw - brief - fol),
               reduction_pct=round(100 * (1 - seen / raw), 2) if raw else 0.0,
               disk_mb=round(sum(os.path.getsize(f) for f in glob.glob(os.path.join(log_dir(), "*"))) / MB, 2))
    by = {}
    for r in runs:
        b = by.setdefault(r.get("name"), [0, 0, 0])
        b[0] += 1; b[1] += r.get("raw_bytes", 0); b[2] += r.get("brief_bytes", 0)
    res["by_name"] = {k: dict(runs=v[0], raw_tokens=tok(v[1]), brief_tokens=tok(v[2])) for k, v in by.items()}
    if a.json:
        print(json.dumps(res, indent=1)); return 0
    print(f"[runbrief] {res['runs']} runs ({res['failed']} failed), {res['followups']} follow-up reads")
    print(f"  raw output      ~{res['raw_tokens']:,} tokens   (what the agent would have read)")
    print(f"  briefs          ~{res['brief_tokens']:,} tokens")
    print(f"  follow-ups      ~{res['followup_tokens']:,} tokens   (grep/show)")
    print(f"  net saved       ~{res['net_saved_tokens']:,} tokens   ({res['reduction_pct']}% less)")
    print(f"  log folder      {res['disk_mb']} MB")
    for k, v in sorted(by.items(), key=lambda x: -x[1][1])[:8]:
        print(f"    {k:<14} {v[0]:>4} runs  ~{tok(v[1]):>10,} -> ~{tok(v[2]):,} tokens")
    print("  (tokens estimated as chars/4)")
    return 0

def cmd_clean(a):
    n = 0
    for f in glob.glob(os.path.join(log_dir(), "*")):
        try: os.remove(f); n += 1
        except OSError: pass
    print(f"[runbrief] removed {n} log file(s)")
    return 0

def cmd_install(a):
    """Copy this file to a directory on PATH as `runbrief` (plus runbrief.cmd on Windows)."""
    import shutil
    src = os.path.abspath(__file__)
    path_dirs = [os.path.abspath(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    cands = [a.dir] if a.dir else [os.path.expanduser("~/.local/bin"), "/usr/local/bin",
                                   os.path.join(sys.prefix, "Scripts"), os.path.join(sys.prefix, "bin")]
    for d in cands:
        d = os.path.abspath(d)
        if not a.dir and d not in path_dirs:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            if os.name == "nt":
                shutil.copy2(src, os.path.join(d, "runbrief.py"))
                with open(os.path.join(d, "runbrief.cmd"), "w") as f:
                    f.write(f'@"{sys.executable}" "%~dp0runbrief.py" %*\n')
                dst = os.path.join(d, "runbrief.cmd")
            else:
                dst = os.path.join(d, "runbrief")
                with open(src, "rb") as fi, open(dst, "wb") as fo:
                    fo.write(b"#!" + sys.executable.encode() + b"\n" + fi.read().split(b"\n", 1)[1])
                os.chmod(dst, 0o755)
            print(f"[runbrief] installed: {dst}" + ("" if d in path_dirs else f"  (add {d} to PATH)"))
            return 0
        except OSError:
            continue
    print(f"[runbrief] no writable directory on PATH; use: python3 {src} ...")
    return 1

# --------------------------------------------------------------------------- cli

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):     # never crash on a console that cannot print a character
        try: stream.reconfigure(errors="replace")
        except (AttributeError, ValueError): pass
    argv = list(sys.argv[1:] if argv is None else argv)
    subs = {"run", "grep", "show", "stats", "clean", "install"}
    if argv and argv[0] in ("-V", "--version"):
        print(f"runbrief {__version__}"); return 0
    if argv and argv[0] not in subs and argv[0] not in ("-h", "--help"):
        argv = ["run"] + argv
    ap = argparse.ArgumentParser(prog="runbrief", description="Run noisy commands, hand your AI agent a brief.")
    sp = ap.add_subparsers(dest="sub", required=True)

    r = sp.add_parser("run", help="run a command and print a brief")
    r.add_argument("-n", "--name")
    r.add_argument("-T", "--timeout", type=float, help="kill the whole process tree after N seconds")
    r.add_argument("-e", "--max-errors", type=int, default=20)
    r.add_argument("--max-warnings", type=int, default=8)
    r.add_argument("-w", "--warnings", action="store_true", help="list warnings even on success")
    r.add_argument("-c", "--context", type=int, default=0, help="lines to show after each error")
    r.add_argument("-t", "--tail", type=int, default=None, help="tail lines on failure (default 5, or 25 if no error matched)")
    r.add_argument("--json", action="store_true")
    r.add_argument("cmd", nargs=argparse.REMAINDER)

    g = sp.add_parser("grep", help="search the latest log")
    g.add_argument("regex"); g.add_argument("-n", "--name"); g.add_argument("--log")
    g.add_argument("-C", "--context", type=int, default=2); g.add_argument("-m", "--max", type=int, default=30)

    s = sp.add_parser("show", help="print lines around a line number of the latest log")
    s.add_argument("-L", "--line", type=int, required=True); s.add_argument("-n", "--name"); s.add_argument("--log")
    s.add_argument("-C", "--context", type=int, default=20)

    st = sp.add_parser("stats", help="token savings so far")
    st.add_argument("--json", action="store_true"); st.add_argument("--reset", action="store_true")
    sp.add_parser("clean", help="delete all logs")
    ins = sp.add_parser("install", help="install a `runbrief` command on PATH")
    ins.add_argument("dir", nargs="?")

    a = ap.parse_args(argv)
    if a.sub == "run":
        return cmd_run(a)
    return {"grep": cmd_grep, "show": cmd_show, "stats": cmd_stats, "clean": cmd_clean,
            "install": cmd_install}[a.sub](a)

if __name__ == "__main__":
    sys.exit(main())
