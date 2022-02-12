import logging as log
import re

from avocado.core import exceptions
from avocado.utils import distro

from virttest import libvirt_version
from virttest import utils_package
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def install_pkgs(session, pkgs, test):
    """
    Install packages within the vm

    :param session: vm session
    :param pkgs: str or list, package names to install
    :param test: the test object
    :raises: test.error if installation fails
    """
    if pkgs:
        if not utils_package.package_install(pkgs, session=session):
            test.error("Fail to install package '%s'" % pkgs)


def check_cmd_in_guest(cmd_in_guest, vm_session, params, test):
    """
    Execute a command within the guest and do checking

    :param cmd_in_guest: str, commands to execute in the VM
    :param vm_session: aexpect session for the VM
    :param params: dict, parameters to use
                  hidden_attr's value should be a dict
    :param test: test object
    :raises: test.fail if checkpoints fail
    """
    _, output = utils_misc.cmd_status_output(cmd_in_guest,
                                             shell=True,
                                             ignore_status=False,
                                             verbose=True,
                                             session=vm_session)
    logging.debug("Command '%s' result: %s", cmd_in_guest, output)
    hidden_attr = params.get('hidden_attr')
    if hidden_attr:
        (repl, found) = ('not', True) if hidden_attr['kvm_hidden_state'] == 'on' else ('', False)
        if output.count('KVM') == found:
            test.fail("'KVM' is %s expected when state is "
                      "%s" % (repl, hidden_attr['kvm_hidden_state']))
    logging.debug("Checking in check_cmd_in_guest() is successful.")


def run(test, params, env):
    """
    Test vm features
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    hyperv_attr = eval(params.get('hyperv_attr', '{}'))
    pmu_attr = eval(params.get('pmu_attr', '{}'))
    pvspinlock_attr = eval(params.get('pvspinlock_attr', '{}'))
    kvm_poll_control_attr = eval(params.get('kvm_poll_control_attr', '{}'))
    hidden_attr = eval(params.get('hidden_attr', '{}'))
    qemu_include = params.get('qemu_include', '')
    qemu_exclude = params.get('qemu_exclude', '')
    cmd_in_guest = params.get('cmd_in_guest')
    pkgs = params.get('pkgs')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm_session = None
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

        if kvm_poll_control_attr:
            if not libvirt_version.version_compare(6, 10, 0):
                test.cancel('This version of libvirt does not support'
                            ' kvm poll-control')

        # Set feature attrs
        test_attrs = [pmu_attr, pvspinlock_attr, kvm_poll_control_attr, hidden_attr]
        [vm_xml.VMXML.set_vm_features(vm_name, **fea_attr)
         for fea_attr in test_attrs if fea_attr]

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
        vm_session = vm.wait_for_login()
        install_pkgs(vm_session, pkgs, test)
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
        if qemu_include:
            libvirt.check_qemu_cmd_line(qemu_include)
        if qemu_exclude:
            if libvirt.check_qemu_cmd_line(qemu_exclude, err_ignore=True):
                test.fail('Unexpected "%s" was found '
                          'in qemu command line' % qemu_exclude)
        if cmd_in_guest:
            cmd_params = {'hidden_attr': hidden_attr}
            check_cmd_in_guest(cmd_in_guest, vm_session, cmd_params, test)

    finally:
        if vm_session:
            vm_session.close()
        bkxml.sync()
