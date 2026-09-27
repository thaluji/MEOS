# Stage 18: Disk interrupt handler

Implements [roadmap Stage 18](https://exposnitc.github.io/Roadmap.html#stage-18--disk-interrupt-handler-6-hours): Exec calls Disk Load, which acquires the disk, starts an asynchronous transfer, and blocks in WAIT_DISK. The disk interrupt releases the disk and wakes its waiters while preserving the interrupted process's registers and stack.

This stage reuses the unchanged timer, scheduler, console, Read/Write handlers, memory/process managers, and IDLE program from `../Program_Loader_module`. Its own Exit handler switches to a kernel stack before calling modules.

## Build and test

From the repository root:

```sh
sh Assignments/Disk_Interrupt_Handler/build.sh
python3 Assignments/Disk_Interrupt_Handler/test_stage18.py
```

Tests use a newly formatted temporary disk, leaving your existing disks untouched. They run the actual XSM simulator with timeouts and check:

- Missing-file Exec returns -1, and the shell can retry and run `odd.xsm` (1, 3, ..., 99), with three timer/disk latency combinations.
- A four-page executable reaches code in its fourth page with timer 5 and disk latency 1024.
- Instrumented temporary kernel copies verify the boot free-page count, all 16 resource-table words, and four disk interrupts that release INIT from WAIT_DISK while IDLE runs.
- Acquire Disk retries after two scheduler wakeups and preserves the requesting PID. This contention test uses a scheduler test double; it is not a multiple-user-process scheduling test.

## Load and run interactively

From the repository root, after building:

```sh
cd xfs-interface
./xfs-interface run ../Assignments/Disk_Interrupt_Handler/commands.xfs
cd ../xsm
./xsm --timer 10 --disk 100
```

Enter `odd.xsm`. Shell version I is replaced by the program on successful Exec; after the program exits the machine halts. A missing executable prints -1 and the shell waits for another filename.

Loading changes the OS blocks of `xfs-interface/disk.xfs`. If `odd.xsm` is already installed, XFS refuses the final executable load; the OS loads preceding it still succeed. To replace that executable after modifying it, remove that specific file with `./xfs-interface rm odd.xsm`, then reload. Do not format your existing disk to rerun this stage.

## Errors corrected

- `mod_0.spl` and `mod_4.spl`: the new disk branches lacked `then` and a matching `endif`.
- `mod_4.spl`: declaring `memoryPage` as another alias for R3 before the terminal code removed the `word` alias. Disk aliases now appear inside the disk branch, after the terminal code.
- `mod_7.spl`: the correct free count, 41, was overwritten with 45 even though pages 76–86 were allocated. Removed the overwrite.
- `mod_7.spl`: INIT's resource table was not initialized. All 16 words are now set to -1.
- `int_9.spl`: the original stride-2 loop initialized only the first word of each resource entry. It now initializes all 16 words immediately after reacquiring the user area.
- `int_2.spl`: the scan skipped PID 0. It now scans every process entry. Removed the optional debug breakpoints so debug-mode runs do not pause at each completion.
- The inherited Exit handler called modules using a logical user SP in kernel mode. The stage's `int_10.spl` now switches stacks first.
- Added a load script that installs the disk handler with `--int=disk`, a shell, an odd-number program, and repeatable tests.

The original disk-handler stack switch, backup/restore, disk release, and WAIT_DISK wakeup logic were otherwise correct. Disk Load does not need to restore R1–R4 after its final scheduler call because it returns immediately; Exec already saves the registers it needs across the module call.
