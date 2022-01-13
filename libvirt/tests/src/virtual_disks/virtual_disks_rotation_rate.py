import logging as log
import time

from virttest import libvirt_version
from virttest import utils_disk
from virttest import utils_package
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_misc import cmd_status_output


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def install_pkg(test, pkg_list, vm_session):
    """
    Install required packages in the vm

    :param test: test object
    :param pkg_list: list, package names
    :param vm_session: vm session
    """
    if not utils_package.package_install(pkg_list, vm_session):
        test.error("Failed to install package '{}' in the vm".format(pkg_list))


def run_cmd_in_guest(test, vm_session, cmd, any_error=False):
    """
    Run a command within the guest.

    :param test:  test object
    :param vm_session: the vm session
    :param cmd: str, the command to be executed in the vm
    :param ignore_status: False to test.error when failed, otherwise True
    :param expect_fail: True if failure is expected, otherwise False
    """
    status, output = cmd_status_output(cmd, shell=True, session=vm_session)
    libvirt.check_status_output(status, output, any_error=any_error)


def create_second_disk(disk_dict):
    """
    Create another disk using given parameters

    :param disk_dict: dict, parameters to use
    :return: disk xml
    """
    disk_path = disk_dict.get('source_file')
    disk_format = disk_dict.get('driver_type', 'raw')
    libvirt.create_local_disk('file', disk_path, '1', disk_format)

    return libvirt.create_disk_xml(disk_dict)


def run(test, params, env):
    """
    Run tests with disk target configurations
    """

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    at_dt = params.get('at_dt')
    cmds_in_guest = eval(params.get('cmds_in_guest'))
    target_rotation = params.get('target_rotation')

    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        if not at_dt:
            libvirt.set_vm_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        logging.debug(vm_xml.VMXML.new_from_dumpxml(vm_name))
        vm_session = vm.wait_for_login()

        pkg_list = params.get("install_pkgs")
        if pkg_list:
            install_pkg(test, eval(pkg_list), vm_session)

        if at_dt:
            old_parts = utils_disk.get_parts_list(vm_session)
            disk_xml = create_second_disk(params)
            virsh.attach_device(vm_name, disk_xml, debug=True, ignore_status=False)
            pat_in_dumpxml = params.get('pattern_in_dumpxml')
            libvirt_vmxml.check_guest_xml(vm_name, pat_in_dumpxml, status_error=False)
            time.sleep(10)
            added_parts = utils_disk.get_added_parts(vm_session, old_parts)
            if not added_parts or len(added_parts) != 1:
                test.error("Only one new partition is expected in the VM, "
                           "but found {}".format(added_parts))
            cmd = cmds_in_guest[0] % added_parts[0]
            run_cmd_in_guest(test, vm_session, cmd)
            virsh.detach_device(vm_name, disk_xml, debug=True, ignore_status=False)
            cmd = cmds_in_guest[1] % added_parts[0]
            run_cmd_in_guest(test, vm_session, cmd, any_error=True)
            libvirt_vmxml.check_guest_xml(vm_name, pat_in_dumpxml, status_error=True)
        else:
            if cmds_in_guest:
                for cmd_index in range(0, len(cmds_in_guest)):
                    any_error = False
                    if not target_rotation and cmd_index == 0:
                        any_error = True
                    run_cmd_in_guest(test, vm_session, cmds_in_guest[cmd_index], any_error=any_error)
    finally:
        backup_xml.sync()
        source_file = params.get('source_file')
        if source_file:
            libvirt.delete_local_disk('file', source_file)
