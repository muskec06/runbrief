#!/usr/bin/env python3
"""runbrief - run noisy commands, hand your AI agent a brief.

Runs a build / test / simulator command, stores the full output in a log
file, and prints a compact, root-cause-first summary. The agent reads the
log only when the summary is not enough, and only the part it needs.

  runbrief run   [-n NAME] [-T SEC] [-H N] [-e N] [-c N] [-w] [--json] -- <command ...>
  runbrief grep  REGEX [-n NAME | --log PATH] [-C 2] [-m 30]
  runbrief show  -L LINE [-n NAME | --log PATH] [-C 20]
  runbrief stats [--svg FILE] [--since HOURS] [--json] [--reset]
  runbrief clean
  runbrief install [DIR]      # put a `runbrief` command on PATH

A single string after `--` runs through the shell (pipes, &&, env vars work).
`runbrief -n build -- make` (no subcommand) is the same as `runbrief run ...`.

Zero dependencies, Python 3.10+, Linux / macOS / Windows. MIT licence.
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

__version__ = "1.1.0"

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
    gi = os.path.join(base_dir(), ".gitignore")
    if not os.path.exists(gi):           # logs and stats must never end up in the user's repo
        try:
            with open(gi, "w") as f:
                f.write("# created by runbrief: keep logs/stats out of git, keep patterns.txt\n*\n!.gitignore\n!patterns.txt\n")
        except OSError:
            pass
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
    r"Segmentation fault|core dumped|Assertion .*failed|\bpanic:",
    r"(?-i:\b(FATAL|PANIC|FAULT)\b(?!\.\w))|(?-i:^Aborted( \(core dumped\))?$)",  # not file names like Fatal.pm
    r"^error(\[\w+\])?: ",                              # rustc, generic tools
    # ---- Linux tools: shells, package managers, sanitizers, kernel, services
    r"^E: |^dpkg: error|npm ERR!|^npm error |^fatal: |^configure: error|failed to solve:",
    r":\d+: \*\*\* |^g?make(\[\d+\])?: \*\*\* (No rule to make target|.*missing separator)",
    r"\bruntime error:|Sanitizer:|==\d+== (Invalid (read|write|free)|Conditional jump|Use of uninitialised|.*definitely lost: [1-9])",
    r"\b(command not found|No such file or directory|Permission denied|Cannot allocate memory|Out of memory"
    r"|Killed process|No space left on device|Read-only file system|Connection refused|Address already in use)\b",
    r"(?-i:\b(BUG:|Oops:|Call Trace:|general protection fault|[Kk]ernel panic|segfault at|Failed to start ))",
    r"\berror -\d+\b",                                  # kernel style: 'read error -71'
]
WARN_PATTERNS = [
    r":\d+(:\d+)?:\s*warning\b",
    r"\(\d+(,\d+)?\)\s*:\s*warning\b",
    r"\bwarning\s+(C|CS|LNK|MSB|NU|CA)\d{3,5}\b",
    r"^CMake Warning\b",
    r"^W: |npm WARN|^npm warn ",
    r"\b\w*Warning: ",
]
# Consequence lines: true failures, but never the root cause. Counted, not listed.
CASCADE_PATTERNS = [
    r"^g?make(\[\d+\])?: \*\*\* (?!No rule to make target|.*missing separator)", r"^ninja: build stopped", r"^FAILED: ",
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
            "required from", "requested here", "tests ", "test run", "% tests",
            "e: ", "w: ", "err!", "npm ", "denied", "not found", "no such file", "killed", "memory",
            "no space", "read-only", "refused", "in use", "oops", "bug:", "call trace", "invalid",
            "uninitialised", "lost:", "sanitizer", "segfault", "failed to")
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

def disp_path(p):
    """Shorter, still unambiguous path: relative inside the cwd, '.../a/b' for long outside paths."""
    cwd = os.getcwd().rstrip("\\/") + os.sep
    if p.startswith(cwd):
        return p[len(cwd):]
    parts = re.split(r"[\\/]", p)
    return ".../" + "/".join(parts[-3:]) if len(p) > 60 and len(parts) > 4 else p

SUFFIX_RE = re.compile(r"  (<- .+|\(from [^)]+\))$")

def fit(text, width=240):
    """Shorten text to width but never cut a trailing '<- file:line in f' / '(from file:line)'."""
    text = text.rstrip()
    cwd = os.getcwd().rstrip("\\/") + os.sep
    if cwd in text:
        text = text.replace(cwd, "")
    m = LOC_RE.match(text.strip())
    if m and len(m.group(1)) > 60:
        lead = text[:len(text) - len(text.lstrip())]
        text = lead + text.strip().replace(m.group(1), disp_path(m.group(1)), 1)
    if len(text) <= width:
        return text
    m = SUFFIX_RE.search(text)
    if m:
        suf = m.group(0)
        if len(suf) > width // 2:          # very deep path inside the suffix: keep its tail
            suf = re.sub(r"((?:<- |\(from ))(\S+?)(:\d+)", lambda g: g.group(1) + ".../"
                         + "/".join(re.split(r"[\\/]", g.group(2))[-2:]) + g.group(3), suf, count=1)
        return text[:max(20, width - 6 - len(suf))] + " ..." + suf
    return text[:width] + " ..."

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
        if key in table:
            table[key][2] += 1
        elif len(table) < 5000:
            table[key] = [n, fit(text), 1, []]
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
            where = f"  <- {disp_path(user[-1][0])}:{user[-1][1]} in {user[-1][2]}" if user else ""
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
    head_lines = []
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
            if len(head_lines) < a.head:
                head_lines.append((n, s))
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
            + f" | warnings {len(an.warnings)}" + trim_note + f" | log {rel}"
            + (f" | read: runbrief show -n {name} -L 1 -C 40" if rc == 0 and not an.errors and not head_lines else ""))
        for r in an.results[-2:]:
            out(f"tests: {r}")
        if head_lines:
            out(f"head (first {len(head_lines)} of {n:,} lines):")
            for k, s in head_lines:
                out(f"  {short(s, 200)}")
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

def collect_stats(since_hours=None):
    sp = os.path.join(base_dir(), "stats.jsonl")
    rows = []
    if os.path.exists(sp):
        for ln in open(sp, encoding="utf-8"):
            try: rows.append(json.loads(ln))
            except ValueError: pass
    if since_hours:
        cut = time.time() - since_hours * 3600
        rows = [r for r in rows if r.get("ts", 0) >= cut]
    runs = [r for r in rows if r.get("kind") == "run"]
    follow = [r for r in rows if r.get("kind") in ("grep", "show")]
    # An agent never reads more than its tool-output limit of one command, so each run's raw
    # output is counted only up to RUNBRIEF_STATS_CAP_TOKENS (0 = no cap).
    cap_tok = int(env_num("RUNBRIEF_STATS_CAP_TOKENS", 25000))
    capb = cap_tok * 4 if cap_tok > 0 else float("inf")
    for r in runs:
        r["_raw"] = min(r.get("raw_bytes", 0), capb)
    raw = sum(r["_raw"] for r in runs)
    raw_uncapped = sum(r.get("raw_bytes", 0) for r in runs)
    brief = sum(r.get("brief_bytes", 0) for r in runs)
    fol = sum(r.get("brief_bytes", 0) for r in follow)
    read = brief + fol
    by = OrderedDict()
    for r in runs:
        b = by.setdefault(r.get("name") or "?", dict(runs=0, failed=0, raw=0, read=0))
        b["runs"] += 1; b["raw"] += r["_raw"]; b["read"] += r.get("brief_bytes", 0)
        b["failed"] += r.get("status") != "ok"
    for r in follow:                           # follow-up reads count against their command
        b = by.get(r.get("name") or "?")
        if b: b["read"] += r.get("brief_bytes", 0)
    files = glob.glob(os.path.join(log_dir(), "*.log*"))
    ts = [r.get("ts", 0) for r in rows if r.get("ts")]
    return dict(
        runs=len(runs), failed=sum(1 for r in runs if r.get("status") != "ok"), followups=len(follow),
        raw_tokens=tok(raw), raw_uncapped_tokens=tok(raw_uncapped), cap_tokens=cap_tok,
        capped_runs=sum(1 for r in runs if r.get("raw_bytes", 0) > capb), brief_tokens=tok(brief), followup_tokens=tok(fol), read_tokens=tok(read),
        net_saved_tokens=tok(raw - read), reduction_pct=round(100 * (1 - read / raw), 2) if raw else 0.0,
        disk_mb=round(sum(os.path.getsize(f) for f in files) / MB, 2), log_files=len(files),
        first_ts=min(ts) if ts else None, last_ts=max(ts) if ts else None,
        by_name={k: dict(runs=v["runs"], failed=v["failed"], raw_tokens=tok(v["raw"]), read_tokens=tok(v["read"]))
                 for k, v in sorted(by.items(), key=lambda kv: -(kv[1]["raw"] - kv[1]["read"]))},
        recent=[dict(ts=r.get("ts"), name=r.get("name"), status=r.get("status"), seconds=r.get("seconds"),
                     raw_tokens=tok(r["_raw"]), capped=r.get("raw_bytes", 0) > capb,
                     read_tokens=tok(r.get("brief_bytes", 0)))
                for r in runs[-6:]])

def can_unicode():
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc and not os.environ.get("RUNBRIEF_ASCII")

def bar(frac, width, uni):
    frac = max(0.0, min(1.0, frac))
    if not uni:
        n = int(round(frac * width)); return "#" * n + "." * (width - n)
    eighths = int(round(frac * width * 8))
    full, part = divmod(eighths, 8)
    return ("\u2588" * full + ("" if part == 0 else " \u258f\u258e\u258d\u258c\u258b\u258a\u2589"[part])).ljust(width, "\u00b7")

def fmt_pct(x):
    if x >= 99.995 and x < 100:
        return ">99.99%"
    return f"{x:.2f}%" if x >= 99.9 else f"{x:.1f}%"

def when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"

def render_stats_text(st, uni):
    h = "\u2500" if uni else "-"
    arrow = "\u2192" if uni else "->"
    L = []
    span = f" | {when(st['first_ts'])} {arrow} {when(st['last_ts'])[11:]}" if st["first_ts"] else ""
    L.append(f"runbrief stats | {st['runs']} runs ({st['failed']} failed) | {st['followups']} follow-up reads{span}")
    L.append(h * 72)
    capnote = (f"(raw output; {st['capped_runs']} huge run(s) counted as {st['cap_tokens']:,})"
               if st["capped_runs"] else "(raw output of every command)")
    L.append(f"  would have read   {st['raw_tokens']:>12,} tokens   {capnote}")
    L.append(f"  actually read     {st['read_tokens']:>12,} tokens   (briefs {st['brief_tokens']:,} + follow-ups {st['followup_tokens']:,})")
    L.append(f"  saved             {st['net_saved_tokens']:>12,} tokens")
    L.append(f"  {bar(st['reduction_pct'] / 100, 52, uni)}  {fmt_pct(st['reduction_pct'])} saved")
    if st["by_name"]:
        total = max(1, st["net_saved_tokens"])
        L.append("")
        L.append(f"  {'command':<12}{'runs':>5}{'raw tokens':>14}{'read':>9}{'saved':>9}   share of savings")
        for k, v in list(st["by_name"].items())[:8]:
            sv = v["raw_tokens"] - v["read_tokens"]
            pct = 100 * (1 - v["read_tokens"] / v["raw_tokens"]) if v["raw_tokens"] else 0
            L.append(f"  {k[:12]:<12}{v['runs']:>5}{v['raw_tokens']:>14,}{v['read_tokens']:>9,}{fmt_pct(pct):>9}   "
                     f"{bar(sv / total, 18, uni)} {100 * sv / total:4.0f}%")
    if st["recent"]:
        L.append("")
        L.append("  recent runs")
        for r in reversed(st["recent"]):
            ok = ("\u2713" if uni else "ok") if r["status"] == "ok" else ("\u2717" if uni else "!!")
            L.append(f"   {ok} {when(r['ts'])[11:]}  {str(r['name'])[:12]:<12} {r['raw_tokens']:>11,}{'+' if r.get('capped') else ' '}"
                     f"{arrow} {r['read_tokens']:>5,} tokens  {r['status'] or ''}")
    L.append(h * 72)
    if st["capped_runs"]:
        L.append(f"  + capped at {st['cap_tokens']:,} tokens per run (an agent's tool-output limit); "
                 f"uncapped raw total {st['raw_uncapped_tokens']:,}")
    L.append(f"  logs {st['disk_mb']} MB in {st['log_files']} file(s) | tokens = chars/4 | runbrief stats --svg card.svg")
    return "\n".join(L)

def render_stats_svg(st, title="runbrief"):
    """Self-contained SVG card (dark), safe to embed in a README."""
    from xml.sax.saxutils import escape as E
    W = 820
    names = list(st["by_name"].items())[:5]
    recent = list(reversed(st["recent"]))[:5]
    H = 318 + 34 * len(names) + (44 + 26 * len(recent) if recent else 0)
    mono = "ui-monospace,SFMono-Regular,Menlo,Consolas,'DejaVu Sans Mono',monospace"
    sans = "-apple-system,'Segoe UI',Helvetica,Arial,'DejaVu Sans',sans-serif"
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="runbrief token savings">',
         f'<rect width="{W}" height="{H}" rx="16" fill="#0d1117"/>',
         f'<rect x="1" y="1" width="{W-2}" height="{H-2}" rx="15" fill="none" stroke="#30363d"/>',
         f'<text x="32" y="50" font-family="{mono}" font-size="26" font-weight="700" fill="#e6edf3">{E(title)}</text>',
         f'<text x="{32 + 15 * len(title) + 14}" y="49" font-family="{sans}" font-size="15" fill="#8b949e">'
         f'{st["runs"]} runs \u00b7 {st["failed"]} failed \u00b7 {st["followups"]} follow-up reads'
         + (f' \u00b7 {E(when(st["first_ts"]))} \u2192 {E(when(st["last_ts"])[11:])}' if st["first_ts"] else "") + '</text>']
    cards = [("would have read", f'{st["raw_tokens"]:,}', "#f85149"), ("actually read", f'{st["read_tokens"]:,}', "#3fb950"),
             ("saved", fmt_pct(st["reduction_pct"]), "#58a6ff")]
    for i, (lab, val, col) in enumerate(cards):
        x = 32 + i * 258
        o.append(f'<rect x="{x}" y="76" width="240" height="96" rx="12" fill="#161b22" stroke="#30363d"/>')
        o.append(f'<text x="{x+18}" y="104" font-family="{sans}" font-size="14" fill="#8b949e">{lab}</text>')
        o.append(f'<text x="{x+18}" y="150" font-family="{sans}" font-size="34" font-weight="700" fill="{col}">{val}</text>')
    o.append(f'<text x="{32+18+170}" y="104" font-family="{sans}" font-size="12" fill="#6e7681">tokens</text>')
    o.append(f'<text x="{290+18+150}" y="104" font-family="{sans}" font-size="12" fill="#6e7681">tokens</text>')
    o.append(f'<text x="{548+222}" y="104" text-anchor="end" font-family="{sans}" font-size="12" fill="#6e7681">'
             f'{st["net_saved_tokens"]:,} tokens</text>')
    # overall bar: read vs raw (read part is drawn with a minimum width so it stays visible)
    y = 196
    frac = st["read_tokens"] / st["raw_tokens"] if st["raw_tokens"] else 0
    o.append(f'<rect x="32" y="{y}" width="756" height="14" rx="7" fill="#f85149" opacity="0.85"/>')
    o.append(f'<rect x="32" y="{y}" width="{max(6, 756 * frac):.1f}" height="14" rx="7" fill="#3fb950"/>')
    o.append(f'<text x="32" y="{y+36}" font-family="{sans}" font-size="13" fill="#8b949e">'
             f'green = what the agent read (briefs {st["brief_tokens"]:,} + follow-ups {st["followup_tokens"]:,}); '
             f'red = raw output it did not have to read</text>')
    y += 64
    o.append(f'<text x="32" y="{y}" font-family="{sans}" font-size="13" fill="#6e7681">COMMAND</text>'
             f'<text x="260" y="{y}" font-family="{sans}" font-size="13" fill="#6e7681">RAW \u2192 READ (tokens)</text>'
             f'<text x="520" y="{y}" font-family="{sans}" font-size="13" fill="#6e7681">SHARE OF SAVINGS</text>')
    total = max(1, st["net_saved_tokens"])
    for k, v in names:
        y += 34
        sv = v["raw_tokens"] - v["read_tokens"]
        o.append(f'<text x="32" y="{y}" font-family="{mono}" font-size="15" fill="#e6edf3">{E(k[:18])}'
                 f'<tspan fill="#6e7681" font-size="13"> \u00d7{v["runs"]}</tspan></text>')
        o.append(f'<text x="260" y="{y}" font-family="{mono}" font-size="15" fill="#8b949e">{v["raw_tokens"]:,} '
                 f'<tspan fill="#6e7681">\u2192</tspan> <tspan fill="#3fb950">{v["read_tokens"]:,}</tspan></text>')
        o.append(f'<rect x="520" y="{y-12}" width="210" height="12" rx="6" fill="#21262d"/>')
        o.append(f'<rect x="520" y="{y-12}" width="{max(4, 210 * sv / total):.1f}" height="12" rx="6" fill="#58a6ff"/>')
        o.append(f'<text x="740" y="{y}" font-family="{sans}" font-size="13" fill="#8b949e">{100 * sv / total:.0f}%</text>')
    if recent:
        y += 44
        o.append(f'<text x="32" y="{y}" font-family="{sans}" font-size="13" fill="#6e7681">RECENT RUNS</text>')
        for r in recent:
            y += 26
            ok = r["status"] == "ok"
            o.append(f'<circle cx="38" cy="{y-5}" r="5" fill="{"#3fb950" if ok else "#f85149"}"/>')
            f14 = f'font-family="{mono}" font-size="14"'
            o.append(f'<text x="52" y="{y}" {f14} fill="#8b949e">{E(when(r["ts"])[11:])}</text>')
            o.append(f'<text x="112" y="{y}" {f14} fill="#e6edf3">{E(str(r["name"])[:14])}</text>')
            o.append(f'<text x="330" y="{y}" text-anchor="end" {f14} fill="#8b949e">'
                     f'{r["raw_tokens"]:,}{"+" if r.get("capped") else ""}</text>')
            o.append(f'<text x="342" y="{y}" {f14} fill="#6e7681">\u2192</text>')
            o.append(f'<text x="420" y="{y}" text-anchor="end" {f14} fill="#3fb950">{r["read_tokens"]:,}</text>')
            o.append(f'<text x="440" y="{y}" {f14} fill="{"#8b949e" if ok else "#f85149"}">{E(str(r["status"]))}</text>')
    o.append(f'<text x="{W-32}" y="{H-18}" text-anchor="end" font-family="{sans}" font-size="12" fill="#6e7681">'
             f'tokens = chars/4'
             + (f' \u00b7 + = huge output counted as {st["cap_tokens"]:,} (tool limit)' if st["capped_runs"] else "")
             + ' \u00b7 github.com/muskec06/runbrief</text>')
    o.append("</svg>")
    return "\n".join(o)

def cmd_stats(a):
    sp = os.path.join(base_dir(), "stats.jsonl")
    if a.reset:
        if os.path.exists(sp): os.remove(sp)
        print("[runbrief] stats reset"); return 0
    st = collect_stats(a.since)
    if a.json:
        print(json.dumps(st, indent=1)); return 0
    if a.svg:
        with open(a.svg, "w", encoding="utf-8") as f:
            f.write(render_stats_svg(st))
        print(f"[runbrief] wrote {a.svg} ({st['runs']} runs, {fmt_pct(st['reduction_pct'])} saved)")
        return 0
    if not st["runs"]:
        print("[runbrief] no runs recorded yet in " + os.path.relpath(base_dir())); return 0
    print(render_stats_text(st, can_unicode()))
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
    r.add_argument("-H", "--head", type=int, default=0,
                   help="also print the first N lines (for big outputs that are themselves the answer)")
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
    st.add_argument("--svg", metavar="FILE", help="write a shareable SVG card")
    st.add_argument("--since", type=float, metavar="HOURS", help="only the last N hours")
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
