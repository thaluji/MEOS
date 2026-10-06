#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."

# Compile exactly the SPL dependencies used by this stage's load script.
# No XFS load/format operations occur during a build.
cd spl
for source in \
    Disk_Interrupt_Handler/osloader Disk_Interrupt_Handler/int_2 \
    Disk_Interrupt_Handler/mod_4 \
    Program_Loader_module/timer Program_Loader_module/console \
    Program_Loader_module/int_6 Program_Loader_module/int_7 \
    Exception_handler/exhandler Exception_handler/mod_2 \
    Fork_system_call/int_10 Fork_system_call/mod_5 \
    Semaphore_module/int_8 Semaphore_module/int_9 \
    Semaphore_module/int_13 Semaphore_module/int_14 \
    Semaphore_module/mod_0 Semaphore_module/mod_1 Semaphore_module/mod_7; do
    build_output=$(./spl "../Assignments/$source.spl" 2>&1)
    if [ -n "$build_output" ]; then
        printf '%s\n' "$build_output"
        exit 1
    fi
    if [ "$(wc -l < "../Assignments/$source.xsm")" -gt 512 ]; then
        printf '%s exceeds its two-page slot\n' "$source"
        exit 1
    fi
done
cd ../expl
for source in Program_Loader_module/idle Semaphore_module/init Semaphore_module/semrw; do
    sh expl "../Assignments/$source.expl"
done
if [ "$(wc -l < ../Assignments/Semaphore_module/init.xsm)" -gt 516 ]; then
    printf '%s\n' 'INIT exceeds its two boot-loaded pages'
    exit 1
fi
if [ "$(wc -l < ../Assignments/Semaphore_module/semrw.xsm)" -gt 1028 ]; then
    printf '%s\n' 'semrw exceeds its four code pages'
    exit 1
fi
