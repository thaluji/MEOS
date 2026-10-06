# Stage 20: Fork system call

Implements [Stage 20](https://exposnitc.github.io/expos-docs/roadmap/stage-20/)
on top of Stage 19's demand paging. Call `exposcall("Fork")` from ExpL:
the parent receives the child's PID, the child receives `0`, and a full
process table returns `-1` to the parent.

## Build and test

From the repository root:

```sh
sh Assignments/Fork_system_call/build.sh
python3 Assignments/Fork_system_call/test_stage20.py
```

The build also compiles the earlier-stage dependencies used by this stage.
The tests format a **temporary disk** and run the real XSM simulator; they do
not load anything onto either existing repository disk. They cover:

- Parent/child Fork, independent Exec, asynchronous disk I/O and Exit with
  three timer/disk settings; the demo prints every number from 1 through 200.
- Separate copies of both user-stack pages, shared heap writes, inherited
  BP, and a syscall whose return value and return IP straddle a page boundary.
- Fork before heap allocation and after heap allocation; inherited disk
  maps and demand faults on all three initially absent code pages.
- All 16 process slots occupied, including PID 15; `-1` on exhaustion even
  with no free memory pages, followed by complete reclamation on Exit.
- PCB reservation, clearing stale mappings and reusing a terminated PID.
- Fork blocking partway through allocation and resuming after a child exits;
  the unfinished child remains ALLOCATED throughout the wait.
- Library reference counts, private/shared page reclamation and cleared
  page/disk maps once all user processes have exited.

The test scheduler includes a temporary observation hook that checks cleanup
and halts when all user processes have terminated. The production scheduler
continues running IDLE, as specified for this stage.

## Load and run

After building, from the repository root:

```sh
cd xfs-interface
# Only if these executable names already exist on the disk:
# ./xfs-interface rm odd.xsm
# ./xfs-interface rm even.xsm
./xfs-interface run ../Assignments/Fork_system_call/commands.xfs
cd ../xsm
./xsm --timer 10 --disk 100
```

`commands.xfs` replaces the OS blocks in `xfs-interface/disk.xfs`. XFS refuses
to overwrite existing executables, so remove the old demo files first if
needed. INIT forks, the parent executes `even.xsm`, and the child executes
`odd.xsm`. Each prints 100 numbers; their interleaving depends on scheduling.
After both exit, press Ctrl+C to stop IDLE.

## Implementation

| File | Responsibility |
| --- | --- |
| `int_8.spl` | Reserve a child PCB; allocate any missing parent heap pages, two private child stack pages and a user area; copy stacks/resources/disk mappings; share library, heap and resident code pages; save BP and set both return values before publishing CREATED. |
| `mod_1.spl` | Get PCB Entry, including ALLOCATED state and clean mappings; reference-counted address-space release; resource cleanup and orphaning children on Exit. |
| `mod_5.spl` | Round-robin scheduling with wraparound and IDLE fallback; restore the saved BP for a CREATED child. |
| `mod_7.spl` | Load INT 8, initialize boot-process metadata/BP and count both boot processes' library references. |
| `int_9.spl` | Replace only the address space during Exec, retaining the PCB, user area and parent/child relationship; balance library references. |
| `int_10.spl` | Release the exiting process and schedule another process. |

Unchanged exception, memory, device, console and timer routines are loaded
from the earlier assignment directories named in `commands.xfs`. Earlier
stage sources are unchanged.

The child stays ALLOCATED while allocation can block, so it cannot run with
an incomplete address space. Its kernel stack is not copied: word zero holds
the parent's BP and KPTR is zero. The per-process resource table occupies
the last 16 words of the child user area. Unmapped code pages remain invalid
and are fetched through the inherited disk map on demand.

This stage has no swapping, file or semaphore system calls. Resource entries
are copied, but open-file/semaphore reference bookkeeping belongs to later
stages. Allocation uses the existing WAIT_MEM behavior: if memory runs out,
Fork waits for another process to release pages. If no process can release
memory, progress requires the later-stage swapping support.
