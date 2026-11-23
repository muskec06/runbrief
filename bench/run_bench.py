#!/usr/bin/env python3
"""Reproducible benchmark for runbrief.

Builds real projects (gcc / clang / CMake / make / ninja / pytest / python)
plus emulator-style workloads, runs every scenario twice - raw, and through
runbrief - and records:

  * raw output size vs brief size (tokens estimated as chars/4)
  * fidelity: are the root-cause markers present in the brief?
  * wall-time overhead of runbrief
  * on-disk log size, plain and gzip

Usage:  python3 bench/run_bench.py [--out bench/results]
Needs:  python3, and for the C/C++ scenarios gcc/g++, clang++, cmake, make, ninja.
Scenarios whose tools are missing are skipped and reported as such.
"""
import argparse, gzip, json, os, platform, shutil, subprocess, sys, tempfile, textwrap, time

HERE = os.path.dirname(os.path.abspath(__file__))
RB = os.path.join(os.path.dirname(HERE), "runbrief.py")
PY = sys.executable

# ------------------------------------------------------------------ project generators

def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(text).lstrip())

def gen_cpp_emulator(root, n_ops=60, n_periph=12, header_bug=False, uart_bug=False, warnings=False):
    """A small but real C++ 'emulator': CPU opcode handlers + peripherals."""
    w(f"{root}/include/regs.h", f"""
        #pragma once
        #include <cstdint>
        struct Regs {{ uint32_t r[16]; uint32_t pc; uint32_t flags; }};
        inline uint32_t reg_read(const Regs& s, int i) {{ return s.r[i & 15]; }}
        {"inline uint32_t reg_mask(const Regs& s) { return s.r[0] & MASK_ALL; }" if header_bug else ""}
    """)
    ops = []
    for i in range(n_ops):
        ops.append(f"src/cpu/op_{i:03d}.cpp")
        w(f"{root}/src/cpu/op_{i:03d}.cpp", f"""
            #include "regs.h"
            #include <vector>
            #include <string>
            void op_{i:03d}(Regs& s) {{
                {"int unused_" + str(i) + " = 0;" if warnings and i % 4 == 0 else ""}
                std::vector<uint32_t> tmp(4, s.pc);
                s.r[{i % 16}] = reg_read(s, {(i + 1) % 16}) + tmp[{i % 4}] + {i};
                s.pc += 4;
            }}
        """)
    per = []
    for i in range(n_periph):
        name = ["uart", "spi", "i2c", "dma", "timer", "gpio", "adc", "dac", "can", "usb", "rtc", "wdt"][i % 12]
        per.append(f"src/periph/{name}.cpp")
        bug = uart_bug and name == "uart"
        w(f"{root}/src/periph/{name}.cpp", f"""
            #include "regs.h"
            #include <map>
            #include <cstdint>
            static std::map<uint32_t, uint32_t> {name}_mmio;
            uint32_t {name}_read(uint32_t addr) {{ return {name}_mmio[addr]; }}
            void {name}_write(uint32_t addr, uint32_t v) {{ {name}_mmio[addr] = v{" + baud_divisor" if bug else ""}; }}
        """)
    w(f"{root}/src/main.cpp", """
        #include "regs.h"
        int main() { Regs s{}; return (int)reg_read(s, 0); }
    """)
    srcs = " ".join(ops + per + ["src/main.cpp"])
    flags = "-Wall -Wextra" if warnings else ""
    w(f"{root}/CMakeLists.txt", f"""
        cmake_minimum_required(VERSION 3.16)
        project(emu CXX)
        set(CMAKE_CXX_STANDARD 17)
        add_compile_options({flags})
        include_directories(include)
        add_executable(emu {srcs})
    """)

def gen_link_error(root):
    w(f"{root}/drv.h", "void dma_start(int ch);\nvoid dma_stop(int ch);\n")
    w(f"{root}/drv.cpp", '#include "drv.h"\nvoid dma_stop(int ch) { (void)ch; }\n')
    w(f"{root}/main.cpp", '#include "drv.h"\nint main() { dma_start(1); dma_stop(1); return 0; }\n')

