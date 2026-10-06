#!/usr/bin/env python3
"""Build and exercise semaphore support on a disposable real-XSM disk."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

STAGE = Path(__file__).resolve().parent
ROOT = STAGE.parent.parent


def run(args, cwd, data=""):
    result = subprocess.run([str(x) for x in args], cwd=cwd, input=data,
                            text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=30)
    assert result.returncode == 0, result.stdout
    assert not re.search(r"error|failed|Unknown identifier|bad", result.stdout, re.I), result.stdout
    return result.stdout


def assemble(body):
    labels, instructions = {}, []
    for line in body:
        if line.endswith(":"):
            labels[line[:-1]] = 2056 + 2 * len(instructions)
        else:
            instructions.append(line)
    for i, line in enumerate(instructions):
        for label, address in labels.items():
            line = line.replace("@" + label, str(address))
        assert "@" not in line, line
        instructions[i] = line
    return "0\n2056\n0\n0\n0\n0\n0\n0\n" + "\n".join(instructions) + "\n"


def syscall(number, argument=0, interrupt=14):
    return [f"MOV R0, {number}", "PUSH R0", f"MOV R0, {argument}"] + ["PUSH R0"] * 4 + [f"INT {interrupt}", "POP R0"] + ["POP R1"] * 4


def write_value(value):
    return ["MOV R0, 5", "PUSH R0", "MOV R0, -2", "PUSH R0",
            f"MOV R0, {value}", "PUSH R0", "PUSH R0", "PUSH R0", "INT 7"] + ["POP R0"] * 5


def check_source(checks):
    return "".join(f'if ({check}) then\n print "bad{i}";\n halt;\nendif;\n'
                   for i, check in enumerate(checks))


class Fixture:
    def __init__(self, directory):
        self.fs, self.machine = directory / "xfs-interface", directory / "xsm"
        self.fs.mkdir()
        self.machine.mkdir()
        shutil.copy2(ROOT / "spl/splconstants.cfg", self.fs)
        self.xfs("fdisk")
        commands = []
        for line in (STAGE / "commands.xfs").read_text().splitlines():
            args = line.split()
            source = (ROOT / "xfs-interface" / args[-1]).resolve()
            shutil.copy2(source, self.fs / source.name)
            args[-1] = source.name
            commands.append(" ".join(args))
        (self.fs / "commands.xfs").write_text("\n".join(commands) + "\n")
        self.xfs("run", "commands.xfs")

    def xfs(self, *args):
        return run([ROOT / "xfs-interface/xfs-interface", *args], self.fs)

    def compile(self, name, source):
        (self.fs / f"{name}.spl").write_text(source)
        run([ROOT / "spl/spl", f"{name}.spl"], self.fs)
        assert len((self.fs / f"{name}.xsm").read_text().splitlines()) <= 512, name

    def boot(self, setup=""):
        source = (STAGE / "mod_7.spl").read_text()
        source = source.replace("\nreturn;", "\nloadi(32, 45);\nloadi(33, 46);\n" + setup + "\nreturn;")
        self.compile("boot", source)
        self.xfs("load", "--module", "7", "boot.xsm")

    def program(self, body, name="probe.xsm", executable=False):
        (self.fs / name).write_text(assemble(body))
        self.xfs("load", "--exec" if executable else "--init", name)

    def observe(self, checks, extra=""):
        self.compile("observer", check_source(checks) + extra + 'print "complete";\nhalt;\n')
        self.xfs("load", "--int=15", "observer.xsm")

    def scheduler(self, hook="", cleanup=False):
        if cleanup:
            hook += '''
R0 = 1;
while (R0 < MAX_PROC_NUM) do
    if ([PROCESS_TABLE + R0 * 16 + 4] != TERMINATED) then
        break;
    endif;
    R0 = R0 + 1;
endwhile;
if (R0 == MAX_PROC_NUM) then
    if ([SYSTEM_STATUS_TABLE + 2] != 48 || [MEMORY_FREE_LIST + 63] != 1 || [MEMORY_FREE_LIST + 64] != 1) then
        print "badmemory";
        halt;
    endif;
    R0 = 0;
    while (R0 < MAX_SEM_COUNT) do
        if ([SEMAPHORE_TABLE + R0 * 4] != 0 || [SEMAPHORE_TABLE + R0 * 4 + 1] != -1) then
            print "badleak";
            halt;
        endif;
        R0 = R0 + 1;
    endwhile;
    print "complete";
    halt;
endif;
'''
        self.compile("scheduler", hook + (ROOT / "Assignments/Fork_system_call/mod_5.spl").read_text())
        self.xfs("load", "--module", "5", "scheduler.xsm")

    def simulate(self, name, expected=(), timer=1000, disk=20, message=None):
        output = run([ROOT / "xsm/xsm", "--timer", timer, "--disk", disk], self.machine)
        assert "complete" in output and "Machine is halting" in output, output
        numbers = [int(n) for n in re.findall(r"(?m)^\s*(-?\d+)\s*$", output)]
        assert sorted(numbers) == sorted(expected), output
        if message:
            assert message in output, output
        print(f"PASS {name} (timer={timer}, disk={disk})")


def test_limits(f):
    f.scheduler()
    for occupied, calls, expected in ((0, 9, list(range(8)) + [-1]), (32, 1, [-2]), (3, 1, [0])):
        allocated = sum(value >= 0 for value in expected)
        f.boot("".join(f"[SEMAPHORE_TABLE + {i * 4}] = 1;\n" for i in range(occupied)))
        body = ["MOV SP, 4603", "MOV BP, 4900"]
        for i in range(calls):
            body += syscall(17, interrupt=13) + [f"MOV [{4700 + i}], R0"]
        f.program(body + ["INT 15"])
        checks = ["SP != 4604", "BP != 4900", "[PROCESS_TABLE + 16 + 9] != 0"]
        checks += [f"[{77 * 512 + 92 + i}] != {value}" for i, value in enumerate(expected)]
        f.observe(checks, f'''
R0 = 0;
while (R0 < 32) do
    R1 = 0;
    if (R0 < {occupied + allocated}) then
        R1 = 1;
    endif;
    if ([SEMAPHORE_TABLE + R0 * 4] != R1 || [SEMAPHORE_TABLE + R0 * 4 + 1] != -1) then
        print "badglobal";
        halt;
    endif;
    R0 = R0 + 1;
endwhile;
R0 = 0;
while (R0 < 8) do
    R1 = -1;
    R2 = -1;
    if (R0 < {allocated}) then
        R1 = SEMAPHORE;
        R2 = {occupied} + R0;
    endif;
    if ([80 * 512 + 496 + R0 * 2] != R1 || [80 * 512 + 497 + R0 * 2] != R2) then
        print "badlocal";
        halt;
    endif;
    R0 = R0 + 1;
endwhile;
''')
        f.simulate(f"Semget limits/mapping with {occupied} existing semaphores")


def test_lock_release(f):
    # The first descriptor maps to global entry 5; slot 3 represents a file.
    cases = [
        ("relock then one unlock", 1, -1, [(19, 0), (19, 0), (20, 0)], [0, 0, 0], 1, -1, True, False),
        ("nonowner unlock rejected", 2, 2, [(20, 0)], [-2], 2, 2, False, False),
        ("already unlocked", 1, -1, [(20, 0)], [0], 1, -1, False, False),
        ("owner release wakes matching waiters", 2, 1, [(18, 0)], [0], 1, -1, True, True),
        ("nonowner release preserves lock", 2, 2, [(18, 0)], [0], 1, 2, False, True),
        ("double release", 1, -1, [(18, 0), (18, 0)], [0, -1], 0, -1, False, True),
        ("invalid descriptors", 1, -1, [(19, -1), (20, 8), (18, 7), (19, 3)], [-1] * 4, 1, -1, False, False),
    ]
    for name, count, owner, operations, expected, final_count, final_owner, wakes, freed in cases:
        f.boot(f'''
[80 * 512 + 496] = SEMAPHORE;
[80 * 512 + 497] = 5;
[80 * 512 + 502] = FILE;
[80 * 512 + 503] = 9;
[SEMAPHORE_TABLE + 20] = {count};
[SEMAPHORE_TABLE + 21] = {owner};
[PROCESS_TABLE + 3 * 16 + 4] = WAIT_SEMAPHORE;
[PROCESS_TABLE + 3 * 16 + 5] = 5;
[PROCESS_TABLE + 4 * 16 + 4] = WAIT_SEMAPHORE;
[PROCESS_TABLE + 4 * 16 + 5] = 6;
[PROCESS_TABLE + 5 * 16 + 4] = WAIT_DISK;
[PROCESS_TABLE + 5 * 16 + 5] = 5;
[PROCESS_TABLE + 15 * 16 + 4] = WAIT_SEMAPHORE;
[PROCESS_TABLE + 15 * 16 + 5] = 5;
''')
        body = ["MOV SP, 4606", "MOV BP, 4900"]
        for i, (number, arg) in enumerate(operations):
            body += syscall(number, arg, 13 if number == 18 else 14) + [f"MOV [{4700 + i}], R0"]
        f.program(body + ["INT 15"])
        checks = ["SP != 4607", "BP != 4900", "[PROCESS_TABLE + 16 + 9] != 0",
                  f"[SEMAPHORE_TABLE + 20] != {final_count}", f"[SEMAPHORE_TABLE + 21] != {final_owner}",
                  f"[80 * 512 + 496] != {-1 if freed else 1}", f"[80 * 512 + 497] != {-1 if freed else 5}",
                  "[80 * 512 + 502] != FILE", "[80 * 512 + 503] != 9"]
        checks += [f"[{77 * 512 + 92 + i}] != {value}" for i, value in enumerate(expected)]
        for pid, state in ((3, "READY" if wakes else "WAIT_SEMAPHORE"), (4, "WAIT_SEMAPHORE"),
                           (5, "WAIT_DISK"), (15, "READY" if wakes else "WAIT_SEMAPHORE")):
            checks.append(f"[PROCESS_TABLE + {pid} * 16 + 4] != {state}")
        f.observe(checks)
        f.simulate(name)


def test_lifecycle(f):
    # Make parent wait until a real child has blocked inside SemLock.
    hook = '''
if ([PROCESS_TABLE + [SYSTEM_STATUS_TABLE + 1] * 16 + 4] == WAIT_SEMAPHORE) then
    [SEMAPHORE_TABLE + 2] = [SEMAPHORE_TABLE + 2] + 1;
    [85 * 512] = 1;
endif;
'''
    f.scheduler(hook, cleanup=True)
    # Save results before Write clobbers R0.
    fresh = ["MOV SP, 4606"] + syscall(20) + ["MOV [4700], R0"] + write_value("[4700]")
    fresh += syscall(17, interrupt=13) + ["MOV [4700], R0"] + write_value("[4700]") + ["INT 10"]
    f.program(fresh, "fresh.xsm", executable=True)

    for action in ("unlock", "release", "exit", "exception", "exec", "failed_exec"):
        f.boot("[SEMAPHORE_TABLE + 2] = 0;\n")
        # Observer checks that the child resumed only after a real sleep,
        # and that restored BP and page-crossing return slots are intact.
        source = check_source(["[SEMAPHORE_TABLE + 2] < 1", "BP != 4900", "SP != 4607",
                               "[PROCESS_TABLE + 32 + 9] != 0", "[SEMAPHORE_TABLE + 1] != 2"])
        source += 'ireturn;\n'
        f.compile("observer", source)
        f.xfs("load", "--int=15", "observer.xsm")
        body = ["MOV SP, 4606", "MOV BP, 4900", "MOV [1024], 0"]
        body += syscall(17, interrupt=13) + syscall(19) + syscall(8, interrupt=8)
        body += ["JZ R0, @child", "wait:", "MOV R0, [1024]", "JZ R0, @wait"]
        expected, message = [42], None
        if action == "unlock":
            body += syscall(20) + ["INT 10"]
        elif action == "release":
            body += syscall(18, interrupt=13) + ["INT 10"]
        elif action == "exit":
            body += ["INT 10"]
        elif action == "exception":
            body += ["MOV R0, 1", "DIV R0, 0"]
            message = "Arithmetic"
        elif action == "exec":
            body += syscall(9, '"fresh.xsm"', 9)
            expected += [-1, 0]
        else:
            body += syscall(9, '"missing.xsm"', 9) + ["MOV [4700], R0"] + write_value("[4700]")
            # Re-lock by same owner must still succeed after failed Exec.
            body += syscall(19) + ["MOV [4700], R0"] + write_value("[4700]")
            body += syscall(20) + ["INT 10"]
            expected += [-1, 0]
        body += ["child:"] + syscall(19) + ["INT 15"]
        body += write_value(42) + syscall(18, interrupt=13) + ["INT 10"]
        f.program(body)
        for timer, disk in ((5, 20), (1000, 100)):
            f.simulate(f"blocked child wakes after parent {action}; complete cleanup", expected, timer, disk, message)


def main():
    run(["sh", STAGE / "build.sh"], ROOT)
    with tempfile.TemporaryDirectory(prefix="sem-", dir="/tmp") as directory:
        f = Fixture(Path(directory))
        f.scheduler(cleanup=True)
        for timer, disk in ((5, 20), (50, 100), (1000, 1024)):
            f.simulate("two writers/one reader: 1..100 exactly once, no leaks", range(1, 101), timer, disk)
        test_limits(f)
        test_lock_release(f)
        test_lifecycle(f)


if __name__ == "__main__":
    main()
