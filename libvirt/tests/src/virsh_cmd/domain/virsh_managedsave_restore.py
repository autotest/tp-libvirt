import logging
import os

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import utils_libguestfs
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from avocado.utils import process

from provider.save import save_base

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test vm restore after managedsave with various conditions
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name

    def check_libvirtd_log():
        """
        Check the libvirtd log for expected messages
        """
        result = utils_misc.wait_for(
            lambda: libvirt.check_logfile(expected_log, libvirtd_log_file), timeout=20)
        if not result:
            test.fail("Can't get expected log %s in %s" % (expected_log, libvirtd_log_file))

    def start_shutdown_cycle():
        """
        Do a managedsave -> start -> shutdown cycle
        """
        LOG.info("Do a start shutdown cycle to clear the saved state:")
        vm.start()
        if os.path.exists(managed_save_file):
            test.fail("The saved file exists after restore: %s", managed_save_file)
        virsh.shutdown(vm_name, debug=True)
        if not vm.wait_for_shutdown():
            test.error('VM failed to shutdown in 60s')

    def setup_saved_file():
        """"
        Prepare the pre-conditions about saved file

        1. Prepare an invalid saved file if needed
        2. Update the saved file to use an occupied one if needed
        3. Remove the saved file if needed
        """
        if file_state == "corrupt":
            LOG.info("Corrupt the saved file by 'echo >' cmd:")
            process.run("echo > %s" % managed_save_file, shell=True, ignore_status=True)
        elif file_state == "occupied":
            LOG.info("Update the saved file to use an occupied image:")
            # Edit the current vm's saved state file to use another running VM's disk path
            LOG.info("Clone a VM with name as %s:", add_vm_name)
            utils_libguestfs.virt_clone_cmd(vm_name, add_vm_name,
                                            True, timeout=360)
            add_vm = vm.clone(add_vm_name)
            add_vm.start()
            # Get the add_vm disk path, and vm disk path, then replace the
            # vm disk path to be add_vm disk path
            add_vm_xml = vm_xml.VMXML.new_from_inactive_dumpxml(add_vm_name)
            add_disk_xml = add_vm_xml.get_devices(device_type="disk")[0]
            add_disk_path = add_disk_xml.source.attrs['file']

            disk_xml = vmxml.get_devices(device_type="disk")[0]
            vm_disk_path = disk_xml.source.attrs['file']
            xmlfile = os.path.join(data_dir.get_tmp_dir(), 'managedsave.xml')
            virsh.managedsave_dumpxml(vm_name, to_file=xmlfile, ignore_status=False, debug=False)

            LOG.info("Update the disk path from %s to %s:", vm_disk_path, add_disk_path)
            cmd = "sed -i 's|%s|%s|' %s" % (vm_disk_path, add_disk_path, xmlfile)
            process.run(cmd, shell=True)
            virsh.managedsave_define(vm_name, xmlfile, debug=True)
            ret = virsh.managedsave_dumpxml(vm_name, '--xpath //disk', debug=True).stdout_text
            LOG.info("After update, the disk in mangedsave state file is: %s", ret)
        elif file_state == "nonexist":
            LOG.info("Delete the saved file:")
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)

    file_state = params.get("file_state")
    vm_managedsaved = "yes" == params.get("vm_managedsaved")
    need_vm2 = "yes" == params.get("need_vm2", "no")
    expected_log = params.get("expected_log", None)
    expected_cmd_err = params.get("expected_cmd_err", None)
    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()
    appendix = utils_misc.generate_random_string(4)
    add_vm_name = ("%s" % vm_name[:-1]) + ("%s" % appendix)

    try:
        LOG.info("TEST_STEP1: Setup before restore the vm:")
        if not vm.is_alive():
            vm.start()
        pre_pid_ping, pre_upsince = save_base.pre_save_setup(vm)
        virsh.managedsave(vm_name, debug=True)
        if not vm_managedsaved:
            start_shutdown_cycle()
        else:
            setup_saved_file()

        LOG.info("TEST_STEP2: Restore the vm by 'virsh start':")
        ret = virsh.start(vm_name, debug=True)
        libvirt.check_result(ret, expected_fails=expected_cmd_err)
        virsh.destroy(add_vm_name, debug=True)
        # if not expect error, vm should freshly start
        if not expected_cmd_err:
            LOG.info("TEST_STEP3: Check the vm should be freshly start:")
            post_pid_ping, post_upsince = save_base.pre_save_setup(vm)
            if pre_upsince == post_upsince:
                test.fail("VM is not freshly start!")
            else:
                LOG.info("The uptime changed: %s vs %s", pre_upsince, post_upsince)
        if file_state == 'corrupt':
            LOG.info("TEST_STEP4: Check libvirtd log:")
            check_libvirtd_log()
    finally:
        virsh.destroy(vm_name, debug=True)
        virsh.undefine(vm_name, "--managed-save --keep-nvram", debug=True)
        if os.path.exists(managed_save_file):
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)
        bk_xml.sync()
        if need_vm2:
            LOG.info("Remove the additional VM: %s", add_vm_name)
            virsh.remove_domain(add_vm_name, "--remove-all-storage --nvram")
