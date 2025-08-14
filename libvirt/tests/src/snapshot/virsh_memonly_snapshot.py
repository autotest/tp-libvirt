# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   virsh_memonly_snapshot.py
#
#   SPDX-License-Identifier: GPL-2.0
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import time
from pathlib import Path

from virttest import virsh, data_dir
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Create a memory-only snapshot of a running VM.

    SC1: CLI only — pass --diskspec snapshot=no for all disks.
    SC2: XML path — set snapshot='no' on all <disk> in guest XML, then run CLI without --diskspec.
    """
    vm_name = params.get("main_vm")
    # Use Avocado tmp dir by default; auto-cleaned by Avocado
    default_mem = Path(data_dir.get_tmp_dir()) / f"{vm_name}-{int(time.time())}.mem"
    mem_file = Path(params.get("mem_file", str(default_mem)))

    variant = str(params.get("variant") or params.get("case") or params.get("scenario") or "")
    set_xml = params.get("set_snapshot_no_in_xml", "no").lower() in ("yes", "true", "1") or (
        "xml_snapshot_no" in variant
    )

    vm = env.get_vm(vm_name)
    if vm is None:
        test.error(f"VM '{vm_name}' not found in env")

    disk_targets = []  # filled in setup_test()

    def _ensure_running():
        if not vm.is_alive():
            vm.start()

    def _all_disk_targets():
        devs = []
        vmx = vm_xml.VMXML.new_from_dumpxml(vm_name)
        for disk in (vmx.get_devices("disk") or []):
            tgt = (disk.target or {}).get("dev")
            if tgt:
                devs.append(tgt)
        return devs

    def _snapshot_create():
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        opts = f"--no-metadata --memspec file={mem_file} --live"
        if not set_xml:
            for dev in disk_targets:
                opts += f" --diskspec {dev},snapshot=no"
        virsh.snapshot_create_as(vm_name, options=opts, ignore_status=False, debug=True)

    backup_xml = None

    def setup_test():
        nonlocal backup_xml, disk_targets
        backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        disk_targets = _all_disk_targets()
        if set_xml:
            vmx = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            for dev in disk_targets:
                disk_dict = {"target": {"dev": dev}, "snapshot": "no"}
                libvirt_vmxml.modify_vm_device(vmx, "disk", disk_dict)
        _ensure_running()

    def run_test():
        _snapshot_create()

    def teardown_test():
        if backup_xml is not None:
            backup_xml.sync()

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
