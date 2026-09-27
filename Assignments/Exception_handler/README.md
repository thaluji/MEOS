# Stage 19: Exception handler and demand paging

Implements the [Stage 19 design](https://exposnitc.github.io/expos-docs/roadmap/stage-19/). Exec initially maps two stack pages and the first code page. Code faults load the missing executable block; the first heap fault allocates both heap pages. Fatal exceptions release the process and invoke the scheduler.

## Build and test

From the repository root:

```sh
sh Assignments/Exception_handler/build.sh
python3 Assignments/Exception_handler/test_stage19.py
```

The test uses a newly formatted temporary disk, never your existing disks. It checks real simulator execution for:

- Missing-file Exec, retry, and odd-number output with three timer/disk settings.
- Faults on code pages 5, 6 and 7, followed by a heap-page-3 fault; both heap pages become usable, and R10/BP survive recovery and disk waits.
- Arithmetic faults, privileged instructions in user mode, illegal addresses, a full user stack, and jumps to code pages absent from the executable.
- Fatal-process memory cleanup and disk-map invalidation, observed at the scheduler boundary using a test-only scheduler hook.
- Sharing a resident page from PID 15, rejecting an invalid PTE with a stale page number, shared-page reference counts, and releasing a temporary heap block without releasing executable file blocks. These use a kernel test harness, not multiple concurrently running user processes.
- All five compiled Stage 19 routines fit their two-page slots.

## Load and run

After building, from the repository root:

```sh
cd xfs-interface
./xfs-interface run ../Assignments/Exception_handler/commands.xfs
cd ../xsm
./xsm --timer 10 --disk 100
```

Enter `odd.xsm`. A failed Exec prints -1 and the shell waits for another filename. A successful Exec replaces this version of the shell. Following a fatal exception, the handler schedules IDLE if no other user process is runnable, so the simulator keeps running; this is expected. The tests stop at that scheduling boundary.

The load command updates OS blocks in `xfs-interface/disk.xfs`. If `odd.xsm` is already installed, XFS may refuse that last executable load; the preceding OS loads still succeed. The new stage reuses Stage 18's disk/device code and Stage 17's timer, scheduler and console code.

## Where the original code went wrong

### `mod_2.spl`

- `fnctionNumber` was misspelled and its `if` lacked `then`.
- The nested function branches lacked matching `endif` statements, preventing compilation. The fallback `R0 = -1` was also placed after an unconditional return inside function 5.
- `while (i < 15)` omitted PID 15. It now uses `MAX_PROC_NUM`.
- A physical page number other than -1 is not by itself proof that a page is resident. Get Code Page now checks the PTE's valid bit and skips terminated processes before sharing it.
- Removed the unconditional debug breakpoint and rejected invalid block numbers before attempting disk I/O.

### `int_9.spl`

- `inodeEntry` was never declared; the alias you defined is `inodeIndex`.
- The `multipush` before Get Code Page had no matching `multipop`. Module calls overwrite scratch registers, including R5/R6, so the following disk-map loop could use a corrupted inode index and PID.
- Restoring R0 in that pop would also be wrong: R0 contains the newly returned physical page. The fix saves/restores R1-R9, leaving R0 available for the page-table update.
- Disk-map initialization began at entry 2, leaving entries 0 and 1 stale. All ten entries are now initialized, with only existing code blocks filled from the inode.

### `exhandler.spl`

- Your kernel-stack switch, `backup`, saved EIP/EPN, allocation of both heap pages, and translated EIP push before `ireturn` were already the right approach. EIP must be retried unchanged after a page fault.
- The code-page path could send disk block -1 to Get Code Page when the requested page was outside the executable. It now terminates that process with an address diagnostic.
- The unconditional heap `else` treated every non-code fault as a heap fault. It now accepts only logical pages 2 and 3.
- The original messages exceeded XSM's single-word string capacity and were truncated in actual execution. The diagnostics now fit: `Stack full`, `Illegal instr`, `Illegal addr`, and `Arithmetic`.
- The entry breakpoint was initially removed, then restored on request together with a second breakpoint before context restoration. They stop only in `--debug` mode. The fatal branch now has no recovery path if the scheduler unexpectedly returns; normal scheduling does not resume a terminated process.

### Missing integration

- Added a stage-specific `mod_7.spl` to initialize disk maps, record boot-loaded INIT/IDLE code blocks, and load the Disk Free List.
- Added a stage-specific `mod_1.spl` to release temporary heap/stack disk blocks and clear the disk map during process cleanup. Executable blocks remain owned by their files.
- Added build/load scripts to install the new handler and matching modules together. Loading the old halt-only handler would bypass all this exception handling.

## See page faults and disk activity

From the repository root:

```sh
python3 Assignments/Exception_handler/debug_stage19.py
```

This invokes `build.sh`, creates a disposable disk, compiles instrumented copies in a temporary directory, and runs `odd.xsm` followed by a generated `fault.xsm`. Normal handler binaries and your existing disks are unchanged by the instrumentation. The full output is also saved to `debug_trace.txt` beside this README.

The debug labels show:

- `DBG diskload`: executable disk block and destination physical frame.
- `DBG diskdone`: disk completion interrupt.
- `DBG exec`: initial code/heap mappings; -1 means unmapped.
- `DBG fault`: exception code EC, PID, faulting instruction EIP, missing logical page EPN, and original user SP.
- `DBG heap`: the physical frames allocated for both heap pages.
- `DBG retry`: the original instruction address about to be retried.
- `DBG share`: a resident code frame reused instead of loaded from disk (neither of these two runs needs sharing).

Observed with timer 10 and disk delay 100:

| Program | Page faults | Disk loads | Result |
| --- | --- | --- | --- |
| `odd.xsm` | 0 | 1, during Exec | Prints odd numbers 1–99 |
| `fault.xsm` | 2: code page 5, heap page 2 | 2: initial code and missing code page | Prints 42 and 43 |

`odd.xsm` fits within its first code page and uses a stack-local variable. Its library and stack are already mapped, so it never accesses an invalid page. An asynchronous disk read during Exec is not a page fault.

Both deliberate faults report EIP 2560. First the CPU cannot fetch the instruction at 2560 because code page 5 is absent. After that code is loaded, retrying the instruction (`MOV [1024], 42`) faults on its destination in heap page 2. The handler allocates both heap pages, retries again, and the write succeeds. The subsequent write to heap page 3 needs no additional fault.

The earlier XFS message that a file already exists happens while installing an executable. It is unrelated to CPU page faults during execution. This trace is a fresh reproduction of the supplied program, not a recording of an earlier terminal session.


## Actual breakpoints in XSM debug mode

`exhandler.spl` now contains `breakpoint;` at handler entry and immediately before `restore;`. XSM ignores these instructions unless `--debug` is enabled.

To build and run a disposable interactive debugger session:

```sh
python3 Assignments/Exception_handler/breakpoint_stage19.py --interactive
```

Enter `fault.xsm` at the application's input prompt. At each `debug>` prompt, enter:

```text
reg EC
reg EIP
reg EPN
pt
dmt
c
```

`c` continues to the next breakpoint; `s` executes one machine instruction. The fault test stops four times: before/after code-page recovery, then before/after heap-page recovery. Entering `odd.xsm` instead produces no exception-handler stops because it causes no page fault.

Without `--interactive`, the script supplies those debugger commands automatically and saves the actual debugger output to `breakpoint_trace.txt`. Both modes run the simulator with `--debug --timer 10 --disk 100` after invoking `build.sh` and loading a temporary disk.
