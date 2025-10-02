# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   virsh_memonly_snapshot.py
#
#   SPDX-License-Identifier: GPL-2.0
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"""
Avocado-VT test: create a *memory-only* snapshot for a running guest.

Two scenarios are supported:
  - xml_diskspec: set <disk snapshot='no'> in domain XML for all disks
  - cli_diskspec: leave XML intact, pass --diskspec <dev>,snapshot=no via CLI

If 'mem_file' is not provided, the test will create a unique path in tmp.
"""

import time
from pathlib import Path

from virttest import virsh, data_dir
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml, libvirt_disk


def run(test, params, env):
    """
    Entry point for Avocado-VT.

    Params (from .cfg):
        main_vm:      VM name under test (required)
        scenario:     'xml_diskspec' (default) or 'cli_diskspec'
        mem_file:     Optional path to store memory dump created by --memspec

    The test ensures the VM is running, applies disk snapshot=no either via
    XML or CLI, then runs:
        virsh snapshot-create-as --no-metadata --memspec file=<mem> --live
    Finally, it restores the original domain XML.
    """
    vm_name = params.get("main_vm")
    if not vm_name:
        test.error("Missing 'main_vm'")

    default_mem = Path(data_dir.get_tmp_dir()) / f"{vm_name}-{int(time.time())}.mem"
    mem_file = Path(params.get("mem_file", str(default_mem)))

    case = params.get("scenario", "")
    cli_diskspec = case == "cli_diskspec"

    vm = env.get_vm(vm_name)
    if vm is None:
        test.error(f"VM '{vm_name}' not found")

    backup_xml = None
    disk_targets = []

    def _ensure_running():
        """Start the VM if it is not already running."""
        if not vm.is_alive():
            vm.start()

    def _snapshot_create():
        """Invoke virsh to create a live memory-only snapshot."""
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        opts = f"--no-metadata --memspec file={mem_file} --live"
        if cli_diskspec:
            for dev in disk_targets:
                opts += f" --diskspec {dev},snapshot=no"
        virsh.snapshot_create_as(vm_name, options=opts, ignore_status=False, debug=True)

    def setup_test():
        """Backup XML, collect disk targets and prepare the scenario."""
        nonlocal backup_xml, disk_targets
        backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        disk_targets = libvirt_disk.all_disk_targets(vm_name)
        test.log.debug(f"scenario: {case}")
        test.log.debug(f"mem_file: {mem_file}")
        test.log.debug(f"disk_targets: {disk_targets}")
        if not cli_diskspec:
            vmx = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            for dev in disk_targets:
                disk_dict = {"target": {"dev": dev}, "snapshot": "no"}
                libvirt_vmxml.modify_vm_device(vmx, "disk", disk_dict)
            test.log.debug(vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))
        _ensure_running()

    def teardown_test():
        """Restore original domain XML (and implicit cleanup)."""
        if backup_xml is not None:
            backup_xml.sync()

    try:
        setup_test()
        _snapshot_create()
    finally:
        teardown_test()
