#!/usr/bin/env python3
"""Run Stage 20 on the real XSM simulator, using only a disposable disk."""
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
    assert result.returncode == 0, f"exit {result.returncode}: {result.stdout}"
    assert not re.search(r"error|failed|Unknown identifier|exception|bad", result.stdout, re.I), result.stdout
    return result.stdout


def write_value(value):
    return ["MOV R0, 5", "PUSH R0", "MOV R0, -2", "PUSH R0",
            f"MOV R0, {value}", "PUSH R0", "PUSH R0", "PUSH R0", "INT 7"] + ["POP R0"] * 5


def fork():
    return ["MOV R0, 8"] + ["PUSH R0"] * 5 + ["INT 8", "POP R0"] + ["POP R1"] * 4


def assemble(body):
    labels = {}
    instructions = []
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


def main():
    run(["sh", STAGE / "build.sh"], ROOT)
    with tempfile.TemporaryDirectory(prefix="s20-", dir="/tmp") as directory:
        work = Path(directory)
        fs, machine = work / "xfs-interface", work / "xsm"
        fs.mkdir()
        machine.mkdir()
        shutil.copy2(ROOT / "spl/splconstants.cfg", fs)

        def xfs(*args):
            return run([ROOT / "xfs-interface/xfs-interface", *args], fs)

        xfs("fdisk")
        commands = []
        for line in (STAGE / "commands.xfs").read_text().splitlines():
            args = line.split()
            source = (ROOT / "xfs-interface" / args[-1]).resolve()
            shutil.copy2(source, fs / source.name)
            args[-1] = source.name
            commands.append(" ".join(args))
        (fs / "commands.xfs").write_text("\n".join(commands) + "\n")
        xfs("run", "commands.xfs")

        def compile_spl(name, source):
            (fs / f"{name}.spl").write_text(source)
            run([ROOT / "spl/spl", f"{name}.spl"], fs)
            assert len((fs / f"{name}.xsm").read_text().splitlines()) <= 512, name

        # Stop only after all users exit, checking that shared/private pages
        # and library counts have returned to the IDLE-only baseline.
        # Production Stage 20 intentionally continues scheduling IDLE.
        stop = '''
R0 = 1;
while (R0 < MAX_PROC_NUM) do
    if ([PROCESS_TABLE + R0 * 16 + 4] != TERMINATED) then
        break;
    endif;
    R0 = R0 + 1;
endwhile;
if (R0 == MAX_PROC_NUM) then
    if ([SYSTEM_STATUS_TABLE + 2] != 48 || [MEMORY_FREE_LIST + 63] != 1 || [MEMORY_FREE_LIST + 64] != 1) then
        print "badfree";
        halt;
    endif;
    R0 = 1;
    while (R0 < MAX_PROC_NUM) do
        if ([PROCESS_TABLE + R0 * 16 + 15] == 10) then
            R1 = 0;
            while (R1 < 10) do
                if ([PAGE_TABLE_BASE + R0 * 20 + 2 * R1] != -1 || [DISK_MAP_TABLE + R0 * 10 + R1] != -1) then
                    print "badcleanup";
                    halt;
                endif;
                R1 = R1 + 1;
            endwhile;
        endif;
        R0 = R0 + 1;
    endwhile;
    print "complete";
    halt;
endif;
'''
        compile_spl("scheduler", stop + (STAGE / "mod_5.spl").read_text())
        xfs("load", "--module", "5", "scheduler.xsm")

        def simulate(data="", timer=5, disk=100, expected=()):
            output = run([ROOT / "xsm/xsm", "--timer", timer, "--disk", disk], machine, data)
            numbers = [int(n) for n in re.findall(r"(?m)^\s*(-?\d+)\s*$", output)]
            assert sorted(numbers) == sorted(expected), output
            assert "complete" in output and "Machine is halting" in output, output
            return output

        for timer, disk in ((5, 20), (10, 100), (50, 1024)):
            simulate(timer=timer, disk=disk, expected=range(1, 201))
        print("PASS Fork + independent parent/child Exec, disk waits and Exit at three timer settings")

        # Exec test executables through the previous shell so their heap and
        # extra code pages start absent, as in normal demand-paged programs.
        shutil.copy2(ROOT / "Assignments/Disk_Interrupt_Handler/init.xsm", fs / "shell.xsm")
        xfs("load", "--init", "shell.xsm")

        def program(name, body):
            (fs / name).write_text(assemble(body))
            xfs("load", "--exec", name)

        # Place the syscall return slot at 4607 and return IP at 4608, on
        # different logical stack pages. BP and both complete pages must
        # be inherited, while later stack writes must remain private.
        body = ["MOV SP, 4602", "MOV BP, 4990", "MOV [4096], 2020", "MOV [5000], 2021", "MOV [1024], 0"] + fork()
        body += ["MOV [5001], R0"] + write_value("BP") + write_value("[5001]")
        body += ["MOV R0, [5001]", "JZ R0, @child", "parent:",
                 "MOV R0, [1024]", "MOV R1, 99", "EQ R0, R1", "JZ R0, @parent"]
        body += write_value("[4096]") + write_value("[5000]") + write_value("[1536]") + ["INT 10", "child:"]
        body += ["MOV [4096], 3020", "MOV [5000], 3021", "MOV [1536], 88", "MOV [1024], 99"]
        body += write_value("[4096]") + write_value("[5000]") + ["INT 10"]
        program("share.xsm", body)
        simulate("share.xsm\n", expected=[4990, 4990, 0, 2, 2020, 2021, 3020, 3021, 88])
        print("PASS resident heap sharing, two private stack pages, boundary return slots and child BP")

        # Both processes fault in the inherited executable after Fork.
        body = ["MOV SP, 4095"] + fork() + ["MOV [1024], 77", "MOV [1536], 78", "JMP 2560"]
        while 2056 + 2 * len(body) < 2560:
            body.append("MOV R0, 0")
        body += ["JMP 3072"]
        while 2056 + 2 * len(body) < 3072:
            body.append("MOV R0, 0")
        body += ["JMP 3584"]
        while 2056 + 2 * len(body) < 3584:
            body.append("MOV R0, 0")
        body += write_value("[1024]") + ["INT 10"]
        program("lazy.xsm", body)
        simulate("lazy.xsm\n", expected=[77, 77])
        print("PASS absent heap allocation at Fork, inherited disk maps and child/parent code faults on pages 5/6/7")

        # Hold all children alive until the parent has filled every PCB,
        # including PID 15. Failure must return before trying to allocate
        # memory: at this point all 52 user-pool pages are in use too.
        body = ["MOV SP, 4100", "MOV [4096], 0", "MOV [1024], 0", "again:"] + fork()
        body += ["JZ R0, @child", "MOV R1, -1", "EQ R1, R0", "JNZ R1, @full",
                 "MOV [4097], R0", "MOV R1, [4096]", "ADD R1, 1", "MOV [4096], R1"]
        body += write_value("[4097]") + ["JMP @again", "full:"]
        body += write_value("-1") + write_value("[4096]")
        body += ["MOV [1024], 1", "INT 10", "child:", "MOV R0, [1024]", "JZ R0, @child"]
        body += write_value("0") + ["INT 10"]
        program("full.xsm", body)
        simulate("full.xsm\n", timer=10, expected=list(range(2, 16)) + [-1, 14] + [0] * 14)
        print("PASS all 16 PCB slots, PID 15, Fork failure with no free PCB/pages and orphan child exits")

        # Boot INIT has one extra resident code page, so the 14th child
        # blocks after allocating its stacks, before allocating its user
        # area. With a long timer, earlier children first run at this wait.
        # Their exits wake the parent, which must finish the reserved child.
        memory_source = (ROOT / "Assignments/Exception_handler/mod_2.spl").read_text()
        memory_source = memory_source.replace(
            "while ([SYSTEM_STATUS_TABLE + 2] == 0) do",
            'while ([SYSTEM_STATUS_TABLE + 2] == 0) do\n        print "waitmem";')
        compile_spl("memory", memory_source)
        xfs("load", "--module", "2", "memory.xsm")
        body = ["MOV SP, 4100", "MOV [4096], 0", "again:"] + fork()
        body += ["JZ R0, @child", "MOV R0, [4096]", "ADD R0, 1", "MOV [4096], R0",
                 "MOV R1, 14", "LT R0, R1", "JNZ R0, @again"]
        body += write_value("[4096]") + ["INT 10", "child:"] + write_value("0") + ["INT 10"]
        (fs / "wait.xsm").write_text(assemble(body))
        xfs("load", "--init", "wait.xsm")
        output = simulate(timer=1024, expected=[14] + [0] * 14)
        assert "waitmem" in output, output
        print("PASS Fork blocks on memory with child ALLOCATED, resumes after child Exit and completes safely")

        # Check reusable PCB initialization independently of scheduling.
        compile_spl("pcbs", '''
loadi(42, 55);
loadi(43, 56);
SP = 94 * 512 - 1;
R8 = 0;
while (R8 < MAX_PROC_NUM) do
    [PROCESS_TABLE + R8 * 16 + 4] = READY;
    R8 = R8 + 1;
endwhile;
[PROCESS_TABLE + 15 * 16 + 4] = TERMINATED;
[PAGE_TABLE_BASE + 300 + 8] = 90;
[DISK_MAP_TABLE + 150 + 4] = 100;
R1 = GET_PCB_ENTRY;
call MOD_1;
if (R0 != 15 || [PROCESS_TABLE + 240 + 1] != 15 || [PROCESS_TABLE + 240 + 4] != ALLOCATED) then
    print "badpcb";
    halt;
endif;
if ([PROCESS_TABLE + 240 + 14] != PAGE_TABLE_BASE + 300 || [PROCESS_TABLE + 240 + 15] != 10) then
    print "badpt";
    halt;
endif;
R8 = 0;
while (R8 < 10) do
    if ([PAGE_TABLE_BASE + 300 + R8 * 2] != -1 || [DISK_MAP_TABLE + 150 + R8] != -1) then
        print "badstale";
        halt;
    endif;
    R8 = R8 + 1;
endwhile;
R1 = GET_PCB_ENTRY;
call MOD_1;
if (R0 != -1) then
    print "badfull";
    halt;
endif;
[PROCESS_TABLE + 240 + 4] = TERMINATED;
R1 = GET_PCB_ENTRY;
call MOD_1;
if (R0 != 15) then
    print "badreuse";
    halt;
endif;
print "complete";
halt;
''')
        xfs("load", "--os", "pcbs.xsm")
        simulate()
        print("PASS Get PCB Entry reservation, full-table failure, stale mappings cleared and PID reuse")


if __name__ == "__main__":
    main()
