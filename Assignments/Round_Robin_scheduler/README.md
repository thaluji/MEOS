# Stage 10 — Round Robin Scheduler

Multiprogramming for eXpOS. The timer interrupt preempts whatever is running and hands the CPU
to the next runnable process, so three processes make progress at once on one machine.

This is the foundation the later stages build on: every stage after this one relies on a process
being able to give up the CPU inside the kernel and pick up exactly where it left off.

## Files

| File | Role |
| --- | --- |
| `mod_5.spl` | **Scheduler.** Saves the outgoing process's kernel SP, PTBR and PTLR into its process table entry, picks the next `READY`/`CREATED` process, restores its context. |
| `timer.spl` | **Timer interrupt.** Switches to the kernel stack, `backup`s the user context, marks the process `READY`, calls the scheduler. |
| `int_7.spl` | `Write` — prints directly from the interrupt routine. There is no terminal locking yet, which is exactly the problem [`../Resource_mgr_module`](../Resource_mgr_module) fixes. |
| `int_10.spl` | `Exit` — marks the process `TERMINATED`, halts when nothing is left. |
| `mod_7.spl` | **Boot module.** Loads the interrupt routines and modules, sets up the page tables and process table for INIT and the even program. |
| `osloader.spl` | OS startup code; sets up IDLE and calls the boot module. |
| `init.expl` | PID 1 — prints the odd numbers 1…99. |
| `even.expl` | PID 2 — prints the even numbers 2…100. |
| `idle.expl` | PID 0 — loops printing `333`. |
| `commands.xfs` | XFS load script for the whole stage. |

## How the switch works

`mod_5.spl` never touches the outgoing process's return address — it stays on that process's own
kernel stack. The scheduler swaps `SP` to the *incoming* process's kernel stack and returns, so
the `return` that fires belongs to a different process's `call MOD_5` than the one that entered.
When the original process is scheduled again, some other process's scheduler invocation pops its
return address and it continues on the line after its own `call`.

A `CREATED` process is the exception: it has no saved kernel context to return into, so the
scheduler sets `SP` to its UPTR and uses `ireturn` to drop straight into user mode at the entry
point the boot module parked on its stack.

## Build and run

```bash
# Start in the repository root

# 1. SPL (must run from spl/ — it reads splconstants.cfg from the cwd)
cd spl
for f in mod_5 mod_7 int_7 int_10 timer osloader haltprog; do
    ./spl ../Assignments/Round_Robin_scheduler/$f.spl
done

# 2. ExpL
cd ../expl
for f in init even idle; do sh expl ../Assignments/Round_Robin_scheduler/$f.expl; done

# 3. load onto the disk
cd ../xfs-interface
./xfs-interface rm even.xsm          # only if even.xsm is already on the disk
./xfs-interface run ../Assignments/Round_Robin_scheduler/commands.xfs

# 4. run
cd ../xsm
./xsm --timer 5
```

Expected output: the odd numbers, the even numbers and IDLE's endless `333` all interleaved. The
ordering is not deterministic — that is the point of the stage.

(`int_7.spl` also has a `print "[sys_write]:X"` on its failure branch, but every write here uses
file descriptor `-2`, so that branch is never taken.)

## Notes

- **`init.expl` contains a `breakpoint;`** inside its loop, so `./xsm --debug` stops once per
  number printed. It is ignored without `--debug`.
- **IDLE prints here.** From [`../Resource_mgr_module`](../Resource_mgr_module) onward it must
  not — once the terminal is a lockable resource, an IDLE that competes for it can block in
  `WAIT_TERMINAL`, which is the state the scheduler falls back to when nothing else is runnable.
- **The scheduler scans `currentPID + 1 … 15` with no wraparound**, defaulting to PID 0. It
  self-corrects because IDLE is PID 0 and rescans from 1, but it wants a proper wraparound scan.
