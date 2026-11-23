#!/usr/bin/env python3
"""Render the runbrief README demo video (1280x720, 30 fps) and a GIF.

  python3 docs/video/make_video.py RAW.txt BRIEF.txt OUTDIR [--frames t1,t2,...]

RAW.txt / BRIEF.txt are real outputs of the benchmark's CMake scenario.
Needs Pillow and ffmpeg. With --frames it only writes PNG stills for checking.
"""
import math, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 30
BG, PANEL, BAR = (13, 17, 23), (22, 27, 34), (33, 38, 45)
FG, DIM, MUTED = (230, 237, 243), (139, 148, 158), (72, 79, 88)
RED, GREEN, BLUE, YELLOW, PURPLE = (248, 81, 73), (63, 185, 80), (88, 166, 255), (210, 153, 34), (188, 140, 255)
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONOB = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SANSB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_fc = {}
def F(path, size):
    k = (path, size)
    if k not in _fc:
        _fc[k] = ImageFont.truetype(path, size)
    return _fc[k]

def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3

def mix(c, a):
    return tuple(int(BG[i] + (c[i] - BG[i]) * a) for i in range(3))

def center(d, y, text, font, fill):
    w = d.textlength(text, font=font)
    d.text(((W - w) / 2, y), text, font=font, fill=fill)

def window(d, x, y, w, h, title):
    d.rounded_rectangle([x, y, x + w, y + h], 14, fill=PANEL, outline=BAR, width=2)
    d.rounded_rectangle([x, y, x + w, y + 38], 14, fill=BAR)
    d.rectangle([x, y + 24, x + w, y + 38], fill=BAR)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([x + 18 + i * 22, y + 13, x + 30 + i * 22, y + 25], fill=c)
    tw = d.textlength(title, font=F(SANS, 15))
    d.text((x + (w - tw) / 2, y + 10), title, font=F(SANS, 15), fill=DIM)

def counter(d, x, y, label, value, color):
    d.text((x, y), label, font=F(SANS, 18), fill=DIM)
    d.text((x, y + 24), f"{value:,} tokens", font=F(SANSB, 40), fill=color)

def caption(d, text, a=1.0, sub=None):
    center(d, 34, text, F(SANSB, 34), mix(FG, a))
    if sub:
        center(d, 80, sub, F(SANS, 20), mix(DIM, a))

def clip(d, text, font, maxw):
    if d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"

# ------------------------------------------------------------------ scenes

def s_title(t, D):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    a = ease(t / 0.8)
    # subtle grid of "log lines" fading behind
    for i in range(22):
        y = 40 + i * 30
        ww = 200 + (i * 137) % 700
        if 200 < y < 500 or y > 620:
            continue                      # keep the text area clean
        d.rounded_rectangle([80 + (i * 53) % 200, y, 80 + (i * 53) % 200 + ww, y + 8], 4, fill=mix(BAR, 0.45))
    center(d, 230, "runbrief", F(MONOB, 112), mix(FG, a))
    b = ease((t - 0.6) / 0.8)
    center(d, 380, "Run noisy commands. Hand your AI agent a brief.", F(SANS, 34), mix(BLUE, b))
    c = ease((t - 1.3) / 0.8)
    center(d, 450, "build logs · test runs · emulator traces  →  root cause in a few lines", F(SANS, 22), mix(DIM, c))
    center(d, 640, "by @muskec06", F(SANS, 18), mix(MUTED, c))
    return im

def s_raw(t, D, raw):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    caption(d, "Without runbrief, the agent reads every line", 1, "and re-reads them on every following turn of the session")
    x, y, w, h = 60, 130, 1160, 460
    window(d, x, y, w, h, "bash: make VERBOSE=1 -k -j2")
    font = F(MONO, 13); lh = 17
    rows = (h - 60) // lh
    prompt = "$ make -C build VERBOSE=1 -k -j2"
    shown = int(len(raw) * ease((t - 0.6) / (D - 1.6))) if t > 0.6 else 0
    lines = [prompt] + raw[:shown]
    view = lines[-rows:]
    for i, ln in enumerate(view):
        col = FG if ln.startswith("$") else (RED if "error" in ln else (YELLOW if "***" in ln else DIM))
        d.text((x + 20, y + 52 + i * lh), clip(d, ln, font, w - 40), font=font, fill=col)
    frac = shown / len(raw)
    counter(d, 900, 610, "context cost", int(16638 * frac), RED)
    d.text((60, 640), f"{shown:,} / {len(raw):,} lines", font=F(SANS, 18), fill=DIM)
    return im

