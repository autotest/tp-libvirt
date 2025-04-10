#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

"""
Test cases about CPU metrics commands
"""
import os
import pwd
import re
import shutil
import stat
import platform

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError
from virttest.utils_libvirt import libvirt_bios
from virttest.utils_libvirt import libvirt_vmxml


def manage_unprivileged_user(unprivileged_user, test, is_create=True):
    """
    Create or delete the specified user account.

    :param unprivileged_user: str, the user's name
    :param test: test object
    :param is_create: bool, True to create the account,
                            False to delete the account
    """
    process_opt = {'verbose': True, 'ignore_status': False, 'shell': True}
    user_exists = is_user_exists(unprivileged_user, test)
    if is_create:
        if not user_exists:
            process.run('useradd %s' % unprivileged_user, **process_opt)
        else:
            test.log.info("User '%s' already exists, so no user will be "
                          "created", unprivileged_user)
    else:
        if user_exists:
            test.log.debug("Begin to delete user '%s'", unprivileged_user)
            process.run('userdel -fr %s' % unprivileged_user, **process_opt)
        else:
            test.log.warning("User '%s' does not exist, so the account can "
                             "not be deleted", unprivileged_user)


def is_user_exists(unprivileged_user, test):
    """
    Check if the specified user exists in OS

    :param unprivileged_user: str, the user name
    :param test: test object

    :return: bool, True if the user already exists, otherwise False
    """
    try:
        pwd.getpwnam(unprivileged_user)
    except KeyError:
        test.log.info("User '%s' does not exist.", unprivileged_user)
        return False
    return True


def setup_default(vm, params, test):
    """
    Default clean up function for tests

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    """
    pass


def cleanup_default(vm, params, test):
    """
    Default clean up function for tests

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    """
    try:
        if vm.is_alive():
            vm.destroy(gracefully=False)
    except Exception as details:
        test.log.error("Got error when cleanup the test: %s", details)
    backup_vmxml = params.get("backup_vmxml")
    if backup_vmxml:
        backup_vmxml.sync()