def gen_template_error(root):
    w(f"{root}/bus.cpp", """
        #include <map>
        #include <vector>
        #include <string>
        #include <algorithm>
        #include <memory>
        struct Device { std::string name; int irq; };
        int main() {
            std::map<std::string, std::vector<std::unique_ptr<Device>>> bus;
            std::vector<std::unique_ptr<Device>> devs;
            auto copy = devs;                                // unique_ptr is not copyable
            std::sort(bus.begin(), bus.end());               // map iterators are not random access
            bus["uart"].push_back(Device{"uart0", 5});       // wrong element type
            return 0;
        }
    """)

def gen_pytest(root, n=400):
    w(f"{root}/emu/__init__.py", "")
    w(f"{root}/emu/cpu.py", """
        MASK = 0xFFFFFFFF
        def add(a, b): return (a + b) & MASK
        def sub(a, b): return (a - b) & MASK
        def shl(a, n): return (a << n) & MASK
        def decode(word):
            op = (word >> 26) & 0x3F
            if op > 0x30: raise ValueError(f"illegal opcode 0x{op:02X}")
            return op
    """)
    w(f"{root}/tests/test_cpu.py", f"""
        import pytest
        from emu import cpu
        @pytest.mark.parametrize("a,b", [(i, i * 7) for i in range({n // 2})])
        def test_add(a, b): assert cpu.add(a, b) == (a + b) & cpu.MASK
        @pytest.mark.parametrize("a,b", [(i * 3, i) for i in range({n // 2 - 3})])
        def test_sub(a, b): assert cpu.sub(a, b) == a - b
        def test_shl_overflow(): assert cpu.shl(0x80000000, 1) == 0x100000000
        def test_decode_illegal(): assert cpu.decode(0xFC000000) == 0x3F
        def test_sub_wrap(): assert cpu.sub(0, 1) == -1
    """)

def gen_python_crash(root):
    w(f"{root}/emu_cfg.py", """
        import json, logging
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        log = logging.getLogger("emu")
        def parse_periph(p):
            return {"base": int(p["base"], 16), "irq": p["irq"], "clock": p["clock_hz"]}
        def build_board(cfg):
            for i in range(800):
                log.info("mapping region %d at 0x%08X", i, 0x40000000 + i * 0x100)
            return [parse_periph(p) for p in cfg["peripherals"]]
        cfg = json.loads('{"peripherals": [{"base": "0x40001000", "irq": 5, "clock_hz": 48000000},'
                         ' {"base": "0x40002000", "irq": 6}]}')
        build_board(cfg)
    """)

EMU_SIM = r'''
import sys, time
lines, fault_at, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
w = sys.stdout.write
for i in range(lines):
    w("[%09d] pc=0x%08X op=%-5s r0=0x%08X r1=0x%08X sp=0x%08X\n" % (i, 0x1000 + 4 * i, ("ldr", "str", "add", "b", "mov")[i % 5], i * 3, i ^ 0xFFFF, 0x20008000 - (i % 64) * 4))
    if i == fault_at:
        if mode == "fault":
            w("[EMU] FAULT: illegal opcode 0xFF at pc=0x%08X (HardFault escalated)\n" % (0x1000 + 4 * i))
        elif mode == "quiet":
            w("uart0: unexpected register value CR1=0x0000DEAD after reset (expected 0x00000000)\n")
sys.exit(1)
'''

ENDLESS_SIM = r'''
import sys, time
i = 0
while True:
    sys.stdout.write("".join("[sim] t=%010d bus=AHB addr=0x%08X data=0x%08X\n" % (i + k, 0x40000000 + (i + k) % 4096, (i + k) * 2654435761 & 0xFFFFFFFF) for k in range(2000)))
    sys.stdout.flush(); i += 2000
    if i == 100000: sys.stdout.write("dma.c:211: error: DMA channel 3 underrun\n")
    time.sleep(0.002)
'''

