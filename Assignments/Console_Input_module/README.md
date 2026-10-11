# Stage 12 — Console Input Module

Terminal *input* for eXpOS. Builds on [`../Resource_mgr_module`](../Resource_mgr_module), which
made terminal writes atomic. This stage adds the `Read` system call (INT 6), a **Terminal Read**
function in the device manager, and the **console interrupt handler** — the piece of hardware
plumbing that tells the OS a word has actually arrived from the keyboard.

The whole stage exists because of one fact about the XSM `IN` instruction: **it does not wait.**
The machine issues the read request and keeps executing. Data arrival is announced later, and
asynchronously, by the console interrupt.

## Control flow

```
user program
   read(n)   →   exposcall("Read", -1, &n)
      └── INT 6                                       int_6.spl
            └── MOD_4  fn 4  Terminal Read            mod_4.spl
                  ├── MOD_0  fn 8  Acquire Terminal   mod_0.spl
                  ├── read;                 (XSM `IN` — returns immediately)
                  ├── state = WAIT_TERMINAL
                  ├── MOD_5 (scheduler)  ────────────────────┐
                  │                                          │  IDLE runs in user mode
                  │      ~~~ user presses ENTER ~~~          │
                  │                                          │
                  │   console interrupt ─────────────────────┘   console.spl
                  │      ├── P0  →  INPUT BUFFER of the reader
                  │      └── MOD_0 fn 9 Release Terminal → reader back to READY
                  │
                  └── INPUT BUFFER  →  *n     (once rescheduled)
```

The two halves of a read run in **different processes**. The reader falls asleep inside
`MOD_5`; the console interrupt is serviced on whatever process happens to be running at the
time (here, IDLE), and it is that process's interrupt handler that hands the word over and
wakes the reader up.

## Files

| File | Role |
| --- | --- |
| `int_6.spl` | **`Read` system call** (syscall number 7). Pulls the file descriptor and the word address off the user stack, calls module 4. |
| `console.spl` | **Console interrupt handler.** Copies `P0` into the reader's INPUT BUFFER and releases the terminal on its behalf. |
| `mod_4.spl` | **Device Manager.** Terminal Write (fn 3) and the new Terminal Read (fn 4). |
| `mod_0.spl` | **Resource Manager.** Acquire Terminal (fn 8), Release Terminal (fn 9). Unchanged logic, but Release is now also called from an interrupt handler. |
| `mod_7.spl` | **Boot module.** Loads INT 6 and the console handler; sets up only IDLE and INIT. |
| `mod_5.spl` | Round-robin scheduler (unchanged). |
| `int_7.spl` | `Write` system call (unchanged). |
| `int_10.spl` | `Exit` — marks the process `TERMINATED`, halts when none are left. |
| `osloader.spl` | OS startup code; sets up IDLE and calls the boot module. |
| `timer.spl` | Timer interrupt — preempts the running process and calls the scheduler. |
| `init.expl` | PID 1 — reads five words from the console and echoes each back. |
| `idle.expl` | PID 0 — spins. Must **not** use the terminal. |
| `commands.xfs` | XFS load script for the whole stage. |

## Who releases the terminal?

Different for the two directions, and this is the part worth getting right:

| | Acquired by | Released by | Why |
| --- | --- | --- | --- |
| **Write** | Terminal Write | Terminal Write | `OUT` prints synchronously and raises no interrupt, so the writer is still running when the write completes. |
| **Read** | Terminal Read | **console interrupt handler** | The reader is asleep in `WAIT_TERMINAL` when the word arrives. It cannot release anything — somebody else has to. |

The handler releases with `reqPID` taken from the Terminal Status Table, **not** with the PID of
the currently running process. The running process never held the terminal, and
`RELEASE_TERMINAL` rejects a release from a non-holder.

Releasing is also what wakes the reader: `RELEASE_TERMINAL` sweeps the process table and moves
every `WAIT_TERMINAL` process back to `READY`.

## Data structures

**Terminal Status Table** (`TERMINAL_STATUS_TABLE`, address 29568):

| Word | Field | Meaning |
| --- | --- | --- |
| 0 | STATUS | `0` = terminal free, `1` = held |
| 1 | PID | PID of the holder — the console interrupt handler's only way of knowing who asked for input |

**Process table entry**, word 8 — `INPUT BUFFER`. The per-process mailbox the console interrupt
handler drops the word into. Terminal Read picks it up from there after being rescheduled.

## `Read` calling convention (user stack at `INT 6`)

| Slot | Contents |
| --- | --- |
| `userSP - 5` | system call number (7) |
| `userSP - 4` | file descriptor — `-1` for the terminal |
| `userSP - 3` | **logical** address of the word to fill in |
| `userSP - 1` | return value (`0` = success, `-1` = unsupported descriptor) |

Note the asymmetry with `Write`, which passes the value itself and uses descriptor `-2`. `Read`
passes an *address*, so Terminal Read has to translate it through the caller's page table
(`PTBR`) before storing the result.

