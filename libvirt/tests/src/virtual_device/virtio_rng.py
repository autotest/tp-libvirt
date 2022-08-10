import logging as log

from avocado.utils import process

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.virt_vm import VMStartError
from virttest.staging import service

from provider.virtio_rng import check_points as virtio_provider

logging = log.getLogger('avocado.' + __name__)


def check_attached_rng_device(vm_name, rng_device_dict, remove_keys=None):
    """
    Make sure the XML contains all information required by the test after
    the VM is started.

    :params vm_name: Name of the virtual machine to test
    :params rng_device_dict: Dictionary containing data that should be
    :params remove_keys: Array of keys that should be removed when doing
    the check
    in VM rng
    """

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    virtio_provider.comp_rng_xml(vmxml, rng_device_dict, remove_keys)


def check_detached_rng_device(vm_name, test):
    """
    Checks if VM XML from dump does not contain a RNG device. Should be used
    after RNG device was detached.

    params vm_name: Name of the VM to check.
    params test: Avocado test object
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if "<rng model=" in vmxml:
        logging.debug("Whole VMXML is: %s" % vmxml)
        test.fail("VM still has RNG device after detachment.")


def handle_connection_mode_fail(rng_port):
    """
    Starts a separate process feeding data rng device in vm and restarts vm.

    :param rng_port: Port where we should listen for connections from VM
    :return: Background job feeding data to the opened port
    """
    # The second grep filters the grep process itself, otherwise the cmd would
    # always be successful, because it would match at least one process,
    # the grep process
    cmd = 'ps aux | grep "*. /sbin/rngd -f -r /dev/urandom -o /dev/random" | grep -v grep'
    result = process.run(cmd, shell=True, ignore_status=True)
    if result.exit_status == 0:
        rngd_service = service.Factory.create_service("rngd")
        rngd_service.stop()
    bgjob = utils_misc.AsyncJob(f"cat /dev/urandom | nc -l localhost {rng_port}")
    return bgjob


def create_attach_check_rng_device(vm_name, rng_device_dict, remove_keys=None):
    """
    Creates, attaches, and checks rng device.

    :param vm_name: Name of the VM where the device should be attached.
    :param rng_device_dict: Rng device dictionary from which the device should
    be created
    :param remove_keys:  Array of keys that should be removed from dict
    :return: Rng device that was created according to  rng_device_dict
    """
    rng_dev = libvirt_vmxml.create_vm_device_by_type(
        "rng", rng_device_dict)
    virsh.attach_device(vm_name, rng_dev.xml, flagstr="--config", debug=True)
    check_attached_rng_device(vm_name, rng_device_dict, remove_keys)
    return rng_dev


def do_common_check_after_vm_start(vm_name, rng_dev, rng_device_dict, test, vm):
    """
    Contains most common steps we do after we start the VM.

    :param vm_name: Name of VM we're working with
    :param rng_dev: Random number generator device object
    :param rng_device_dict: A device dict that was used to create rng device in previous step
    :param test: Test object from avocado
    :param vm: VM object from avocado
    """
    ssh_session = vm.wait_for_login()
    virtio_provider.check_guest_dump(ssh_session)
    ssh_session.close()
    virsh.detach_device(vm_name, rng_dev.xml, flagstr="--config")
    check_detached_rng_device(vm_name, test)


def setup_basic(vm):
    """
    Function that prepares a test environment and guest.

    :params vm: The virtual machine object
    """
    if vm.is_alive():
        vm.destroy(gracefully=False)

    libvirt_vmxml.remove_vm_devices_by_type(vm, "rng")


def execute_basic(test, params, vm):
    """
    Function that runs the checks that are in the test

    :params test: The avocado test object
    :params params: Parameters for the test
    :params vm: The VM object
    """
    vm_name = params.get("main_vm")
    rng_device_dict = eval(params.get("rng_device_dict"))
    rng_dev = create_attach_check_rng_device(vm_name, rng_device_dict)

    vm.start()
    check_attached_rng_device(vm_name, rng_device_dict)

    if params.get("backend_dev"):
        virtio_provider.check_host(params.get("backend_dev"))
    do_common_check_after_vm_start(vm_name, rng_dev, rng_device_dict, test, vm)


def cleanup_basic(vmxml_backup, vm):
    """
    Remove the changes made by setup_test function

    :params vmxml_backup: Backup XML to restore
    :params vm: The vm object to remove
    """
    if vm.is_alive():
        vm.destroy(gracefully=False)
    logging.info("Restoring vm...")
    vmxml_backup.sync()


def execute_coldplug_unplug_egd_tcp_connect_mode(test, params, vm):
    """
    Runs check according to connect mode definitions in cfg file.

    :params test: The avocado test object
    :params params: Parameters for the test
    :params vm: The VM object
    """
    vm_name = params.get("main_vm")
    rng_device_dict = eval(params.get("rng_device_dict"))
    feed_process = None

    rng_dev = create_attach_check_rng_device(vm_name, rng_device_dict,
                                             remove_keys=["backend_dev"])

    try:
        vm.start()
    except VMStartError as error:
        logging.debug(f"VMXML during expected failure to start:\n{vm_xml.VMXML.new_from_dumpxml(vm_name)}")
        rng_port = params.get("rng_port")
        if f"Failed to connect to 'localhost:{rng_port}': Connection refused" in str(error):
            feed_process = handle_connection_mode_fail(rng_port)
            vm.start()
        else:
            test.fail(f"Unexpected error during VM startup, error: {error}")
    check_attached_rng_device(vm_name, rng_device_dict, remove_keys=["backend_dev"])
    do_common_check_after_vm_start(vm_name, rng_dev, rng_device_dict, test, vm)

    if feed_process:
        feed_process.kill_func()


def execute_coldplug_unplug_egd_tcp_bind_mode(test, params, vm):
    """
    Runs check according to bind mode definitions in cfg file.

    :params test: The avocado test object
    :params params: Parameters for the test
    :params vm: The VM object
    """
    vm_name = params.get("main_vm")
    rng_device_dict = eval(params.get("rng_device_dict"))
    rng_dev = create_attach_check_rng_device(vm_name, rng_device_dict,
                                             remove_keys=["backend_dev"])

    vm.start()
    check_attached_rng_device(vm_name, rng_device_dict, remove_keys=["backend_dev"])

    do_common_check_after_vm_start(vm_name, rng_dev, rng_device_dict, test, vm)


def run(test, params, env):
    """
    Main function of the test to run, executed by avocado

    :params test: The avocado test object
    :params params: Parameters for the test
    :params env: The avocado test environment object
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    test_case = params.get("test_case", "")
    setup_test = (eval(f"setup_{test_case}") if f"setup_{test_case}"
                  in globals() else setup_basic)
    execute_test = (eval(f"execute_{test_case}") if f"execute_{test_case}"
                    in globals() else execute_basic)
    cleanup_test = (eval(f"cleanup_{test_case}") if f"cleanup_{test_case}"
                    in globals() else cleanup_basic)

    try:
        setup_test(vm)
        execute_test(test, params, vm)
    finally:
        cleanup_test(vmxml_backup, vm)
