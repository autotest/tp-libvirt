import os
import re

from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def get_size_birth_from_nvram(vm_name, test):
    """
    Get the size and birth values from nvram file

    :param vm_name: vm name
    :return: (size, birth)
    """
    cmd = 'stat /var/lib/libvirt/qemu/nvram/%s_VARS.fd' % vm_name
    ret = process.run(cmd, ignore_status=False, shell=True)
    size = re.search(r"Size:\s*(\d*)", ret.stdout_text).group(1)
    birth = re.search(r"Birth:\s*(.*)", ret.stdout_text).group(1)
    test.log.debug("Return current nvram file with "
                   "size({}) and birth({})".format(size, birth))
    return size, birth


def setup_reset_nvram(guest_xml, params, virsh_func, test, *args):
    """
    Setup for the tests, including
    1. Configure os firmware attribute
    2. Create nvram file and make its size invalid

    :param guest_xml: the guest xml
    :param params: dict for parameters of the tests
    :param virsh_func: virsh function will be invoked
    :param args:  tuple, virsh function uses
    """
    test.log.info("Config guest xml with firmware efi")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    os_attrs = eval(params.get('os_attrs'))
    if os_attrs:
        osxml = vm_xml.VMOSXML()
        osxml.setup_attrs(**os_attrs)
        guest_xml.os = osxml
        guest_xml.sync()
    test.log.debug("After configuration, vm xml:\n%s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))
    test.log.info("Start vm to create %s_VARS.fd file" % vm_name)
    virsh.start(vm_name)
    test.log.info("Modify the nvram file to make it invalid")
    cmd = 'echo > /var/lib/libvirt/qemu/nvram/%s_VARS.fd' % vm_name
    process.run(cmd, ignore_status=False, shell=True)
    test.log.debug("Prepare the required vm state")
    if len(args) > 1:
        virsh_func(args[0], args[1], ignore_status=False)
    else:
        virsh_func(args[0], ignore_status=False)


def common_test_steps(virsh_func, func_args, params, test):
    """
    The common test steps shared by test cases

    :param virsh_func: virsh function to be invoked
    :param func_args: str, parameter value for virsh function
    :param params: dict, test parameters
    :param test:  test object
    :raises: test.fail if size or birth was not changed
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    test.log.debug("Step 1: Get the invalid nvram file size and birth ")
    old_size, old_birth = get_size_birth_from_nvram(vm_name, test)
    test.log.debug("Step 2: Operate on the vm and check expected result")
    ret = virsh_func(func_args)
    err_msg = params.get('err_msg')
    libvirt.check_result(ret, expected_fails=err_msg)
    test.log.debug("Step 3: Operate on the vm again but with --reset-nvram option")
    ret = virsh_func(func_args, options=params.get('option'))
    libvirt.check_exit_status(ret)
    test.log.debug("Step 4: Verify the valid nvram file was recreated")
    new_size, new_birth = get_size_birth_from_nvram(vm_name, test)
    if (new_size == old_size or new_birth == old_birth):
        test.fail("New nvram file with size '{}' birth '{}' "
                  "should not be equal to old ones".format(new_size,
                                                           new_birth))


def test_start_destroyed_vm(guest_xml, params, test):
    """
    Test scenario:
     - Destroyed the vm
     - Start the vm and failure is expected with invalid nvram file size
     - Start the vm successfully with --reset-nvram
     - Check the nvram file is recreated as expected

    :param guest_xml: guest xml
    :param params: dict, test parameters
    :param test: test object
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    setup_reset_nvram(guest_xml, params, virsh.destroy, test, vm_name)
    common_test_steps(virsh.start, vm_name, params, test)


def test_start_managedsaved_vm(guest_xml, params, test):
    """
    Test scenario:
     - Managedsave the vm
     - Start the vm and failure is expected with invalid nvram file size
     - Start the vm successfully with --reset-nvram
     - Check the nvram file is recreated as expected

    :param guest_xml: guest xml
    :param params: dict, test parameters
    :param test: test object
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    setup_reset_nvram(guest_xml, params, virsh.managedsave, test, vm_name)
    common_test_steps(virsh.start, vm_name, params, test)


def test_restore_saved_vm(guest_xml, params, test):
    """
    Test scenario:
     - Save the vm
     - Restore the vm and failure is expected with invalid nvram file size
     - Restore the vm successfully with --reset-nvram
     - Check the nvram file is recreated as expected

    :param guest_xml: guest xml
    :param params: dict, test parameters
    :param test: test object
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    save_file = os.path.join(data_dir.get_data_dir(), params.get('output_file'))
    setup_reset_nvram(guest_xml, params, virsh.save, test, vm_name, save_file)
    common_test_steps(virsh.restore, save_file, params, test)


def test_create_destroyed_vm(guest_xml, params, test):
    """
    Test scenario:
     - Destroyed the vm
     - Create the vm and failure is expected with invalid nvram file size
     - Create the vm successfully with --reset-nvram
     - Check the nvram file is recreated as expected

    :param guest_xml: guest xml
    :param params: dict, test parameters
    :param test: test object
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    setup_reset_nvram(guest_xml, params, virsh.destroy, test, vm_name)
    vm_file = os.path.join(data_dir.get_data_dir(), params.get('output_file'))
    virsh.dumpxml(vm_name, to_file=vm_file)
    common_test_steps(virsh.create, vm_file, params, test)


def teardown_reset_nvram(params):
    """
    Clean up test environment

    :param params: dict, test parameters
    """
    output_file = params.get('output_file')
    if output_file:
        output_file = os.path.join(data_dir.get_data_dir(), output_file)
        if os.path.exists(output_file):
            os.remove(output_file)


def run(test, params, env):
    """
    Test cases for --reset-nvram option
    """

    libvirt_version.is_libvirt_feature_supported(params)
    case = params.get('test_case', '')
    vm_name = params.get('main_vm', '')
    guest_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_guest_xml = guest_xml.copy()
    run_test = eval('test_%s' % case)

    try:
        run_test(guest_xml, params, test)
    finally:
        bk_guest_xml.sync(options='--nvram')
        teardown_reset_nvram(params)
