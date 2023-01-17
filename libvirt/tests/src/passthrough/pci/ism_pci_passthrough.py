import logging as log

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML


logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test for ISM device passthrough to libvirt guest.
    """
    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pci_dev = params.get("pci_dev", "pci_0000_00_00_0")
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
        if "ism" != pci_xml.driver_name:
            test.error("Device %s is not a ISM device: %s" % pci_xml)
        pci_address = pci_xml.cap.get_address_dict()
        vmxml.add_hostdev(pci_address)
        vmxml.sync()

        vm.start()
        session = vm.wait_for_login()

        output = session.cmd_output("lspci")
        devices = output.split('\n')
        if not len(devices) >= 1 or "ISM" not in devices[0]:
            test.fail("Expected 1 ISM PCI device but got: %s" % output)
    finally:
        if session:
            session.close()
        vm.destroy()
        backup_xml.sync()
