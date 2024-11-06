import logging as log
import re

from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base as guest_os

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def get_mem_reserve_size_in_vm(test, contr_addr, params, session):
    """
    Get mem reserve size of specified controller within vm

    :param test: test object
    :param contr_addr: str, the controller address
    :param params: dict, test parameters
    :param session: vm session
    :return: int, the controller's memory reserve size
    """
    contr_pattern = params.get("contr_pattern")
    # According to test results, pcie root port's address in vm xml
    # is consistent with the one within the vm, but pci-bridge doesn't.
    # For example,
    # Pci-bridge address in vm xml:
    # <address type="pci" domain="0x0000" bus="0x0f" slot="0x01" function="0x0" />
    # But
    # lspci shows in the vm:
    # 08:01.0 PCI bridge: Red Hat, Inc. QEMU PCI-PCI bridge
    # So codes do not match the address for pci-bridge so far
    if contr_pattern.count("%s"):
        contr_pattern = params.get("contr_pattern") % contr_addr

    lspci_output = session.cmd_output("lspci").strip()
    test.log.debug("The lspci in vm:\n%s", lspci_output)

    found_contr = re.findall(contr_pattern, lspci_output)
    if not found_contr:
        test.fail("Can not find the controller with pattern %s" % contr_pattern)
    test.log.debug("Found the controller:%s", found_contr[0])
    contr_addr = found_contr[0].split(" ")[0]
    cmd = "lspci -vvv -s %s | grep 'Memory behind bridge:'" % contr_addr
    contr_info = session.cmd_output(cmd).strip()
    test.log.debug("Got the controller detailed information:%s", contr_info)
    mem_reserve_size = re.findall("\[size=(.*)M\]", contr_info)[0]
    test.log.debug("Got the default memory reserve size: %sM", mem_reserve_size)
    return int(mem_reserve_size)


def get_controller_address(controller_xml, test):
    """
    Get the controller's address from vm xml.
    It only applies to pcie-root-port controller because pci-bridge's
    address in vm xml is different with the one within the vm.

    For example, bus='0x00' slot='0x02' function='0x7'
    In the lspci, the device address: 00:02.7

    :param controller_xml: the controller's xml object
    :param test: test object
    :return: str, the expected controller's address within the vm
    """
    test.log.debug("The controller:%s", controller_xml)
    cntl_attrs = controller_xml.fetch_attrs()["address"]["attrs"]
    bus = cntl_attrs["bus"].removeprefix("0x")
    slot = cntl_attrs["slot"].removeprefix("0x")
    func = cntl_attrs["function"].removeprefix("0x")
    addr_in_vm = bus + ":" + slot + "." + func
    test.log.debug("The address for this controller:%s", addr_in_vm)
    return addr_in_vm


def get_controller(test, vm_xml, contr_dict):
    """
    Get the controller from the vm xml

    :param test: test object
    :param vm_xml: VMXML object
    :param contr_dict: dict, the controller's configuration
    :return: controller xml object
    """
    contr_index = contr_dict["index"]
    contr_model = contr_dict["model"]
    contr_type = contr_dict["type"]
    for cntl in vm_xml.devices.by_device_tag('controller'):
        if (cntl.type == contr_type and
                cntl.model == contr_model and
                cntl.index == contr_index):
            logging.debug("Found the controller:%s", cntl)
            return cntl
    test.error("Fail to get the controller in guest xml with "
               "index %s, model %s, type %s" % (contr_index,
                                                contr_model,
                                                contr_type))


def check__memReserve_by_controller_xml(test, params, vm_name, target_cntl):
    """
    Check memory reserve value of specified controller in vm xml

    :param test: test object
    :param params: dict, test parameters
    :param vm_name: str, vm name
    :param target_cntl: controller's xml
    """
    cur_vm_xml = VMXML.new_from_dumpxml(vm_name)
    expect_memReserve = params.get("memReserve")
    cntl = get_controller(test, cur_vm_xml, target_cntl)
    actual_memReserve = cntl.target.get("memReserve")
    if not actual_memReserve or int(actual_memReserve) != int(expect_memReserve):
        test.fail("Expect memReserve to be %s, "
                  "but found %s" % (expect_memReserve, actual_memReserve))
    else:
        test.log.debug("Verify: The memReserve value in guest xml - PASS")


