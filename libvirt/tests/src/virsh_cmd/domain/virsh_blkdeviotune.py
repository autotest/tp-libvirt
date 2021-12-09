import logging
import tempfile


from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import virsh
from virttest import virt_vm
from virttest import remote
from virttest import data_dir

from virttest.utils_test import libvirt


def check_blkdeviotune(params):
    """
    Check block device I/O parameters
    """
    vm_name = params.get("main_vm")
    vm = params.get("vm")
    options = params.get("blkdevio_options")
    device = params.get("device_name", "")
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

    if options and "config" in options and vm.is_alive():
        blkdev_xml = virt_xml_obj.get_blkdevio_params(vm_name, "--inactive")
    else:
        blkdev_xml = virt_xml_obj.get_blkdevio_params(vm_name)

    logging.debug("The parameters are from XML parser %s", blkdev_xml)

    blkdevio_list = ["total_bytes_sec", "read_bytes_sec", "write_bytes_sec",
                     "total_iops_sec", "read_iops_sec", "write_iops_sec",
                     "total_bytes_sec_max", "total_iops_sec_max", "group_name"]

    if vm.is_alive() and (not options or "config" not in options):
        for k in blkdevio_list:
            arg_from_test_input = params.get("blkdevio_" + k)
            arg_from_cmd_output = dicts.get(k)
            logging.debug("output type:%s, output=%s", type(arg_from_cmd_output), arg_from_cmd_output)
            logging.debug("input type:%s, input=%s", type(arg_from_test_input), arg_from_test_input)

            if arg_from_test_input:
                if isinstance(arg_from_cmd_output, int):
                    arg_from_cmd_output = str(arg_from_cmd_output)

                if arg_from_test_input != arg_from_cmd_output:
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


def get_blkdevio_parameter(params, test):
    """
    Get the blkdevio parameters
    @params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    options = params.get("blkdevio_options")
    device = params.get("device_name")

    result = virsh.blkdeviotune(vm_name, device, options=options, debug=True)
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr.strip())
        else:
            test.fail("Unexpected return code %d" % status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr.strip())
        else:
            logging.info(result.stdout.strip())


def set_blkdevio_parameter(params, test):
    """
    Set the blkdevio parameters
    @params: the parameter dictionary
    """
    vm_name = params.get("main_vm")
    device = params.get("device_name")
    options = params.get("blkdevio_options")

    blkdevio_params = {x[9:]: params.get(x) for x in params.keys()
                       if x.startswith('blkdevio_')}
    result = virsh.blkdeviotune(vm_name, device, options=options,
                                params=blkdevio_params, debug=True)
    logging.debug("Guest XML:\n%s", libvirt_xml.VMXML.new_from_dumpxml(vm_name))
    status = result.exit_status
    # Check status_error
    status_error = params.get("status_error", "no")

    if status_error == "yes":
        if status:
            logging.info("It's an expected %s", result.stderr)
        else:
            test.fail("Unexpected return code %d" % status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            if check_blkdeviotune(params):
                logging.info(result.stdout.strip())
            else:
                test.fail("The result is inconsistent between "
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
    attach_disk = "yes" == params.get("attach_disk", "no")
    attach_before_start = "yes" == params.get("attach_before_start", "yes")
    disk_type = params.get("disk_type", 'file')
    disk_format = params.get("disk_format", 'qcow2')
    disk_bus = params.get("disk_bus", 'virtio')
    disk_alias = params.get("disk_alias")
    attach_options = params.get("attach_options")
    slice_test = "yes" == params.get("disk_slice_enabled", "yes")
    test_size = params.get("test_size", "1")

    original_vm_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Used for default device of blkdeviotune
    device = params.get("device_name", "vmblk")
    sys_image_target = vm.get_first_disk_devices()["target"]

    # Make sure vm is down if start not requested
    if (start_vm == "no" or attach_before_start) and vm and vm.is_alive():
        vm.destroy()

    disk_source = tempfile.mktemp(dir=data_dir.get_tmp_dir())
    params["input_source_file"] = disk_source
    params["disk_slice"] = {"slice_test": "yes"}
    if attach_disk and not slice_test:
        libvirt.create_local_disk(disk_type, path=disk_source, size='1',
                                  disk_format=disk_format)
        attach_extra = ""
        if disk_alias:
            attach_extra += " --alias %s" % disk_alias
        if disk_bus:
            attach_extra += " --targetbus %s" % disk_bus
        if disk_format:
            attach_extra += " --subdriver %s" % disk_format
        if attach_options:
            attach_extra += " %s" % attach_options
    test_dict = dict(params)
    test_dict['vm'] = vm
    # Coldplug disk with slice image
    if attach_disk and slice_test and attach_before_start:
        libvirt.create_local_disk(disk_type="file", extra=" -o preallocation=full",
                                  path=disk_source, disk_format="qcow2", size=test_size)
        disk_xml = libvirt.create_disk_xml(params)
        ret = virsh.attach_device(vm_name, disk_xml, flagstr="--config")
        libvirt.check_exit_status(ret)
    # Coldplug disk without slice image
    if attach_disk and attach_before_start and not slice_test:
        ret = virsh.attach_disk(vm_name, disk_source, device,
                                extra=attach_extra, debug=True)
        libvirt.check_exit_status(ret)
    # Recover previous running guest
    if vm and not vm.is_alive() and start_vm == "yes":
        try:
            vm.start()
            vm.wait_for_login().close()
        except (virt_vm.VMError, remote.LoginError) as detail:
            vm.destroy()
            test.fail(str(detail))

    # Hotplug disk with slice image
    if attach_disk and slice_test and not attach_before_start:
        libvirt.create_local_disk(disk_type="file", extra=" -o preallocation=full",
                                  path=disk_source, disk_format="qcow2", size=test_size)
        disk_xml = libvirt.create_disk_xml(params)
        ret = virsh.attach_device(vm_name, disk_xml, flagstr="")
        libvirt.check_exit_status(ret)

    # Hotplug disk without slice image
    if attach_disk and not attach_before_start and not slice_test:
        ret = virsh.attach_disk(vm_name, disk_source, device,
                                extra=attach_extra, debug=True)
        libvirt.check_exit_status(ret)

    if device == "vmblk":
        test_dict['device_name'] = sys_image_target

    # Make sure libvirtd service is running
    if not utils_libvirtd.libvirtd_is_running():
        test.cancel("libvirt service is not running!")

    # Positive and negative testing
    try:
        if change_parameters == "no":
            get_blkdevio_parameter(test_dict, test)
        else:
            set_blkdevio_parameter(test_dict, test)
    finally:
        # Restore guest
        original_vm_xml.sync()
        libvirt.delete_local_disk('file', path=disk_source)
