#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
sh Assignments/Exception_handler/build.sh
cd spl
for name in int_8 int_9 int_10 mod_1 mod_5 mod_7; do
    output=$(./spl "../Assignments/Fork_system_call/$name.spl" 2>&1)
    if [ -n "$output" ]; then
        printf '%s\n' "$output"
        exit 1
    fi
    # One instruction occupies two words; each handler/module gets 1024.
    if [ "$(wc -l < "../Assignments/Fork_system_call/$name.xsm")" -gt 512 ]; then
        printf '%s exceeds its two-page slot\n' "$name"
        exit 1
    fi
done
cd ../expl
for name in init odd even; do
    sh expl "../Assignments/Fork_system_call/$name.expl"
done