## Memory and disk layout

| Component | Disk blocks | Memory pages | Loaded by |
| --- | --- | --- | --- |
| Exception handler | 15–16 | 2–3 | module 7 |
| Timer interrupt | 17–18 | 4–5 | module 7 |
| **Console interrupt** | **21–22** | **8–9** | module 7 |
| **INT 6 (`Read`)** | **27–28** | **14–15** | module 7 |
| INT 7 (`Write`) | 29–30 | 16–17 | module 7 |
| INT 10 (`Exit`) | 35–36 | 22–23 | module 7 |
| Module 0 (resource manager) | 53–54 | 40–41 | module 7 |
| Module 4 (device manager) | 61–62 | 48–49 | module 7 |
| Module 5 (scheduler) | 63–64 | 50–51 | module 7 |
| Module 7 (boot) | 67–68 | 54–55 | OS loader |
| Library | 13–14 | 63–64 | module 7 |
| INIT code | 7–8 | 65–66 | module 7 |
| IDLE code | 11–12 | 69–70 | OS loader |

The page numbers are fixed, not chosen: the XSM interrupt vector puts the console handler at
address 4096 (page 8) and INT 6 at 7168 (page 14), matching `CONSOLE` and `INT_6` in
`spl/splconstants.cfg`.

Per-process pages — IDLE: stack 81, user area 82, heap 78–79. INIT: stack 76–77, user area 80,
heap 84–85.

## Build and run

```bash
# 1. compile the SPL sources (spl must be run from its own directory)
cd spl
for f in mod_0 mod_4 mod_5 mod_7 int_6 int_7 int_10 timer console osloader haltprog; do
    ./spl ../Assignments/Console_Input_module/$f.spl
done

# 2. compile the user programs
cd ../expl
for f in init idle; do sh expl ../Assignments/Console_Input_module/$f.expl; done

# 3. load everything onto the disk
cd ../xfs-interface
./xfs-interface run ../Assignments/Console_Input_module/commands.xfs

# 4. run
cd ../xsm
./xsm
```

The machine stops for input five times. Type a number (or a word) and press ENTER; it is echoed
back:

```
$ ./xsm
7
7
13
13
hello
hello
-5
-5
0
0
Machine is halting.
```

Piping works too, which is what makes the stage testable non-interactively:

```bash
printf '7\n13\n21\n99\n256\n' | ./xsm
```

Strings and negative numbers both survive the trip — an XSM word carries its own type, and
`P0`, the INPUT BUFFER and the destination word are all plain words.

## Why this stage cannot fire a console interrupt in kernel mode

`machine_execute_interrupt_do` in `xsm/machine.c` registers an exception if an interrupt is
raised while the machine is in kernel mode, which looks alarming for an asynchronous interrupt
that can land anywhere. It cannot actually happen: `machine_run` only calls
`machine_post_execute` — the function that counts down the console timer *and* raises the
interrupt — when the mode is `PRIVILEGE_USER`. So the 20-instruction console countdown does not
even start ticking until some process is back in user mode, and the interrupt is always
delivered at a user-mode instruction boundary.

This is also why the `WAIT_TERMINAL` handoff is safe rather than racy: the reader is guaranteed
to reach the scheduler and give up the CPU before the interrupt that wakes it can be delivered.

## Why IDLE matters now

In the previous stage IDLE was just a placeholder. Here it is load-bearing: while INIT sleeps in
`WAIT_TERMINAL`, IDLE is the *only* runnable process, and the machine has to be in user mode for
the console interrupt to be delivered at all. If IDLE halted, blocked, or competed for the
terminal, the read would never complete and the system would hang.

For the same reason `idle.expl` must not call `Write`: an IDLE that blocks in `WAIT_TERMINAL` is
exactly the state the scheduler falls back to when nothing else is runnable.

## Notes

- **`WAIT_TERMINAL` is genuinely exercised for the first time.** In stage 11 the state was
  implemented but unreachable (writes never yield). Here every single `read` goes through it,
  which is also the end-to-end proof that the handler works — if the console interrupt failed to
  wake INIT, the machine would spin in IDLE forever instead of reaching `Machine is halting.`
- **Only IDLE and INIT run.** The roadmap drops the third process for this stage, so `mod_7.spl`
  marks PIDs 2–15 `TERMINATED` and no `--exec` file is loaded.
- **`breakpoint` statements were removed from `mod_0.spl`.** They served the stage-11 assignment;
  here they would interrupt an interactive session that is already reading from stdin.
- **Known rough edge in the scheduler.** `mod_5.spl` still scans `currentPID + 1 … 15` with no
  wraparound, defaulting to PID 0. Harmless with two processes, but it wants a wraparound scan
  before later stages.
- **`xfs-interface` crashes on very long paths.** Loading via a ~120-character absolute path
  under `/private/tmp` killed it with SIGTRAP; short relative paths work. Worth knowing if a
  `load` command fails with no error message.
