#!/usr/bin/env python3
"""Build and trace Stage 19 on a disposable disk; keep normal binaries unchanged."""
from pathlib import Path
import re
import shutil
import tempfile
from test_stage19 import ROOT, STAGE, run


def main():
    run(["sh", STAGE / "build.sh"], ROOT)
    with tempfile.TemporaryDirectory(prefix="s19dbg-", dir="/tmp") as directory:
        work = Path(directory)
        fs, machine = work / "xfs-interface", work / "xsm"
        fs.mkdir()
        machine.mkdir()
        shutil.copy2(ROOT / "spl/splconstants.cfg", fs / "splconstants.cfg")

        def xfs(*args):
            return run([ROOT / "xfs-interface/xfs-interface", *args], fs)

        def compile_debug(name, source):
            (fs / f"{name}.spl").write_text(source)
            run([ROOT / "spl/spl", f"{name}.spl"], fs)
            assert len((fs / f"{name}.xsm").read_text().splitlines()) <= 512, name

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

        source = (STAGE / "exhandler.spl").read_text()
        source = source.replace("multipush(EIP);", '''multipush(EIP);
print "DBG fault";
print "EC";
print EC;
print "PID";
print [SYSTEM_STATUS_TABLE + 1];
print "EIP";
print EIP;
print "EPN";
print EPN;
print "userSP";
print [PROCESS_TABLE + [SYSTEM_STATUS_TABLE + 1] * 16 + 13];''')
        source = source.replace('[PTBR + 7] = "1110";', '''[PTBR + 7] = "1110";
print "DBG heap";
print "page2";
print [PTBR + 4];
print "page3";
print [PTBR + 6];''')
        source = source.replace('multipop(EIP);', '''multipop(EIP);
print "DBG retry";
print EIP;''')
        compile_debug("exhandler", source)
        xfs("load", "--exhandler", "exhandler.xsm")

        source = (STAGE / "mod_2.spl").read_text()
        source = source.replace("R0 = pageNumber;\n                                return;", '''R0 = pageNumber;
                                print "DBG share";
                                print R0;
                                return;''')
        source = source.replace("multipush(R5);", '''print "DBG diskload";
            print "block";
            print blockNumber;
            print "frame";
            print pageNumber;
            multipush(R5);''')
        compile_debug("mod_2", source)
        xfs("load", "--module", "2", "mod_2.xsm")

        source = (ROOT / "Assignments/Disk_Interrupt_Handler/int_2.spl").read_text()
        source = source.replace("backup;", 'backup;\nprint "DBG diskdone";')
        compile_debug("int_2", source)
        xfs("load", "--int=disk", "int_2.xsm")

        source = (STAGE / "int_9.spl").read_text()
        source = source.replace("// ---- Hand control", '''print "DBG exec";
print "code4";
print [PTBR + 8];
print "code5";
print [PTBR + 10];
print "heap2";
print [PTBR + 4];
print "heap3";
print [PTBR + 6];
// ---- Hand control''')
        compile_debug("int_9", source)
        xfs("load", "--int=9", "int_9.xsm")

        # A real user instruction fetch faults on code page 5. It then writes
        # to heap page 2 and page 3; only the first heap access should fault.
        body = ["MOV SP, 4095", "JMP 2560"]
        while 2056 + 2 * len(body) < 2560:
            body.append("MOV R0, 0")
        body += ["MOV [1024], 42", "MOV [1536], 43"]
        for value in ("[1024]", "[1536]"):
            body += ["MOV R0, 5", "PUSH R0", "MOV R0, -2", "PUSH R0",
                     f"MOV R0, {value}", "PUSH R0", "PUSH R0", "PUSH R0",
                     "INT 7"] + ["POP R0"] * 5
        body.append("INT 10")
        (fs / "fault.xsm").write_text("0\n2056\n0\n0\n0\n0\n0\n0\n" + "\n".join(body) + "\n")
        xfs("load", "--exec", "fault.xsm")

        logs = []
        for name, expected_faults, expected_loads in [("odd.xsm", [], 1), ("fault.xsm", [5, 2], 2)]:
            output = run([ROOT / "xsm/xsm", "--timer", "10", "--disk", "100"], machine, name + "\n")
            faults = [int(n) for n in re.findall(r"EPN\s*\n\s*(\d+)", output)]
            assert faults == expected_faults, output
            assert output.count("DBG diskload") == expected_loads, output
            assert output.count("DBG diskdone") == expected_loads, output
            assert output.count("DBG retry") == len(expected_faults), output
            assert "Machine is halting" in output, output
            if name == "odd.xsm":
                assert re.search(r"(?m)^1\n3\n5\n", output), output
                assert "97\n99\n" in output, output
            else:
                assert "42\n43\n" in output, output
                assert output.count("DBG heap") == 1, output
            header = f"=== {name}: {len(faults)} page faults, {expected_loads} disk loads ==="
            print(header)
            print(output)
            logs.append(header + "\n" + output)
        target = STAGE / "debug_trace.txt"
        target.write_text("\n".join(logs))
        print(f"PASS: trace saved to {target}")


if __name__ == "__main__":
    main()
