import logging

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test vm boot with nvram source
    """
    def test_file():
        """
        Test file backed source
        """
        os_xml = vmxml.os
        LOG.debug('Os xml before test: %s', os_xml)

        fd_file = os_xml.nvram
        LOG.debug('Nvram file: %s', fd_file)

        os_nvram_attrs = {'nvram_attrs':
                          {'template': os_xml.nvram_attrs['template'],
                           'type': 'file'},
                          'nvram_source': {'attrs': {'file': fd_file}}}
        os_xml.del_nvram()
        os_xml.setup_attrs(**os_nvram_attrs)
        LOG.debug('Updated os xml: %s', os_xml)
        vmxml.os = os_xml
        # Destroy vm to avoid console confusion
        vm.destroy()
        vmxml.sync()

        vm.start()
        LOG.debug('VM xml after reboot: %s', virsh.dumpxml(vm_name).stdout_text)

        session = vm.wait_for_serial_login()
        libvirt.check_cmd_output(cmd='dmesg | grep secure',
                                 content='Secure boot enabled',
                                 session=session)

    # Variables assignment
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    case = params.get('case', '')
    bk_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Get test function
    test_func = eval('test_%s' % case)

    try:
        libvirt_version.is_libvirt_feature_supported(params)
        test_func()

    finally:
        bk_vmxml.sync()
