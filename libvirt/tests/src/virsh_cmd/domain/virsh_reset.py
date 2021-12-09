import logging
import time

from avocado.utils import process

from virttest import utils_misc
from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test virsh reset command
    """

    if not virsh.has_help_command('reset'):
        test.cancel("This version of libvirt does not support "
                    "the reset test")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm_ref = params.get("reset_vm_ref")
    readonly = params.get("readonly", False)
    status_error = ("yes" == params.get("status_error", "no"))
    start_vm = ("yes" == params.get("start_vm"))

    vm = env.get_vm(vm_name)
    domid = vm.get_id()
    domuuid = vm.get_uuid()
    bef_pid = process.getoutput("pidof -s qemu-kvm")

    if vm_ref == 'id':
        vm_ref = domid
    elif vm_ref == 'uuid':
        vm_ref = domuuid
    else:
        vm_ref = vm_name

    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    # change the disk cache to default
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    def change_cache(vmxml, mode):
        """
        Change the cache mode

        :param vmxml: instance of VMXML
        :param mode: cache mode you want to change
        """
        devices = vmxml.devices
        disk_index = devices.index(devices.by_device_tag('disk')[0])
        disk = devices[disk_index]
        disk_driver = disk.driver
        disk_driver['cache'] = mode
        disk.driver = disk_driver
        vmxml.devices = devices
        vmxml.define()

    try:
        change_cache(vmxml_backup.copy(), "default")

        tmpfile = "/home/%s" % utils_misc.generate_random_string(6)
        logging.debug("tmpfile is %s", tmpfile)
        if start_vm:
            session = vm.wait_for_login()
            session.cmd("rm -rf %s && sync" % tmpfile)
            status = session.get_command_status("touch %s && ls %s" %
                                                (tmpfile, tmpfile))
            if status == 0:
                logging.info("Succeed generate file %s", tmpfile)
            else:
                test.fail("Touch command failed!")

        # record the pid before reset for compare
        output = virsh.reset(vm_ref, readonly=readonly,
                             unprivileged_user=unprivileged_user,
                             uri=uri, ignore_status=True, debug=True)
        if output.exit_status != 0:
            if status_error:
                logging.info("Failed to reset guest as expected, Error:%s.",
                             output.stderr)
                return
            else:
                test.fail("Failed to reset guest, Error:%s." %
                          output.stderr)
        elif status_error:
            test.fail("Expect fail, but succeed indeed.")

        session.close()
        time.sleep(5)
        session = vm.wait_for_login()
        status = session.get_command_status("ls %s" % tmpfile)
        if status == 0:
            test.fail("Fail to reset guest, tmpfile still exist!")
        else:
            aft_pid = process.getoutput("pidof -s qemu-kvm")
            if bef_pid == aft_pid:
                logging.info("Succeed to check reset, tmpfile is removed.")
            else:
                test.fail("Domain pid changed after reset!")
        session.close()

    finally:
        vmxml_backup.sync()