def gen_msbuild_fixture(path, n_proj=40):
    """MSBuild/.NET output in the real format (no .NET SDK available in the benchmark host).
    MSBuild prints each error/warning during the build AND again in the final summary."""
    out = ["MSBuild version 17.9.8+b34f75857 for .NET", "  Determining projects to restore..."]
    out += [f"  Restored C:\\src\\Emu\\Emu.Periph{i:02d}\\Emu.Periph{i:02d}.csproj (in {120 + i} ms)." for i in range(n_proj)]
    diags = []
    for i in range(n_proj):
        p = f"C:\\src\\Emu\\Emu.Periph{i:02d}\\Emu.Periph{i:02d}.csproj"
        for k in range(3):
            d = f"C:\\src\\Emu\\Emu.Periph{i:02d}\\Device{k}.cs({10 + k},16): warning CS8618: Non-nullable property 'Bus' must contain a non-null value when exiting constructor. Consider declaring the property as nullable. [{p}]"
            out.append(d); diags.append(d)
        if i == 17:
            for d in [f"C:\\src\\Emu\\Emu.Periph17\\UartDevice.cs(88,13): error CS0103: The name 'baudDivisor' does not exist in the current context [{p}]",
                      f"C:\\src\\Emu\\Emu.Periph17\\UartDevice.cs(95,22): error CS1061: 'IBus' does not contain a definition for 'WriteWord' and no accessible extension method 'WriteWord' accepting a first argument of type 'IBus' could be found (are you missing a using directive or an assembly reference?) [{p}]"]:
                out.append(d); diags.append(d)
        else:
            out.append(f"  Emu.Periph{i:02d} -> C:\\src\\Emu\\Emu.Periph{i:02d}\\bin\\Debug\\net8.0\\Emu.Periph{i:02d}.dll")
    out += ["", "Build FAILED.", ""] + diags + [f"    {n_proj * 3} Warning(s)", "    2 Error(s)", "", "Time Elapsed 00:00:41.27"]
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")

# ------------------------------------------------------------------ scenarios

def scenarios(ws):
    have = lambda *t: all(shutil.which(x) for x in t)
    S = []
    p = f"{ws}/cpp_err"
    S.append(dict(id="cmake-make-cpp-errors", title="C++ emulator, CMake + make VERBOSE=1 -k, header bug + uart bug",
                  need=have("cmake", "make", "g++"),
                  setup=lambda: (gen_cpp_emulator(p, header_bug=True, uart_bug=True),
                                 run(f"cmake -S {p} -B {p}/build -G 'Unix Makefiles' > /dev/null")),
                  cmd=f"make -C {p}/build clean > /dev/null; make -C {p}/build VERBOSE=1 -k -j2",
                  markers=["MASK_ALL", "baud_divisor"]))
    p2 = f"{ws}/cpp_ok"
    S.append(dict(id="cmake-ninja-cpp-success", title="Same project, fixed, -Wall -Wextra, ninja -v (success with warnings)",
                  need=have("cmake", "ninja", "g++"),
                  setup=lambda: (gen_cpp_emulator(p2, warnings=True),
                                 run(f"cmake -S {p2} -B {p2}/build -G Ninja > /dev/null")),
                  cmd=f"ninja -C {p2}/build -t clean > /dev/null; ninja -C {p2}/build -v",
                  markers=["OK"], expect_ok=True))
    p3 = f"{ws}/tmpl"
    S.append(dict(id="clang-template-errors", title="clang++ template errors (unique_ptr copy, std::sort on map)",
                  need=have("clang++"), setup=lambda: gen_template_error(p3),
                  cmd=f"clang++ -std=c++17 -c {p3}/bus.cpp -o /dev/null",
                  markers=["bus.cpp:10", "bus.cpp:11", "bus.cpp:12"]))
    S.append(dict(id="gcc-template-errors", title="g++ on the same file",
                  need=have("g++"), setup=lambda: gen_template_error(p3),
                  cmd=f"g++ -std=c++17 -c {p3}/bus.cpp -o /dev/null",
                  markers=["bus.cpp:10", "bus.cpp:11", "bus.cpp:12"]))
    p4 = f"{ws}/link"
    S.append(dict(id="link-error", title="Linker: undefined reference",
                  need=have("g++"), setup=lambda: gen_link_error(p4),
                  cmd=f"g++ {p4}/main.cpp {p4}/drv.cpp -o {p4}/a.out",
                  markers=["dma_start"]))
    p5 = f"{ws}/pyt"
    S.append(dict(id="pytest-verbose", title="pytest -v, 400 tests, 3 failures",
                  need=True, setup=lambda: gen_pytest(p5),
                  cmd=f"cd {p5} && {PY} -m pytest -v -p no:cacheprovider tests",
                  markers=["test_shl_overflow", "test_decode_illegal", "test_sub_wrap", "3 failed"]))
    p6 = f"{ws}/pycrash"
    S.append(dict(id="python-crash", title="Python emulator config loader: 800 log lines then KeyError",
                  need=True, setup=lambda: gen_python_crash(p6),
                  cmd=f"{PY} {p6}/emu_cfg.py",
                  markers=["KeyError: 'clock_hz'", "emu_cfg.py:5"]))
    sim = f"{ws}/emu_sim.py"
    S.append(dict(id="emulator-trace-fault", title="Emulator instruction trace, 200k lines, FAULT in the middle",
                  need=True, setup=lambda: w(sim, EMU_SIM),
                  cmd=f"{PY} {sim} 200000 120000 fault",
                  markers=["illegal opcode 0xFF"]))
    S.append(dict(id="emulator-quiet-bug", title="Same trace, bug line has NO error keyword (built-in patterns only)",
                  need=True, setup=lambda: w(sim, EMU_SIM),
                  cmd=f"{PY} {sim} 200000 120000 quiet",
                  markers=["CR1=0x0000DEAD"]))
    S.append(dict(id="emulator-quiet-bug+pattern", title="Same, with one line in .runbrief/patterns.txt",
                  need=True, setup=lambda: w(sim, EMU_SIM), patterns="unexpected register value",
                  cmd=f"{PY} {sim} 200000 120000 quiet",
                  markers=["CR1=0x0000DEAD"]))
    fx = f"{ws}/msbuild.txt"
    S.append(dict(id="msbuild-csharp-fixture", title="MSBuild/C# output, 40 projects (real format, replayed fixture)",
                  need=True, setup=lambda: gen_msbuild_fixture(fx),
                  cmd=f"cat {fx}; exit 1", markers=["CS0103", "CS1061"]))
    es = f"{ws}/endless.py"
    S.append(dict(id="endless-sim-timeout", title="Simulator that never exits, stopped after 8 s (-T 8, 20 MB log cap)",
                  need=True, setup=lambda: w(es, ENDLESS_SIM), timeout=8,
                  env={"RUNBRIEF_MAX_MB": "20"},
                  cmd=f"{PY} {es}", markers=["DMA channel 3 underrun"]))
    return S

