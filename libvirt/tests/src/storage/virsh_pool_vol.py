import os
import tempfile
import logging as log

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_storage
from virttest.utils_test import libvirt as utlv
from virttest.staging import service


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test virsh storage pool and volume lifecycle for dir-type pools
    (dirpool / fspool / defaultpool) covering two volume formats (qcow2,
    raw) and two disk attach/detach methods.


    :param test:   Avocado-VT test object.
    :param params: Cartesian-config parameter dictionary.
    :param env:    Avocado-VT environment object.

    """
    pool_name = params.get("pool_name", "dirpool")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target",
                             "/var/lib/libvirt/images/dirpool")
    pool_source_format = params.get("pool_source_format", "ext4")
    vol_name = params.get("vol_name", "vol1.qcow2")
    vol_capacity = params.get("vol_capacity", "10G")
    vol_format = params.get("vol_format", "qcow2")
    status_error = "yes" == params.get("status_error", "no")
    test_attach = "yes" == params.get("test_attach", "no")
    test_attach_device = "yes" == params.get("test_attach_device", "no")
    disk_target = params.get("disk_target", "vdh")
    disk_bus = params.get("disk_bus", "virtio")
    disk_slot = params.get("disk_slot", "0x09")
    at_options = params.get("at_options", "--live")
    dt_options = params.get("dt_options", "--live")
    ad_options = params.get("ad_options", "--persistent")
    dd_options = params.get("dd_options", "")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vol_path = os.path.join(pool_target, vol_name)

    def check_pool_list(expect_present=True):
        """Return False only when pool_name is present but inactive."""
        result = virsh.pool_list("--all", ignore_status=True)
        utlv.check_exit_status(result, False)
        pool_line = next((line for line in result.stdout.strip().splitlines()
                          if pool_name in line), None)
        found = pool_line is not None
        pool_state = None
        if pool_line:
            pool_cols = pool_line.split()
            if len(pool_cols) >= 2:
                pool_state = pool_cols[1]
        if expect_present and not found:
            test.fail("Pool '%s' not found in 'virsh pool-list --all'"
                      % pool_name)
        if expect_present and pool_state == "inactive":
            logging.debug("pool-list check: pool='%s' present=%s state=%s (expected=%s)",
                          pool_name, found, pool_state, expect_present)
            return False
        if not expect_present and found:
            test.fail("Pool '%s' still present in pool-list after cleanup"
                      % pool_name)
        logging.debug("pool-list check: pool='%s' present=%s state=%s (expected=%s)",
                      pool_name, found, pool_state, expect_present)
        return True

    def check_vol_list(expect_present=True):
        """Assert vol_name is (or is not) visible in virsh vol-list <pool>."""
        result = virsh.vol_list(pool_name, ignore_status=True)
        utlv.check_exit_status(result, False)
        found = any(vol_name in line
                    for line in result.stdout.strip().splitlines())
        if expect_present and not found:
            test.fail("Volume '%s' not found in pool '%s' vol-list"
                      % (vol_name, pool_name))
        if not expect_present and found:
            test.fail("Volume '%s' still listed in pool '%s' after delete"
                      % (vol_name, pool_name))
        logging.debug("vol-list check: vol='%s' present=%s (expected=%s)",
                      vol_name, found, expect_present)

    def check_domblklist(expect_target, expect_source, expect_present=True):
        """
        Verify target/source mapping in virsh domblklist output.

        :param expect_target:  guest device target  e.g. "vdh"
        :param expect_source:  host image path      e.g. /var/.../vol1.qcow2
        :param expect_present: True  -> fail if mapping not found
                               False -> fail if target is still listed
        """
        result = virsh.domblklist(vm_name, debug=True)
        if result.exit_status:
            test.fail("virsh domblklist failed for '%s': %s"
                      % (vm_name, result.stderr.strip()))
        output = result.stdout_text.strip()
        logging.debug("domblklist output for %s:\n%s", vm_name, output)
        found = False
        for line in output.splitlines():
            cols = line.split()
            if len(cols) < 2:
                continue
            if cols[0] == expect_target and cols[1] == expect_source:
                found = True
                break
        if expect_present and not found:
            test.fail("Disk target='%s' source='%s' not found in domblklist"
                      % (expect_target, expect_source))
        if not expect_present and any(
                line.split()[0] == expect_target
                for line in output.splitlines() if line.split()):
            test.fail("Disk target='%s' still present in domblklist after detach"
                      % expect_target)
        logging.debug("domblklist check: target='%s' present=%s (expected=%s)",
                      expect_target, found, expect_present)

    def build_disk_xml(source_path, driver_type, target_dev, bus, slot):
        """
        Write a <disk> XML element to a temp file and return its path.

        :param source_path: absolute path to the image file
        :param driver_type: 'raw' or 'qcow2'
        :param target_dev:  guest device name  e.g. 'vdb'
        :param bus:         bus type           e.g. 'virtio'
        :param slot:        PCI slot hex       e.g. '0x09'
        :returns: path to the temp XML file (caller is responsible for removal)
        """
        xml_content = (
            "<disk type='file' device='disk'>\n"
            "  <driver name='qemu' type='{driver}'/>\n"
            "  <source file='{src}'/>\n"
            "  <target dev='{dev}' bus='{bus}'/>\n"
            "  <address type='pci' domain='0x0000' bus='0x00'"
            " slot='{slot}' function='0x0'/>\n"
            "</disk>\n"
        ).format(driver=driver_type, src=source_path,
                 dev=target_dev, bus=bus, slot=slot)
        fd, xml_path = tempfile.mkstemp(suffix='.xml', prefix='disk_')
        try:
            os.write(fd, xml_content.encode())
        finally:
            os.close(fd)
        logging.debug("Disk XML written to %s:\n%s", xml_path, xml_content)
        return xml_path

    multipathd = service.Factory.create_service("multipathd")
    multipathd_status = multipathd.status()
    if multipathd_status:
        multipathd.stop()

    _existing = virsh.pool_info(pool_name, ignore_status=True)
    if _existing.exit_status == 0:
        logging.warning("Pre-flight: pool '%s' already exists — tearing "
                        "down before test start.", pool_name)
        virsh.pool_destroy(pool_name, ignore_status=True)
        virsh.pool_undefine(pool_name, ignore_status=True)

    # State flags used by the finally block to decide what needs cleanup.
    pool_defined = False
    pool_started = False
    vol_created = False
    fs_source_dev = None

    try:
        if not os.path.isdir(pool_target):
            logging.info("Creating pool target directory: %s", pool_target)
            os.makedirs(pool_target)
        pool_define_extra = ""
        if pool_type == "fs":
            root_src = process.run(
                "findmnt -n -o SOURCE /",
                shell=True, ignore_status=False).stdout_text.strip()
            root_dev_name = os.path.basename(root_src)
            root_pkname = process.run(
                "lsblk -ndo PKNAME %s" % root_src,
                shell=True, ignore_status=True).stdout_text.strip()
            root_disk_name = root_pkname or root_dev_name
            lsblk_cmd = "lsblk -dn -o NAME,TYPE,MOUNTPOINT,PKNAME"
            lsblk_result = process.run(lsblk_cmd, shell=True, ignore_status=False)
            raw_disk = None
            for line in lsblk_result.stdout_text.strip().splitlines():
                cols = line.split(None, 3)
                if len(cols) < 2:
                    continue
                dev_name = cols[0]
                dev_type = cols[1]
                mountpoint = cols[2] if len(cols) > 2 else ""
                parent_name = cols[3] if len(cols) > 3 else ""
                if dev_type == "part":
                    if mountpoint:
                        continue
                    if parent_name == root_disk_name:
                        continue
                    fs_source_dev = "/dev/%s" % dev_name
                    break
                if dev_type == "disk" and not mountpoint and dev_name != root_disk_name:
                    raw_disk = "/dev/%s" % dev_name
            if not fs_source_dev and raw_disk:
                fs_source_dev = raw_disk
            if not fs_source_dev:
                test.cancel("No safe unused partition or raw disk found for fs pool")
            logging.info("Using fs pool source device: %s", fs_source_dev)
            utlv.mkfs(fs_source_dev, pool_source_format)
            pool_define_extra = "--source-dev %s --source-format %s" % (
                fs_source_dev, pool_source_format)
        logging.info("Defining '%s' pool '%s' at '%s'",
                     pool_type, pool_name, pool_target)
        result = virsh.pool_define_as(
            pool_name, pool_type, pool_target,
            extra=pool_define_extra,
            ignore_status=True, debug=True)
        utlv.check_exit_status(result, status_error)
        if result.exit_status:
            return          # expected error path
        pool_defined = True
        _info = virsh.pool_info(pool_name, ignore_status=True)
        if _info.exit_status != 0:
            test.fail("pool-define-as reported success but pool '%s' is not "
                      "visible to libvirt (pool-info failed: %s)"
                      % (pool_name, _info.stderr.strip()))
        logging.debug("pool-info confirmed pool '%s' is defined:\n%s",
                      pool_name, _info.stdout.strip())
        logging.info("Starting pool '%s'", pool_name)
        result = virsh.pool_start(pool_name, ignore_status=True, debug=True)
        if result.exit_status:
            test.fail("virsh pool-start '%s' failed: %s"
                      % (pool_name, result.stderr.strip()))
        pool_started = True
        logging.debug("Refreshing pool '%s' after start", pool_name)
        virsh.pool_refresh(pool_name, ignore_status=True, debug=True)
        logging.info("Enabling autostart for pool '%s'", pool_name)
        result = virsh.pool_autostart(pool_name, ignore_status=True, debug=True)
        utlv.check_exit_status(result)
        check_pool_list(expect_present=True)
        logging.info("Creating %s volume '%s' (%s) in pool '%s'",
                     vol_format, vol_name, vol_capacity, pool_name)
        result = virsh.vol_create_as(
            vol_name, pool_name,
            vol_capacity, "0", vol_format,
            ignore_status=True, debug=True)
        utlv.check_exit_status(result, status_error)
        if result.exit_status:
            return          # expected error path
        vol_created = True
        check_pool_list(expect_present=True)
        check_vol_list(expect_present=True)

        # Log the host path libvirt resolved for the new volume
        vol_path_res = virsh.vol_path(
            vol_name, pool_name, ignore_status=True, debug=True)
        if not vol_path_res.exit_status:
            logging.info("Volume host path: %s", vol_path_res.stdout.strip())

        if test_attach or test_attach_device:
            vm = env.get_vm(vm_name)
            if vm is None:
                # VM not yet registered in the env (e.g. vms param was empty
                # during preprocess).  Create the object and register it so
                # subsequent env.get_vm() calls also work.
                vm_type = params.get("vm_type", "libvirt")
                target = params.get("target")
                vm = env.create_vm(vm_type, target, vm_name, params, test.bindir)
            if vm is None:
                test.error("Cannot create VM object for '%s'; check that the "
                           "libvirt domain exists on the host." % vm_name)
            if vm.is_dead():
                logging.info("Starting VM '%s' before attach step", vm_name)
                vm.start()
                vm.wait_for_login().close()

        if test_attach:
            logging.info("Attaching '%s' to guest '%s' as target '%s' "
                         "with options '%s'",
                         vol_path, vm_name, disk_target, at_options)
            result = virsh.attach_disk(
                vm_name, vol_path, disk_target,
                extra=at_options,
                ignore_status=True, debug=True)
            utlv.check_exit_status(result, status_error)
            if result.exit_status:
                return      # expected error path

            check_domblklist(disk_target, vol_path, expect_present=True)
            logging.info("Detaching target '%s' from guest '%s' "
                         "with options '%s'",
                         disk_target, vm_name, dt_options)
            result = virsh.detach_disk(
                vm_name, disk_target,
                extra=dt_options,
                ignore_status=True, debug=True)
            utlv.check_exit_status(result)
            check_domblklist(disk_target, vol_path, expect_present=False)
        if test_attach_device:
            xml_file = None
            try:
                xml_file = build_disk_xml(
                    vol_path, vol_format, disk_target, disk_bus, disk_slot)
                logging.info("attach-device: xml='%s' -> guest '%s' "
                             "(source='%s', options='%s')",
                             xml_file, vm_name, vol_path, ad_options)
                result = virsh.attach_device(
                    domainarg=vm_name,
                    filearg=xml_file,
                    flagstr=ad_options,
                    ignore_status=True, debug=True)
                utlv.check_exit_status(result, status_error)
                if result.exit_status:
                    return      # expected error path

                check_domblklist(disk_target, vol_path, expect_present=True)
                _dd_flags = dd_options
                logging.info("detach-device: xml='%s' from guest '%s' "
                             "(target='%s', options='%s')",
                             xml_file, vm_name, disk_target, _dd_flags)
                result = virsh.detach_device(
                    domainarg=vm_name,
                    filearg=xml_file,
                    flagstr=_dd_flags,
                    ignore_status=True, debug=True)
                utlv.check_exit_status(result)
                check_domblklist(disk_target, vol_path, expect_present=False)

            finally:
                if xml_file and os.path.exists(xml_file):
                    os.remove(xml_file)
                    logging.debug("Removed temp XML file: %s", xml_file)

        logging.info("TEST PASSED -- pool='%s' format=%s vol='%s' "
                     "attach_tested=%s attach_device_tested=%s",
                     pool_name, vol_format, vol_name,
                     test_attach, test_attach_device)

    finally:
        logging.debug("=== Cleanup start for pool '%s' ===", pool_name)

        if test_attach and vm_name:
            _vm = env.get_vm(vm_name)
            if _vm is None:
                vm_type = params.get("vm_type", "libvirt")
                _vm = env.create_vm(vm_type, params.get("target"),
                                    vm_name, params, test.bindir)
            if _vm is not None and not _vm.is_dead():
                bl = virsh.domblklist(vm_name, ignore_status=True)
                if bl.exit_status == 0:
                    for _line in bl.stdout_text.strip().splitlines():
                        _cols = _line.split()
                        if _cols and _cols[0] == disk_target:
                            logging.debug("Cleanup: detaching live target "
                                          "'%s' from '%s'",
                                          disk_target, vm_name)
                            virsh.detach_disk(vm_name, disk_target,
                                              extra=dt_options,
                                              ignore_status=True, debug=True)
                            break

        if vol_created:
            logging.debug("Deleting volume '%s' from pool '%s'",
                          vol_name, pool_name)
            res = virsh.vol_delete(vol_name, pool_name,
                                   ignore_status=True, debug=True)
            if res.exit_status:
                logging.warning("vol-delete failed (may already be removed): %s",
                                res.stderr.strip())
            else:
                check_vol_list(expect_present=False)

        # Destroy the pool 
        if pool_started:
            logging.debug("Destroying pool '%s'", pool_name)
            if not virsh.pool_destroy(pool_name):
                logging.warning("pool-destroy returned non-zero for '%s'",
                                pool_name)

        if pool_defined:
            logging.debug("Undefining pool '%s'", pool_name)
            res = virsh.pool_undefine(pool_name, ignore_status=True, debug=True)
            if res.exit_status:
                logging.warning("pool-undefine failed for '%s': %s",
                                pool_name, res.stderr.strip())
            else:
                check_pool_list(expect_present=False)

        if os.path.isdir(pool_target):
            logging.debug("Removing pool target directory: %s", pool_target)
            process.run("rm -rf %s" % pool_target,
                        shell=True, ignore_status=True)

        if pool_type == "fs" and fs_source_dev:
            logging.debug("Cleaning filesystem signature on source device: %s",
                          fs_source_dev)
            process.run("wipefs -a %s" % fs_source_dev,
                        shell=True, ignore_status=True)
        if multipathd_status:
            multipathd.start()

        logging.debug("Cleanup complete for pool '%s'", pool_name)
