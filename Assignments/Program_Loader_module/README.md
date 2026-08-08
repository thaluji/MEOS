# Stage 13 — Program Loader (Exec System Call)

The `Exec` system call for eXpOS. Builds on
[`../Console_Input_module`](../Console_Input_module) and adds the three pieces that let a running
process throw away its own address space and become a different program:

- **INT 9** — the `Exec` system call
- **Module 1** — the Process Manager, which hands a process's memory back
- **Module 2** — the Memory Manager, which owns the Memory Free List

This is the first stage where the OS *allocates* memory rather than handing out a layout the
boot module hardcoded.

## Control flow

```
user program
   t = exposcall("Exec", "abc.xsm")
      └── INT 9                                          int_9.spl
            ├── search the memory copy of the inode table
            │     └── not found →  return -1  (the ONLY way back to the caller)
            │
            ├── MOD_1  fn 3  Exit Process                mod_1.spl
            │     ├── MOD_1 fn 4  Free Page Table
            │     │      └── MOD_2 fn 2  Release Page   (× every private page)
            │     └── MOD_1 fn 2  Free User Area Page
            │            └── MOD_2 fn 2  Release Page
            │
            ├── re-claim the user area page  (we are standing on it)
            ├── MOD_2  fn 1  Get Free Page               mod_2.spl
            │        × 2 heap, × 2 stack, × N code
            ├── loadi  code blocks  disk → memory
            └── ireturn into the new program
```

## Files

| File | Role |
| --- | --- |
| `int_9.spl` | **`Exec` system call.** Looks the file up, tears the old address space down, builds a new one, jumps into it. |
| `mod_1.spl` | **Process Manager.** Free User Area Page (fn 2), Exit Process (fn 3), Free Page Table (fn 4). |
| `mod_2.spl` | **Memory Manager.** Get Free Page (fn 1), Release Page (fn 2). Owns the Memory Free List. |
| `int_10.spl` | `Exit` — now calls Exit Process so a dying process returns its memory. |
| `mod_7.spl` | **Boot module.** Loads INT 9, modules 1 and 2, and the memory copy of the inode table; initialises the Memory Free List. |
| `mod_0.spl` | Resource Manager — Acquire/Release Terminal (unchanged). |
| `mod_4.spl` | Device Manager — Terminal Write and Terminal Read (unchanged). |
| `mod_5.spl` | Round-robin scheduler (unchanged). |
| `int_6.spl` / `int_7.spl` | `Read` / `Write` system calls (unchanged). |
| `console.spl` / `timer.spl` | Console and timer interrupt handlers (unchanged). |
| `osloader.spl` | OS startup code; sets up IDLE and calls the boot module. |
| `init.expl` | PID 1 — fails one Exec on purpose, then execs `abc.xsm`. |
| `abc.expl` | The program INIT turns into. Same PID, new address space. |
| `idle.expl` | PID 0 — spins. Must not use the terminal. |
| `commands.xfs` | XFS load script for the whole stage. |

## Data structures

**Memory Free List** (`MEMORY_FREE_LIST`, 29184) — 128 words, one per physical page. It holds a
**reference count**, not a flag: a page is only genuinely free at zero. That distinction does
nothing yet (nothing is shared and reference-counted at this stage) but it is why Release Page
increments the free count *inside* an `if`, and it is what makes shared pages work later.

Pages 0–75 are the OS, the interrupt routines, the modules, the library and the IDLE image; they
are pinned at 1 so Get Free Page never hands them out. Pages 76–127 are the user pool.

**System Status Table** (`SYSTEM_STATUS_TABLE`, 29560):

| Word | Field |
| --- | --- |
| 0 | Current user id |
| 1 | Current PID |
| 2 | **Memory Free Count** |
| 3 | **Wait Memory Count** |

**Inode table** (`INODE_TABLE`, 30208) — 60 entries × 16 words, loaded into pages 59–60 from disk
blocks 3–4 by the boot module. Exec searches this *memory copy*; it never touches the disk copy.

| Word | Field |
| --- | --- |
| 0 | File type (`EXEC` = 3) |
| 1 | File name — e.g. `"abc.xsm"` |
| 2 | File size, in words |
| 8–11 | Data block numbers on disk |

## Memory layout

| Pages | Contents |
| --- | --- |
| 0–75 | OS, interrupts, modules, inode/user table (59–60), library (63–64), IDLE code (69–70) |
| 76–127 | User pool, managed by module 2 |

At boot, 11 pages of the pool are already spoken for, so `MEM_FREE_COUNT = 52 - 11 = 41`:

| Pages | Owner |
| --- | --- |
| 76, 77 | INIT stack |
| 78, 79 | IDLE heap |
| 80 | INIT user area |
| 81 | IDLE stack |
| 82 | IDLE user area |
| 83, 84 | INIT code |
| 85, 86 | INIT heap |

**INIT's code is loaded into the user pool (83–84), not into the OS region.** That is a
deliberate change from earlier stages. If INIT's code sat at pages 65–66 like before, Free Page
Table would release two pages that Get Free Page can never hand out again — the free count would
drift upward by two on the very first `exec` and the Memory Free List would stop meaning what it
says. The same reasoning is why INIT's heap moved to 85–86 instead of sharing 78–79 with IDLE:
freeing a page another process still maps would eventually hand IDLE's heap to somebody else.

