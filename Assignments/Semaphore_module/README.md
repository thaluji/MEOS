# Semaphore support for eXpOS

This stage builds on `Fork_system_call` and adds the four semaphore system
calls: `Semget`, `Semrelease`, `SemLock`, and `SemUnLock`. The sample user
program has one reader and two writers sharing a one-word heap buffer.
It prints every number from 1 through 100 once; scheduling determines the
order.

## Build and verify

From the repository root:

```sh
sh Assignments/Semaphore_module/build.sh
python3 Assignments/Semaphore_module/test_semaphores.py
```

The test builds the stage, formats a **temporary disk**, and runs the real
XSM simulator. It leaves `xfs-interface/disk.xfs` alone. It checks the
reader/writer result and total cleanup at several timer/disk settings;
descriptor validation and exhaustion; selective wakeups; and cleanup when
a process releases, exits, faults, or replaces its program with `Exec`.
Failed `Exec` is also checked: it retains its semaphore references.

## Run the demo on the repository disk

After building, from the repository root:

```sh
cd xfs-interface
./xfs-interface run ../Assignments/Semaphore_module/commands.xfs
cd ../xsm
./xsm --timer 5 --disk 20
```

The load script replaces the OS blocks on `xfs-interface/disk.xfs`.
If `semrw.xsm` already exists on that disk, remove that executable with
`./xfs-interface rm semrw.xsm` before loading. The boot-loaded INIT
executes `semrw.xsm`. After the three user processes exit, IDLE keeps
running; press Ctrl+C to stop the simulator.

## How the pieces fit

| File | Added behavior |
| --- | --- |
| `mod_7.spl` | Initializes 32 semaphore entries and loads INT 13/14. |
| `mod_0.spl` | Allocates/releases global semaphore entries and wakes matching waiters. |
| `int_13.spl` | `Semget` and `Semrelease`; validates process-local descriptors. |
| `int_14.spl` | `SemLock` and `SemUnLock`; blocks, schedules, rechecks and wakes. |
| `int_8.spl` | `Fork` copies semaphore descriptors and increments global process counts. |
| `mod_1.spl` | Exit and fatal exceptions release remaining semaphore references before freeing the user area. |
| `int_9.spl` | Successful `Exec` releases old references before clearing its resource table. |
| `init.expl`, `semrw.expl` | Launch and run the reader/writer example. |

Each process has eight resource slots in the last 16 words of its user-area
page. A semaphore descriptor (SEMID) is an index into **that process's**
resource table. Its entry points to one of the 32 **global** semaphore
table entries. Each global entry has a process count at offset 0 and a
locking PID at offset 1. `Semget` allocates both a local slot and a global
entry; `Fork` gives the child a reference to the same global entry.

`SemLock` stores `(WAIT_SEMAPHORE, global index)` in the process table if
someone else owns the lock, then calls the scheduler. When the process
resumes, it checks the lock again. Being marked `READY` does not itself
give it ownership. `SemUnLock` wakes only processes waiting for that
semaphore. `Semrelease` detaches one process; it unlocks the semaphore
only when that process is its lock owner.

The demo obtains its semaphore **before** forking, so all three processes
share the same lock. Each process exits with its descriptor still open to
exercise automatic cleanup. Assignments involving the separate reader/
writer source or performance measurements are outside this demo.
