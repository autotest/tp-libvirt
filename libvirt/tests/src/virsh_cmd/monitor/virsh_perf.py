import logging
import ast

from virttest import virsh
from virttest import virt_vm

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def check_event_value(vm_name, perf_option, event):
    """
    Check domstats output and if the event has a value as expect
    1. if perf_option == --disable, there isn't a value/line
    2. if perf_option == --enable, there is a value/line
    :param vm_name: Domain name,id
    :param perf_option: --enable or  --disable
    :param vent: perf event name
    """
    logging.debug("check_event_value: vm_name= %s, perf_option=%s, event=%s",
                  vm_name, perf_option, event)

    ret = False
    result = virsh.domstats(vm_name, "--perf", ignore_status=True,
                            debug=True)
    libvirt.check_exit_status(result)
    output = result.stdout.strip()
    logging.debug("domstats output is %s", output)

    if perf_option == '--enable':
        for line in output.split('\n'):
            if '.' in line and event == (line.split('.')[1]).split('=')[0]:
                ret = True
    else:
        ret = True
        for line in output.split('\n'):
            if '.' in line and event == (line.split('.')[1]).split('=')[0]:
                ret = False
    return ret


def check_perf_result(vm_name, perf_option, events):
    """
    Check the result of perf cmd, including the events being enabled and
    disabled. If no event is in a wrong state, return "". Or, return the
    events in wrong state.

    :param vm_name: Domain name,id
    :param perf_option: --enable or  --disable
    :param events: perf event names seperated by comma
    """
    # logging.debug("events:%s in check_perf_result", events)
    ret_event = ""
    # If there is a event list, get the first event group seperated by ' '
    events_list = events.strip().split(' ')
    for event in events_list[0].split(','):
        if not check_event_value(vm_name, perf_option, event):
            logging.debug("event:%s, perf_option:%s", event, perf_option)
            ret_event = ret_event + event
    if len(events_list) > 1:
        for event in events_list[1].split(','):
            if perf_option.strip() == '--enable':
                perf_opt = '--disable'
            else:
                perf_opt = '--enable'
            if not check_event_value(vm_name, perf_opt, event):
                logging.debug("event:%s, perf_opt:%s", event, perf_opt)
                ret_event = ret_event + event
    return ret_event


def run(test, params, env):
    """
    Test command: virsh perf
    1. prepare vm
    2  Perform virsh perf operation.
    3. Confirm the test result
    4. Recover test environment
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    test_vm = env.get_vm(vm_name)
    perf_option = params.get("perf_option")
    events = params.get("events")
    virsh_opt = params.get("virsh_opt")
    vm_active = params.get("vm_active")
    status_error = params.get("status_error", "no") == "yes"
    status_disable = params.get("status_disable", "no") == "yes"
    event_item_list = [ast.literal_eval(x)
                       for x in params.get("event_items", "").split()]

    try:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        backup_xml = vmxml.copy()
        if event_item_list:
            perf = vm_xml.VMPerfXML()
            eventxml_list = []
            events_no = ''
            events_yes = ''
            events_yes_list = []
            events_no_list = []
            for i in range(len(event_item_list)):
                eventxml = perf.EventXML()
                eventxml.update(event_item_list[i])
                eventxml_list.append(eventxml)
                if event_item_list[i]['enabled'] != 'no':
                    events_yes_list.append(event_item_list[i]['name'])
                    continue
                events_no_list.append(event_item_list[i]['name'])
            events_yes = ','.join(events_yes_list)
            events_no = ','.join(events_no_list)
            perf.events = eventxml_list
            vmxml.perf = perf
            logging.debug("vm xml: %s", vmxml)
            vmxml.sync()
            test_vm.start()
            # using '--enable' for the normal test
            result = check_perf_result(vm_name, '--enable', events_yes)
            if result != "":
                test.fail("Check domstats output failed for %s" % result)
            if status_disable:
                # using '--disable' for the negative test
                result = check_perf_result(vm_name, '--disable', events_no)
                if result != "":
                    test.fail("Check domstats output failed for %s" % result)

        # To test disable action, enable the events first
        if perf_option == '--disable':
            result = virsh.perf(vm_name, "--enable", events, "",
                                ignore_status=True, debug=True)
            status = result.exit_status
            if status:
                if ("unable to enable/disable perf events" in
                        result.stderr.lower()):
                    test.cancel("Some of the events is not supported")
                else:
                    test.fail("Failed to enable evt before testing disable!")

        if not event_item_list:
            result = virsh.perf(vm_name, perf_option, events, virsh_opt,
                                ignore_status=True, debug=True)
            status = result.exit_status

        if any([status_error, status_disable, event_item_list]):
            if not event_item_list and not status:
                # if "argument unsupported: parameter" in result.stderr:
                #    test.cancel(result.stderr)
                test.fail("Run virsh cmd successfully with wrong command!")
        else:
            if status:
                if ("unable to enable/disable perf events" in
                        result.stderr.lower()):
                    test.cancel("Some of the events is not supported")
                else:
                    test.fail("Run virsh cmd failed with right command")
            else:
                # "--config" and "--live" can be used together, we need to
                # check the effect for both two parameters
                if (virsh_opt.strip().find('--config') != -1 and
                        virsh_opt.strip().find('--live') != -1):
                    result = check_perf_result(vm_name, perf_option, events)
                    if result != "":
                        test.fail("Check domstats output failed for %s"
                                  "with --config --live and no vm restarted" %
                                  result)
                # Event is enabled/disabled immediately when vm is active and
                # "--config" is not used, or otherwise it affects when vm start
                # or restart
                if not vm_active:
                    try:
                        test_vm.start()
                    except virt_vm.VMStartError as info:
                        if str(info).find("unable to enable host cpu") != 1:
                            test.cancel("Some of the events is not supported")
                elif vm_active and virsh_opt.strip().find('--config') != -1:
                    test_vm.destroy()
                    try:
                        test_vm.start()
                    except virt_vm.VMStartError as info:
                        if str(info).find("unable to enable host cpu") != 1:
                            test.cancel("Some of the events is not supported")
                result = check_perf_result(vm_name, perf_option, events)
                if result != "":
                    test.fail("Check domstats output failed for %s" % result)
    finally:
        if test_vm.is_alive():
            test_vm.destroy(gracefully=False)
        backup_xml.sync()
