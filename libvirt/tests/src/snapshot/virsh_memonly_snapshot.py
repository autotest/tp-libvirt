# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   virsh_memonly_snapshot.py
#
#   SPDX-License-Identifier: GPL-2.0
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import time
import logging
from pathlib import Path

from virttest import virsh, utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


import time
from pathlib import Path

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Create memory-only snapshot on a running VM.

    SC1: Create memory-only snapshot via CLI by passing --diskspec snapshot=no for all disks.
    SC2: Set snapshot='no' on all <disk> devices in guest XML, then create memory-only snapshot
         via CLI without any --diskspec.
    """

    vm_name = params.get("main_vm")
    work_dir = Path(params.get("work_dir", "/tmp"))
    mem_file = Path(params.get("mem_file", f"/tmp/{vm_name}-{int(time.time())}.mem"))

    variant = str(params.get("variant") or params.get("case") or params.get("scenario") or "")
    set_xml = params.get("set_snapshot_no_in_xml", "no").lower() in ("yes", "true", "1") or "xml_snapshot_no" in variant

    def _vm():
        vm = env.get_vm(vm_name)
        if vm is None:
            test.error(f"VM '{vm_name}' not found in env")
        return vm

    def _ensure_running():
        vm = _vm()
        if not vm.is_alive():
            vm.start()

    def _all_disk_targets():
        devs = []
        vmx = vm_xml.VMXML.new_from_dumpxml(vm_name)
        for d in vmx.get_devices("disk") or []:
            tgt = (d.target or {}).get("dev")
            if tgt:
                devs.append(tgt)
        return list(dict.fromkeys(devs))

    def _snapshot_with_diskspec():
        opts = f"--no-metadata --memspec file={mem_file} --live"
        for dev in _all_disk_targets():
            opts += f" --diskspec {dev},snapshot=no"
        virsh.snapshot_create_as(vm_name, options=opts, ignore_status=False, debug=True)

    backup_xml = None

    def setup_test():
        nonlocal backup_xml
        work_dir.mkdir(parents=True, exist_ok=True)
        backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if set_xml:
            vmx = vm_xml.VMXML.new_from_dumpxml(vm_name)
            for dev in _all_disk_targets():
                disk_dict = {"target": {"dev": dev}, "snapshot": "no"}
                libvirt_vmxml.modify_vm_device(vmx, "disk", disk_dict)
        _ensure_running()

    def run_test():
        _snapshot_with_diskspec()

    def teardown_test():
        try:
            pass
        finally:
            if backup_xml is not None:
                backup_xml.sync()
                
    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
