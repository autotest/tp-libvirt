import logging as log
import time
import re

from avocado.core import exceptions
from avocado.utils import process

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def set_attach_options(iface_type=None, iface_source=None, iface_mac=None,
                       iface_model=None, flags=""):
    """
    Set attach-interface options.

    :param iface_type: network interface type
    :param iface_source: source of network interface
    :param iface_mac: interface MAC address
    :param iface_model: interface model type
    :param flags: attach interface flags (--live, --config, etc.)
    :return: formatted options string
    """
    options = ""
    if iface_type is not None:
        options += " --type '{}'".format(iface_type)
    if iface_source is not None:
        options += " --source '{}'".format(iface_source)
    if iface_mac is not None:
        options += " --mac '{}'".format(iface_mac)
    if iface_model is not None:
        options += " --model '{}'".format(iface_model)
    if flags:
        options += " {}".format(flags)
    return options


def check_interface_xml(vm_name, iface_type, iface_source, iface_mac,
                        check_active=True):
    """
    Comprehensive XML verification for attach-interface testing.

    :param vm_name: name of domain
    :param iface_type: interface device type
    :param iface_source: interface source
    :param iface_mac: interface MAC address
    :param check_active: whether to check active or inactive XML
    :return: True if interface found, False otherwise
    """
    try:
        if check_active:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            xml_type = "active"
        else:
            vmxml = vm_xml.VMXML.new_from_dumpxml(
                vm_name, options="--inactive")
            xml_type = "inactive"

        logging.info("Checking %s XML for interface %s", xml_type, iface_mac)

        ifaces = vmxml.devices.by_device_tag('interface')
        for iface in ifaces:
            if (iface.type_name == iface_type and
                    iface.mac_address == iface_mac):
                if iface_source is not None:
                    if (iface.xmltreefile.find('source') is not None and
                            iface.source.get('network') == iface_source):
                        logging.info("Found interface %s in %s XML",
                                     iface_mac, xml_type)
                        return True
                else:
                    logging.info("Found interface %s in %s XML",
                                 iface_mac, xml_type)
                    return True

        logging.info("Interface %s not found in %s XML", iface_mac, xml_type)
        return False
    except Exception as e:
        logging.error("Failed to check %s XML: %s", xml_type, e)
        return False


def check_interface_in_vm_with_ip_l(vm, iface_mac):
    """
    VM-level interface verification using 'ip l | grep' command.

    :param vm: VM instance
    :param iface_mac: MAC address to check
    :return: tuple (status, message, interface_name)
    """
    try:
        session = vm.wait_for_login()

        logging.info("Checking VM interface using 'ip l | grep' command for MAC %s", iface_mac)

        grep_cmd = "ip l | grep -i -B1 '%s'" % iface_mac.lower()
        status, output = session.cmd_status_output(grep_cmd)
        session.close()

        if status != 0 or not output.strip():
            return (1, "Interface with MAC %s not found in VM via 'ip l | grep'" % iface_mac, None)

        logging.debug("'ip l | grep' output: %s", output)

        interface_name = None
        lines = output.strip().split('\n')

        for line in lines:
            match = re.match(r'^\d+:\s+(\S+):', line.strip())
            if match:
                interface_name = match.group(1).rstrip('@')
                break

        if interface_name:
            logging.info("Interface %s found in VM via 'ip l | grep' (interface: %s)",
                         iface_mac, interface_name)
            return (0, "Interface successfully found in VM", interface_name)
        else:
            logging.warning("MAC %s found but interface name extraction failed", iface_mac)
            return (0, "Interface found but name extraction failed", None)

    except Exception as detail:
        return (1, "Failed to login to VM or execute 'ip l | grep': %s" % detail, None)


