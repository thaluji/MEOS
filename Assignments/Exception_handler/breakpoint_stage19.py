#!/usr/bin/env python3
"""Run XSM --debug with real exception-handler breakpoints on a temporary disk."""
from pathlib import Path
import subprocess
import sys
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

        command = [str(ROOT / "xsm/xsm"), "--debug", "--timer", "10", "--disk", "100"]
        if "--interactive" in sys.argv:
            print("Enter fault.xsm to trigger code and heap faults, or odd.xsm for no faults.", flush=True)
            print("At each breakpoint: reg EC, reg EIP, reg EPN, pt, dmt, then c.", flush=True)
            subprocess.run(command, cwd=machine, check=True)
            return
        # Four stops: entry/recovered code fault, entry/recovered heap fault.
        inspect = "reg EC\nreg EIP\nreg EPN\npt\ndmt\nc\n"
        output = run(command, machine, "fault.xsm\n" + inspect * 4)
        assert output.count("BRKP") == 4, output
        assert "42\n43\n" in output and "Machine is halting" in output, output
        target = STAGE / "breakpoint_trace.txt"
        target.write_text(output)
        print(output)
        print(f"PASS: four real BRKP stops; debugger transcript saved to {target}")


if __name__ == "__main__":
    main()