New disk/memory loads in this stage:

| Component | Disk blocks | Memory pages |
| --- | --- | --- |
| INT 9 (`Exec`) | 33–34 | 20–21 |
| Module 1 (process manager) | 55–56 | 42–43 |
| Module 2 (memory manager) | 57–58 | 44–45 |
| Inode table + user table | 3–4 | 59–60 |

## The two things that are easy to get wrong

**1. The user area page has to be taken straight back.** Exit Process releases *everything*,
including the user area page — but that page is the kernel stack currently executing Exec. So the
moment it comes back, Exec re-claims it by hand:

```spl
[MEMORY_FREE_LIST + userAreaPage] = [MEMORY_FREE_LIST + userAreaPage] + 1;
[SYSTEM_STATUS_TABLE + 2] = [SYSTEM_STATUS_TABLE + 2] - 1;
```

There is no window for anybody else to grab it, because kernel code cannot be preempted in XSM —
the timer only fires in user mode.

**2. The lookup must happen before anything is freed.** A failed Exec returns `-1` to its caller,
so the caller has to still exist. Once Exit Process runs there is no caller and no way back —
which is why `int_9.spl` marks that spot `point of no return` and does the inode search well
above it.

Exit Process also leaves the process `TERMINATED`, so Exec has to set it back to `RUNNING`. Miss
that line and the process runs fine until its first context switch, then silently never gets
scheduled again.

## Build and run

```bash
cd /Users/muhammedthalzihap/Project/myexpos

# 1. SPL (must run from spl/ — it reads splconstants.cfg from the cwd)
cd spl
for f in mod_0 mod_1 mod_2 mod_4 mod_5 mod_7 int_6 int_7 int_9 int_10 \
         timer console osloader haltprog; do
    ./spl ../Assignments/Program_Loader_module/$f.spl
done

# 2. ExpL
cd ../expl
for f in init idle abc; do sh expl ../Assignments/Program_Loader_module/$f.expl; done

# 3. load onto the disk
cd ../xfs-interface
./xfs-interface rm abc.xsm            # only if abc.xsm is already there
./xfs-interface run ../Assignments/Program_Loader_module/commands.xfs

# 4. run
cd ../xsm
./xsm
```

Expected output:

```
1111        INIT is alive
-1          Exec("nofile.xsm") failed and INIT survived it
100
200
300
400
500         abc.xsm, running as PID 1 with a brand new address space
Machine is halting.
```

`9999` is never printed — that is the point. The successful `Exec` never returns to INIT.

## Verifying the accounting

Printing the right numbers does not prove the memory bookkeeping is right. Put a `breakpoint;`
just before the `ireturn` in `int_9.spl` and another after the `call MOD_1` in `int_10.spl`,
rebuild, and run `./xsm --debug`, then use `sst`, `mf` and `pt 1`.

Measured on this build:

| Point | `Memory Free Count` | Check |
| --- | --- | --- |
| boot | 41 | 52 pool pages − 11 in use |
| end of Exec | **42** | 41 +6 (page table) +1 (user area) −1 (re-claim) −5 (2 heap, 2 stack, 1 code) |
| after `Exit` | **48** | 42 +6 — only IDLE's 4 pages (78, 79, 81, 82) still held |

And `pt 1` at the end of Exec shows the new address space, with INIT's old pages recycled
straight back into it:

```
VIRT: 0,1  → 63, 64    library
VIRT: 2,3  → 76, 77    heap    (were INIT's stack)
VIRT: 4    → 85        code    (was  INIT's heap)
VIRT: 5,6,7 → -1                unused code slots
VIRT: 8,9  → 83, 84    stack   (were INIT's code)
```

Every page balances — no leaks, no double frees.

## Notes

- **`numBlocks` is derived from the file size**, `((size - 1) / 512) + 1`, while `xfs-interface`
  allocates `(lines / 256) + 1` blocks. The two disagree when a file is an exact multiple of 256
  lines, where xfs over-allocates by one empty block; loading one block fewer is correct in that
  case, so the formula is right and the extra disk block is simply unused.
- **Module 1 reaches the page table through the process table** (`PROCESS_TABLE + 16*pid + 14/15`)
  rather than through the live `PTBR`. Identical while a process execs itself, but it keeps the
  functions usable for a different PID — which `Exit` in later stages will need.
- **Register discipline.** Module 1 clobbers as far as R6, module 2 and the scheduler as far as
  R4. `int_9.spl` keeps its live state in R5–R9 and pushes it explicitly around every `call`;
  relying on a callee to preserve anything would be a trap.
- **Wait Memory Count is maintained but never exercised.** With 41 free pages and one exec, Get
  Free Page never has to wait. The `WAIT_MEM` path is there for the swapper in later stages.
- **Known rough edge in the scheduler.** `mod_5.spl` still scans `currentPID + 1 … 15` with no
  wraparound, defaulting to PID 0.
