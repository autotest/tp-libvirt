import re
import time
import logging
import aexpect

from virttest import virsh
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test command: virsh net-event

    1. Prepare a new network.
    2. Running virsh net-event with different options, and start/stop
       the network if needed, then check the output of net-event.
    3. Clean the environment.
    """

    prepare_net = "yes" == params.get("prepare_net", "yes")
    net_addr = params.get("net_addr")
    net_name = params.get("net_name")
    net_event_list = "yes" == params.get("net_event_list", "no")
    net_event_loop = "yes" == params.get("net_event_loop", "no")
    net_event_timestamp = "yes" == params.get("net_event_timestamp", "no")
    net_event_name = params.get("net_event_name")
    net_event_timeout = params.get("net_event_timeout")
    net_event_amount = int(params.get("net_event_amount", 1))
    status_error = "yes" == params.get("status_error", "no")
    net_event_option = params.get("net_event_option", "")
    virsh_dargs = {'debug': True, 'ignore_status': True}
    net_event_interrupt = False
    libv_net = None
    expected_event_list = []
    virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC, auto_close=True)

    def trigger_net_event(event_amount=1):
        """
        Trigger network start/stop actions in event_number times
        """
        i = event_amount // 2
        event_list = []
        try:
            while i > 0:
                virsh.net_start(net_name, **virsh_dargs)
                event_list.append("Started")
                virsh.net_destroy(net_name, **virsh_dargs)
                event_list.append("Stopped")
                i -= 1
            if event_amount % 2:
                virsh.net_start(net_name, **virsh_dargs)
                event_list.append("Started")
        finally:
            return event_list

    def check_output(output, expected_event_list):
        """
        Check received net-event in output.

        :param output: The virsh shell output, such as:
            Welcome to virsh, the virtualization interactive terminal.

            Type:  'help' for help with commands
                   'quit' to quit

            virsh # event 'lifecycle' for network virttest_net: Started
            events received: 1

            virsh #
         : expected_event_list: A list of expected events
            ['Started', 'Stopped', ..]
        """
        event_match_str = "event 'lifecycle' for network %s: %s"
        if net_event_interrupt:
            output = output.strip().splitlines()[5:]
        else:
            output = output.strip().splitlines()[5:-2]
        output = [o.replace("virsh #", "").strip() for o in output]
        # Both order and content should match
        index = 0
        for event_str in expected_event_list:
            match_str = event_match_str % (net_name, event_str)
            logging.debug("Expected output: %s", match_str)
            logging.debug("Actual output: %s", output[index])
            if not output[index].count(match_str):
                test.fail("Event received not match")
            if net_event_timestamp:
                if not re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", output[index]):
                    test.fail("Can not get timestamp in output")
            index += 1

    try:
        if prepare_net:
            libv_net = utlv.LibvirtNetwork("vnet", address=net_addr,
                                           net_name=net_name, persistent=True)
            # Destroy the network
            if virsh.net_state_dict()[net_name]['active']:
                virsh.net_destroy(net_name)
            logging.info("Defined network %s", net_name)

        if net_event_list:
            net_event_option += " --list"
        if net_event_loop:
            net_event_option += " --loop"
        if net_event_timestamp:
            net_event_option += " --timestamp"

        if not status_error and not net_event_list:
            # Assemble the net-event command
            net_event_cmd = "net-event %s" % net_event_option
            if net_name:
                net_event_cmd += " --network %s" % net_name
            if net_event_name:
                net_event_cmd += " --event %s" % net_event_name
            if net_event_timeout:
                net_event_cmd += " --timeout %s" % net_event_timeout
                if not status_error:
                    net_event_timeout = int(net_event_timeout)
            # Run the command in a new virsh session, then waiting for
            # 'lifecycle' events
            logging.info("Sending '%s' to virsh shell", net_event_cmd)
            virsh_session.sendline(net_event_cmd)
        else:
            result = virsh.net_event(network=net_name, event=net_event_name,
                                     event_timeout=net_event_timeout,
                                     options=net_event_option, **virsh_dargs)
            utlv.check_exit_status(result, status_error)

        if not status_error:
            # Verify 'lifecycle' events
            if not net_event_list and net_event_name == 'lifecycle':
                expected_event_list = trigger_net_event(net_event_amount)
                if net_event_timeout:
                    # Make sure net-event will timeout on time
                    time.sleep(net_event_timeout)
                elif net_event_loop:
                    virsh_session.send_ctrl("^C")
                    net_event_interrupt = True
                ret_output = virsh_session.get_stripped_output()
                check_output(ret_output, expected_event_list)
    finally:
        virsh_session.close()
        if libv_net:
            libv_net.cleanup()
