import logging
from avocado.core import exceptions
from virttest.libvirt_xml import vm_xml


LOG = logging.getLogger('avocado.' + __name__)


def comp_hostdev_xml(vm, device_type, hostdev_dict):
    """
    Compare the first hostdev xml of the VM

    :param vm: VM object
    :param device_type: device type
    :param hostdev_dict: hostdev parameters dict
    :raise: TestFail if comparison fails
    """
    vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
        .devices.by_device_tag(device_type)[0]
    LOG.debug("hostdev XML: %s", vm_hostdev)
    vm_hostdev_dict = vm_hostdev.fetch_attrs()
    for attr, value in hostdev_dict.items():
        if attr == 'source':
            vm_hostdev_val = vm_hostdev_dict.get(attr).get('untyped_address')
            value = hostdev_dict[attr]['untyped_address']
        elif attr in ['address', 'hostdev_address']:
            vm_hostdev_val = vm_hostdev_dict.get(attr).get('attrs')
            value = hostdev_dict[attr]['attrs']
        else:
            vm_hostdev_val = vm_hostdev_dict.get(attr)

        if vm_hostdev_val != value:
            raise exceptions.TestFail('hostdev xml(%s) comparison failed.'
                                      'Expected "%s",got "%s".'
                                      % (attr, value, vm_hostdev_val))
