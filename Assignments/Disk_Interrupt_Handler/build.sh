#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
cd spl
for name in osloader mod_0 mod_4 mod_7 int_2 int_9 int_10; do
    ./spl "../Assignments/Disk_Interrupt_Handler/$name.spl"
done
for name in mod_1 mod_2 mod_5 timer console int_6 int_7 haltprog; do
    ./spl "../Assignments/Program_Loader_module/$name.spl"
done
cd ../expl
sh expl ../Assignments/Program_Loader_module/idle.expl
sh expl ../Assignments/Disk_Interrupt_Handler/init.expl
sh expl ../Assignments/Disk_Interrupt_Handler/odd.expl
