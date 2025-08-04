import logging

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test Define VM with wrong cpu topology
    """
    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get('main_vm')

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vmxml.copy()
    tmp_vm_name = 'tmp-vm-' + utils_misc.generate_random_string(3)
    vcpu = int(params.get('vcpu'))
    cpu_attrs = eval(params.get('cpu_attrs', {}))
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')

    try:
        tmp_vmxml = vmxml.copy()
        tmp_vmxml.setup_attrs(cpu=cpu_attrs)
        tmp_vmxml.vcpu = vcpu

        tmp_vmxml.vm_name = tmp_vm_name
        tmp_vmxml.del_uuid()
        osxml = tmp_vmxml.os
        os_attrs = osxml.fetch_attrs()
        for k, v in list(os_attrs.items()):
            if k.startswith('nvram'):
                os_attrs.pop(k)
        new_os = vm_xml.VMOSXML()
        new_os.setup_attrs(**os_attrs)
        tmp_vmxml.os = new_os

        tmp_vmxml.xmltreefile.write()
        logging.debug(tmp_vmxml)

        result = virsh.create(tmp_vmxml.xml, debug=True)
        libvirt.check_exit_status(result, status_error)
        libvirt.check_result(result, err_msg)

    finally:
        bkxml.sync()
