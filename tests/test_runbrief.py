"""Behavioural tests for runbrief. Run: python -m pytest -q tests/"""
import glob
import gzip
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

RB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runbrief.py")
PY = sys.executable


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    e = dict(os.environ, RUNBRIEF_DIR=str(tmp_path / ".runbrief"), PYTHONIOENCODING="utf-8")
    return e


def rb(env, *args, extra=None):
    e = dict(env, **(extra or {}))
    p = subprocess.run([PY, RB, *args], capture_output=True, text=True, env=e, timeout=120)
    return p.returncode, p.stdout


def script(tmp_path, body, name="s.py"):
    f = tmp_path / name
    f.write_text(textwrap.dedent(body))
    return str(f)


def logs(env, pattern="*"):
    return sorted(glob.glob(os.path.join(env["RUNBRIEF_DIR"], "logs", pattern)))


# ---------------------------------------------------------------- basics

def test_small_output_is_passed_through_verbatim(env):
    rc, out = rb(env, "-n", "hello", "--", PY, "-c", "print('answer=42')")
    assert rc == 0
    assert "OK" in out and "answer=42" in out


def test_large_success_is_one_line(env, tmp_path):
    s = script(tmp_path, "for i in range(5000): print('compiling unit', i)")
    rc, out = rb(env, "-n", "b", "--", PY, s)
    assert rc == 0
    assert out.strip().count("\n") == 0, out
    assert "5,000 lines" in out


def test_exit_code_is_propagated(env, tmp_path):
    s = script(tmp_path, "import sys\nfor i in range(100): print(i)\nsys.exit(7)")
    rc, out = rb(env, "-n", "x", "--", PY, s)
    assert rc == 7 and "FAIL exit=7" in out


def test_shell_string_mode(env):
    rc, out = rb(env, "-n", "sh", "--", "echo one && echo two")
    assert rc == 0 and "one" in out and "two" in out


# ---------------------------------------------------------------- error extraction

def test_compiler_errors_deduped_in_first_seen_order(env, tmp_path):
    s = script(tmp_path, """
        for i in range(300): print('[ 10%%] Building CXX object op%d.o' % i)
        print("src/uart.cpp:12:5: error: 'reg' was not declared in this scope")
        for i in range(20): print("src/uart.cpp:%d:5: error: 'reg' was not declared in this scope" % (13 + i))
        print("src/dma.c:7:1: error: expected ';' before '}' token")
        print("make[2]: *** [uart.o] Error 1")
        print("make[1]: *** [all] Error 2")
        raise SystemExit(2)
    """)
    rc, out = rb(env, "-n", "build", "--", PY, s)
    assert rc == 2
    lines = out.splitlines()
    err_lines = [l for l in lines if l.startswith("  L")]
    assert "uart.cpp:12:5" in err_lines[0] and "[x21]" in err_lines[0]
    assert "dma.c:7:1" in err_lines[1]
    assert "build-system: 2 failure line(s)" in out
    assert "Building CXX" not in out          # progress noise is gone


def test_msvc_csharp_and_linker_formats(env, tmp_path):
    s = script(tmp_path, r"""
        for i in range(100): print('  Compiling...')
        print(r"C:\src\Driver.cs(12,5): error CS0103: The name 'foo' does not exist in the current context [C:\src\Emu.csproj]")
        print(r"uart.obj : error LNK2019: unresolved external symbol _hal_init referenced in function _main")
        print(r"C:\src\cpu.cpp(88): error C2065: 'opcode': undeclared identifier")
        print("/usr/bin/ld: main.o: in function `main': undefined reference to `dma_start'")
        print("Build FAILED.")
        raise SystemExit(1)
    """)
    rc, out = rb(env, "-n", "msb", "--", PY, s)
    for needle in ("CS0103", "LNK2019", "C2065", "undefined reference"):
        assert needle in out, needle
    assert "build-system: 1" in out


