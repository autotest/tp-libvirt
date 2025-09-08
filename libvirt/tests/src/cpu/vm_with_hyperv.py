import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test start VM with hyper-v related features
    """
    vm_name = params.get('main_vm')

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vmxml.copy()
    hv_attrs = eval(params.get('hv_attrs', '{}'))
    clock_attrs = eval(params.get('clock_attrs', '{}'))
    expect_qemu_cmd = 'yes' == params.get('expect_qemu_cmd', 'no')
    qemu_cmd = params.get('qemu_cmd')

    try:
        # Configure Hyper-V features
        features = vmxml.features
        features.setup_attrs(hyperv=hv_attrs)
        vmxml.features = features

        # Configure clock settings if specified
        if clock_attrs:
            vmxml.setup_attrs(clock=clock_attrs)

        vmxml.sync()

        virsh.start(vm_name, **VIRSH_ARGS)
        libvirt.check_qemu_cmd_line(qemu_cmd, expect_exist=expect_qemu_cmd)

    finally:
        bkxml.sync()
