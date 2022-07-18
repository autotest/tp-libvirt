import logging as log

from avocado.utils import process

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtio_rng import check_points as virtio_provider

logging = log.getLogger('avocado.' + __name__)
locs = locals()


def check_attached_rng_device(vm_name, rng_device_dict):
    """
    Make sure the XML contains all information required by the test after
    the VM is started.

    :params vm_name: Name of the virtual machine to test
    :params rng_device_dict: Dictionary containing data that should be
    in VM rng
    """

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    virtio_provider.comp_rng_xml(vmxml, rng_device_dict)


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
    Starts a separate process feeding data to /dev/urandom and restarts vm.
    The following code is just a first idea, untested

    :param rng_port: Port where we should listen for connections from VM
    """
    cmd = "/sbin/rngd -f -r /dev/urandom -o /dev/random"
    result = process.run(cmd, shell=True)
    if result.exit_status != 0:
        process.run("systemctl stop rngd.service")
    bgjob = utils_misc.AsyncJob(f"cat /dev/urandom | nc -l localhost -p {rng_port}")
    return bgjob


def create_attach_check_rng_device(vm_name, rng_device_dict):
    """
    Creates, attaches, and checks rng device.

    :param vm_name: Name of the VM where the device should be attached.
    :param rng_device_dict: Rng device dictionary from which the device should
    be created
    """
    rng_dev = libvirt_vmxml.create_vm_device_by_type(
        "rng", rng_device_dict)
    virsh.attach_device(vm_name, rng_dev.xml, flagstr="--config", debug=True)
    check_attached_rng_device(vm_name, rng_device_dict)
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
    check_attached_rng_device(vm_name, rng_device_dict)
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
    rng_dev = libvirt_vmxml.create_vm_device_by_type(
        "rng", rng_device_dict)
    virsh.attach_device(vm_name, rng_dev.xml, flagstr="--config", debug=True)
    check_attached_rng_device(vm_name, rng_device_dict)

    vm.start()
    check_attached_rng_device(vm_name, rng_device_dict)

    ssh_session = vm.wait_for_login()
    if params.get("backend_dev"):
        virtio_provider.check_host(params.get("backend_dev"))
    virtio_provider.check_guest_dump(ssh_session)
    virsh.detach_device(vm_name, rng_dev.xml, flagstr="--config")
    check_detached_rng_device(vm_name, test)
    ssh_session.close()


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


def execute_coldplug_unplug_egd_backend_connect_mode(test, params, vm):
    """
    Runs check according to
    https://polarion.engineering.redhat.com/polarion/#/project/RHELVIRT/workitem?id=RHEL-112411
    in connect mode.

    :params test: The avocado test object
    :params params: Parameters for the test
    :params vm: The VM object
    """
    vm_name = params.get("main_vm")
    rng_device_dict = eval(params.get("rng_device_dict"))
    feed_process = None

    rng_dev = create_attach_check_rng_device(vm_name, rng_device_dict)

    try:
        vm.start()
    except:
        feed_process = handle_connection_mode_fail(params.get("rng_port"))
        vm.start()
    do_common_check_after_vm_start(vm_name, rng_dev, rng_device_dict, test, vm)

    if feed_process:
        feed_process.kill_func()


def execute_coldplug_unplug_egd_backend_bind_mode(test, params, vm):
    """
    Runs check according to
    https://polarion.engineering.redhat.com/polarion/#/project/RHELVIRT/workitem?id=RHEL-112411
    in bind mode.

    :params test: The avocado test object
    :params params: Parameters for the test
    :params vm: The VM object
    """
    vm_name = params.get("main_vm")
    rng_device_dict = eval(params.get("rng_device_dict"))
    rng_dev = create_attach_check_rng_device(vm_name, rng_device_dict)

    vm.start()
    check_attached_rng_device(vm_name, rng_device_dict)

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
    module_fields = dir(__import__(__name__))
    setup_test = (eval(f"setup_{test_case}") if f"setup_{test_case}"
                  in module_fields else setup_basic)
    execute_test = (eval(f"execute_{test_case}") if f"execute_{test_case}"
                    in module_fields else execute_basic)
    cleanup_test = (eval(f"cleanup_{test_case}") if f"cleanup_{test_case}"
                    in module_fields else cleanup_basic)

    try:
        setup_test(vm)
        execute_test(test, params, vm)
    finally:
        cleanup_test(vmxml_backup, vm)