def test_python_traceback_compacted_to_user_frame(env, tmp_path):
    s = script(tmp_path, """
        import json
        for i in range(200): print('loading peripheral', i)
        def load_cfg(txt):
            return json.loads(txt)
        load_cfg('{bad json')
    """)
    rc, out = rb(env, "-n", "py", "--", PY, s)
    assert rc == 1
    err = [l for l in out.splitlines() if "JSONDecodeError" in l]
    assert err and "s.py:5 in load_cfg" in err[0], out     # user frame, not json/decoder.py


def test_pytest_result_line_and_failures(env, tmp_path):
    (tmp_path / "test_x.py").write_text(textwrap.dedent("""
        import pytest
        @pytest.mark.parametrize('i', range(120))
        def test_ok(i): assert i >= 0
        def test_bad(): assert 1 + 1 == 3
    """))
    rc, out = rb(env, "-n", "test", "--", PY, "-m", "pytest", "-v", str(tmp_path / "test_x.py"))
    assert rc == 1
    assert "tests:" in out and "1 failed, 120 passed" in out
    assert "test_bad" in out


def test_custom_patterns_file(env, tmp_path):
    os.makedirs(env["RUNBRIEF_DIR"], exist_ok=True)
    with open(os.path.join(env["RUNBRIEF_DIR"], "patterns.txt"), "w") as f:
        f.write("# emulator\n\\[EMU\\] bad opcode\n")
    s = script(tmp_path, """
        for i in range(500): print('pc=%08x' % i)
        print('[EMU] bad opcode 0xFF at pc=0x1F4')
        for i in range(500): print('pc=%08x' % i)
    """)
    rc, out = rb(env, "-n", "emu", "--", PY, s)
    assert "bad opcode 0xFF" in out


def test_no_pattern_failure_shows_compacted_tail(env, tmp_path):
    s = script(tmp_path, """
        for i in range(400): print('tick', i)
        print('weird thing happened')
        raise SystemExit(3)
    """)
    rc, out = rb(env, "-n", "w", "--", PY, s)
    assert "weird thing happened" in out
    assert "similar]" in out and "no known error pattern" in out


# ---------------------------------------------------------------- timeout & big logs

def test_timeout_kills_whole_process_tree(env, tmp_path):
    marker = tmp_path / "alive"
    child = script(tmp_path, f"""
        import time, pathlib
        time.sleep(3)
        pathlib.Path({str(marker)!r}).write_text('x')
    """, "child.py")
    parent = script(tmp_path, f"""
        import subprocess, sys, time
        subprocess.Popen([sys.executable, {child!r}])
        while True:
            print('trace', flush=True); time.sleep(0.01)
    """, "parent.py")
    t0 = time.time()
    rc, out = rb(env, "-n", "emu", "-T", "1", "--", PY, parent)
    assert rc == 124 and "TIMEOUT" in out
    assert time.time() - t0 < 10
    time.sleep(3.5)
    assert not marker.exists(), "grandchild survived the timeout"


@pytest.mark.skipif(os.name == "nt", reason="size math assumes \\n line endings")
def test_log_cap_trims_middle_but_keeps_errors_and_line_numbers(env, tmp_path):
    s = script(tmp_path, """
        print('boot.c:1:1: error: early failure')
        for i in range(60000): print('trace pc=%08x op=nop' % i)
        print('irq.c:9:9: error: late failure')
        for i in range(1000): print('trace tail %d' % i)
    """)
    rc, out = rb(env, "-n", "emu", "--", PY, s, extra={"RUNBRIEF_MAX_MB": "0.2"})
    assert "early failure" in out and "late failure" in out
    assert "trimmed" in out
    log = logs(env, "emu-*")[-1]
    assert os.path.getsize(log) < 0.25 * 1024 * 1024
    late = [l for l in out.splitlines() if "late failure" in l][0]
    line_no = late.split(":")[0].strip().lstrip("L")
    rc, shown = rb(env, "show", "-n", "emu", "-L", line_no, "-C", "0")
    assert "late failure" in shown, shown


# ---------------------------------------------------------------- retention / disk