def update_vm_xml(test, params, vm_xml):
    """
    Update vm xml with necessary controllers

    :param test: test object
    :param params: dict, test parameters
    :param vm_xml: VMXML object
    """
    def _add_contr(controller_dict):
        """
        Common function for adding a controller to vm xml

        :param controller_dict: dict, controller configuration
        """
        dev_obj = libvirt_vmxml.create_vm_device_by_type("controller", controller_dict)
        libvirt.add_vm_device(
            vm_xml, dev_obj, virsh_instance=virsh
        )
        test.log.debug("Add controller configuration:%s", controller_dict)

    test.log.debug("Step: Get maximum index of specified controller")
    depend_contr_dict = params.get("depend_contr_dict")
    contr_dict = params.get("contr_dict")
    ret_indexes = libvirt_pcicontr.get_max_contr_indexes(vm_xml,
                                                         'pci',
                                                         "pcie-root-port")
    cntr_index = int(ret_indexes[0])
    if depend_contr_dict:
        cntr_index += 1
        depend_contr_dict = eval(depend_contr_dict % cntr_index)
        _add_contr(depend_contr_dict)

    test.log.debug("Step: Add controller with specified configuration")
    contr_dict = contr_dict % (cntr_index + 1)
    params["contr_dict"] = contr_dict
    _add_contr(eval(contr_dict))


def run(test, params, env):
    """
    Test PCI controllers' memory reserve option
    1. Backup guest xml before the tests
    2. Modify guest xml and define the guest
    3. Start guest
    4. Do checking in vm xml and within vm
    """
    libvirt_version.is_libvirt_feature_supported(params)

    firmware_type = params.get("firmware_type")
    loader_dict = eval(params.get("loader_dict", "{}"))
    new_memReserve = params.get("memReserve")
    expect_memReserve = params.get("expect_memReserve", new_memReserve)
    params['expect_memReserve'] = expect_memReserve
    virsh_options = {'debug': True, 'ignore_status': False}

    vm_name = guest_os.get_vm(params)
    vm = env.get_vm(vm_name)
    vm_xml_obj = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml_obj.copy()
    try:
        test.log.debug("Step: Update vm firmware configuration")
        vmxml = guest_os.prepare_os_xml(vm_name, loader_dict, firmware_type=firmware_type)
        test.log.debug("Step: Configure the controller with specified memReserve value")
        update_vm_xml(test, params, vmxml)
        cur_vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        contr_dict = eval(params.get("contr_dict"))
        test.log.debug("Step: Get the added controller xml")
        target_cntl = get_controller(test, cur_vm_xml, contr_dict)
        test.log.debug("Step: Get the address of the added controller")
        contr_address = get_controller_address(target_cntl, test)

        test.log.debug("Step: Start vm")
        virsh.start(vm_name, **virsh_options)
        logging.debug("Test VM XML after starting:"
                      "\n%s", VMXML.new_from_dumpxml(vm_name))

        test.log.debug("Step: Check vm dumpxml for specified memReserve value")
        check__memReserve_by_controller_xml(test, params, vm_name, target_cntl)
        test.log.debug("Step: Check the controller's new reserved memory value in vm")
        session = vm.wait_for_login()
        actual_reserve_size = get_mem_reserve_size_in_vm(test,
                                                         contr_address,
                                                         params,
                                                         session
                                                         )
        actual_reserve_size = int(actual_reserve_size) * 1024
        if actual_reserve_size != int(expect_memReserve):
            test.fail("Expect mem reserve size "
                      "in vm to be %d, but found %d" % (expect_memReserve,
                                                        actual_reserve_size))
        else:
            test.log.debug("Verify: The mem reserve size "
                           "in vm is %d as expected - PASS", actual_reserve_size)
        session.close()
    finally:
        vm_xml_backup.sync()
