import re

from avocado.core import exceptions
from avocado.utils import distro

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from virttest import libvirt_version


def run(test, params, env):
    """
    Test vm features
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    hyperv_attr = eval(params.get('hyperv_attr', '{}'))
    pmu_attr = eval(params.get('pmu_attr', '{}'))
    pvspinlock_attr = eval(params.get('pvspinlock_attr', '{}'))

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
        if pmu_attr:
            vm_xml.VMXML.set_vm_features(
                vm_name,
                **{'pmu': pmu_attr['pmu']}
            )

        if pvspinlock_attr:
            vm_xml.VMXML.set_vm_features(
                vm_name,
                **{'pvspinlock_state': pvspinlock_attr['pvspinlock_state']}
            )

        # Test vm start
        try:
            ret = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(ret)
        except exceptions.TestFail as details:
            if re.search(r"host doesn\'t support paravirtual spinlocks",
                         str(details)):
                test.cancel("This host doesn't support paravirtual spinlocks.")
            else:
                test.fail('VM failed to start:\n%s' % details)
        vm.wait_for_login().close()

        if hyperv_attr:
            # Check hyperv settings in qemu command line
            for attr in hyperv_attr:
                if libvirt_version.version_compare(5, 6, 0):
                    exp_str = 'hv-' + attr
                else:
                    exp_str = 'hv_' + attr
                if hyperv_attr[attr] == 'off':
                    if libvirt.check_qemu_cmd_line(exp_str, True):
                        test.fail("Unexpected '%s' was found in "
                                  "qemu command line" % exp_str)
                else:
                    libvirt.check_qemu_cmd_line(exp_str)

        if pmu_attr:
            libvirt.check_qemu_cmd_line('pmu=' + pmu_attr['pmu'])

        if pvspinlock_attr:
            if distro.detect().name == 'rhel' and int(distro.detect().version) < 8:
                if pvspinlock_attr['pvspinlock_state'] == 'on':
                    exp_str = r'\+kvm_pv_unhalt'
                else:
                    exp_str = r'\-kvm_pv_unhalt'
            else:
                exp_str = 'kvm-pv-unhalt=' + pvspinlock_attr['pvspinlock_state']

            libvirt.check_qemu_cmd_line(exp_str)

    finally:
        bkxml.sync()
