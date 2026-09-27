#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
sh Assignments/Disk_Interrupt_Handler/build.sh
cd spl
for name in exhandler int_9 mod_1 mod_2 mod_7; do
    output=$(./spl "../Assignments/Exception_handler/$name.spl" 2>&1)
    if [ -n "$output" ]; then
        printf '%s\n' "$output"
        exit 1
    fi
done
