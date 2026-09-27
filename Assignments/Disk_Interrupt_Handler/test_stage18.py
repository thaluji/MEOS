#!/usr/bin/env python3
"""Compile and run Stage 18 on a disposable XFS disk, using real XSM tools."""
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
                            stderr=subprocess.STDOUT, timeout=20)
    assert result.returncode == 0, result.stdout
    assert not re.search(r"error|exception|failed|Unknown identifier", result.stdout,
                         re.I), result.stdout
    return result.stdout


def main():
    run(["sh", STAGE / "build.sh"], ROOT)
    # Short paths also avoid the XFS interface's small pathname buffer.
    with tempfile.TemporaryDirectory(prefix="s18-", dir="/tmp") as directory:
        work = Path(directory)
        fs = work / "xfs-interface"
        machine = work / "xsm"
        fs.mkdir()
        machine.mkdir()
        def xfs(*args):
            return run([ROOT / "xfs-interface/xfs-interface", *args], fs)

        xfs("fdisk")
        commands = []
        for line in (STAGE / "commands.xfs").read_text().splitlines():
            args = line.split()
            source = (ROOT / "xfs-interface" / args[-1]).resolve()
            target = fs / source.name
            shutil.copy2(source, target)
            args[-1] = source.name
            commands.append(" ".join(args))
        (fs / "commands.xfs").write_text("\n".join(commands) + "\n")
        xfs("run", "commands.xfs")

        def simulate(data, timer=10, disk=20, expected=None):
            output = run([ROOT / "xsm/xsm", "--timer", timer, "--disk", disk],
                         machine, data)
            numbers = [int(n) for n in re.findall(r"(?m)^\s*(-?\d+)\s*$", output)]
            assert numbers == expected, output
            assert "Machine is halting" in output, output
            return output

        for timer, disk in [(5, 20), (10, 100), (50, 1024)]:
            simulate("nofile.xsm\nodd.xsm\n", timer, disk, [-1] + list(range(1, 100, 2)))
            print(f"PASS shell: missing file, retry, odd numbers; timer={timer}, disk={disk}")

        # A four-page executable jumps straight to its fourth page. A missing
        # transfer or a clobbered Exec loop counter cannot pass this test.
        body = ["JMP 3584"] + ["MOV R0, 0"] * 763
        body += ["MOV SP, 4095", "MOV R0, 5", "PUSH R0", "MOV R0, -2",
                 "PUSH R0", "MOV R0, 1818", "PUSH R0", "PUSH R0", "PUSH R0",
                 "INT 7", "POP R0", "POP R0", "POP R0", "POP R0", "POP R0", "INT 10"]
        (fs / "four.xsm").write_text("0\n2056\n0\n0\n0\n0\n0\n0\n" + "\n".join(body) + "\n")
        xfs("load", "--exec", "four.xsm")
        simulate("four.xsm\n", 5, 1024, [1818])
        print("PASS four-page Exec with slow disk and frequent timer interrupts")

        # Compile instrumented copies only: assertions run inside the guest
        # before INIT starts, at each disk completion, and after Exec loading.
        shutil.copy2(ROOT / "spl/splconstants.cfg", fs / "splconstants.cfg")
        def compile_copy(name, source):
            (fs / f"{name}.spl").write_text(source)
            run([ROOT / "spl/spl", f"{name}.spl"], fs)
        def check(condition, tag):
            return f'if ({condition}) then\nprint "{tag}";\nhalt;\nendif;\n'

        boot_checks = check("[SYSTEM_STATUS_TABLE + 2] != 41", "badfree")
        for i in range(496, 512):
            boot_checks += check(f"[80 * 512 + {i}] != -1", "badresource")
        compile_copy("mod_7", (STAGE / "mod_7.spl").read_text().replace("return;", boot_checks + "return;"))
        xfs("load", "--module", "7", "mod_7.xsm")

        disk_checks = check("[DISK_STATUS_TABLE] != 1", "badbusy")
        disk_checks += check("[SYSTEM_STATUS_TABLE + 1] != 0", "badinterrupted")
        disk_checks += check("[PROCESS_TABLE + 16 + 4] != WAIT_DISK", "badwait")
        disk_checks += 'print "diskdone";\n'
        source = (STAGE / "int_2.spl").read_text().replace("backup;", "backup;\n" + disk_checks)
        source = source.replace("restore;", check("[PROCESS_TABLE + 16 + 4] != READY", "badwake") + "restore;")
        compile_copy("int_2", source)
        xfs("load", "--int=disk", "int_2.xsm")

        # INT 9 has a two-page limit; a compact loop fits where 16 checks don't.
        exec_checks = '''R4 = 496;
while (R4 < 512) do
    if ([userAreaPage * 512 + R4] != -1) then
        print "badresource";
        halt;
    endif;
    R4 = R4 + 1;
endwhile;
'''
        compile_copy("int_9", (STAGE / "int_9.spl").read_text().replace("// ---- Hand control", exec_checks + "// ---- Hand control"))
        xfs("load", "--int=9", "int_9.xsm")
        output = simulate("four.xsm\n", 5, 1024, [1818])
        assert output.count("diskdone") == 4, output
        assert "bad" not in output, output
        print("PASS guest assertions: boot count/resources, four WAIT_DISK -> READY completions, Exec resources")

        # Exercise Acquire Disk's busy loop with a scheduler test double.
        # Two wakeups are required: the first deliberately leaves it locked.
        compile_copy("scheduler_test", '''
if ([PROCESS_TABLE + 7 * 16 + 4] != WAIT_DISK) then
    print "badwait";
    halt;
endif;
[95 * 512] = [95 * 512] + 1;
if ([95 * 512] == 2) then
    [DISK_STATUS_TABLE] = 0;
endif;
R1 = -99;
R2 = -99;
return;
''')
        xfs("load", "--module", "5", "scheduler_test.xsm")
        compile_copy("acquire_test", '''
loadi(40, 53);
loadi(41, 54);
loadi(50, 63);
loadi(51, 64);
SP = 94 * 512 - 1;
[95 * 512] = 0;
[DISK_STATUS_TABLE] = 1;
R1 = ACQUIRE_DISK;
R2 = 7;
call MOD_0;
if ([95 * 512] != 2 || [DISK_STATUS_TABLE] != 1 || [DISK_STATUS_TABLE + 4] != 7) then
    print "badacquire";
    halt;
endif;
print 1819;
halt;
''')
        xfs("load", "--os", "acquire_test.xsm")
        simulate("", expected=[1819])
        print("PASS Acquire Disk contention: retries twice and preserves PID across scheduler calls")


if __name__ == "__main__":
    main()
