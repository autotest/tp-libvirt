import logging

from virttest import virsh
from virttest import libvirt_version
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_cpu
from virttest.utils_libvirt import libvirt_numa
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test hmat of memory
    """
    vm_name = params.get('main_vm')

    qemu_checks = []
    for i in range(1, 5):
        qemu_checks.extend(params.get('qemu_checks%d' % i, '').split('`'))

    def check_numainfo_in_guest(check_list, content):
        """
        Check if numa information in guest is correct

        :param check_list: list of string under checking
        :param content: the whole output from the numactl cmd
        :raise: test.fail if numa info item is not in content
        """
        content_str = ' '.join(content.split())
        logging.debug("content_str:%s" % content_str)
        for item in check_list:
            item_str = ' '.join(item.split(' '))
            if content_str.find(item_str) != -1:
                logging.info(item)
            else:
                test.fail('Item %s not in content %s' % (item_str, content))

    def check_list_in_content(check_list, content):
        """
        Check if items in check_list are in content

        :param check_list: list of string under checking
        :param content: the whole content which may includes the strings
        :raise: test.fail if the item in check_list is not in content
        """
        for item in check_list:
            if item in content:
                logging.info("item: %s" % item)
            else:
                test.fail('Item %s not in content %s' % (item, content))

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    chk_case = params.get('chk')
    try:
        vm = env.get_vm(vm_name)
        if not libvirt_version.version_compare(6, 6, 0):
            test.cancel("Current version doesn't support the function")

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Set cpu according to params
        libvirt_cpu.add_cpu_settings(vmxml, params)
        if chk_case == "hmat":
            libvirt_numa.create_hmat_xml(vmxml, params)
        if chk_case == "cell_distances":
            libvirt_numa.create_cell_distances_xml(vmxml, params)
        logging.debug(virsh.dumpxml(vm_name))

        virsh.start(vm_name, debug=True, ignore_status=False)

        # Check qemu command line one by one
        for item in qemu_checks:
            libvirt.check_qemu_cmd_line(item)

        vm_session = vm.wait_for_login()
        if chk_case == "hmat":
            dmsg_list = []
            for i in range(1, 5):
                dmsg_list.extend(params.get('dmsg_checks%d' % i, '').split('`'))
            content = vm_session.cmd('dmesg').strip()
            check_list_in_content(dmsg_list, content)

        if chk_case == "cell_distances":
            # Install numactl in guest
            if not utils_package.package_install('numactl', vm_session):
                test.fail("package {} installation fail".format('numactl'))
            check_list = []
            for i in range(1, 4):
                check_list.extend(params.get('numactl_exp%d' % i, '').split('`'))
            numactl_output = vm_session.cmd('numactl -H').strip()
            check_numainfo_in_guest(check_list, numactl_output)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()
