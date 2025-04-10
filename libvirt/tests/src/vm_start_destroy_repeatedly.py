import logging
from virttest import test_setup
from virttest import virsh
from virttest import utils_misc
from virttest.staging import utils_memory
from virttest.libvirt_xml import vm_xml


def power_cycle_vm(test, vm, vm_name, login_timeout, startup_wait, resume_wait):
    """
    Cycle vm through start-login-shutdown loop

    :param vm: vm object
    :param vm_name: vm name
    :param login_timeout: timeout given to vm.wait_for_login
    :param startup_wait: how long to wait after the vm has been started
    :param resume_wait: how long to wait after the paused vm is resumed

    This tests the vm startup and destroy sequences by:
        1) Starting the vm
        2) Verifying the vm is alive
        3) Logging into vm
        4) Logging out of the vm
        5) Destroying the vm
    """

    virsh.start(vm_name, options="--paused", ignore_status=False)
    utils_misc.wait_for(lambda: vm.state() == "paused", startup_wait)

    virsh.resume(vm_name, ignore_status=False)
    utils_misc.wait_for(lambda: vm.state() == "running", resume_wait)

    session = vm.wait_for_login(timeout=login_timeout)
    session.close()

    virsh.shutdown(vm_name, ignore_status=False)

    utils_misc.wait_for(lambda: vm.state() == "shut off", 360)
    if vm.state() != "shut off":
        test.fail("Failed to shutdown VM")


def setup_hugepage(vm, params):
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    memory_amount = int(vm_attrs["memory"])

    #Reserve memory for hugepages
    hugepage_size = utils_memory.get_huge_page_size()
    hugepage_nr = int(memory_amount) / hugepage_size

    config_params = params.copy()
    config_params["mem"] = memory_amount
    config_params["target_hugepages"] = hugepage_nr
    hpc = test_setup.HugePageConfig(config_params)
    hpc.setup()

    #Prepare VM XML
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    backup_xml = vmxml.copy()

    #Remove old memory tags
    vmxml.xmltreefile.remove_by_xpath("/memory")
    vmxml.xmltreefile.remove_by_xpath("/currentMemory")

    #Include memory backing
    mb_xml = vm_xml.VMMemBackingXML()
    mb_params = eval(params.get("mb_params", "{}"))
    mb_xml.setup_attrs(**mb_params)
    vmxml.setup_attrs(**vm_attrs)
    vmxml.mb = mb_xml

    #The relevant bug only appears if disk cache='none'
    xmltreefile = vmxml.__dict_get__('xml')
    disk_nodes = xmltreefile.find("devices").findall("disk")
    qcow_disk = [disk for disk in disk_nodes if disk.find("driver").get("type") == "qcow2"][0]
    if qcow_disk.find("driver").get("cache") != "none":
        qcow_disk.find("driver").set("cache", "none")

    vmxml.xmltreefile.write()
    vmxml.sync()
    logging.info("New XML for Hugepage testing: {}".format(vmxml))

    return hugepage_nr, backup_xml, hpc


def check_hugepage_status(test, hugepage_nr):
    if hugepage_nr != utils_memory.get_num_huge_pages():
        test.fail("Total number of hugepages does not match. Expected: {}. Actual: {}"
                  .format(hugepage_nr, utils_memory.get_num_huge_pages()))
    if hugepage_nr != utils_memory.get_num_huge_pages_free():
        test.fail("Number of free huge pages does not match. Expected: {}. Actual: {}"
                  .format(hugepage_nr, utils_memory.get_num_huge_pages_free()))
    if utils_memory.get_num_huge_pages_rsvd() != 0:
        test.fail("Huge pages still reserved. Expected: 0. Actual: {}"
                  .format(utils_memory.get_num_huge_pages_rsvd()))


def run(test, params, env):
    """
    Test qemu-kvm startup reliability

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    num_cycles = int(params.get("num_cycles"))                  # Parameter to control the number of times to start/restart the vm
    login_timeout = float(params.get("login_timeout", 240))     # Controls vm.wait_for_login() timeout
    startup_wait = float(params.get("startup_wait", 240))       # Controls wait time for virsh.start()
    resume_wait = float(params.get("resume_wait", 240))          # Controls wait for virsh.resume()
    hugepage_check = bool(params.get("check_hugepage_status", False))
    vm_memory = int(params.get("vm_memory", 8388608))

    backup_xml = None
    hugepage_nr = None
    hpc = None
    if hugepage_check:
        hugepage_nr, backup_xml, hpc = setup_hugepage(vm, params)
        logging.info("hugepage_nr: {}".format(hugepage_nr))

    try:
        for i in range(num_cycles):
            logging.info("Starting vm '%s' -- attempt #%d", vm_name, i+1)

            power_cycle_vm(test, vm, vm_name, login_timeout, startup_wait, resume_wait)

            logging.info("Completed vm '%s' power cycle #%d", vm_name, i+1)
    finally:
        if hugepage_check:
            check_hugepage_status(test, hugepage_nr)
            backup_xml.sync()
            hpc.cleanup()
