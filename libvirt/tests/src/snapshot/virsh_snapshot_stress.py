# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   virsh_snapshot_stress.py
#
#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import shutil
import time
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from virttest import virsh, utils_libvirtd
from virttest.libvirt_xml import vm_xml
from provider.snapshot import snapshot_base

virsh_dargs = {"debug": True, "ignore_status": False}


def _bool(v):
    return str(v).strip().lower() in ("yes", "true", "1")


def run(test, params, env):
    # ================== HELPER FUNCTIONS (INSIDE run) ===================
    def _flatten(result):
        while isinstance(result, list) and result:
            result = result[0]
        return result

    def _v(cmd, *a, **kw):
        dargs = virsh_dargs.copy()
        dargs.update(kw)
        r = cmd(*a, **dargs)
        result = _flatten(r)
        if hasattr(result, "exit_status") and result.exit_status != 0:
            if not dargs.get("ignore_status", False):
                if "already exists" not in result.stderr_text:
                    test.fail(f"{result.command} failed:\n{result.stderr_text}")
        return result

    def _disk_path(vm, target):
        result = _v(virsh.domblklist, vm, options="--details")
        
        for line in result.stdout_text.splitlines()[2:]:
            p = line.split()
            if len(p) == 4 and p[2] == target:
                return Path(p[3])
                
        test.fail(f"disk {target} not found")

    def _patch_xml_text(xml_text, target, new_path):
        tree = ET.fromstring(xml_text)
        node = tree.find(f".//disk/target[@dev='{target}']/../source")
        if node is None:
            test.fail(f"Could not find disk with target '{target}' in the XML.")
        node.set("file", str(new_path))
        return ET.tostring(tree, encoding="unicode")

    def _get_snapshot_names(vm_name):
        result = _v(virsh.snapshot_list, vm_name)

        stdout_text = ""
        if hasattr(result, 'exit_status') and result.exit_status == 0:
            if hasattr(result, 'stdout_text'):
                stdout_text = result.stdout_text
        elif isinstance(result, str):
            stdout_text = result
        
        names = []
        if stdout_text:
            lines = stdout_text.splitlines()
            if len(lines) > 2:
                for line in lines[2:]:
                    if line.strip():
                        parts = line.split()
                        if parts:
                            names.append(parts[0])
        return names


    def _delete_existing_snapshots(vm_name: str, is_external: bool):
        names = _get_snapshot_names(vm_name)
        if not names:
            test.log.info("TEST_SETUP: No leftover snapshots found.")
            return

        test.log.warning("TEST_SETUP: Found %d leftover snapshots, deleting...", len(names))
        for snap in reversed(names):
            test.log.info("Deleting snapshot: %s", snap)
            try:
                options = "--children" if is_external else ""
                _v(virsh.snapshot_delete, vm_name, snap, options=options, ignore_status=True)
            except Exception as e:
                logging.warning("Could not delete leftover snapshot %s: %s", snap, e)

        left = _get_snapshot_names(vm_name)
        if left:
            test.fail(f"Failed to wipe all leftover snapshots before the test: {left}")


    if snap_type not in ("internal", "external"):
        test.fail("snapshot_type must be internal|external")

    vm    = env.get_vm(vm_name)
    libv  = utils_libvirtd.Libvirtd()
    sutil = snapshot_base.SnapshotTest(vm, test, params)

    work_dir.mkdir(parents=True, exist_ok=True)
    xml_changed         = False
    original_xml_text   = None
    orig_path           = None
    new_path            = None

    # ================== SETUP ===================
    def setup_test():
        nonlocal xml_changed, original_xml_text, orig_path, new_path

        test.log.info("TEST_SETUP: Waiting for VM to be fully booted...")
        vm.wait_for_login().close()
        test.log.info("TEST_SETUP: VM is ready.")

        _delete_existing_snapshots(vm_name, is_external)

        original_xml_text = _v(virsh.dumpxml, vm_name).stdout_text
        orig_path = _disk_path(vm_name, target_dev)

        if not str(orig_path).startswith(str(work_dir)):
            test.log.info("TEST_SETUP: Disk is not in work_dir, will use a copy.")

            test.log.info("TEST_SETUP: Powering off VM to copy disk...")
            vm.destroy()
            vm.wait_for_shutdown()

            new_path = work_dir / orig_path.name
            if new_path.exists():
                os.remove(new_path)
            test.log.info("TEST_SETUP: Copying '%s' -> '%s'", orig_path, new_path)
            shutil.copy2(orig_path, new_path)

            patched_xml_text = _patch_xml_text(original_xml_text, target_dev, new_path)
            vm_xml.VMXML(xml=patched_xml_text, virsh_instance=virsh).define()
            xml_changed = True

        if not vm.is_alive():
            test.log.info("TEST_SETUP: Starting VM for the test.")
            vm.start()
            vm.wait_for_login().close()

    # ================== RUN =====================
    def run_test():
        test.log.info("TEST_STEP1: create %d %s snapshots", count, snap_type)

        for i in range(count):
            snap_name = f"stress-snap-{i}"
            test.log.info("Creating snapshot %s (%d/%d)", snap_name, i + 1, count)

            if is_external:
                snap_disk = work_dir / f"{snap_name}-{target_dev}.qcow2"
                diskspec = f"{target_dev},file={snap_disk},snapshot=external"
                _v(virsh.snapshot_create_as, vm_name, snapshotname=snap_name,
                   options=f"--diskspec {diskspec} --disk-only")
            else:
                _v(virsh.snapshot_create_as, vm_name, snapshotname=snap_name,
                   options="--disk-only")

            if interval:
                time.sleep(interval)

        test.log.info("TEST_STEP2: restart libvirtd")
        libv.restart()

        test.log.info("TEST_STEP3: sanity checks")
        vm.wait_for_login().close()
        _v(virsh.domstats,   vm_name)
        _v(virsh.domblkinfo, vm_name, target_dev)
        
        snap_names_in_run = _get_snapshot_names(vm_name)
        if len(snap_names_in_run) != count:
            test.fail(f"expected {count} snapshots, got {len(snap_names_in_run)}")
        
        sutil.log_snapshot_list()

    def teardown_test():
        nonlocal original_xml_text, new_path, xml_changed

        if cleanup:
            _delete_existing_snapshots(vm_name, is_external)

        if xml_changed:
            test.log.info("TEST_TEARDOWN: restoring original XML and qcow2")
            if vm.is_alive():
                vm.destroy()
                vm.wait_for_shutdown()

            if new_path and new_path.exists():
                os.remove(new_path)

            if original_xml_text:
                vm_xml.VMXML(xml=original_xml_text, virsh_instance=virsh).define()

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
