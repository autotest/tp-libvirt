import logging
import platform

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic
from virttest.utils_test import libvirt
from virttest.libvirt_version import version_compare


def run(test, params, env):
    """
    Test only ppc hosts
    """
    if 'ppc64le' not in platform.machine().lower():
        test.cancel('This case is for ppc only.')
    vm_name = params.get('main_vm', 'EXAMPLE')
    status_error = 'yes' == params.get('status_error', 'no')
    case = params.get('case', '')
    error_msg = params.get('error_msg', '')

    # Backup vm xml
    bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Assign address to panic device
        if case == 'panic_address':

            # Check if there is already a panic device on vm, remove it if true
            origin_panic = vmxml.get_devices('panic')
            if origin_panic:
                for dev in origin_panic:
                    vmxml.del_device(dev)
                vmxml.sync()

            # Create panic device to add to vm
            panic_dev = Panic()
            panic_dev.model = 'pseries'
            panic_dev.addr_type = 'isa'
            panic_dev.addr_iobase = '0x505'
            logging.debug(panic_dev)
            vmxml.add_device(panic_dev)
            if version_compare(7, 0, 0):
                cmd_result = virsh.define(vmxml.xml, debug=True)
            else:
                vmxml.sync()
                cmd_result = virsh.start(vm_name, debug=True, ignore_status=True)

        # Get Ethernet pci devices
        if case == 'unavail_pci_device':
            lspci = process.run('lspci|grep Ethernet', shell=True).stdout_text.splitlines()
            pci_ids = [line.split()[0] for line in lspci]
            logging.debug(pci_ids)
            max_id = max([int(pci_id.split('.')[-1]) for pci_id in pci_ids])
            prefix = pci_ids[-1].split('.')[0]

            # Create fake pci ids
            for i in range(5):
                max_id += 1
                # function must be <= 7
                if max_id > 7:
                    break
                new_pci_id = '.'.join([prefix, str(max_id)])
                new_pci_xml = libvirt.create_hostdev_xml(new_pci_id)
                vmxml.add_device(new_pci_xml)
            vmxml.sync()
            logging.debug('Vm xml after adding unavailable pci devices: \n%s', vmxml)

        # Check result if there's a result to check
        if 'cmd_result' in locals():
            libvirt.check_exit_status(cmd_result, status_error)
            if error_msg:
                libvirt.check_result(cmd_result, [error_msg])

    finally:
        # In case vm disappeared after test
        if case == 'unavail_pci_device':
            virsh.define(bk_xml.xml, debug=True)
        else:
            bk_xml.sync()
