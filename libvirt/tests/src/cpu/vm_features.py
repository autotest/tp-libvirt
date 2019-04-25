from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider import libvirt_version


def run(test, params, env):
    """
    Test vm features
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    hyperv_attr = eval(params.get('hyperv_attr', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:

        # Set hyperv attribs if there're attribs to set
        if hyperv_attr:
            if set(hyperv_attr.keys()).intersection(('tlbflush',
                                                     'frequencies',
                                                     'reenlightenment')):

                # Compare libvirt version to decide if test is valid
                if not libvirt_version.version_compare(5, 0, 0):
                    test.cancel('This version of libvirt does\'nt support '
                                'the setting: %r' % hyperv_attr)

            vm_xml.VMXML.set_vm_features(
                vm_name,
                **{'hyperv_%s_state' % key: value
                   for key, value in hyperv_attr.items()}
            )

            # Test vm start
            ret = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(ret)
            vm.wait_for_login().close()

            # Check hyperv settings in qemu command line
            for attr in hyperv_attr:
                libvirt.check_qemu_cmd_line('hv_' + attr)

    finally:
        bkxml.sync()