def test_retention_keeps_1_ok_5_fail_and_gzips_older(env, tmp_path):
    ok = script(tmp_path, "for i in range(100): print('ok', i)", "ok.py")
    bad = script(tmp_path, "for i in range(100): print('x', i)\nraise SystemExit(1)", "bad.py")
    for _ in range(3):
        rb(env, "-n", "b", "--", PY, ok)
    for _ in range(7):
        rb(env, "-n", "b", "--", PY, bad)
    files = logs(env, "b-*")
    oks = [f for f in files if "-ok.log" in f]
    fails = [f for f in files if "-fail.log" in f]
    assert len(oks) == 1 and len(fails) == 5, files
    plain = [f for f in files if f.endswith(".log")]
    assert len(plain) == 1                      # only the newest stays uncompressed
    gz = [f for f in files if f.endswith("-fail.log.gz")][0]
    with gzip.open(gz, "rt") as fh:
        assert "# runbrief" in fh.readline()
    rc, out = rb(env, "grep", "x 5$", "--log", gz)
    assert "1 match" in out


def test_folder_cap_deletes_oldest(env, tmp_path):
    s = script(tmp_path, "for i in range(40000): print('line %08d padding padding padding' % i)")
    for k in range(4):
        rb(env, "-n", f"n{k}", "--", PY, s, extra={"RUNBRIEF_DIR_MAX_MB": "1.5", "RUNBRIEF_GZIP": "0"})
    total = sum(os.path.getsize(f) for f in logs(env))
    assert total <= 1.5 * 1024 * 1024 + 2_000_000   # newest file is always kept
    assert len(logs(env)) < 4


# ---------------------------------------------------------------- stats / json / follow-ups

def test_stats_records_runs_and_followups(env, tmp_path):
    s = script(tmp_path, "for i in range(3000): print('noise', i)\nprint('a.c:1:1: error: boom')\nraise SystemExit(1)")
    rb(env, "-n", "b", "--", PY, s)
    rb(env, "grep", "boom", "-n", "b")
    rc, out = rb(env, "stats", "--json")
    st = json.loads(out)
    assert st["runs"] == 1 and st["followups"] == 1
    assert st["raw_tokens"] > 10 * (st["brief_tokens"] + st["followup_tokens"])
    assert 0 < st["reduction_pct"] < 100


def test_stats_can_be_disabled(env, tmp_path):
    rb(env, "-n", "b", "--", PY, "-c", "print(1)", extra={"RUNBRIEF_STATS": "0"})
    assert not os.path.exists(os.path.join(env["RUNBRIEF_DIR"], "stats.jsonl"))


def test_json_output(env, tmp_path):
    s = script(tmp_path, "for i in range(100): print(i)\nprint('m.c:3:4: error: bad')\nraise SystemExit(2)")
    rc, out = rb(env, "run", "--json", "-n", "j", "--", PY, s)
    doc = json.loads(out)
    assert doc["exit"] == 2 and doc["errors"][0]["text"].startswith("m.c:3:4")


# ---------------------------------------------------------------- heuristics

def test_rare_line_found_without_any_pattern(env, tmp_path):
    s = script(tmp_path, """
        for i in range(5000):
            print('[%06d] pc=0x%08X op=%s' % (i, 0x1000 + 4 * i, ('ldr', 'str', 'add')[i % 3]))
            if i == 2500: print('uart0: unexpected register value CR1=0x0000DEAD after reset')
        raise SystemExit(1)
    """)
    rc, out = rb(env, "-n", "emu", "--", PY, s)
    assert "rare lines" in out and "CR1=0x0000DEAD" in out


@pytest.mark.skipif(not __import__("shutil").which("g++"), reason="g++ not installed")
def test_gcc_template_error_points_at_user_line(env, tmp_path):
    src = tmp_path / "bus.cpp"
    src.write_text("#include <map>\n#include <string>\n#include <algorithm>\n#include <vector>\n"
                   "int main() {\n  std::map<std::string, int> m;\n  std::sort(m.begin(), m.end());\n"
                   + "".join(f"  int pad{i} = {i}; (void)pad{i};\n" for i in range(5)) + "}\n")
    rc, out = rb(env, "-n", "cc", "--", "g++", "-std=c++17", "-c", str(src), "-o", os.devnull,
                 extra={"RUNBRIEF_PASSTHROUGH_LINES": "0"})
    assert rc != 0
    assert "(from bus.cpp:7)" in out, out
