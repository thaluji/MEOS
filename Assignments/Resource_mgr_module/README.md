# Stage 11 — Resource Manager Module

Terminal sharing for eXpOS. Builds directly on the round-robin scheduler from
[`../Round_Robin_scheduler`](../Round_Robin_scheduler): instead of printing straight from the
`Write` interrupt routine, a process must now **acquire the terminal, print, and release it**,
so two processes can write concurrently without interleaving into each other's output.

## Control flow

```
user program
   exposcall("Write", -2, word)
      └── INT 7                                   int_7.spl
            └── MOD_4  fn 3  Terminal Write       mod_4.spl
                  ├── MOD_0  fn 8  Acquire Terminal   mod_0.spl
                  │        └── MOD_5 (scheduler) while the terminal is busy
                  ├── print word
                  └── MOD_0  fn 9  Release Terminal   mod_0.spl
                           └── wakes every WAIT_TERMINAL process
```

## Files

| File | Role |
|---|---|
| `mod_0.spl` | **Resource Manager.** Acquire Terminal (fn 8) and Release Terminal (fn 9). |
| `mod_4.spl` | **Device Manager.** Terminal Write (fn 3) — abstraction between `Write` and module 0. |
| `int_7.spl` | `Write` system call. Reads the word off the user stack, calls module 4. |
| `mod_7.spl` | **Boot module.** Loads modules 0/4/5, sets up page tables and the process table, initialises the Terminal Status Table. |
| `mod_5.spl` | Round-robin scheduler (unchanged from the previous stage). |
| `int_10.spl` | `Exit` — marks the process `TERMINATED`, halts when none are left. |
| `osloader.spl` | OS startup code; sets up IDLE and calls the boot module. |
| `timer.spl` | Timer interrupt — preempts the running process and calls the scheduler. |
| `init.expl` | PID 1 — prints the odd numbers 1…99. |
| `even.expl` | PID 2 — prints the even numbers 2…100. |
| `idle.expl` | PID 0 — spins. Must **not** use the terminal. |
| `commands.xfs` | XFS load script for the whole stage. |

## Data structures

**Terminal Status Table** (`TERMINAL_STATUS_TABLE`, address 29568):

| Word | Field | Meaning |
|---|---|---|
| 0 | STATUS | `0` = terminal free, `1` = held |
| 1 | PID | PID of the holder |

Initialised to `STATUS = 0`, `PID = -1` by the boot module before the first process runs.

A waiting process is parked in the `WAIT_TERMINAL` (8) state in word 4 of its process table
entry, and moved back to `READY` by Release Terminal.

## Memory and disk layout

These addresses are **not** arbitrary — `spl/splconstants.cfg` hardwires `MOD_0 = 20480`
(page 40) and `MOD_4 = 24576` (page 48), so the boot module must load each module at exactly
the page the `call MOD_n` instruction jumps to.

| Module | Disk blocks | Memory pages |
|---|---|---|
| Module 0 (resource manager) | 53–54 | 40–41 |
| Module 4 (device manager) | 61–62 | 48–49 |
| Module 5 (scheduler) | 63–64 | 50–51 |
| Module 7 (boot) | 67–68 | 54–55 |

## Build and run

```bash
# 1. compile the SPL sources (spl must be run from its own directory)
cd spl
for f in mod_0 mod_4 mod_5 mod_7 int_7 int_10 timer haltprog osloader; do
    ./spl ../Assignments/Resource_mgr_module/$f.spl
done

# 2. compile the user programs
cd ../expl
for f in init even idle; do ./expl ../Assignments/Resource_mgr_module/$f.expl; done

# 3. load everything onto the disk
cd ../xfs-interface
./xfs-interface rm even.xsm          # only if even.xsm is already on the disk
./xfs-interface run ../Assignments/Resource_mgr_module/commands.xfs

# 4. run
cd ../xsm
./xsm --timer 5
```

Expected output: the numbers 1…100, each exactly once, followed by `Machine is halting.`

The strictly ascending order is a coincidence, not a guarantee. The two programs cost the
same number of user-mode instructions per print, so under a fixed rotation they reach the
terminal at the same rate and alternate. Make them asymmetric, or raise the quantum past one
loop iteration (`--timer 200`), and the ordering changes — what the resource manager
guarantees is that each write is *atomic*, not that writes happen in any particular order.

## Why the wait is a loop and not a single wait

Release Terminal wakes **every** process blocked on the terminal, but only the first one
scheduled wins the race and locks it. The rest must find the terminal busy again and go back
to sleep, so the check has to be a `while`, not an `if`. A request queue per resource would
avoid the busy loop by waking only the process at the head; eXpOS skips that for simplicity.

## Assignment 1 — dumping the Terminal Status Table

`breakpoint` statements sit just before the `return` of both Acquire Terminal and Release
Terminal in `mod_0.spl`. Run in debug mode and type `terminalstatus` at each stop:

```
$ cd xsm && ./xsm --timer 5 --debug
IP = 20530: BRKP → terminalstatus → Status: 1   PID: 1     (acquired by INIT)
IP = 20610: BRKP → terminalstatus → Status: 0   PID: 1     (released)
IP = 20530: BRKP → terminalstatus → Status: 1   PID: 2     (acquired by the even program)
IP = 20610: BRKP → terminalstatus → Status: 0   PID: 2
```

`BRKP` is ignored unless the machine is started with `--debug`, so the same binaries serve
both the normal and the debug run.

## Q1 — can deadlock occur?

**No.** Of the four necessary conditions, mutual exclusion and no-preemption hold, but
**hold-and-wait and circular wait do not**. There is exactly one shared resource at this
stage, and a process never requests a second resource while holding the first — it acquires
the terminal, prints, and releases it. With no process holding one resource while waiting on
another, no cycle can form.

Starvation *is* possible: with no request queue, an unlucky process can lose the race for the
terminal repeatedly. That is not deadlock. Deadlock becomes a genuine risk in later stages,
once a process can hold the disk while requesting an inode or a buffer.

## Notes

- **The `WAIT_TERMINAL` path is unreachable in this stage.** `xsm/machine.c` only ticks the
  timer in user mode, so a process is never preempted inside a system call; acquire → print →
  release completes in one uninterrupted kernel stretch and the terminal is never found busy.
  The path was verified separately with a throwaway build of `mod_4.spl` that yields to the
  scheduler while holding the terminal: 3 blocking waits, 100 acquires, 100 releases, correct
  output, no deadlock. It becomes live in later stages, where a process blocks on the disk
  while holding a resource.
- **IDLE must not touch the terminal.** `idle.expl` no longer calls `Write` — an idle process
  that competes for the terminal both floods the output and can block in `WAIT_TERMINAL`,
  which is exactly the state the scheduler falls back to when nothing else is runnable.
- **Known rough edge in the scheduler.** `mod_5.spl` scans `currentPID + 1 … 15` with no
  wraparound, defaulting to PID 0. It self-corrects today because IDLE is PID 0 and rescans
  from 1, but it should get a wraparound scan before later stages, where a lower-numbered
  runnable process could otherwise be skipped.
