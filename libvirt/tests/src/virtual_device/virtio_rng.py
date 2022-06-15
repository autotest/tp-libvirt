import logging as log

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtio_rng import check_points as virtio_provider

logging = log.getLogger('avocado.' + __name__)


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


def setup_test(vm):
    """
    Function that prepares a test environment and guest.

    :params vm: The virtual machine object
    """
    if vm.is_alive():
        vm.destroy(gracefully=False)

    libvirt_vmxml.remove_vm_devices_by_type(vm, "rng")


def execute_test(test, params, vm):
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
    virtio_provider.check_host(params.get("backend_dev"))
    virtio_provider.check_guest_dump(ssh_session)
    virsh.detach_device(vm_name, rng_dev.xml, flagstr="--config")
    check_detached_rng_device(vm_name, test)
    ssh_session.close()


def cleanup_test(vmxml_backup, vm):
    """
    Remove the changes made by setup_test function

    :params vmxml_backup: Backup XML to restore
    :params vm: The vm object to remove
    """
    if vm.is_alive():
        vm.destroy(gracefully=False)
    logging.info("Restoring vm...")
    vmxml_backup.sync()


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

    try:
        setup_test(vm)
        execute_test(test, params, vm)
    finally:
        cleanup_test(vmxml_backup, vm)
