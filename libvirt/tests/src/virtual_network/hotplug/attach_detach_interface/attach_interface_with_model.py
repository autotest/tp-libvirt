import os
import uuid
import re

from virttest import libvirt_version
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from avocado.utils import process

from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml.devices import interface
from virttest.utils_test import libvirt

from provider.virtual_network import network_base
from provider.interface import interface_base

VIRSH_ARGS = {"ignore_status": False, "debug": True}


def clear_audit_log(test):
    """
    Clear the audit log on host

    :param test: Test instance
    """
    try:
        # Clear audit.log.
        audit_dir = "/var/log/audit/"
        for file in os.listdir(audit_dir):
            if file.startswith("audit.log"):
                file_path = os.path.join(audit_dir, file)
                process.run(f"> {file_path}", shell=True, ignore_status=True)
        test.log.debug("Audit log files cleared successfully")

    except Exception as e:
        test.fail(f"Failed to clear audit log: {e}")


def check_audit_log_for_interface_operation(vm_name, mac_addr, operation, test):
    """
    Check the audit log after interface attach/detach operation

    :param vm_name: VM name
    :param mac_addr: MAC address of the interface
    :param operation: "attach" or "detach"
    :param test: Test instance
    :return: True if expected audit log found, False otherwise
    """
    try:
        cmd = f"ausearch --start recent -m VIRT_RESOURCE -i | grep {operation}"
        result = process.run(cmd, shell=True, ignore_status=True)
        output = result.stdout_text if result.stdout_text else ""
        test.log.debug(f"Audit log search result for {operation}: {output}")

        if operation == "attach":
            expected_pattern = f"attach vm={vm_name}.*new-net={mac_addr}"
        else:  # operation == "detach"
            expected_pattern = f"detach vm={vm_name}.*old-net={mac_addr}"

        if re.search(expected_pattern, output):
            test.log.debug(f"Found expected {operation} audit log entry")
            return True
        else:
            test.log.warning(f"Expected pattern '{expected_pattern}' not found in audit log")
            return False
    except Exception as e:
        test.log.warning(f"Failed to check audit log: {e}")
        return False


def check_model_controller(vm_name, pci_model, test):
    """
    Checks that the controllers are the expected pci model

    :param vm_name: VM name
    :param pci_model: The expected pci model
    :param test: Test instance
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    iface = vmxml.get_devices("interface")[0]
    test.log.debug(f"Interface xml after vm started:\n{iface}")
    ctrl_index = int(iface.fetch_attrs()["address"]["attrs"]["bus"], 16)
    controllers = vmxml.get_devices("controller")
    iface_controller = [c for c in controllers if c.type == "pci" and
                        c.index == str(ctrl_index)][0]
    test.log.debug(f"Controller xml:\n{iface_controller}")

    if iface_controller.model == pci_model:
        test.log.debug("XML controller model check: PASS")
    else:
        test.fail(f"Expect pci model: {pci_model}, "
                  f"and got {iface_controller.model}")


def run(test, params, env):
    """
    Attach-interface with different models and options
    """
    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    outside_ip = params.get("outside_ip")
    pci_model = params.get("pci_model")
    iface_driver = params.get("iface_driver")
    model_type = params.get("model_type")
    check_pci_model = params.get("check_pci_model", "yes") == "yes"
    bridge_controller_needed = params.get("bridge_controller_needed", "yes") == "yes"
    check_audit_log = params.get_boolean("check_audit_log")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if bridge_controller_needed:
            pci_controllers = vmxml.get_controllers("pci")
            for controller in pci_controllers:
                if controller.get("model") == "pcie-to-pci-bridge":
                    break
            else:
                controller_dict = {"model": "pcie-to-pci-bridge"}
                libvirt_vmxml.modify_vm_device(vmxml, "controller", controller_dict, 50)

        libvirt_vmxml.remove_vm_devices_by_type(vm, "interface")
        test.log.debug(f"VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}")

        vm.start()
        session = vm.wait_for_serial_login()

        if check_audit_log:
            clear_audit_log(test)

        mac = utils_net.generate_mac_address_simple()
        alias_name = "ua-" + str(uuid.uuid1())
        options = "network default --model {} --alias {} --mac {}".format(
            model_type, alias_name, mac)
        iface = interface.Interface()
        iface.xml = virsh.attach_interface(vm_name, f"{options} --print-xml", **VIRSH_ARGS).stdout_text.strip()
        test.log.debug(iface.fetch_attrs())
        exp_iface = {'model': model_type, 'source': {'network': 'default'},
                     'type_name': 'network',
                     'alias': {'name': alias_name},
                     'mac_address': mac}
        iface_attrs = iface.fetch_attrs()

        if exp_iface != iface_attrs:
            test.fail("Failed to print xml! Expected: %s, Got: %s." % (exp_iface, iface_attrs))
        virsh.attach_interface(vm_name, options, **VIRSH_ARGS)

        if check_audit_log:
            if not check_audit_log_for_interface_operation(vm_name, mac, "attach", test):
                test.fail(f"Expected audit log entry for attach not found for VM {vm_name} with MAC {mac}")
        iflist = libvirt.get_interface_details(vm_name)
        test.log.debug(f"iflist of vm: {iflist}")
        iface_info = iflist[0]
        if iface_info["model"] == model_type:
            test.log.debug("Model check of domiflist: PASS")
        else:
            test.fail(f"Expect interface model {model_type}, "
                      f"but got {iface_info['model']}")

        session = vm.wait_for_serial_login()
        vm_iface_info = interface_base.get_vm_iface_info(session)
        if vm_iface_info.get("driver") != iface_driver:
            test.fail("Failed to get expected driver \"{iface_driver}\" in ethtool output")

        ips = {"outside_ip": outside_ip}
        network_base.ping_check(params, ips, session, force_ipv4=True)

        if check_pci_model:
            check_model_controller(vm_name, pci_model, test)

        virsh.detach_device_alias(vm_name, alias_name,
                                  wait_for_event=True,
                                  event_timeout=20,
                                  **VIRSH_ARGS)

        if check_audit_log:
            if not check_audit_log_for_interface_operation(vm_name, mac, "detach", test):
                test.fail(f"Expected audit log entry for detach not found for VM {vm_name} with MAC {mac}")

        iflist = libvirt.get_interface_details(vm_name)
        test.log.debug(f"iflist of vm: {iflist}")
        if iflist:
            test.fail("Found unexpected interface in %s." % iflist)
        vm_iface = interface_base.get_vm_iface(session, True)
        if vm_iface:
            test.fail("Found unexpected interface in %s." % vm_iface)
        else:
            test.log.debug(vm_iface)
        session.close()

    finally:
        bkxml.sync()
