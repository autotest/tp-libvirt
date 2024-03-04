# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from avocado.utils import memory

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base


def adjust_virtio_params(params):
    """
    Adjust all virtio related size and xpath.

    :param params: dict wrapped with params.
    """
    plug_target_size = int(params.get('plug_target_size', 0))
    plug_request_size = int(params.get('plug_request_size', 0))
    target_size, request_size = int(params.get('target_size')), int(params.get('request_size'))
    plug_size_unit = params.get('plug_size_unit')
    plug_request_unit = params.get('plug_request_unit')
    size_unit, request_unit = params.get('size_unit'), params.get('request_unit')

    def _convert_size(curr_size, curr_unit, var_str):
        if curr_unit != "KiB":
            new_size = memory_base.convert_data_size(str(curr_size) + curr_unit)
            params.update({var_str: int(new_size)})

    _convert_size(target_size, size_unit, 'target_size')
    _convert_size(request_size, request_unit, 'request_size')
    _convert_size(plug_target_size, plug_size_unit, 'plug_target_size')
    _convert_size(plug_request_size, plug_request_unit, 'plug_request_size')


def adjust_virtio_xpath(params):
    """
    Adjust all expected virtio xpath in different scenarios.

    :param params: dict wrapped with params.
    :return virtio_xpath, unplug_xpath, plug_xpath, expected defined virtio xpath,
    expected plugged virtio xpath, expected unplugged virtio xpath.
    """
    default_hugepage_size = memory.get_huge_page_size()
    adjust_virtio_params(params)

    plug_target_size = params.get('plug_target_size')
    plug_request_size = params.get('plug_request_size')
    target_size, request_size = params.get('target_size'), params.get('request_size')
    case = params.get("case")
    virtio_xpath = params.get("virtio_xpath")
    unplug_xpath = params.get("unplug_xpath")
    plug_xpath = params.get("plug_xpath")

    if case == "source_and_mib":
        virtio_xpath = virtio_xpath % (target_size, default_hugepage_size,
                                       request_size)
        unplug_xpath = eval(virtio_xpath)
        plug_xpath = eval(plug_xpath % (plug_target_size, default_hugepage_size,
                                        plug_request_size))
    elif case == "plug_exceeded_max_mem":
        virtio_xpath = virtio_xpath % default_hugepage_size
        plug_xpath = eval(plug_xpath % (plug_target_size, default_hugepage_size,
                                        plug_request_size))
    else:
        virtio_xpath = virtio_xpath % default_hugepage_size
        if unplug_xpath:
            unplug_xpath = eval(unplug_xpath % default_hugepage_size)
        if plug_xpath:
            plug_xpath = eval(plug_xpath % default_hugepage_size)
    return eval(virtio_xpath), unplug_xpath, plug_xpath


def check_xpath_unexist(test, vmxml, not_exist_path):
    """
    Check specific xpath info not exist in guest xml.

    :param test: test object.
    :param vmxml: current guest xml.
    :param not_exist_path: The xpath that should not exist in guest.
    """
    if libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml, not_exist_path, ignore_status=True):
        test.fail("Expect to not get %s in guest" % not_exist_path)


def run(test, params, env):
    """
    Verify virtio-mem memory device cold-plug and cold-unplug
    with different configs
    """
    def setup_test():
        """
        Check Versions.
        """
        test.log.info("TEST_SETUP: Check related version")
        memory_base.check_supported_version(params, test, vm)

    def run_test():
        """
        Define guest, then cold unplug and cold plug virtio mem device.
        """
        test.log.info("TEST_SETUP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.devices = vmxml.devices.append(
            libvirt_vmxml.create_vm_device_by_type('memory', virtio_dict))
        virsh.define(vmxml.xml, debug=True, ignore_status=False)

        test.log.info("TEST_STEP2: Check guest xml")
        virtio_xpath, unplug_xpath, plug_xpath = adjust_virtio_xpath(params)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, numa_xpath+virtio_xpath)

        if unplug_dict:
            test.log.info("TEST_SETUP3,4:Cold unplug a virtio-mem and check xml")
            cold_unplug_obj = libvirt_vmxml.create_vm_device_by_type(
                'memory', unplug_dict)
            ret = virsh.detach_device(vm.name, cold_unplug_obj.xml,
                                      flagstr=plug_option, debug=True)

            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if unplug_error:
                libvirt.check_result(ret, unplug_error)
                libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, numa_xpath+virtio_xpath)
            else:
                libvirt.check_exit_status(ret)
                check_xpath_unexist(test, vmxml, unplug_xpath)

        if plug_dict:
            test.log.info("TEST_SETUP5-7: Cold plug virtio-mem and check xml")
            cold_plug_obj = libvirt_vmxml.create_vm_device_by_type(
                'memory', plug_dict)
            ret = virsh.attach_device(vm.name, cold_plug_obj.xml,
                                      flagstr=plug_option, debug=True)
            if case in ["with_addr", "source_and_mib", "duplicate_addr"]:
                vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                if plug_error:
                    libvirt.check_result(ret, plug_error, any_error=True)
                    libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, numa_xpath+virtio_xpath)
                    if case != "duplicate_addr":
                        check_xpath_unexist(test, vmxml, plug_xpath)
                else:
                    libvirt.check_exit_status(ret)
                    libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, numa_xpath+plug_xpath)
            elif case in ["plug_exceeded_max_mem"]:
                for plug_time in range(0, 3):
                    ret = virsh.attach_device(vm.name, cold_plug_obj.xml,
                                              flagstr=plug_option, debug=True)
                    if plug_time == 2:
                        libvirt.check_result(ret, plug_error)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    case = params.get("case")
    default_hugepage_size = memory.get_huge_page_size()

    plug_option = params.get("plug_option")
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    numa_xpath = eval(params.get("numa_xpath", ""))
    unplug_error = params.get("unplug_error")
    plug_error = params.get("plug_error")
    virtio_dict = eval(params.get('virtio_dict', '{}') % default_hugepage_size)
    unplug_dict = params.get('unplug_dict')
    plug_dict = params.get('plug_dict')

    unplug_dict = eval(unplug_dict % default_hugepage_size) if unplug_dict else ''
    plug_dict = eval(plug_dict % default_hugepage_size) if plug_dict else ''

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
