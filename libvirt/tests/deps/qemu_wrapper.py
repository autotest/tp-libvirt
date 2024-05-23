#!/usr/bin/env python

import os
import sys
import time

TIMEOUT = 5
QEMU_PATH = "/usr/libexec/qemu-kvm"

time.sleep(TIMEOUT)
sys.argv[0] = QEMU_PATH
os.execv(QEMU_PATH, sys.argv)