# ------------------------------------------------------------------ runner

def run(cmd, **kw):
    return subprocess.run(cmd, shell=True, **kw)

def raw_run(cmd, timeout=None):
    t0 = time.time()
    full = f"timeout -s KILL {timeout} bash -c {json.dumps(cmd)}" if timeout else cmd
    p = subprocess.run(full, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.stdout, time.time() - t0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ws = tempfile.mkdtemp(prefix="runbrief-bench-")
    results = []
    for sc in scenarios(ws):
        if not sc["need"]:
            print(f"- skip {sc['id']} (tool missing)"); continue
        sc["setup"]()
        rbdir = os.path.join(ws, "rb-" + sc["id"])
        os.makedirs(rbdir, exist_ok=True)
        if sc.get("patterns"):
            with open(os.path.join(rbdir, "patterns.txt"), "w") as f:
                f.write(sc["patterns"] + "\n")
        raw, raw_t = raw_run(sc["cmd"], sc.get("timeout"))
        env = dict(os.environ, RUNBRIEF_DIR=rbdir, **sc.get("env", {}))
        args = [PY, RB, "run", "-n", "s"] + (["-T", str(sc["timeout"])] if sc.get("timeout") else []) + ["--", sc["cmd"]]
        t0 = time.time()
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=ws)
        rb_t = time.time() - t0
        brief = p.stdout.decode("utf-8", "replace")
        raw_s = raw.decode("utf-8", "replace")
        hits = [m for m in sc["markers"] if (m == "OK" and "] s: OK" in brief) or (m != "OK" and m in brief)]
        raw_hits = [m for m in sc["markers"] if m == "OK" or m in raw_s]
        logs = [os.path.join(rbdir, "logs", f) for f in os.listdir(os.path.join(rbdir, "logs"))]
        log = max(logs, key=os.path.getmtime)
        log_bytes = os.path.getsize(log)
        with open(log, "rb") as fh:
            gz_bytes = len(gzip.compress(fh.read(), 6))
        r = dict(id=sc["id"], title=sc["title"], cmd=sc["cmd"],
                 raw_lines=raw_s.count("\n"), raw_bytes=len(raw), raw_tokens=round(len(raw) / 4),
                 brief_lines=brief.count("\n"), brief_bytes=len(p.stdout), brief_tokens=round(len(p.stdout) / 4),
                 reduction_pct=round(100 * (1 - len(p.stdout) / max(1, len(raw))), 2),
                 markers=sc["markers"], found=hits, fidelity=f"{len(hits)}/{len(sc['markers'])}",
                 raw_has_markers=f"{len(raw_hits)}/{len(sc['markers'])}",
                 raw_seconds=round(raw_t, 2), runbrief_seconds=round(rb_t, 2),
                 log_mb=round(log_bytes / 2**20, 2), log_gz_mb=round(gz_bytes / 2**20, 3),
                 exit=p.returncode, brief=brief)
        results.append(r)
        print(f"- {r['id']:<28} {r['raw_tokens']:>10,} -> {r['brief_tokens']:>5,} tok  "
              f"({r['reduction_pct']:>6.2f}% less)  fidelity {r['fidelity']}  "
              f"time {r['raw_seconds']}s / {r['runbrief_seconds']}s  log {r['log_mb']} MB (gz {r['log_gz_mb']})")
    meta = dict(date=time.strftime("%Y-%m-%d"), python=platform.python_version(), platform=platform.platform(),
                cpus=os.cpu_count(), tools={t: (subprocess.run(f"{t} --version", shell=True, capture_output=True, text=True).stdout.splitlines() or ["-"])[0]
                                          for t in ("gcc", "clang", "cmake", "ninja", "make")})
    with open(os.path.join(a.out, "results.json"), "w") as f:
        json.dump(dict(meta=meta, results=results), f, indent=1)
    for r in results:                      # do not publish temp paths
        r["cmd"], r["brief"] = r["cmd"].replace(ws, "<tmp>"), r["brief"].replace(ws, "<tmp>")
    with open(os.path.join(a.out, "results.json"), "w") as f:
        json.dump(dict(meta=meta, results=results), f, indent=1)
    write_md(os.path.join(a.out, "results.md"), meta, results)
    shutil.rmtree(ws, ignore_errors=True)

