import time
import logging
import aexpect
from avocado.utils import process

from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import nodedev_xml


def run(test, params, env):
    """
    Test command: virsh nodedev-event
    1. Check a  nodedevice.
    2. Running virsh nodedev-event with different options, detach and reattach
       the nodedevice if needed, then check the output of nodedev-event.
    3. Clean the environment.
    """
    def trigger_nodedev_event(nodedev_event_name, event_amount):
        """
        :param event_amount: number of event loop times
        Trigger nodedevice detach/reattach in event_number times
        """
        event_list = []
        if nodedev_event_name == 'lifecycle':
            if event_amount == 1:
                virsh.nodedev_detach(device_address)
                event_list.append("Deleted")
            if event_amount > 1:
                if event_amount % 2 == 0:
                    while event_amount > 0:
                        virsh.nodedev_detach(device_address)
                        event_list.append("Deleted")
                        virsh.nodedev_reattach(device_address)
                        event_list.append("Created")
                        time.sleep(2)
                        event_amount -= 2
                if event_amount % 2 == 1:
                    event_amount -= 1
                    while event_amount > 0:
                        virsh.nodedev_detach(device_address)
                        event_list.append("Deleted")
                        virsh.nodedev_reattach(device_address)
                        event_list.append("Created")
                        time.sleep(2)
                        event_amount -= 2
                    virsh.nodedev_detach(device_address)
                    event_list.append("Deleted")
        return event_list

    def nodedev_rollback(nodedev_event_name, event_amount):
        """
        :param event_amount: number of event loop times
        Trigger nodedevice detach/reattach in event_number times
        """
        if nodedev_event_name == 'lifecycle':
            if event_amount % 2 == 1:
                virsh.nodedev_reattach(device_address)

    def check_event_output(output, expected_event_list):
        """
        Check received nodedev-event in output.
        :param output: The virsh shell output, such as:
            Welcome to virsh, the virtualization interactive terminal.
            Type:  'help' for help with commands
            'quit' to quit
            virsh # event 'lifecycle' for node device net_enp134s16_1a_a2_b0_9a_a2_a0: Deleted
            events received: 1
        :param expected_event_list: A list of expected events
            ['Deleted', 'Created', ..]
        """
        event_match_str = "event 'lifecycle' for node device %s: %s"
        if nodedev_event_interrupt:
            output = output.strip().splitlines()[5:]
        else:
            output = output.strip().splitlines()[5:-2]
        output = [o.replace("virsh #", "").strip() for o in output]
        # for reattach, the device will be named 'eth0' and renamed to the actual name. nodedev-event will catch
        # the "eth0" created and deleted as well. Delete the event for device name here:
        output_new = [event for event in output if device_name in event]
        # Both order and content should match
        if len(output_new) < len(expected_event_list):
            test.fail("Event did not have enough output. Expected: {} Actual: {}"
                      .format(expected_event_list, output_new))
        index = 0
        for event_str in expected_event_list:
            match_str = event_match_str % (device_name, event_str)
            logging.debug("Expected output: %s", match_str)
            logging.debug("Actual output: %s", output_new[index])
            if not output_new[index].count(match_str):
                test.fail("Event received not match")
            index += 1

    def network_device_name():
        """
        Get the address of network pci device
        """
        net_list = virsh.nodedev_list(tree='', cap='net')
        net_lists = net_list.stdout.strip().splitlines()
        device_check = False
        route_cmd = " route | grep default"
        route_default = process.run(route_cmd, shell=True).stdout_text.strip().split(' ')
        ip_default = route_default[-1]

        for net_device_name in net_lists:
            if net_device_name.find(ip_default) == -1:
                net_device_address = nodedev_xml.NodedevXML.new_from_dumpxml(net_device_name).parent
                if 'pci' in net_device_address:
                    device_check = True
                    return net_device_name
        if not device_check:
            test.cancel('Param device_address is not configured.')

    def network_device_address(net_device_name):
        """
        Get the address of network pci device
        :param net_device_name: The name of net_device gotten fron host
        """
        net_device_address = nodedev_xml.NodedevXML.new_from_dumpxml(net_device_name).parent
        return net_device_address

    def check_kernel_option():
        """
        Check the kernel option if the kernel cmdline include  "iommu=on" option
        """
        check_cmd = "egrep '(intel|amd)_iommu=on' /proc/cmdline"
        try:
            check_result = process.run(check_cmd, shell=True)
        except Exception:
            test.cancel("Operation not supported: neither VFIO nor KVM device assignment"
                        "is currently supported on this system")
        else:
            logging.debug('IOMMU is enabled')

    # Check kernel iommu option
    check_kernel_option()

    # Init variables
    nodedev_event_list = params.get("nodedev_event_list")
    nodedev_event_loop = params.get("nodedev_event_loop")
    nodedev_event_option = params.get("nodedev_event_option")
    nodedev_event_name = params.get("nodedev_event_name")
    nodedev_event_timeout = params.get("nodedev_event_timeout")
    nodedev_event_amount = int(params.get("nodedev_event_amount", 1))
    nodedev_event_device = params.get("nodedev_event_device")
    nodedev_event_timestamp = params.get("nodedev_event_timestamp")
    status_error = params.get("status_error", "no") == 'yes'
    expected_event_list = []
    virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC, auto_close=True)
    nodedev_event_interrupt = False
    device_name = network_device_name()
    device_address = network_device_address(device_name)

    try:
        if nodedev_event_list:
            nodedev_event_option += " --list"
            cmd_result = virsh.nodedev_event(event=None, event_timeout=None, options=nodedev_event_option)
            libvirt.check_exit_status(cmd_result, status_error)
        if nodedev_event_loop:
            nodedev_event_option += " --loop"
        if nodedev_event_device:
            if not status_error:
                nodedev_event_device = device_name
            nodedev_event_option += " --device" + " " + nodedev_event_device
        if nodedev_event_timeout:
            nodedev_event_option += " --timeout" + " " + nodedev_event_timeout
        if nodedev_event_timestamp:
            nodedev_event_option += " --timestamp"
            # Assemble the nodedev-event command
        if not status_error:
            nodedev_event_cmd = 'nodedev-event' + ' --event %s' % nodedev_event_name + nodedev_event_option
            logging.info("Sending '%s' to virsh shell", nodedev_event_cmd)
            virsh_session.sendline(nodedev_event_cmd)
            expected_event_list = trigger_nodedev_event(nodedev_event_name, nodedev_event_amount)

            if nodedev_event_timeout:
                time.sleep(int(nodedev_event_timeout))
            if nodedev_event_loop == 'yes':
                time.sleep(2)
                virsh_session.send_ctrl("^C")
                nodedev_event_interrupt = True

            ret_output = virsh_session.get_stripped_output()

            if nodedev_event_timestamp:
                timestamp = time.strftime("%Y-%m-%d")
                if timestamp in ret_output:
                    check_event_output(ret_output, expected_event_list)
            else:
                check_event_output(ret_output, expected_event_list)
        else:
            cmd_result = virsh.nodedev_event(event=nodedev_event_name, options=nodedev_event_option)
            libvirt.check_exit_status(cmd_result, status_error)
    finally:
        virsh_session.close()
        nodedev_rollback(nodedev_event_name, nodedev_event_amount)
