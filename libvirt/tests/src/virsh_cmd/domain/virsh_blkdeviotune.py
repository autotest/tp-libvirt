import logging

from autotest.client.shared import error

from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import virsh


def check_blkdeviotune(params):
    """
    Check block device I/O parameters
    """
    vm_name = params.get("main_vm")
    vm = params.get("vm")
    options = params.get("blkdevio_options")
    device = params.get("blkdevio_device", "")
    result = virsh.blkdeviotune(vm_name, device)
    dicts = {}
    # Parsing command output and putting them into python dictionary.
    cmd_output = result.stdout.strip().splitlines()
    for l in cmd_output:
        k, v = l.split(':')
        if v.strip().isdigit():
            dicts[k.strip()] = int(v.strip())
        else:
            dicts[k.strip()] = v.strip()

    logging.debug("The arguments are from test input %s", dicts)

    virt_xml_obj = libvirt_xml.vm_xml.VMXML(virsh_instance=virsh)

    if "config" in options and vm.is_alive():
        blkdev_xml = virt_xml_obj.get_blkdevio_params(vm_name, "--inactive")
    else:
        blkdev_xml = virt_xml_obj.get_blkdevio_params(vm_name)

    logging.debug("The parameters are from XML parser %s", blkdev_xml)

    blkdevio_list = ["total_bytes_sec", "read_bytes_sec", "write_bytes_sec",
                     "total_iops_sec", "read_iops_sec", "write_iops_sec"]

    if vm.is_alive() and "config" not in options:
        for k in blkdevio_list:
            arg_from_test_input = params.get("blkdevio_" + k)
            arg_from_cmd_output = dicts.get(k)
            if (arg_from_test_input and
                    int(arg_from_test_input) != arg_from_cmd_output):
                logging.error("To expect <%s=%s>",
                              arg_from_test_input, arg_from_cmd_output)
                return False
    else:
        for k in blkdevio_list:
            arg_from_test_input = params.get("blkdevio_" + k)
            arg_from_xml_output = blkdev_xml.get(k)
            if (arg_from_test_input and
                    int(arg_from_test_input) != arg_from_xml_output):
                logging.error("To expect <%s=%s>",
                              arg_from_test_input, arg_from_xml_output)
                return False
    return True


def get_blkdevio_parameter(params):
    """
    Get the blkdevio parameters
    @params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    options = params.get("blkdevio_options")
    device = params.get("blkdevio_device")

    result = virsh.blkdeviotune(vm_name, device, options=options, debug=True)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            raise error.TestFail("Unexpected return code %d" % status)
    elif status_error == "no":
        if status:
            raise error.TestFail(result.stderr)
        else:
            logging.info(result.stdout)


def set_blkdevio_parameter(params):
    """
    Set the blkdevio parameters
    @params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    total_bytes_sec = params.get("blkdevio_total_bytes_sec")
    read_bytes_sec = params.get("blkdevio_read_bytes_sec")
    write_bytes_sec = params.get("blkdevio_write_bytes_sec")
    total_iops_sec = params.get("blkdevio_total_iops_sec")
    read_iops_sec = params.get("blkdevio_read_iops_sec")
    write_iops_sec = params.get("blkdevio_write_iops_sec")
    device = params.get("blkdevio_device")
    options = params.get("blkdevio_options")

    result = virsh.blkdeviotune(vm_name, device,
                                options, total_bytes_sec,
                                read_bytes_sec, write_bytes_sec,
                                total_iops_sec, read_iops_sec,
                                write_iops_sec, debug=True)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            raise error.TestFail("Unexpected return code %d" % status)
    elif status_error == "no":
        if status:
            raise error.TestFail(result.stderr)
        else:
            if check_blkdeviotune(params):
                logging.info(result.stdout)
            else:
                raise error.TestFail("The result is inconsistent between "
                                     "test input and command/XML output")


def run(test, params, env):
    """
    Test blkdevio tuning

    Positive test has covered the following combination.
    -------------------------
    | total | read  | write |
    -------------------------
    |   0   |   0   |   0   |
    | non-0 |   0   |   0   |
    |   0   | non-0 | non-0 |
    |   0   | non-0 |  0    |
    |   0   |   0   | non-0 |
    -------------------------

    Negative test has covered unsupported combination and
    invalid command arguments.

    NB: only qemu-kvm-rhev supports block I/O throttling on >= RHEL6.5,
    the qemu-kvm is okay for block I/O throttling on >= RHEL7.0.
    """

    # Run test case
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    start_vm = params.get("start_vm", "yes")
    change_parameters = params.get("change_parameters", "no")
    original_vm_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Used for default device of blkdeviotune
    device = params.get("blkdevio_device", "vmblk")
    sys_image_target = vm.get_first_disk_devices()["target"]

    # Make sure vm is down if start not requested
    if start_vm == "no" and vm and vm.is_alive():
        vm.destroy()

    # Recover previous running guest
    if vm and not vm.is_alive() and start_vm == "yes":
        vm.start()

    test_dict = dict(params)
    test_dict['vm'] = vm
    if device == "vmblk":
        test_dict['blkdevio_device'] = sys_image_target

    # Make sure libvirtd service is running
    if not utils_libvirtd.libvirtd_is_running():
        raise error.TestNAError("libvirt service is not running!")

    # Positive and negative testing
    try:
        if change_parameters == "no":
            get_blkdevio_parameter(test_dict)
        else:
            set_blkdevio_parameter(test_dict)
    finally:
        # Restore guest
        original_vm_xml.sync()