def write_md(path, meta, res):
    tot_raw = sum(r["raw_tokens"] for r in res)
    tot_brief = sum(r["brief_tokens"] for r in res)
    L = [f"# runbrief benchmark results ({meta['date']})", "",
         f"Host: {meta['platform']}, {meta['cpus']} CPUs, Python {meta['python']}.  "
         + ", ".join(f"{k}: {v}" for k, v in meta["tools"].items() if v != "-"), "",
         "Tokens are estimated as bytes/4 for both columns, so the reduction ratio does not depend on the estimate.",
         "Fidelity = root-cause markers present in the brief / markers defined for the scenario.", "",
         "| Scenario | Raw lines | Raw tokens | Brief tokens | Reduction | Fidelity | Time raw / runbrief | Log / gzip |",
         "|---|---:|---:|---:|---:|:-:|---:|---:|"]
    for r in res:
        L.append(f"| {r['title']} | {r['raw_lines']:,} | {r['raw_tokens']:,} | {r['brief_tokens']:,} | "
                 f"{r['reduction_pct']:.1f}% | {r['fidelity']} | {r['raw_seconds']}s / {r['runbrief_seconds']}s | "
                 f"{r['log_mb']} MB / {r['log_gz_mb']} MB |")
    L += ["", f"**Total:** {tot_raw:,} raw tokens -> {tot_brief:,} brief tokens "
              f"({100 * (1 - tot_brief / tot_raw):.2f}% less).", "", "## Briefs", ""]
    for r in res:
        L += [f"### {r['id']}", "", f"`{r['cmd']}`", "", "```", r["brief"].rstrip(), "```", ""]
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")

if __name__ == "__main__":
    main()