def s_brief(t, D, brief):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    caption(d, "With runbrief, the root cause in 149 tokens", 1, "full log saved to disk, opened only when needed")
    x, y, w, h = 60, 130, 1160, 360
    window(d, x, y, w, h, "bash")
    cmd = '$ runbrief -n build -- "make -C build VERBOSE=1 -k -j2"'
    k = int(len(cmd) * ease(t / 1.2))
    font = F(MONO, 15); lh = 26
    d.text((x + 20, y + 56), cmd[:k], font=font, fill=FG)
    n = int(max(0, (t - 1.5)) / 0.35)
    for i, ln in enumerate(brief[:n]):
        yy = y + 56 + (i + 1) * lh + 6
        if ln.startswith("[runbrief]"):
            parts = [(ln.split("|")[0], RED), ("|" + "|".join(ln.split("|")[1:5]), DIM)]
            xx = x + 20
            for txt, col in parts:
                txt = clip(d, txt, font, w - 40 - (xx - x - 20)); d.text((xx, yy), txt, font=font, fill=col); xx += d.textlength(txt, font=font)
            continue
        col = DIM
        if "error:" in ln: col = FG
        if ln.startswith("build-system"): col = YELLOW
        if ln.startswith("more:"): col = MUTED
        txt = clip(d, ln, font, w - 40)
        if "[x73]" in ln:
            base = txt.replace("[x73]", "").rstrip()
            d.text((x + 20, yy), base, font=font, fill=col)
            bx = x + 20 + d.textlength(base + "  ", font=font)
            d.rounded_rectangle([bx - 4, yy - 2, bx + d.textlength("[x73]", font=font) + 4, yy + 20], 5, fill=(70, 30, 30))
            d.text((bx, yy), "[x73]", font=font, fill=RED)
        else:
            d.text((x + 20, yy), txt, font=font, fill=col)
    # callouts
    c = ease((t - 4.2) / 0.6)
    if c > 0:
        notes = [("73 copies of one header error → one line with a count", RED),
                 ("the second, unrelated bug is not buried", GREEN),
                 ("75 make failure lines → one consequence line", YELLOW)]
        for i, (txt, col) in enumerate(notes):
            d.ellipse([80, 518 + i * 34, 92, 530 + i * 34], fill=mix(col, c))
            d.text((104, 512 + i * 34), txt, font=F(SANS, 20), fill=mix(FG, c))
    if n >= 1:
        counter(d, 900, 520, "context cost", 149, GREEN)
        d.text((900, 590), "was 16,638  (−99.1%)", font=F(SANS, 18), fill=mix(DIM, ease((t - 2) / 0.5)))
    return im

ROWS = [("CMake + make build, 2 bugs", 16638, 149),
        ("MSBuild / C#, 40 projects", 15746, 189),
        ("Python crash after 800 log lines", 12519, 77),
        ("pytest -v, 400 tests, 3 failing", 8534, 172),
        ("200k-line emulator trace", 3850018, 84)]

def s_numbers(t, D):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    caption(d, "Measured on real builds, tests and traces", 1, "gcc 13 · clang 18 · CMake · make · ninja · pytest  —  bench/run_bench.py")
    x0, x1 = 420, 1180
    for i, (name, raw, br) in enumerate(ROWS):
        y = 140 + i * 66
        g = ease((t - 0.2 - i * 0.18) / 0.9)
        d.text((60, y + 6), name, font=F(SANS, 20), fill=FG)
        full = x1 - x0
        d.rounded_rectangle([x0, y, x0 + max(6, full * g), y + 18], 6, fill=mix(RED, 0.85))
        d.text((x0, y + 22), f"raw {raw:,}", font=F(SANS, 15), fill=DIM)
        gb = ease((t - 1.2 - i * 0.18) / 0.6)
        bw = max(4, full * br / raw)
        d.rounded_rectangle([x0, y + 40, x0 + bw * gb + 0.5, y + 52], 4, fill=GREEN)
        if gb > 0.5:
            d.text((x0 + bw + 12, y + 37), f"brief {br}", font=F(SANSB, 15), fill=GREEN)
    stats = [("98.3%", "fewer tokens on\nbuilds and tests", GREEN), ("22 / 22", "root causes kept\nin 12 scenarios", BLUE),
             ("≈0%", "build-time overhead\n(compiler dominates)", PURPLE)]
    for i, (big, small, col) in enumerate(stats):
        a = ease((t - 2.6 - i * 0.35) / 0.6)
        cx = 60 + i * 400
        d.text((cx, 495), big, font=F(SANSB, 58), fill=mix(col, a))
        for j, s in enumerate(small.split("\n")):
            d.text((cx, 570 + j * 26), s, font=F(SANS, 20), fill=mix(DIM, a))
    return im

