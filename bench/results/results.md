# runbrief benchmark results (2026-11-23)

Host: Linux-6.18.44-fc-v726-x86_64-with-glibc2.39, 2 CPUs, Python 3.13.18.  gcc: gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0, clang: Ubuntu clang version 18.1.3 (1ubuntu1), cmake: cmake version 3.28.3, ninja: 1.11.1, make: GNU Make 4.3

Tokens are estimated as bytes/4 for both columns, so the reduction ratio does not depend on the estimate.
Fidelity = root-cause markers present in the brief / markers defined for the scenario.

| Scenario | Raw lines | Raw tokens | Brief tokens | Reduction | Fidelity | Time raw / runbrief | Log / gzip |
|---|---:|---:|---:|---:|:-:|---:|---:|
| C++ emulator, CMake + make VERBOSE=1 -k, header bug + uart bug | 607 | 16,638 | 163 | 99.0% | 2/2 | 6.07s / 6.24s | 0.06 MB / 0.003 MB |
| Same project, fixed, -Wall -Wextra, ninja -v (success with warnings) | 135 | 7,064 | 32 | 99.5% | 1/1 | 7.03s / 6.86s | 0.03 MB / 0.002 MB |
| clang++ template errors (unique_ptr copy, std::sort on map) | 96 | 2,739 | 276 | 89.9% | 3/3 | 0.33s / 0.37s | 0.01 MB / 0.002 MB |
| g++ on the same file | 54 | 1,599 | 203 | 87.3% | 3/3 | 0.28s / 0.31s | 0.01 MB / 0.001 MB |
| Linker: undefined reference | 3 | 39 | 52 | -34.8% | 1/1 | 0.05s / 0.1s | 0.0 MB / 0.0 MB |
| pytest -v, 400 tests, 3 failures | 448 | 8,534 | 172 | 98.0% | 4/4 | 0.5s / 0.65s | 0.03 MB / 0.003 MB |
| Python emulator config loader: 800 log lines then KeyError | 811 | 12,519 | 84 | 99.3% | 2/2 | 0.06s / 0.08s | 0.05 MB / 0.004 MB |
| Emulator instruction trace, 200k lines, FAULT in the middle | 200,001 | 3,850,018 | 84 | 100.0% | 1/1 | 1.3s / 1.42s | 14.69 MB / 2.278 MB |
| Same trace, bug line has NO error keyword (built-in patterns only) | 200,001 | 3,850,020 | 245 | 100.0% | 1/1 | 1.08s / 1.45s | 14.69 MB / 2.278 MB |
| Same, with one line in .runbrief/patterns.txt | 200,001 | 3,850,020 | 88 | 100.0% | 1/1 | 1.28s / 1.5s | 14.69 MB / 2.278 MB |
| MSBuild/C# output, 40 projects (real format, replayed fixture) | 332 | 15,746 | 189 | 98.8% | 2/2 | 0.0s / 0.08s | 0.06 MB / 0.002 MB |
| Simulator that never exits, stopped after 8 s (-T 8, 20 MB log cap) | 4,374,002 | 64,516,512 | 85 | 100.0% | 1/1 | 8.0s / 8.11s | 11.51 MB / 2.168 MB |

**Total:** 76,131,448 raw tokens -> 1,673 brief tokens (100.00% less).

## Briefs

### cmake-make-cpp-errors

`make -C <tmp>/cpp_err/build clean > /dev/null; make -C <tmp>/cpp_err/build VERBOSE=1 -k -j2`

