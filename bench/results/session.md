# A real debug session through runbrief (2026-11-23)

Every command below was executed; outputs are verbatim (temp paths shortened).

## 1. Build

```text
$ runbrief -n build -- make -C ~/emu/build VERBOSE=1 -k -j2
[runbrief] build: FAIL exit=2 | 6.4s | 607 lines | errors 2 (74 hits) | warnings 0 | log .runbrief/logs/build-20261123-000756-037-fail.log
errors (first-seen order, showing 2/2):
  L18: ~/emu/include/regs.h:5:59: error: ‘MASK_ALL’ was not declared in this scope  [x73]
  L499: ~/emu/src/periph/uart.cpp:6:68: error: ‘baud_divisor’ was not declared in this scope
build-system: 75 failure line(s), first L26: make[2]: *** [CMakeFiles/emu.dir/build.make:90: CMakeFiles/emu.dir/src/cpu/op_001.cpp.o] Error 1
more: runbrief grep 'REGEX' -n build  |  runbrief show -n build -L <line> -C 20
```

## 2. Look at the first error in context

```text
$ runbrief show -n build -L 18 -C 4
[runbrief] .runbrief/logs/build-20261123-000756-037-fail.log: lines 14-22
  L14- [  2%] Building CXX object CMakeFiles/emu.dir/src/cpu/op_001.cpp.o
  L15- /usr/bin/c++  -I~/emu/include -std=gnu++17 -MD -MT CMakeFiles/emu.dir/src/cpu/op_001.cpp.o -MF CMakeFiles/emu.dir/src/cpu/op_001.cpp.o.d -o CMakeFiles/emu.dir/src/cpu/op_001.cpp.o -c ~/emu/src/cpu/op_001.cpp
  L16- In file included from ~/emu/src/cpu/op_000.cpp:1:
  L17- ~/emu/include/regs.h: In function ‘uint32_t reg_mask(const Regs&)’:
  L18: ~/emu/include/regs.h:5:59: error: ‘MASK_ALL’ was not declared in this scope
  L19-     5 | inline uint32_t reg_mask(const Regs& s) { return s.r[0] & MASK_ALL; }
  L20-       |                                                           ^~~~~~~~
  L21- In file included from ~/emu/src/cpu/op_001.cpp:1:
  L22- ~/emu/include/regs.h: In function ‘uint32_t reg_mask(const Regs&)’:
```

## 3. Fix regs.h, rebuild

```text
$ runbrief -n build -- make -C ~/emu/build VERBOSE=1 -k -j2
[runbrief] build: FAIL exit=2 | 7.2s | 244 lines | errors 1 | warnings 0 | log .runbrief/logs/build-20261123-000802-558-fail.log
errors (first-seen order, showing 1/1):
  L209: ~/emu/src/periph/uart.cpp:6:68: error: ‘baud_divisor’ was not declared in this scope
build-system: 3 failure line(s), first L214: make[2]: *** [CMakeFiles/emu.dir/build.make:916: CMakeFiles/emu.dir/src/periph/uart.cpp.o] Error 1
more: runbrief grep 'REGEX' -n build  |  runbrief show -n build -L <line> -C 20
```

## 4. Fix uart.cpp, rebuild

```text
$ runbrief -n build -- make -C ~/emu/build VERBOSE=1 -k -j2
[runbrief] build: OK | 0.4s | 95 lines | errors 0 | warnings 0 | log .runbrief/logs/build-20261123-000809-765-ok.log
```

## 5. Run tests

```text
$ runbrief -n test -- cd ~/pyt && /usr/local/bin/python3 -m pytest -v -p no:cacheprovider tests
[runbrief] test: FAIL exit=1 | 0.6s | 448 lines | errors 6 | warnings 0 | log .runbrief/logs/test-20261123-000810-199-fail.log
tests: ======================== 3 failed, 397 passed in 0.27s =========================
errors (first-seen order, showing 6/6):
  L413: E   assert 0 == 4294967296
  L432: E       ValueError: illegal opcode 0x3F
  L439: E   assert 4294967295 == -1
  L445: FAILED tests/test_cpu.py::test_shl_overflow - assert 0 == 4294967296
  L446: FAILED tests/test_cpu.py::test_decode_illegal - ValueError: illegal opcode 0x3F
  L447: FAILED tests/test_cpu.py::test_sub_wrap - assert 4294967295 == -1
more: runbrief grep 'REGEX' -n test  |  runbrief show -n test -L <line> -C 20
```

## 6. Inspect one failure

```text
$ runbrief grep def test_sub_wrap|assert .* == -1 -n test -C 1 -m 3
[runbrief] .runbrief/logs/test-20261123-000810-199-fail.log: 3 match(es) for /def test_sub_wrap|assert .* == -1/
  L436- 
  L437: >   def test_sub_wrap(): assert cpu.sub(0, 1) == -1
  L438-                          ^^^^^^^^^^^^^^^^^^^^^^^^^^
  L439: E   assert 4294967295 == -1
  L440- E    +  where 4294967295 = <function sub at 0x7feeebcdfa60>(0, 1)
  --
  L446- FAILED tests/test_cpu.py::test_decode_illegal - ValueError: illegal opcode 0x3F
  L447: FAILED tests/test_cpu.py::test_sub_wrap - assert 4294967295 == -1
  L448- ======================== 3 failed, 397 passed in 0.27s =========================
```

## 7. Fix and rerun tests

```text
$ runbrief -n test -- cd ~/pyt && /usr/local/bin/python3 -m pytest -v -p no:cacheprovider tests
[runbrief] test: OK | 0.6s | 408 lines | errors 0 | warnings 0 | log .runbrief/logs/test-20261123-000810-901-ok.log
tests: ============================= 400 passed in 0.29s ==============================
```

## 8. Run the emulator

```text
$ runbrief -n emu -T 60 -- /usr/local/bin/python3 ~/emu_sim.py 200000 150000 fault
[runbrief] emu: FAIL exit=1 | 1.5s | 200,001 lines | errors 1 | warnings 0 | log .runbrief/logs/emu-20261123-000811-585-fail.log
errors (first-seen order, showing 1/1):
  L150002: [EMU] FAULT: illegal opcode 0xFF at pc=0x000937C0 (HardFault escalated)
more: runbrief grep 'REGEX' -n emu  |  runbrief show -n emu -L <line> -C 20
```

## runbrief stats

```text
[runbrief] 6 runs (4 failed), 2 follow-up reads
  raw output      ~3,897,411 tokens   (what the agent would have read)
  briefs          ~627 tokens
  follow-ups      ~425 tokens   (grep/show)
  net saved       ~3,896,358 tokens   (99.97% less)
  log folder      14.74 MB
    emu               1 runs  ~ 3,850,018 -> ~82 tokens
    build             3 runs  ~    30,764 -> ~322 tokens
    test              2 runs  ~    16,628 -> ~224 tokens
  (tokens estimated as chars/4)
```
