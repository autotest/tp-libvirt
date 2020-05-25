import logging
import os
import re
import uuid
import aexpect

from virttest import virsh
from virttest import data_dir
from virttest import utils_net
from virttest import utils_misc
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

    def guest_cmd_check(cmd, session, pattern):
        """
        Check cmd output with pattern in session
        """
        try:
            cmd_status, output = session.cmd_status_output(cmd, timeout=10)
            logging.info("exit: %s, output: %s",
                         cmd_status, output)
            return re.search(pattern, output)
        except (aexpect.ShellTimeoutError, aexpect.ShellStatusError) as e:
            logging.debug(e)
            return re.search(pattern, str(e.__str__))

    def guest_if_state(if_name, session):
        """
        Get the domain link state from the guest
        """
        # Get link state by ethtool
        cmd = "ethtool %s" % if_name
        pattern = "Link detected: ([a-zA-Z]+)"
        ret = guest_cmd_check(cmd, session, pattern)
        if ret:
            return ret.group(1) == "yes"
        else:
            return False

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
        if not utils_misc.wait_for(lambda: guest_if_state(if_name, session), 5):
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
        if utils_misc.wait_for(lambda: guest_if_state(if_name, session), 5):
            logging.error("Guest link should be down now")
            return False

        # Passed all test
        return True

    vm_name = []
    # vm_name list:first element for original name in config
    vm_name.append(params.get("main_vm", "avocado-vt-vm1"))
    vm = env.get_vm(vm_name[0])
    options = params.get("if_options", "--config")
    start_vm = params.get("start_vm", "no")
    domain = params.get("domain", "name")
    if_device = params.get("if_device", "net")
    if_name = params.get("if_name", "vnet0")
    if_operation = params.get("if_operation", "up")
    status_error = params.get("status_error", "no")
    mac_address = vm.get_virsh_mac_address(0)
    check_link_state = "yes" == params.get("check_link_state", "no")
    check_link_by_update_device = "yes" == params.get(
        "excute_update_device", "no")
    device = "vnet0"
    username = params.get("username")
    password = params.get("password")

    # Back up xml file.
    vm_xml_file = os.path.join(data_dir.get_tmp_dir(), "vm.xml")
    virsh.dumpxml(vm_name[0], extra="--inactive", to_file=vm_xml_file)

    # Vm status
    if start_vm == "yes" and vm.is_dead():
        vm.start()

    elif start_vm == "no" and vm.is_alive():
        vm.destroy()

    # vm_name list: second element for 'domain' in virsh command
    if domain == "ID":
        # Get ID for the running domain
        vm_name.append(vm.get_id())
    elif domain == "UUID":
        # Get UUID for the domain
        vm_name.append(vm.get_uuid())
    elif domain == "no_match_UUID":
        # Generate a random UUID
        vm_name.append(uuid.uuid1())
    elif domain == "no_match_name":
        # Generate a random string as domain name
        vm_name.append(utils_misc.generate_random_string(6))
    elif domain == " ":
        # Set domain name empty
        vm_name.append("''")
    else:
        # Set domain name
        vm_name.append(vm_name[0])

    try:
        # Test device net or mac address
        if if_device == "net" and vm.is_alive():
            device = if_name
            # Get all vm's interface device
            device = vm_xml.VMXML.get_net_dev(vm_name[0])[0]

        elif if_device == "mac":
            device = mac_address

        # Test no exist device
        if if_device == "no_exist_net":
            device = "vnet-1"
        elif if_device == "no_exist_mac":
            # Generate random mac address for negative test
            device = utils_net.VirtIface.complete_mac_address("01:02")
        elif if_device == " ":
            device = "''"

        # Setlink opertation
        result = domif_setlink(vm_name[1], device, if_operation, options)
        status = result.exit_status
        logging.info("Setlink done")

        # Getlink opertation
        get_result = domif_getlink(vm_name[1], device, options)
        getlink_output = get_result.stdout.strip()

        # Check the getlink command output
        if status_error == "no":
            if not re.search(if_operation, getlink_output):
                test.fail("Getlink result should "
                          "equal with setlink operation")

        logging.info("Getlink done")
        # If --config or --persistent is given should restart the vm then test link status
        if any(options == option for option in ["--config", "--persistent"]) and vm.is_alive():
            vm.destroy()
            vm.start()
            logging.info("Restart VM")

        elif start_vm == "no":
            vm.start()

        error_msg = None
        if status_error == "no":
            # Serial login the vm to check link status
            # Start vm check the link statue
            session = vm.wait_for_serial_login(username=username,
                                               password=password)
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
            domif_setlink(vm_name[0], device, "up", "")
            if not utils_misc.wait_for(
                    lambda: guest_if_state(guest_if_name, session), 5):
                error_msg = "Link state isn't up in guest"

            # Ignore status of this one
            cmd = 'ip link set down %s' % guest_if_name
            session.cmd_status_output(cmd, timeout=10)
            pattern = "%s:.*state DOWN.*" % guest_if_name
            pattern_cmd = 'ip addr show dev %s' % guest_if_name
            guest_cmd_check(pattern_cmd, session, pattern)

            cmd = 'ip link set up %s' % guest_if_name
            session.cmd_status_output(cmd, timeout=10)
            pattern = "%s:.*state UP.*" % guest_if_name
            if not guest_cmd_check(pattern_cmd, session, pattern):
                error_msg = ("Could not bring up interface %s inside guest"
                             % guest_if_name)
        else:  # negative test
            # stop guest, so state is always consistent on next start
            vm.destroy()

        if error_msg:
            test.fail(error_msg)

        # Check status_error
        if status_error == "yes":
            if status:
                logging.info("Expected error (negative testing). Output: %s",
                             result.stderr.strip())

            else:
                test.fail("Unexpected return code %d "
                          "(negative testing)" % status)
        elif status_error != "no":
            test.error("Invalid value for status_error '%s' "
                       "(must be 'yes' or 'no')" % status_error)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        virsh.undefine(vm_name[0])
        virsh.define(vm_xml_file)
        os.remove(vm_xml_file)
