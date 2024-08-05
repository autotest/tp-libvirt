import os

from avocado.utils import process

from virttest import virt_vm

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Write a wrapper for qemu to wait several seconds (>3) before starts.

    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    emulator_dict = eval(params.get("emulator_dict"))
    qemu_wrapper_path = params.get("qemu_wrapper_path")
    qemu_wrapper = """#!/usr/bin/env python
import os
import sys
import time

TIMEOUT = 5
QEMU_PATH = "/usr/libexec/qemu-kvm"

time.sleep(TIMEOUT)
sys.argv[0] = QEMU_PATH
os.execv(QEMU_PATH, sys.argv)

"""
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    xml_backup = vmxml.copy()

    try:
        with open(qemu_wrapper_path, "w") as f:
            f.write(qemu_wrapper)

        os.chown(qemu_wrapper_path, 107, 107)
        os.chmod(qemu_wrapper_path, 0o755)
        process.run("chcon system_u:object_r:qemu_exec_t:s0 %s" % qemu_wrapper_path, shell=True)

        libvirt_vmxml.modify_vm_device(vmxml, "emulator", emulator_dict)
        vm.start()
    except virt_vm.VMStartError as e:
        test.fail("Fail to Start vm with qemu monitor socket creation delay: %s" % e)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        if os.path.exists(qemu_wrapper_path):
            os.remove(qemu_wrapper_path)
        xml_backup.sync()