```
[runbrief] s: FAIL exit=2 | 6.2s | 607 lines | errors 2 (74 hits) | warnings 0 | log rb-cmake-make-cpp-errors/logs/s-20261123-000707-436-fail.log
errors (first-seen order, showing 2/2):
  L18: <tmp>/cpp_err/include/regs.h:5:59: error: ‘MASK_ALL’ was not declared in this scope  [x73]
  L500: <tmp>/cpp_err/src/periph/uart.cpp:6:68: error: ‘baud_divisor’ was not declared in this scope
build-system: 75 failure line(s), first L26: make[2]: *** [CMakeFiles/emu.dir/build.make:76: CMakeFiles/emu.dir/src/cpu/op_000.cpp.o] Error 1
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### cmake-ninja-cpp-success

`ninja -C <tmp>/cpp_ok/build -t clean > /dev/null; ninja -C <tmp>/cpp_ok/build -v`

```
[runbrief] s: OK | 6.8s | 135 lines | errors 0 | warnings 1 | log rb-cmake-ninja-cpp-success/logs/s-20261123-000720-915-ok.log
```

### clang-template-errors

`clang++ -std=c++17 -c <tmp>/tmpl/bus.cpp -o /dev/null`

```
[runbrief] s: FAIL exit=1 | 0.3s | 96 lines | errors 4 (5 hits) | warnings 0 | log rb-clang-template-errors/logs/s-20261123-000728-109-fail.log
errors (first-seen order, showing 4/4):
  L1: <tmp>/tmpl/bus.cpp:12:17: error: no matching member function for call to 'push_back'
  L12: /usr/bin/../lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13/bits/stl_uninitialized.h:90:21: error: static assertion failed due to requirement 'is_constructible<std::unique_ptr<Device, std::default_delete<Device>>, const std::unique_p ...  (from bus.cpp:10)
  L35: /usr/bin/../lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13/bits/stl_construct.h:119:25: error: call to deleted constructor of 'std::unique_ptr<Device>'  (from bus.cpp:10)
  L61: /usr/bin/../lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13/bits/stl_algo.h:1948:22: error: invalid operands to binary expression ('std::_Rb_tree_iterator<std::pair<const std::basic_string<char>, std::vector<std::unique_ptr<Device>>> ...  (from bus.cpp:11)  [x2]
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### gcc-template-errors

`g++ -std=c++17 -c <tmp>/tmpl/bus.cpp -o /dev/null`

```
[runbrief] s: FAIL exit=1 | 0.3s | 54 lines | errors 3 | warnings 0 | log rb-gcc-template-errors/logs/s-20261123-000728-759-fail.log
errors (first-seen order, showing 3/3):
  L2: <tmp>/tmpl/bus.cpp:12:26: error: no matching function for call to ‘std::vector<std::unique_ptr<Device> >::push_back(Device)’
  L24: /usr/include/c++/13/bits/stl_algo.h:1948:50: error: no match for ‘operator-’ (operand types are ‘std::_Rb_tree_iterator<std::pair<const std::__cxx11::basic_string<char>, std::vector<std::unique_ptr<Device> > > > ...  (from bus.cpp:11)
  L51: /usr/include/c++/13/bits/stl_uninitialized.h:90:56: error: static assertion failed: result type must be constructible from input type  (from bus.cpp:10)
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### link-error

`g++ <tmp>/link/main.cpp <tmp>/link/drv.cpp -o <tmp>/link/a.out`

```
[runbrief] s: FAIL exit=1 | 3 line(s), shown in full:
/usr/bin/ld: /tmp/cciUPwVB.o: in function `main':
main.cpp:(.text+0xe): undefined reference to `dma_start(int)'
collect2: error: ld returned 1 exit status
```

### pytest-verbose

`cd <tmp>/pyt && /usr/local/bin/python3 -m pytest -v -p no:cacheprovider tests`

```
[runbrief] s: FAIL exit=1 | 0.6s | 448 lines | errors 6 | warnings 0 | log rb-pytest-verbose/logs/s-20261123-000729-753-fail.log
tests: ======================== 3 failed, 397 passed in 0.26s =========================
errors (first-seen order, showing 6/6):
  L413: E   assert 0 == 4294967296
  L432: E       ValueError: illegal opcode 0x3F
  L439: E   assert 4294967295 == -1
  L445: FAILED tests/test_cpu.py::test_shl_overflow - assert 0 == 4294967296
  L446: FAILED tests/test_cpu.py::test_decode_illegal - ValueError: illegal opcode 0x3F
  L447: FAILED tests/test_cpu.py::test_sub_wrap - assert 4294967295 == -1
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### python-crash

`/usr/local/bin/python3 <tmp>/pycrash/emu_cfg.py`

```
[runbrief] s: FAIL exit=1 | 0.0s | 811 lines | errors 1 | warnings 0 | log rb-python-crash/logs/s-20261123-000730-445-fail.log
errors (first-seen order, showing 1/1):
  L811: KeyError: 'clock_hz'  <- <tmp>/pycrash/emu_cfg.py:5 in parse_periph
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### emulator-trace-fault

`/usr/local/bin/python3 <tmp>/emu_sim.py 200000 120000 fault`

```
[runbrief] s: FAIL exit=1 | 1.4s | 200,001 lines | errors 1 | warnings 0 | log rb-emulator-trace-fault/logs/s-20261123-000731-827-fail.log
errors (first-seen order, showing 1/1):
  L120002: [EMU] FAULT: illegal opcode 0xFF at pc=0x00076300 (HardFault escalated)
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### emulator-quiet-bug

`/usr/local/bin/python3 <tmp>/emu_sim.py 200000 120000 quiet`

```
[runbrief] s: FAIL exit=1 | 1.4s | 200,001 lines | errors 0 | warnings 0 | log rb-emulator-quiet-bug/logs/s-20261123-000734-506-fail.log
rare lines (shape seen <=2x in an otherwise repetitive output; heuristic):
  L120002: uart0: unexpected register value CR1=0x0000DEAD after reset (expected 0x00000000)
tail (last lines without a known pattern):
  L199997: [000199995] pc=0x000C44EC op=ldr   r0=0x000927B1 r1=0x0003F2C4 sp=0x20007F14
  L199998: [000199996] pc=0x000C44F0 op=str   r0=0x000927B4 r1=0x0003F2C3 sp=0x20007F10
  L199999: [000199997] pc=0x000C44F4 op=add   r0=0x000927B7 r1=0x0003F2C2 sp=0x20007F0C
  L200000: [000199998] pc=0x000C44F8 op=b     r0=0x000927BA r1=0x0003F2C1 sp=0x20007F08
  L200001: [000199999] pc=0x000C44FC op=mov   r0=0x000927BD r1=0x0003F2C0 sp=0x20007F04
note: no known error pattern matched. Add one to .runbrief/patterns.txt (e.g. \[EMU\] FAULT) or inspect with grep/show.
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### emulator-quiet-bug+pattern

`/usr/local/bin/python3 <tmp>/emu_sim.py 200000 120000 quiet`

```
[runbrief] s: FAIL exit=1 | 1.5s | 200,001 lines | errors 1 | warnings 0 | log rb-emulator-quiet-bug+pattern/logs/s-20261123-000737-340-fail.log
errors (first-seen order, showing 1/1):
  L120002: uart0: unexpected register value CR1=0x0000DEAD after reset (expected 0x00000000)
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### msbuild-csharp-fixture

`cat <tmp>/msbuild.txt; exit 1`

```
[runbrief] s: FAIL exit=1 | 0.0s | 332 lines | errors 2 (4 hits) | warnings 1 | log rb-msbuild-csharp-fixture/logs/s-20261123-000738-990-fail.log
errors (first-seen order, showing 2/2):
  L114: C:\src\Emu\Emu.Periph17\UartDevice.cs(88,13): error CS0103: The name 'baudDivisor' does not exist in the current context [C:\src\Emu\Emu.Periph17\Emu.Periph17.csproj]  [x2]
  L115: C:\src\Emu\Emu.Periph17\UartDevice.cs(95,22): error CS1061: 'IBus' does not contain a definition for 'WriteWord' and no accessible extension method 'WriteWord' accepting a first argument of type 'IBus' could be found (are you missing a usin ...  [x2]
build-system: 2 failure line(s), first L205: Build FAILED.
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

### endless-sim-timeout

`/usr/local/bin/python3 <tmp>/endless.py`

```
[runbrief] s: TIMEOUT after 8s | 8.1s | 1,093,182 lines | errors 1 | warnings 0 | log trimmed 5x (cap 20 MB) | log rb-endless-sim-timeout/logs/s-20261123-000747-066-timeout.log
errors (first-seen order, showing 1/1):
  L100001: dma.c:211: error: DMA channel 3 underrun
more: runbrief grep 'REGEX' -n s  |  runbrief show -n s -L <line> -C 20
```

