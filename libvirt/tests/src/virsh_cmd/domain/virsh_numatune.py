import logging

from avocado.utils import path as utils_path
from avocado.utils import process

from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.cpu import cpus_parser
from virttest.libvirt_xml.xcepts import LibvirtXMLAccessorError
from virttest.staging import utils_cgroup


def check_numatune_xml(params):
    """
    Compare mode and nodeset value with guest XML configuration
    :params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    mode = params.get("numa_mode", "")
    nodeset = params.get("numa_nodeset", "")
    options = params.get("options", "")
    # --config option will act after vm shutdown.
    if options == "config":
        virsh.shutdown(vm_name)
    # The verification of the numa params should
    # be done when vm is running.
    if not virsh.is_alive(vm_name):
        virsh.start(vm_name)

    try:
        numa_params = libvirt_xml.VMXML.get_numa_memory_params(vm_name)
    # VM XML omit numa entry when the placement is auto and mode is strict
    # So we need to set numa_params manually when exception happens.
    except LibvirtXMLAccessorError:
        numa_params = {'placement': 'auto', 'mode': 'strict'}

    if not numa_params:
        logging.error("Could not get numa parameters for %s", vm_name)
        return False

    mode_from_xml = numa_params['mode']
    # if the placement is auto, there is no nodeset in numa param.
    try:
        nodeset_from_xml = numa_params['nodeset']
    except KeyError:
        nodeset_from_xml = ""

    if mode and mode != mode_from_xml:
        logging.error("To expect %s: %s", mode, mode_from_xml)
        return False

    # The actual nodeset value is different with guest XML configuration,
    # so need to compare them via a middle result, for example, if you
    # set nodeset is '0,1,2' then it will be a list '0-2' in guest XML
    nodeset = cpus_parser(nodeset)
    nodeset_from_xml = cpus_parser(nodeset_from_xml)

    if nodeset and nodeset != nodeset_from_xml:
        logging.error("To expect %s: %s", nodeset, nodeset_from_xml)
        return False

    return True


def get_numa_parameter(test, params, cgstop):
    """
    Get the numa parameters
    :params: the parameter dictionary
    :cgstop: whether cg were stopped prior to get
    """
    vm_name = params.get("main_vm")
    options = params.get("options", None)
    result = virsh.numatune(vm_name, options=options)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            logging.info("It's an expected error")
        else:
            # If we stopped control groups, then we expect a different
            # result in this failure case; however, if there were no
            # control groups to stop, then don't error needlessly
            if not cgstop:
                test.fail("Unexpected return code %d" % status)
            else:
                logging.info("Control groups stopped, thus expected success")
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            logging.info(result.stdout.strip())


def set_numa_parameter(test, params, cgstop):
    """
    Set the numa parameters
    :params: the parameter dictionary
    :cgstop: whether cg were stopped prior to get
    """
    vm_name = params.get("main_vm")
    mode = params.get("numa_mode")
    nodeset = params.get("numa_nodeset")
    options = params.get("options", None)
    start_vm = params.get("start_vm", "yes")
    exceed_num = "yes" == params.get("exceed_num", "no")

    # Get host numa node list
    host_numa_node = utils_misc.NumaInfo()
    node_list = host_numa_node.online_nodes_withmem
    logging.debug("host online nodes with memory %s", node_list)

    # Get host numa node number if exceed_num
    if exceed_num:
        nodeset = str(len(host_numa_node.all_nodes) + 1)
        params['numa_nodeset'] = nodeset

    # Get original numatune memory mode
    ori_mode = ''
    ori_numatune = {}
    if libvirt_xml.VMXML.new_from_dumpxml(vm_name).xmltreefile.find('numatune'):
        ori_numatune = libvirt_xml.VMXML.get_numa_memory_params(vm_name)
        ori_mode = ori_numatune['mode'] if 'mode' in ori_numatune else ''

    # Don't use libvirt_xml here because testing numatune command
    result = virsh.numatune(vm_name, mode, nodeset, options, debug=True)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    # For a running domain, the mode can't be changed, and the nodeset can
    # be changed only the domain was started with a mode of 'strict' which
    # should be the same with original mode
    if ori_mode == mode and (ori_numatune.get('nodeset') == nodeset or not nodeset):
        status_error = "no"
    if mode == "strict" and start_vm == "yes":
        status_error = "no"
    if ori_mode and ori_mode != mode and start_vm == "yes":
        status_error = "yes"

    # TODO, the '--config' option will affect next boot, and if the guest
    # is shutoff status, the '--current' option will be equivalent to
    # '--config', if users give a specified nodeset range is more than
    # host NUMA nodes, and use virsh numatune with '--config' or '--current'
    # option to set the invalid nodeset to a guest with shutoff status, and
    # then virsh numatune will return 0 rather than 1, because the libvirt just
    # check it when starting the guest, however, the current virsh.start()
    # can't meet this requirement.

    if status_error == "yes":
        if status:
            logging.info("It's an expected error")
        else:
            # If we stopped control groups, then we expect a different
            # result in this failure case; however, if there were no
            # control groups to stop, then don't error needlessly
            if not cgstop:
                test.fail("Unexpected return code %d" % status)
            else:
                logging.info("Control groups stopped, thus expected success")
    elif status_error == "no":
        if status:
            used_node = cpus_parser(nodeset)
            if not set(used_node).issubset(node_list):
                test.cancel("Host does not support requested"
                            " nodeset %s" % used_node)
            else:
                test.fail(result.stderr)
        else:
            if check_numatune_xml(params):
                logging.info(result.stdout.strip())
            else:
                test.fail("The 'mode' or/and 'nodeset' are"
                          " inconsistent with numatune XML")


def dynamic_node_replacement(params, numa_info, test_obj):
    """
    Replace numa node parameters dynamically per current system configuration, but only in required tests

    :param numa_info: available numa node info from avocado-vt/utils_misc
    :param params: all params passed to test
    :param test_obj: test object - for cancel case
    """
    node_list = numa_info.get_online_nodes_withmem()
    dynamic_nodeset = params.get('dynamic_nodeset', 'no') == 'yes'
    if dynamic_nodeset and 'numa_nodeset' in params:
        params['numa_nodeset'] = ','.join([str(elem) for elem in node_list])
        logging.debug('The parameter "numa_nodeset" from config file is going to be replaced by: {} '
                      'available on this system'.format(params['numa_nodeset']))


def run(test, params, env):
    """
    Test numa tuning

    1) Positive testing
       1.1) get the current numa parameters for a running/shutoff guest
       1.2) set the current numa parameters for a running/shutoff guest
           1.2.1) set valid 'mode' parameters
           1.2.2) set valid 'nodeset' parameters
    2) Negative testing
       2.1) get numa parameters
           2.1.1) invalid options
           2.1.2) stop cgroup service
       2.2) set numa parameters
           2.2.1) invalid 'mode' parameters
           2.2.2) invalid 'nodeset' parameters
           2.2.3) change 'mode' for a running guest and 'mode' is not 'strict'
           2.2.4) change 'nodeset' for running guest with mode of 'interleave'
                  'interleave' or 'preferred' numa mode
           2.2.5) stop cgroup service
    """

    try:
        utils_path.find_command("numactl")
    except utils_path.CmdNotFoundError:
        test.cancel("Command 'numactl' is missing. You must "
                    "install it.")

    # Run test case
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    original_vm_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    host_numa_node = utils_misc.NumaInfo()
    dynamic_node_replacement(params, host_numa_node, test)
    cg = utils_cgroup.CgconfigService()
    status_error = params.get("status_error", "no")
    libvirtd = params.get("libvirtd", "on")
    cgconfig = params.get("cgconfig", "on")
    start_vm = params.get("start_vm", "no")
    change_parameters = params.get("change_parameters", "no")

    # Make sure vm is down if start not requested
    if start_vm == "no" and vm.is_alive():
        vm.destroy()

    # positive and negative testing #########

    ori_pid_libvirtd = process.getoutput("pidof libvirtd")
    cgstop = False
    try:
        if status_error == "no":
            if change_parameters == "no":
                get_numa_parameter(test, params, cgstop)
            else:
                set_numa_parameter(test, params, cgstop)
        if cgconfig == "off":
            # If running, then need to shutdown a running guest before
            # stopping cgconfig service and will start the guest after
            # restarting libvirtd service
            if cg.cgconfig_is_running():
                if vm.is_alive():
                    vm.destroy()
                cg.cgconfig_stop()
            cgstop = True

        # If we stopped cg, then refresh libvirtd service
        # to get latest cgconfig service change; otherwise,
        # if no cg change restart of libvirtd is pointless
        if cgstop and libvirtd == "restart":
            try:
                utils_libvirtd.libvirtd_restart()
            finally:
                # Not running is not a good thing, but it does happen
                # and it will affect other tests
                if not utils_libvirtd.libvirtd_is_running():
                    test.cancel("libvirt service is not running!")

        # Recover previous running guest
        if (cgconfig == "off" and libvirtd == "restart" and
                not vm.is_alive() and start_vm == "yes"):
            vm.start()
        if status_error == "yes":
            if change_parameters == "no":
                get_numa_parameter(test, params, cgstop)
            else:
                set_numa_parameter(test, params, cgstop)

            aft_pid_libvirtd = process.getoutput("pidof libvirtd")
            if libvirtd == "on":
                if not utils_libvirtd.libvirtd_is_running() or ori_pid_libvirtd != aft_pid_libvirtd:
                    test.fail("Libvirtd crash after numatune operation")
    finally:
        # Restore guest
        original_vm_xml.sync()

        # If we stopped cg, then recover and refresh libvirtd to recognize
        if cgstop:
            cg.cgconfig_start()
            utils_libvirtd.libvirtd_restart()