def setup_with_unprivileged_user(vm, params, test):
    """
    Setup function for the test includes:
    1. Create a unprivileged user if it does not exist
    2. Update the vm xml to use interface with 'user' type
       which is only supported for unprivileged user
    3. Update the vm xml to use boot disk image in user's home directory
       where the unprivileged user has access to
    4. Prepare boot disk image file in user's home directory
    5. Define and start the vm using unprivileged user

    :param vm: the vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    unprivileged_user = params.get('unprivileged_user')
    unprivileged_boot_disk_path = params.get('unprivileged_boot_disk_path')
    unprivileged_user_dumpxml_path = params.get('unprivileged_user_dumpxml_path')

    test.log.debug("Step: Create the unprivileged user account")
    manage_unprivileged_user(unprivileged_user, test)

    test.log.debug("Step: Update vm xml with new boot image path and user type interface")
    virsh_opt = {'debug': True, 'ignore_status': False, 'unprivileged_user': unprivileged_user}
    interface_attrs = eval(params.get('interface_attrs', '{}'))
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    params['backup_vmxml'] = vmxml.copy()
    test.log.debug("Remove 'dac' security driver for unprivileged user")
    vmxml.del_seclabel(by_attr=[('model', 'dac')])
    libvirt_vmxml.modify_vm_device(vmxml, 'interface', interface_attrs)
    vmxml.xmltreefile.remove_by_xpath("/devices/interface/driver", True)
    boot_disk = vmxml.devices.by_device_tag('disk')[0]
    first_disk_source = boot_disk.fetch_attrs()['source']['attrs']['file']
    unprivileged_boot_disk_path = os.path.join(unprivileged_boot_disk_path, os.path.basename(first_disk_source))
    disk_attrs = {'source': {'attrs': {'file': unprivileged_boot_disk_path}}}
    libvirt_vmxml.modify_vm_device(vmxml, 'disk', disk_attrs)
    try:
        os_attrs = {
                "loader": vmxml.os.loader,
                "loader_readonly": vmxml.os.loader_readonly,
                "loader_type": vmxml.os.loader_type
                }
    except LibvirtXMLNotFoundError:
        pass
    vmxml.os = libvirt_bios.remove_bootconfig_items_from_vmos(vmxml.os)
    if vmxml.xmltreefile.find('features'):
        arch = platform.machine().lower()
        if vmxml.features.has_feature('acpi') and 'aarch64' in arch:
            vmxml.set_os_attrs(**os_attrs)
    test.log.debug('VM XML after updating:\n%s', vmxml)

    test.log.debug("Step: Prepare boot disk image in unprivileged user's home directory")
    shutil.copy(first_disk_source, unprivileged_boot_disk_path)
    shutil.chown(unprivileged_boot_disk_path, unprivileged_user)

    test.log.debug("Step: Define and start vm using unprivileged user")
    shutil.copy(vmxml.xml, unprivileged_user_dumpxml_path)
    os.chmod(unprivileged_user_dumpxml_path, stat.S_IRWXO)
    virsh.define(unprivileged_user_dumpxml_path, **virsh_opt)
    virsh.start(vm.name, **virsh_opt)


def run_with_unprivileged_user(vm, params, test):
    """
    Verify for unprivileged user test that cpu_stats and domstats commands
    should return valid values for unprivileged user

    :param vm: the vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    unprivileged_user = params.get("unprivileged_user")
    domstats_option = params.get("domstats_option")
    cpu_stats_option = params.get("cpu_stats_option")
    virsh_opt = {'debug': True, 'ignore_status': False, 'unprivileged_user': unprivileged_user}
    domstats_output = virsh.domstats(vm.name,
                                     domstats_option,
                                     **virsh_opt).stdout_text.strip()

    pattern = r'cpu\.time=(\d+)\n\s+cpu\.user=(\d+)\n\s+cpu\.system=(\d+)'
    match = re.findall(pattern, domstats_output)
    if not match:
        test.fail("Expect cpu.time, cpu.user and cpu.system "
                  "from domstats to have values, but found %s" % domstats_output)
    else:
        test.log.debug("Step: Get cpu.time, cpu.user and cpu.system from domstats: %s" % match)

    cpu_stats_output = virsh.cpu_stats(vm.name, cpu_stats_option, **virsh_opt).stdout_text.strip()
    pattern = r'cpu_time\s+(.*)\s+seconds\n\s+user_time\s+(.*)\s+seconds\n\s+system_time\s+(.*)\s+seconds'
    match = re.findall(pattern, cpu_stats_output)
    if not match:
        test.fail("Expect cpu_time, user_time and system_time from cpu-stats"
                  "have values, but found %s" % cpu_stats_output)
    else:
        test.log.debug("Step: Get cpu_time, user_time and "
                       "system_time from cpu-stats: %s" % match)


def cleanup_with_unprivileged_user(vm, params, test):
    """
    Clean up function for unprivileged user test

    :param vm: the vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    unprivileged_user = params.get("unprivileged_user")
    virsh_opt = {'debug': True, 'ignore_status': False, 'unprivileged_user': unprivileged_user}
    unprivileged_user_dumpxml_path = params.get('unprivileged_user_dumpxml_path')
    try:
        if os.path.exists(unprivileged_user_dumpxml_path):
            test.log.debug("Step: Remove dumpxml file used by unprivileged user")
            os.remove(unprivileged_user_dumpxml_path)
        if virsh.domain_exists(vm.name, **virsh_opt):
            test.log.debug("Step: Destory and undefine the vm created by unprivileged user")
            if virsh.is_alive(vm.name, **virsh_opt):
                virsh.destroy(vm.name, **virsh_opt)
            virsh.undefine(vm.name, options='--nvram', **virsh_opt)
        test.log.debug("Step: Delete the unprivileged user account")
        manage_unprivileged_user(unprivileged_user, test, is_create=False)
    finally:
        cleanup_default(vm, params, test)


def run(test, params, env):
    """
    Test scenarios using vcpu metrics functions, like: domstats, cpu-stats
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    test_case = params.get('test_case', '')
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        globals() else setup_default
    run_test = eval("run_%s" % test_case)
    cleanup_test = eval("cleanup_%s" % test_case) if "cleanup_%s" % test_case in \
        globals() else cleanup_default
    try:
        setup_test(vm, params, test)
        run_test(vm, params, test)
    finally:
        cleanup_test(vm, params, test)
