import logging
import re

from avocado.utils import process

from virttest import libvirt_cgroup
from virttest import libvirt_xml
from virttest import utils_disk
from virttest import utils_libvirtd
from virttest import virsh

from virttest.staging import utils_cgroup
from virttest.utils_misc import get_dev_major_minor


def check_blkiotune(test, params):
    """
    To compare weight and device-weights value with guest XML
    configuration, virsh blkiotune output and corresponding
    blkio.weight and blkio.weight_device value from cgroup.

    :param test: the test handle
    :param params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    vm = params.get("vm")
    options = params.get("options", None)
    weight = params.get("blkio_weight", "")
    cgconfig = params.get("cgconfig", "on")
    device_weights = params.get("blkio_device_weights", "")
    result = virsh.blkiotune(vm_name)
    dicts = {}
    # Parsing command output and putting them into python dictionary.
    cmd_output = result.stdout.strip().splitlines()
    for l in cmd_output:
        k, v = l.split(':')
        dicts[k.strip()] = v.strip()

    logging.debug("The blkiotune result from virsh command is:\n%s", dicts)

    virt_xml_obj = libvirt_xml.vm_xml.VMXML(virsh_instance=virsh)

    # To change a running guest with 'config' option, which will affect
    # next boot, if don't shutdown the guest, we need to run virsh dumpxml
    # with 'inactive' option to get guest XML changes.
    if options == "config" and vm and vm.is_alive():
        blkio_params = virt_xml_obj.get_blkio_params(vm_name, "--inactive")
    else:
        blkio_params = virt_xml_obj.get_blkio_params(vm_name)

    logging.debug("The blkiotune result from guest xml is:\n%s", blkio_params)

    device_weights_from_xml = ""
    weight_from_cgroup = ""
    device_weight_from_cgroup = ""

    weight_from_xml = blkio_params.get("weight", "")
    device_weights_path_from_xml = blkio_params.get("device_weights_path")
    device_weights_weight_from_xml = blkio_params.get("device_weights_weight")
    weight_from_cmd_output = dicts['weight']
    device_weights_from_cmd_output = dicts['device_weight']

    # The device-weights is a single string listing, in the format
    # of /path/to/device,weight
    if device_weights_path_from_xml and device_weights_weight_from_xml:
        device_weights_from_xml = device_weights_path_from_xml + "," + \
                                  device_weights_weight_from_xml

    dev_num = None
    if device_weights:
        dev = device_weights.split(',')[0]
        (major, minor) = get_dev_major_minor(dev)
        dev_num = str(major) + ":" + str(minor)
        device_weights_tmp = device_weights.split(',')[1]

    # To get guest corresponding blkio.weight and blkio.weight_device value
    # from blkio controller of the cgroup.
    if cgconfig == "on" and vm.is_alive():
        blkio_params_from_cgroup = get_blkio_params_from_cgroup(params)
        weight_from_cgroup = blkio_params_from_cgroup.get('weight')
        if weight_from_cgroup.count('default,'):
            weight_from_cgroup = weight_from_cgroup.split(',')[1]
            logging.debug("Changed weight_from_cgroup=%s\n", weight_from_cgroup)
        if dev_num:
            device_weight_from_cgroup = blkio_params_from_cgroup.get(dev_num).get('weight_device')

    # To check specified weight and device_weight value with virsh command
    # output and/or blkio.weight and blkio.weight_device value from blkio
    # controller of the cgroup.
    if vm.is_alive() and options != "config":
        if (weight and weight != weight_from_cmd_output or weight and weight != weight_from_cgroup):
            logging.error("To expect weight %s: %s",
                          weight, weight_from_cmd_output)
            return False
        if (device_weights and device_weights != device_weights_from_cmd_output or device_weights and
                device_weights_tmp != device_weight_from_cgroup):
            # The value 0 to remove that device from per-device listings.
            if (device_weights.split(',')[-1] == '0' and not device_weights_from_cmd_output):
                return True
            else:
                logging.error("To expect device_weights %s: %s",
                              device_weights, device_weights_from_cmd_output)
                return False
    else:
        if weight and weight != weight_from_xml:
            logging.error("To expect weight %s: %s", weight, weight_from_xml)
            return False
        if (device_weights and device_weights_from_xml and device_weights != device_weights_from_xml):
            logging.error("To expect device_weights %s: %s",
                          device_weights, device_weights_from_xml)
            return False

    return True


def get_blkio_params_from_cgroup(params):
    """
    Get a list of domain-specific per block stats from cgroup blkio controller.

    :param params: the parameter dictionary
    """
    vm = params.get("vm")
    vm_pid = vm.get_pid()
    cgtest = libvirt_cgroup.CgroupTest(vm_pid)
    blkio_params_from_cgroup = cgtest.get_standardized_cgroup_info(virsh_cmd='blkiotune')
    logging.debug("The blkio values from cgroup is :'%s'", blkio_params_from_cgroup)
    return blkio_params_from_cgroup


def get_blkio_parameter(test, params, cgstop):
    """
    Get the blkio parameters

    :param test: the test handle
    :param params: the parameter dictionary
    :param cgstop: the status of cgconfig
    """
    vm_name = params.get("main_vm")
    options = params.get("options")

    result = virsh.blkiotune(vm_name, options=options)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            if cgstop:
                test.fail("Unexpected return code %d" % status)
            else:
                logging.info("Control groups stopped, thus expected success")
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            logging.info(result.stdout)


def set_blkio_parameter(test, params, cgstop):
    """
    Set the blkio parameters

    :param test: the test handle
    :param params: the parameter dictionary
    :param cgstop: the status of cgconfig
    """
    vm_name = params.get("main_vm")
    weight = params.get("blkio_weight")
    device_weights = params.get("blkio_device_weights")
    options = params.get("options")

    result = virsh.blkiotune(vm_name, weight, device_weights, options=options, debug=True)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            if cgstop:
                test.fail("Unexpected return code %d" % status)
            else:
                logging.info("Control groups stopped, thus expected success")
    elif status_error == "no":
        is_cfq = params.get('iosche_for_test') == 'cfq'
        if status and not is_cfq and device_weights:
            logging.info("Set/get device weight is only supported for cfq."
                         " It's an expected %s", result.stderr)
        elif status:
            test.fail(result.stderr)
        else:
            if check_blkiotune(test, params):
                logging.info(result.stdout)
            else:
                test.fail("The 'weight' or/and 'device-weights' are"
                          " inconsistent with blkiotune XML or/and"
                          " blkio.weight and blkio.weight_device"
                          " value from cgroup blkio controller")


def prepare_scheduler(params, test, vm):
    """
    1. Save old scheduler for test tear down
    2. Set scheduler for test or cancel test if not supported
    3. Return test parameter dictionary
    :param params: test parameters
    :param test: test instance
    :param vm: test vm instance
    :return: dictionary of test parameters enriched with scheduler dynamic parameters
    """
    test_dict = dict(params)
    test_dict['vm'] = vm

    schedulerfd = params.get('schedulerfd')
    cmd = "cat %s" % schedulerfd
    iosche = process.run(cmd, shell=True).stdout_text
    logging.debug("iosche value is:%s", iosche)
    test_dict['oldmode'] = re.findall(r"\[(.*?)\]", iosche)[0]

    iosche_for_test = ""
    with open(schedulerfd, 'w') as scf:
        if 'cfq' in iosche:
            iosche_for_test = 'cfq'
        elif 'bfq' in iosche:
            iosche_for_test = 'bfq'
        else:
            test.fail('Unknown scheduler in %s' % schedulerfd)
        scf.write(iosche_for_test)
    test_dict['iosche_for_test'] = iosche_for_test
    return test_dict


def run(test, params, env):
    """
    Test blkio tuning

    1) Positive testing
       1.1) get the current blkio parameters for a running/shutoff guest
       1.2) set the current blkio parameters for a running/shutoff guest
    2) Negative testing
       2.1) get blkio parameters for a running/shutoff guest
       2.2) set blkio parameters running/shutoff guest
    """

    # Run test case
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    cg = utils_cgroup.CgconfigService()
    cgconfig = params.get("cgconfig", "on")
    libvirtd = params.get("libvirtd", "on")
    start_vm = params.get("start_vm", "yes")
    status_error = params.get("status_error", "no")
    change_parameters = params.get("change_parameters", "no")
    original_vm_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    first_disk = utils_disk.get_first_disk()
    schedulerfd = params.get('schedulerfd') % first_disk
    params['schedulerfd'] = schedulerfd
    blkio_device_weights = params.get('blkio_device_weights')
    if blkio_device_weights and blkio_device_weights.count('%s'):
        params['blkio_device_weights'] = params.get('blkio_device_weights') % first_disk

    # Make sure vm is down if start not requested
    if start_vm == "no" and vm and vm.is_alive():
        vm.destroy()

    test_dict = prepare_scheduler(params, test, vm)

    # positive and negative testing
    cgstop = False
    try:
        if start_vm == "yes" and not vm.is_alive():
            vm.start()
            vm.wait_for_login()
        if status_error == "no":
            if change_parameters == "no":
                get_blkio_parameter(test, test_dict, cgstop)
            else:
                set_blkio_parameter(test, test_dict, cgstop)
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
                    test.fail("libvirt service is not running!")

        # Recover previous running guest
        if (cgconfig == "off" and libvirtd == "restart" and not vm.is_alive() and start_vm == "yes"):
            vm.start()
        if status_error == "yes":
            if change_parameters == "no":
                get_blkio_parameter(test, test_dict, cgstop)
            else:
                set_blkio_parameter(test, test_dict, cgstop)
    finally:
        # Restore guest
        original_vm_xml.sync()

        with open(schedulerfd, 'w') as scf:
            scf.write(test_dict['oldmode'])

        # If we stopped cg, then recover and refresh libvirtd to recognize
        if cgstop:
            cg.cgconfig_start()
            utils_libvirtd.libvirtd_restart()