def comprehensive_verification(vm_name, vm, iface_type, iface_source, iface_mac,
                               flags, vm_state, params):
    """
    Multi-layer verification including XML files and VM interface status.

    :param vm_name: name of domain
    :param vm: VM instance
    :param iface_type: interface type
    :param iface_source: interface source
    :param iface_mac: interface MAC address
    :param flags: operation flags
    :param vm_state: current VM state
    :param params: test parameters
    """
    errors = []

    logging.info("Starting comprehensive verification for flags: %s, VM state: %s",
                 flags, vm_state)

    # Check active XML only when the VM is running
    expected_active_xml = "yes" == params.get("expected_active_xml", "no")

    # Do not check active XML when VM is shutoff
    if vm_state == "shutoff" and expected_active_xml:
        logging.info(
            "VM is shutoff, skipping active XML check even though expected_active_xml=yes")
        logging.info(
            "For shutoff VM with --current flag, interface should be in inactive XML instead")
    elif vm_state != "shutoff":
        # Check active XML only when VM is running
        active_exists = check_interface_xml(vm_name, iface_type, iface_source,
                                            iface_mac, check_active=True)
        if expected_active_xml and not active_exists:
            errors.append(
                "Interface not found in active XML expected with flags: %s" % flags)
        elif not expected_active_xml and active_exists:
            errors.append(
                "Interface found in active XML but should not exist with flags: %s" % flags)
        else:
            result_msg = "found" if active_exists else "not found"
            logging.info(
                "Interface %s in active XML as expected with flags: %s" % (result_msg, flags))

    # Check inactive XML
    expected_inactive_xml = "yes" == params.get("expected_inactive_xml", "no")
    inactive_exists = check_interface_xml(vm_name, iface_type, iface_source,
                                          iface_mac, check_active=False)
    if expected_inactive_xml and not inactive_exists:
        errors.append(
            "Interface not found in inactive XML expected with flags: %s" % flags)
    elif not expected_inactive_xml and inactive_exists:
        errors.append(
            "Interface found in inactive XML but should not exist with flags: %s" % flags)
    else:
        result_msg = "found" if inactive_exists else "not found"
        logging.info(
            "Interface %s in inactive XML as expected with flags: %s" % (result_msg, flags))

    # Check VM interface using 'ip l' command if required
    expected_vm_interface = "yes" == params.get("expected_vm_interface", "no")
    if expected_vm_interface and vm_state == "running":
        status, msg, iface_name = check_interface_in_vm_with_ip_l(
            vm, iface_mac)
        if status != 0:
            errors.append("VM interface check via 'ip l': %s" % msg)
        else:
            logging.info("Interface found in VM (name: %s)", iface_name)

    if errors:
        raise exceptions.TestFail(
            "verification failed: %s" % "; ".join(errors))

    logging.info("All verifications passed successfully")


def run(test, params, env):
    """
    Test virsh attach-interface command with various options for running/shutdown vm.

    Test scenarios:
    1. For VM running or VM down status
    2. attach-interface command with default network with:
       - "no option"
       - "--live"
       - "--current"
       - "--persistent"
       - "--config"
       - "--config --live"
       - "--current --live" options
    3. Check attach-interface command results,XML file and interface in VM
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Test parameters
    uri = libvirt_vm.normalize_connect_uri(
        params.get("connect_uri", "default"))
    vm_state = params.get("vm_state", "running")
    test_flags = params.get("test_flags", "")
    status_error = "yes" == params.get("status_error", "no")
    expected_error = params.get("expected_error", "")

    # Interface specific attributes
    iface_type = params.get("iface_type", "network")
    iface_source = params.get("iface_source", "default")
    iface_model = params.get("iface_model", "virtio")
    iface_mac = utils_net.generate_mac_address_simple()

    virsh_dargs = {'ignore_status': True, 'uri': uri, 'debug': True}

    logging.info("Starting attach-interface options test")
    logging.info("Test parameters - VM: %s, State: %s, Flags: '%s'",
                 vm_name, vm_state, test_flags)

    # Back up original XML
    if vm.is_alive():
        vm.destroy(gracefully=False)
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Set VM to desired state
        if vm_state == "running":
            logging.info("Setting VM to running state")
            if vm.is_dead():
                vm.start()
                # Ensure VM boots successfully
                session = vm.wait_for_login()
                session.close()
                logging.info("VM started and booted successfully")
        elif vm_state == "shutoff":
            logging.info("Setting VM to shutoff state")
            if vm.is_alive():
                vm.destroy(gracefully=False)
            logging.info("VM is shut down")

        # Prepare attach options
        attach_options = set_attach_options(iface_type, iface_source, iface_mac,
                                            iface_model, test_flags)

        logging.info(
            "Executing attach-interface with options: %s", attach_options)
        logging.info("Generated MAC address: %s", iface_mac)

        # Execute attach-interface command
        attach_result = virsh.attach_interface(
            vm_name, attach_options, **virsh_dargs)

        # Check command execution result
        if status_error:
            if attach_result.exit_status == 0:
                raise exceptions.TestFail(
                    "Expected attach command to fail but it succeeded")
            else:
                logging.info("Attach command failed as expected: %s",
                             attach_result.stderr)
                if expected_error and expected_error not in attach_result.stderr:
                    raise exceptions.TestFail("Expected error '%s' not found in: %s" %
                                              (expected_error, attach_result.stderr))
                return
        else:
            if attach_result.exit_status != 0:
                raise exceptions.TestFail("Attach command failed unexpectedly: %s" %
                                          attach_result.stderr)

        logging.info("Attach command executed successfully")

        # Wait for operation to take effect
        #utils_misc.wait_for(timeout=3)

        # Comprehensive verification
        comprehensive_verification(vm_name, vm, iface_type, iface_source, iface_mac,
                                   test_flags, vm_state, params)

        logging.info("Test completed successfully for flags: %s", test_flags)

    finally:
        # Clean up: restore original VM configuration
        if vm.is_alive():
            vm.destroy(gracefully=False, free_mac_addresses=False)
        backup_xml.sync()
