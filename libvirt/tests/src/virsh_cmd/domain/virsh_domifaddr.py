import logging

from virttest import virsh, utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test virsh domifaddr command
    """
    vm_name = params.get('main_vm')
    test_vm_name = params.get('test_vm_name', vm_name)
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    virsh_options = params.get('virsh_options', '')
    guest_agent_status = params.get('guest_agent_status', '')
    bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        if guest_agent_status:
            if not vm.is_alive():
                vm.start()
            ga_start = True if guest_agent_status == 'start' else False
            session = vm.wait_for_login()
            if not utils_package.package_install('qemu-guest-agent', session, 60):
                test.fail("Failed to install 'qemu-guest-agent' in guest OS.")
            session.close()
            vm.set_state_guest_agent(ga_start)
        virsh_result = virsh.domifaddr(test_vm_name, options=virsh_options,
                                       debug=True)
        libvirt.check_exit_status(virsh_result, status_error)
        if status_error:
            libvirt.check_result(virsh_result, expected_fails=error_msg)

    finally:
        bk_xml.sync()
