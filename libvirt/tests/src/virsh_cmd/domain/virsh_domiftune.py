import logging

from autotest.client.shared import error

from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider import libvirt_version


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
    inbound_from_cmd_output = None
    outbound_from_cmd_output = None
    peak_in_from_cmd_output = None
    peak_out_from_cmd_output = None
    burst_in_from_cmd_output = None
    burst_out_from_cmd_output = None
    inbound_from_xml = None
    outbound_from_xml = None

    logging.debug("Checking inbound=%s outbound=%s", inbound, outbound)
    if vm and vm.is_alive():
        result = virsh.domiftune(vm_name, interface, options=options)
        dicts = {}
        o = result.stdout.strip().split("\n")
        for l in o:
            if l and l.find(':'):
                k, v = l.split(':')
                dicts[k.strip()] = v.strip()

        logging.debug(dicts)
        inbound_from_cmd_output = dicts['inbound.average']
        outbound_from_cmd_output = dicts['outbound.average']
        logging.debug("inbound_from_cmd_output=%s, outbound_from_cmd_output=%s",
                      inbound_from_cmd_output, outbound_from_cmd_output)
        peak_in_from_cmd_output = dicts['inbound.peak']
        peak_out_from_cmd_output = dicts['outbound.peak']
        burst_in_from_cmd_output = dicts['inbound.peak']
        burst_out_from_cmd_output = dicts['outbound.peak']

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
        inbound_from_xml = domiftune_params.get("inbound").get("average")
        outbound_from_xml = domiftune_params.get("outbound").get("average")
    except AttributeError, details:
        logging.error("Error in get inbound/outbound average: %s", details)
    logging.debug("inbound_from_xml=%s, outbound_from_xml=%s",
                  inbound_from_xml, outbound_from_xml)

    if vm and vm.is_alive() and options != "config":
        if test_clear and inbound:
            if inbound_from_xml is not None or \
               inbound_from_cmd_output is not "0" or \
               peak_in_from_cmd_output is not "0" or \
               burst_in_from_cmd_output is not "0":
                logging.error("Inbound was not cleared, xml=%s "
                              "avg=%s peak=%s burst=%s",
                              inbound_from_xml,
                              inbound_from_cmd_output,
                              peak_in_from_cmd_output,
                              burst_in_from_cmd_output)
                return False
        if test_clear and outbound:
            if outbound_from_xml is not None or \
               outbound_from_cmd_output is not "0" or \
               peak_out_from_cmd_output is not "0" or \
               burst_out_from_cmd_output is not "0":
                logging.error("Outbound was not cleared, xml=%s "
                              "avg=%s peak=%s burst=%s",
                              outbound_from_xml,
                              outbound_from_cmd_output,
                              peak_out_from_cmd_output,
                              burst_out_from_cmd_output)
        if test_clear:
            return True
        if inbound and inbound != inbound_from_cmd_output:
            logging.error("To expect inbound %s: %s", inbound,
                          inbound_from_cmd_output)
            return False
        if outbound and outbound != outbound_from_cmd_output:
            logging.error("To expect inbound %s: %s", outbound,
                          outbound_from_cmd_output)
            return False
        if inbound and inbound_from_xml and inbound != inbound_from_xml:
            logging.error("To expect outbound %s: %s", inbound,
                          inbound_from_xml)
            return False
        if outbound and outbound_from_xml and outbound != outbound_from_xml:
            logging.error("To expect outbound %s: %s", outbound,
                          outbound_from_xml)
            return False

    return True


def get_domiftune_parameter(params):
    """
    Get the domiftune parameters
    :params: the parameter dictionary
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
            logging.info("It's an expected error: %s", result.stderr)
        else:
            raise error.TestFail("%d not a expected command return value" %
                                 status)
    elif status_error == "no":
        if status:
            raise error.TestFail("Unexpected result, get domiftune: %s" %
                                 result.stderr)
        else:
            logging.info("getdominfo return succesfully")


def set_domiftune_parameter(params):
    """
    Set the domiftune parameters
    :params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    inbound = params.get("inbound", "")
    outbound = params.get("outbound", "")
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
                inbound = "1,4,6"
                params['inbound'] = "1"
            if outbound:
                save_outbound = outbound
                # average,peak,burst
                outbound = "1,4,6"
                params['outbound'] = "1"
        else:
            # Prior to libvirt 1.2.3 this would be an error
            # So let's just treat it as such. Leaving the
            # inbound/outbound as zero should result in an
            # error on the following set, but a pass for
            # the test since the error is expected.
            status_error = "yes"

    result = virsh.domiftune(vm_name, interface, options, inbound, outbound)
    status = result.exit_status

    if status_error == "yes":
        if status:
            logging.info("It's an expected error: %s", result.stderr)
        else:
            raise error.TestFail("%d not a expected command return value" %
                                 status)
    elif status_error == "no":
        if status:
            raise error.TestFail("Unexpected set domiftune error: %s" %
                                 result.stderr)
        else:
            if not check_domiftune(params, False):
                raise error.TestFail("The 'inbound' or/and 'outbound' are"
                                     " inconsistent with domiftune XML"
                                     " and/or virsh command output")

    # If supported, then here's where we reset the inbound/outbound
    # back to what they were input as and then run the same domiftune
    # command.  That should result in a successful return and should
    # clear the parameter.
    if test_clear:
        if inbound:
            inbound = save_inbound
            params['inbound'] = save_inbound
        if outbound:
            outbound = save_outbound
            params['outbound'] = save_outbound
        result = virsh.domiftune(vm_name, interface, options, inbound, outbound)
        status = result.exit_status
        if status:
            raise error.TestFail("Unexpected failure when clearing: %s" %
                                 result.stderr)
        else:
            if not check_domiftune(params, True):
                raise error.TestFail("The 'inbound' or/and 'outbound' were "
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
    interface = []

    if vm and vm.is_alive():
        virt_xml_obj = vm_xml.VMXML(virsh_instance=virsh)
        interface = virt_xml_obj.get_iface_dev(vm_name)

    test_dict = dict(params)
    test_dict['vm'] = vm
    if interface:
        test_dict['iface_dev'] = interface[0]

    if start_vm == "no" and vm and vm.is_alive():
        vm.destroy()

    # positive and negative testing #########

    if status_error == "no":
        if change_parameters == "no":
            get_domiftune_parameter(test_dict)
        else:
            set_domiftune_parameter(test_dict)

    if status_error == "yes":
        if change_parameters == "no":
            get_domiftune_parameter(test_dict)
        else:
            set_domiftune_parameter(test_dict)
