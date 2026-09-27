#!/usr/bin/env python3
"""Exercise Stage 19 using real XSM tools and a disposable disk."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

STAGE = Path(__file__).resolve().parent
ROOT = STAGE.parent.parent


def run(args, cwd, data="", allow_exception=False):
    result = subprocess.run([str(x) for x in args], cwd=cwd, input=data,
                            text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=20)
    assert result.returncode == 0, result.stdout
    pattern = r"error|failed|Unknown identifier"
    if not allow_exception:
        pattern += r"|exception"
    assert not re.search(pattern, result.stdout, re.I), result.stdout
    return result.stdout


def main():
    run(["sh", STAGE / "build.sh"], ROOT)
    for name in ("exhandler", "int_9", "mod_1", "mod_2", "mod_7"):
        assert len((STAGE / f"{name}.xsm").read_text().splitlines()) <= 512, name
    with tempfile.TemporaryDirectory(prefix="s19-", dir="/tmp") as directory:
        work = Path(directory)
        fs, machine = work / "xfs-interface", work / "xsm"
        fs.mkdir()
        machine.mkdir()

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
        shutil.copy2(ROOT / "spl/splconstants.cfg", fs / "splconstants.cfg")

        def compile_copy(name, source):
            (fs / f"{name}.spl").write_text(source)
            run([ROOT / "spl/spl", f"{name}.spl"], fs)
            assert len((fs / f"{name}.xsm").read_text().splitlines()) <= 512, name

        def simulate(data, timer=5, disk=1024, expected=None, allow_exception=False):
            output = run([ROOT / "xsm/xsm", "--timer", timer, "--disk", disk],
                         machine, data, allow_exception)
            numbers = [int(n) for n in re.findall(r"(?m)^\s*(-?\d+)\s*$", output)]
            assert numbers == expected, output
            assert "Machine is halting" in output, output
            assert "bad" not in output, output
            return output

        def program(name, body):
            (fs / name).write_text("0\n2056\n0\n0\n0\n0\n0\n0\n" + "\n".join(body) + "\n")
            xfs("load", "--exec", name)

        def write_value(value):
            return ["MOV R0, 5", "PUSH R0", "MOV R0, -2", "PUSH R0",
                    f"MOV R0, {value}", "PUSH R0", "PUSH R0", "PUSH R0", "INT 7"] + ["POP R0"] * 5

        for timer, disk in [(5, 20), (10, 100), (50, 1024)]:
            simulate("nofile.xsm\nodd.xsm\n", timer, disk, [-1] + list(range(1, 100, 2)))
        print("PASS Exec failure/retry and odd numbers at three timer/disk settings")

        # Fetch each of the three lazy code pages, then fault on heap page 3.
        # R10 and BP must survive disk waits and exception recovery unchanged.
        body = ["MOV SP, 4095", "MOV R10, 1919", "MOV BP, 1920", "JMP 2560"]
        while 2056 + 2 * len(body) < 2560:
            body.append("MOV R0, 0")
        body.append("JMP 3072")
        while 2056 + 2 * len(body) < 3072:
            body.append("MOV R0, 0")
        body.append("JMP 3584")
        while 2056 + 2 * len(body) < 3584:
            body.append("MOV R0, 0")
        body += ["MOV [1536], R10", "MOV [1024], BP"]
        body += write_value("[1536]") + write_value("[1024]") + ["INT 10"]
        program("four.xsm", body)
        # Observe the actual fault sequence without changing the recovery logic.
        source = (STAGE / "exhandler.spl").read_text().replace(
            "multipush(EIP);", 'multipush(EIP);\nprint "pagefault";\nprint EPN;')
        compile_copy("exhandler", source)
        xfs("load", "--exhandler", "exhandler.xsm")
        output = simulate("four.xsm\n", expected=[5, 6, 7, 3, 1919, 1920])
        assert output.count("pagefault") == 4, output
        print("PASS code pages 5/6/7, both heap pages, EIP retry and R10/BP preservation")

        # Fatal exceptions should release the process and schedule away. Stop
        # the test at that scheduler boundary, before IDLE spins forever.
        checks = '''
if ([PROCESS_TABLE + 16 + 4] == TERMINATED) then
    if ([SYSTEM_STATUS_TABLE + 2] != 48) then
        print "badfree";
        halt;
    endif;
    R2 = 0;
    while (R2 < 10) do
        if ([DISK_MAP_TABLE + 10 + R2] != -1) then
            print "baddiskmap";
            halt;
        endif;
        R2 = R2 + 1;
    endwhile;
    print "terminated";
    halt;
endif;
'''
        compile_copy("scheduler", checks + (ROOT / "Assignments/Program_Loader_module/mod_5.spl").read_text())
        xfs("load", "--module", "5", "scheduler.xsm")
        shutil.copy2(STAGE / "exhandler.xsm", fs / "exhandler.xsm")
        xfs("load", "--exhandler", "exhandler.xsm")
        cases = [
            ("arith.xsm", ["MOV R0, 1", "DIV R0, 0"], "Arithmetic"),
            ("instr.xsm", ["HALT"], "Illegal instr"),
            ("mem.xsm", ["MOV R0, [5120]"], "Illegal addr"),
            ("stack.xsm", ["MOV SP, 5119", "MOV [1024], R0"], "Stack full"),
            ("absent.xsm", ["JMP 3584"], "Illegal addr"),
        ]
        for name, instructions, message in cases:
            program(name, ["MOV SP, 4095"] + instructions + ["INT 10"])
            output = simulate(name + "\n", expected=[], allow_exception=True)
            assert message in output and "terminated" in output, output
        print("PASS arithmetic, illegal instruction/address, full stack and absent code block; resources released")

        # A kernel harness checks sharing at PID 15, invalid mappings, and
        # disk-map cleanup independently of the single-process shell.
        compile_copy("modules", '''
loadi(42, 55);
loadi(43, 56);
loadi(44, 57);
loadi(45, 58);
SP = 94 * 512 - 1;
R8 = 0;
while (R8 < MAX_PROC_NUM) do
    [PROCESS_TABLE + R8 * 16 + 4] = TERMINATED;
    R8 = R8 + 1;
endwhile;
[PROCESS_TABLE + 15 * 16 + 4] = READY;
[DISK_MAP_TABLE + 150 + 4] = 100;
[PAGE_TABLE_BASE + 300 + 8] = 90;
[PAGE_TABLE_BASE + 300 + 9] = "1100";
[MEMORY_FREE_LIST + 90] = 1;
R1 = GET_CODE_PAGE;
R2 = 100;
call MOD_2;
if (R0 != 90 || [MEMORY_FREE_LIST + 90] != 2) then
    print "badshare";
    halt;
endif;
// A stale page number with an invalid PTE must not be shared.
[PROCESS_TABLE + 4] = READY;
[DISK_MAP_TABLE + 4] = 100;
[PAGE_TABLE_BASE + 8] = 91;
[PAGE_TABLE_BASE + 9] = "1000";
[MEMORY_FREE_LIST + 91] = 1;
R1 = GET_CODE_PAGE;
R2 = 100;
call MOD_2;
if (R0 != 90 || [MEMORY_FREE_LIST + 91] != 1) then
    print "badvalid";
    halt;
endif;
R1 = GET_CODE_PAGE;
R2 = -1;
call MOD_2;
if (R0 != -1) then
    print "badblock";
    halt;
endif;
[PROCESS_TABLE + 15 * 16 + 14] = PAGE_TABLE_BASE + 300;
[PROCESS_TABLE + 15 * 16 + 15] = 10;
R8 = 0;
while (R8 < 10) do
    [PAGE_TABLE_BASE + 300 + 2 * R8] = -1;
    [PAGE_TABLE_BASE + 300 + 2 * R8 + 1] = "0000";
    [DISK_MAP_TABLE + 150 + R8] = -1;
    R8 = R8 + 1;
endwhile;
[PAGE_TABLE_BASE + 300 + 8] = 90;
[PAGE_TABLE_BASE + 300 + 9] = "1100";
[DISK_MAP_TABLE + 150 + 4] = 100;
[DISK_FREE_LIST + 100] = 1;
[DISK_MAP_TABLE + 150 + 2] = 101;
[DISK_FREE_LIST + 101] = 1;
[SYSTEM_STATUS_TABLE + 2] = 0;
R1 = FREE_PAGE_TABLE;
R2 = 15;
call MOD_1;
if ([MEMORY_FREE_LIST + 90] != 2 || [DISK_FREE_LIST + 100] != 1 || [DISK_FREE_LIST + 101] != 0) then
    print "badrelease";
    halt;
endif;
R8 = 0;
while (R8 < 10) do
    if ([DISK_MAP_TABLE + 150 + R8] != -1) then
        print "baddiskmap";
        halt;
    endif;
    R8 = R8 + 1;
endwhile;
print 1921;
halt;
''')
        xfs("load", "--os", "modules.xsm")
        simulate("", expected=[1921])
        print("PASS PID 15 code sharing, valid-bit check, reference counts and swap-block cleanup")


if __name__ == "__main__":
    main()
