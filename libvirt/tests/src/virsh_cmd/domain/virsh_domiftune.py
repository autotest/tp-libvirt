import logging
import re

from virttest import virsh
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def check_domiftune(params, test_clear):
    """
    Compare inbound and outbound value with guest XML configuration
    and virsh command output.
    :params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    vm = params.get("vm")
    interface = params.get("iface_dev")
    options = params.get("options")
    inbound = params.get("inbound", "")
    outbound = params.get("outbound", "")
    set_clear = "yes" == params.get("set_clear", "no")
    test_outbound = "yes" == params.get("test_outbound", "no")
    test_inbound = "yes" == params.get("test_inbound", "no")
    status_error = 'yes' == params.get("status_error", "no")
    average_inbound_from_cmd_output = None
    average_outbound_from_cmd_output = None
    peak_inbound_from_cmd_output = None
    peak_outbound_from_cmd_output = None
    burst_inbound_from_cmd_output = None
    burst_outbound_from_cmd_output = None
    average_inbound_from_xml = None
    average_outbound_from_xml = None
    peak_inbound_from_xml = None
    peak_outbound_from_xml = None
    burst_inbound_from_xml = None
    burst_outbound_from_xml = None

    logging.debug("Checking inbound=%s outbound= %s", inbound, outbound)
    # get inbound and outbound parameters from virsh cmd setting
    if not status_error and test_inbound:
        in_list = re.findall(r'[0-9]+', inbound)
        inbound_average = int(in_list[0])
        if len(in_list) == 3:
            inbound_peak = int(in_list[1])
            inbound_burst = int(in_list[2])
    if not status_error and test_outbound:
        out_list = re.findall(r'[0-9]+', outbound)
        outbound_average = int(out_list[0])
        if len(out_list) == 3:
            outbound_peak = int(out_list[1])
            outbound_burst = int(out_list[2])

    # --config affect next boot
    if options == "config":
        vm.destroy()
        vm.start()

    # get inbound and outbound parameters from virsh cmd output
    if vm and vm.is_alive():
        result = virsh.domiftune(vm_name, interface, options=options)
        dicts = {}
        o = result.stdout.strip().split("\n")
        for l in o:
            if l and l.find(':'):
                k, v = l.split(':')
                dicts[k.strip()] = v.strip()

        logging.debug(dicts)
        average_inbound_from_cmd_output = int(dicts['inbound.average'])
        average_outbound_from_cmd_output = int(dicts['outbound.average'])
        peak_inbound_from_cmd_output = int(dicts['inbound.peak'])
        peak_outbound_from_cmd_output = int(dicts['outbound.peak'])
        burst_inbound_from_cmd_output = int(dicts['inbound.burst'])
        burst_outbound_from_cmd_output = int(dicts['outbound.burst'])

        logging.debug("inbound and outbound from cmd output:")
        logging.debug("inbound: %s,%s,%s; outbound: %s,%s,%s"
                      % (average_inbound_from_cmd_output, peak_inbound_from_cmd_output,
                         burst_inbound_from_cmd_output, average_outbound_from_cmd_output,
                         peak_outbound_from_cmd_output, burst_outbound_from_cmd_output))

    # get inbound and outbound parameters from vm xml
    virt_xml_obj = vm_xml.VMXML(virsh_instance=virsh)

    if options == "config" and vm and vm.is_alive():
        domiftune_params = virt_xml_obj.get_iftune_params(
            vm_name, "--inactive")
    elif vm and not vm.is_alive():
        logging.debug("The guest %s isn't running!", vm_name)
        return True
    else:
        domiftune_params = virt_xml_obj.get_iftune_params(vm_name)

    try:
        logging.debug("test inbound is %s, test outbound is %s" %
                      (test_inbound, test_outbound))
        if test_inbound:
            average_inbound_from_xml = int(domiftune_params.get("inbound").get("average"))
            peak_inbound_from_xml = int(domiftune_params.get("inbound").get("peak"))
            burst_inbound_from_xml = int(domiftune_params.get("inbound").get("burst"))
            logging.debug("inbound from xml:")
            logging.debug("%s, %s, %s" % (average_inbound_from_xml, peak_inbound_from_xml,
                                          burst_inbound_from_xml))
        if test_outbound:
            average_outbound_from_xml = int(domiftune_params.get("outbound").get("average"))
            peak_outbound_from_xml = int(domiftune_params.get("outbound").get("peak"))
            burst_outbound_from_xml = int(domiftune_params.get("outbound").get("burst"))
            logging.debug("outbound from xml:")
            logging.debug("%s, %s, %s" % (average_outbound_from_xml, peak_outbound_from_xml,
                                          burst_outbound_from_xml))
    except AttributeError as details:
        logging.error("Error in get inbound/outbound average: %s", details)
    logging.debug("average_inbound_from_xml=%s, average_outbound_from_xml=%s",
                  average_inbound_from_xml, average_outbound_from_xml)

    if vm and vm.is_alive():
        if test_clear and set_clear and test_inbound:
            if average_inbound_from_xml is not None or \
               average_inbound_from_cmd_output != 0 or \
               peak_inbound_from_cmd_output != 0 or \
               burst_inbound_from_cmd_output != 0:
                logging.error("Inbound was not cleared, xml=%s "
                              "avg=%s peak=%s burst=%s",
                              average_inbound_from_xml,
                              average_inbound_from_cmd_output,
                              peak_inbound_from_cmd_output,
                              burst_inbound_from_cmd_output)
                return False
            else:
                return True
        if test_clear and set_clear and test_outbound:
            if average_outbound_from_xml is not None or \
               average_outbound_from_cmd_output != 0 or \
               peak_outbound_from_cmd_output != 0 or \
               burst_outbound_from_cmd_output != 0:
                logging.error("Outbound was not cleared, xml=%s "
                              "avg=%s peak=%s burst=%s",
                              average_outbound_from_xml,
                              average_outbound_from_cmd_output,
                              peak_outbound_from_cmd_output,
                              burst_outbound_from_cmd_output)
                return False
            else:
                return True
        if test_inbound and (inbound_average != average_inbound_from_cmd_output
                             or inbound_peak != peak_inbound_from_cmd_output
                             or inbound_burst != burst_inbound_from_cmd_output):
            logging.error("To expect inbound %s: but got {average: %s, peak:"
                          " %s, burst: %s} from cmd output", inbound,
                          average_inbound_from_cmd_output, peak_inbound_from_cmd_output,
                          burst_inbound_from_cmd_output)
            return False
        if test_inbound and (inbound_average != average_inbound_from_xml
                             or inbound_peak != peak_inbound_from_xml
                             or inbound_burst != burst_inbound_from_xml):
            logging.error("To expect inbound %s: but got {average: %s, peak:"
                          " %s, burst: %s} from xml", inbound,
                          average_inbound_from_xml, peak_inbound_from_xml,
                          burst_inbound_from_xml)
            return False
        if test_outbound and (outbound_average != average_outbound_from_cmd_output
                              or outbound_peak != peak_outbound_from_cmd_output
                              or outbound_burst != burst_outbound_from_cmd_output):
            logging.error("To expect outbound %s: but got {average: %s, peak:"
                          " %s, burst: %s} from cmd output", outbound,
                          average_outbound_from_cmd_output, peak_outbound_from_cmd_output,
                          burst_outbound_from_cmd_output)
            return False
        if test_outbound and (outbound_average != average_outbound_from_xml or
                              outbound_peak != peak_outbound_from_xml or
                              outbound_burst != burst_outbound_from_xml):
            logging.error("To expect outbound %s: but got {average: %s, peak:"
                          " %s, burst: %s} from xml", outbound,
                          average_outbound_from_xml, peak_outbound_from_xml,
                          burst_outbound_from_xml)
            return False

    return True


def check_libvirtd(test, libvirtd):
    """
    Check if libvirtd is running and restart to ensure available for cleanup.

    :param test: test instance
    :param libvirtd: libvirt daemon instance
    """
    if not libvirtd.is_running():
        try:
            libvirtd.start()
        finally:
            test.fail('Libvirtd crashed after running `virsh domiftune`')


def get_domiftune_parameter(params, test, libvirtd):
    """
    Get the domiftune parameters
    :params: the parameter dictionary
    :param test: test instance
    :param libvirtd: libvirt daemon instance
    """
    vm_name = params.get("main_vm")
    options = params.get("options")
    interface = params.get("iface_dev")

    result = virsh.domiftune(vm_name, interface, options=options)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            check_libvirtd(test, libvirtd)
            logging.info("It's an expected error: %s", result.stderr)
        else:
            test.fail("%d not a expected command return value" %
                      status)
    elif status_error == "no":
        if status:
            test.fail("Unexpected result, get domiftune: %s" %
                      result.stderr)
        else:
            logging.info("getdominfo return succesfully")


def set_domiftune_parameter(params, test, libvirtd):
    """
    Set the domiftune parameters
    :params: the parameter dictionary
    :param test: test instance
    :param libvirtd: libvirt daemon instance
    """
    vm_name = params.get("main_vm")
    inbound = params.get("inbound", "")
    outbound = params.get("outbound", "")
    outbound_new = params.get("outbound_new")
    options = params.get("options", None)
    interface = params.get("iface_dev")
    check_clear = params.get("check_clear", "no")
    status_error = params.get("status_error", "no")

    test_clear = False
    if check_clear == "yes":
        # In libvirt 1.2.3, commit id '14973382' it will now be possible
        # to pass a zero (0) as an inbound or outbound parameter to virsh
        # domiftune in order to clear all the settings found. So we'll
        # handle that difference here
        if libvirt_version.version_compare(1, 2, 3):
            test_clear = True
            # Although the .cfg file has "0" for these that will
            # not test whether we can clear the value. So let's
            # set it to "1", then after we are sure we can set it
            # we will clear it and check that it's clear
            if inbound:
                save_inbound = inbound
                # average,peak,burst
                inbound = "2,4,7"
                params['inbound'] = "2,4,7"
            if outbound:
                save_outbound = outbound
                # average,peak,burst
                outbound = "2,4,7"
                params['outbound'] = "2,4,7"
        else:
            # Prior to libvirt 1.2.3 this would be an error
            # So let's just treat it as such. Leaving the
            # inbound/outbound as zero should result in an
            # error on the following set, but a pass for
            # the test since the error is expected.
            status_error = "yes"
    if libvirt_version.version_compare(7, 3, 0) and outbound_new:
        outbound = outbound_new
    result = virsh.domiftune(vm_name, interface, options, inbound, outbound, debug=True)
    status = result.exit_status

    if status_error == "yes":
        if status:
            check_libvirtd(test, libvirtd)
            logging.info("It's an expected error: %s", result.stderr)
        else:
            test.fail("%d not a expected command return value" %
                      status)
    elif status_error == "no":
        if status:
            test.fail("Unexpected set domiftune error: %s" %
                      result.stderr)
        else:
            logging.debug("set domiftune successfully!!!")
            if not check_domiftune(params, False):
                test.fail("The 'inbound' or/and 'outbound' are"
                          " inconsistent with domiftune XML"
                          " and/or virsh command output")

    # If supported, then here's where we reset the inbound/outbound
    # back to what they were input as and then run the same domiftune
    # command.  That should result in a successful return and should
    # clear the parameter.
    if test_clear:
        params['set_clear'] = 'yes'
        if inbound:
            inbound = save_inbound
            params['inbound'] = save_inbound
        if outbound:
            outbound = save_outbound
            params['outbound'] = save_outbound
        result = virsh.domiftune(vm_name, interface, options, inbound, outbound)
        status = result.exit_status
        if status:
            test.fail("Unexpected failure when clearing: %s" %
                      result.stderr)
        else:
            logging.debug("clear the inbound/outbound successfully!!!")
            params['set_clear'] = "yes"
            if not check_domiftune(params, True):
                test.fail("The 'inbound' or/and 'outbound' were "
                          "not cleared.")


def run(test, params, env):
    """
    Test domiftune tuning

    1) Positive testing
       1.1) get the current domiftune parameters for a running guest
       1.2) set the current domiftune parameters for a running guest
    2) Negative testing
       2.1) get domiftune parameters
       2.2) set domiftune parameters
    """

    # Run test case
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    status_error = params.get("status_error", "no")
    start_vm = params.get("start_vm", "yes")
    change_parameters = params.get("change_parameters", "no")
    interface_ref = params.get("interface_ref", "name")

    interface = []

    if vm and not vm.is_alive():
        vm.start()

    if vm and vm.is_alive():
        virt_xml_obj = vm_xml.VMXML(virsh_instance=virsh)
        interface = virt_xml_obj.get_iface_dev(vm_name)
        if_mac = interface[0]
        # Get interface name
        vmxml = virt_xml_obj.new_from_dumpxml(vm_name)
        if_node = vmxml.get_iface_all().get(if_mac)
        if_name = if_node.find('target').get('dev')

    if interface_ref == "name":
        interface = if_name

    if interface_ref == "mac":
        interface = if_mac

    logging.debug("the interface is %s", interface)

    test_dict = dict(params)
    test_dict['vm'] = vm
    if interface:
        test_dict['iface_dev'] = interface

    if start_vm == "no" and vm and vm.is_alive():
        vm.destroy()

    # positive and negative testing #########
    libvirtd = utils_libvirtd.Libvirtd()
    if change_parameters == "no":
        get_domiftune_parameter(test_dict, test, libvirtd)
    else:
        set_domiftune_parameter(test_dict, test, libvirtd)

    if change_parameters != "no":
        ret = virsh.domiftune(vm_name, interface, 'current', '0', '0', debug=True)
        libvirt.check_exit_status(ret)
