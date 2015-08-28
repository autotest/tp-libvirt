import logging
import os
import re
from autotest.client.shared import error
from virttest import virsh
from virttest import utils_net
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test command: virsh domif-setlink and domif-getlink.

    The command   set and get link state of a virtual interface
    1. Prepare test environment.
    2. Perform virsh domif-setlink and domif-getlink operation.
    3. Recover test environment.
    4. Confirm the test result.
    """

    def domif_setlink(vm, device, operation, options):
        """
        Set the domain link state

        :param vm : domain name
        :param device : domain virtual interface
        :param opration : domain virtual interface state
        :param options : some options like --config

        """

        return virsh.domif_setlink(vm, device, operation, options, debug=True)

    def domif_getlink(vm, device, options):
        """
        Get the domain link state

        :param vm : domain name
        :param device : domain virtual interface
        :param options : some options like --config

        """

        return virsh.domif_getlink(vm, device, options,
                                   ignore_status=True, debug=True)

    def guest_if_state(if_name, session):
        """
        Get the domain link state from the guest
        """
        # Get link state by ethtool
        cmd = "ethtool %s" % if_name
        cmd_status, output = session.cmd_status_output(cmd)
        logging.info("ethtool exit: %s, output: %s",
                     cmd_status, output)
        link_state = re.search("Link detected: ([a-zA-Z]+)",
                               output).group(1)
        return link_state == "yes"

    def check_update_device(vm, if_name, session):
        """
        Change link state by upadte-device command, Check the results
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)

        # Get interface xml object
        iface = vmxml.get_devices(device_type="interface")[0]
        if iface.address:
            del iface.address

        # Change link state to up
        iface.link_state = "up"
        iface.xmltreefile.write()
        ret = virsh.update_device(vm.name, iface.xml,
                                  ignore_status=True, debug=True)
        if ret.exit_status:
            logging.error("Failed to update device to up state")
            return False
        if not guest_if_state(if_name, session):
            logging.error("Guest link should be up now")
            return False

        # Change link state to down
        iface.link_state = "down"
        iface.xmltreefile.write()
        ret = virsh.update_device(vm.name, iface.xml,
                                  ignore_status=True, debug=True)
        if ret.exit_status:
            logging.error("Failed to update device to down state")
            return False
        if guest_if_state(if_name, session):
            logging.error("Guest link should be down now")
            return False

        # Passed all test
        return True

    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)
    options = params.get("if_options", "--config")
    start_vm = params.get("start_vm", "no")
    if_device = params.get("if_device", "net")
    if_name = params.get("if_name", "vnet0")
    if_operation = params.get("if_operation", "up")
    status_error = params.get("status_error", "no")
    mac_address = vm.get_virsh_mac_address(0)
    check_link_state = "yes" == params.get("check_link_state", "no")
    check_link_by_update_device = "yes" == params.get(
        "excute_update_device", "no")
    device = "vnet0"

    # Back up xml file.
    vm_xml_file = os.path.join(test.tmpdir, "vm.xml")
    virsh.dumpxml(vm_name, extra="--inactive", to_file=vm_xml_file)

    # Vm status
    if start_vm == "yes" and vm.is_dead():
        vm.start()

    elif start_vm == "no" and vm.is_alive():
        vm.destroy()

    try:
        # Test device net or mac address
        if if_device == "net" and vm.is_alive():
            device = if_name
            # Get all vm's interface device
            device = vm_xml.VMXML.get_net_dev(vm_name)[0]

        elif if_device == "mac":
            device = mac_address

        # Setlink opertation
        result = domif_setlink(vm_name, device, if_operation, options)
        status = result.exit_status
        logging.info("Setlink done")

        # Getlink opertation
        get_result = domif_getlink(vm_name, device, options)
        getlink_output = get_result.stdout.strip()

        # Check the getlink command output
        if not re.search(if_operation, getlink_output) and status_error == "no":
            raise error.TestFail("Getlink result should "
                                 "equal with setlink operation ", getlink_output)

        logging.info("Getlink done")
        # If --config is given should restart the vm then test link status
        if options == "--config" and vm.is_alive():
            vm.destroy()
            vm.start()
            logging.info("Restart VM")

        elif start_vm == "no":
            vm.start()

        error_msg = None
        if status_error == "no":
            # Serial login the vm to check link status
            # Start vm check the link statue
            session = vm.wait_for_serial_login()
            guest_if_name = utils_net.get_linux_ifname(session, mac_address)

            # Check link state in guest
            if check_link_state:
                if (if_operation == "up" and
                        not guest_if_state(guest_if_name, session)):
                    error_msg = "Link state should be up in guest"
                if (if_operation == "down" and
                        guest_if_state(guest_if_name, session)):
                    error_msg = "Link state should be down in guest"

            # Test of setting link state by update_device command
            if check_link_by_update_device:
                if not check_update_device(vm, guest_if_name, session):
                    error_msg = "Check update_device failed"

            # Set the link up make host connect with vm
            domif_setlink(vm_name, device, "up", "")
            if not guest_if_state(guest_if_name, session):
                error_msg = "Link state isn't up in guest"

            # Ignore status of this one
            cmd_status = session.cmd_status('ip link set dev %s down'
                                            % guest_if_name)
            cmd_status = session.cmd_status('ip link set dev %s up'
                                            % guest_if_name)
            if cmd_status != 0:
                error_msg = ("Could not bring up interface %s inside guest"
                             % guest_if_name)
        else:  # negative test
            # stop guest, so state is always consistent on next start
            vm.destroy()

        if error_msg:
            raise error.TestFail(error_msg)

        # Check status_error
        if status_error == "yes":
            if status:
                logging.info("Expected error (negative testing). Output: %s",
                             result.stderr.strip())

            else:
                raise error.TestFail("Unexpected return code %d "
                                     "(negative testing)" % status)
        elif status_error == "no":
            status = cmd_status
            if status:
                raise error.TestFail("Unexpected error (positive testing). "
                                     "Output: %s" % result.stderr.strip())

        else:
            raise error.TestError("Invalid value for status_error '%s' "
                                  "(must be 'yes' or 'no')" % status_error)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        virsh.undefine(vm_name)
        virsh.define(vm_xml_file)
        os.remove(vm_xml_file)