def s_how(t, D):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    caption(d, "One prompt. Any agent.", 1, "paste it as the first message of a chat with code execution")
    x, y, w, h = 110, 140, 1060, 200
    window(d, x, y, w, h, "first message")
    txt = ["Before anything else: git clone --depth 1 https://github.com/muskec06/runbrief /tmp/runbrief",
           "and follow /tmp/runbrief/AGENTS.md for this whole conversation (run its setup step).",
           "Task: fix the UART driver build"]
    total = sum(len(s) for s in txt)
    k = int(total * ease(t / 2.6))
    for i, s in enumerate(txt):
        part = s[:max(0, min(len(s), k))]; k -= len(s)
        d.text((x + 24, y + 62 + i * 34), part, font=F(MONO, 16), fill=FG if i < 2 else BLUE)
    items = [("Claude chat", "prompt or skill"), ("Claude Code", "hooks enforce it"), ("Any shell agent", "AGENTS.md rules"),
             ("1 Python file", "zero dependencies · MIT")]
    for i, (a_, b_) in enumerate(items):
        a = ease((t - 2.4 - i * 0.25) / 0.6)
        cx = 110 + i * 270
        d.rounded_rectangle([cx, 400, cx + 250, 500], 12, fill=mix(PANEL, a), outline=mix(BAR, a), width=2)
        d.text((cx + 18, 418), a_, font=F(SANSB, 22), fill=mix(FG, a))
        d.text((cx + 18, 458), b_, font=F(SANS, 17), fill=mix(DIM, a))
    a = ease((t - 3.6) / 0.6)
    center(d, 560, "logs capped, rotated and gzipped  ·  timeouts kill the whole process tree  ·  net savings with  runbrief stats",
           F(SANS, 18), mix(DIM, a))
    return im

def s_end(t, D):
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    a = ease(t / 0.8)
    center(d, 220, "runbrief", F(MONOB, 96), mix(FG, a))
    center(d, 360, "github.com/muskec06/runbrief", F(MONO, 34), mix(BLUE, ease((t - 0.4) / 0.8)))
    center(d, 430, "★  Star it if it saves you tokens", F(SANS, 24), mix(YELLOW, ease((t - 0.9) / 0.8)))
    center(d, 640, "by @muskec06 · MIT licence", F(SANS, 18), mix(MUTED, ease((t - 0.9) / 0.8)))
    return im

# ------------------------------------------------------------------ render

def scenes(raw, brief):
    return [("01_title", 3.5, s_title), ("02_raw", 6.5, lambda t, D: s_raw(t, D, raw)),
            ("03_brief", 8.0, lambda t, D: s_brief(t, D, brief)), ("04_numbers", 7.0, s_numbers),
            ("05_how", 6.5, s_how), ("06_end", 4.0, s_end)]

def encode(name, D, fn, out):
    tmp = os.path.join(out, f".{name}.tmp.mp4"); dst = os.path.join(out, f"{name}.mp4")
    fade = f"fade=t=in:st=0:d=0.35,fade=t=out:st={D - 0.35:.2f}:d=0.35"
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(FPS), "-i", "-", "-vf", f"{fade},format=yuv420p", "-c:v", "libx264", "-crf", "18",
                          "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(FPS), tmp], stdin=subprocess.PIPE)
    for i in range(int(round(D * FPS))):
        p.stdin.write(fn(i / FPS, D).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        sys.exit(f"ffmpeg failed on {name}")
    os.replace(tmp, dst)
    return dst

def main():
    raw_path, brief_path, out = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    raw = [l.rstrip("\n") for l in open(raw_path, encoding="utf-8")]
    brief = [l.rstrip("\n") for l in open(brief_path, encoding="utf-8") if l.strip()]
    sc = scenes(raw, brief)
    if "--frames" in sys.argv:
        for spec in sys.argv[sys.argv.index("--frames") + 1].split(","):
            idx, tt = spec.split("@")
            name, D, fn = sc[int(idx)]
            fn(float(tt), D).save(os.path.join(out, f"still_{name}_{tt}.png"))
        return
    files = [encode(n, D, fn, out) for n, D, fn in sc]
    lst = os.path.join(out, "list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{os.path.abspath(x)}'\n" for x in files)
    mp4 = os.path.join(out, "runbrief-demo.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                    "-movflags", "+faststart", mp4], check=True)
    gif = os.path.join(out, "runbrief-demo.gif")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-vf",
                    "fps=12,scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=none:diff_mode=rectangle", gif], check=True)
    print(mp4, os.path.getsize(mp4) // 1024, "KB;", gif, os.path.getsize(gif) // 1024, "KB")

if __name__ == "__main__":
    main()
