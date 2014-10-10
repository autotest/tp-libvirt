import re
import logging
from virttest import utils_config
from virttest import utils_libvirtd
from autotest.client import utils
from autotest.client.shared import error


def run(test, params, env):
    """
    Test seccomp_sandbox parameter in qemu.conf.

    1) Change seccomp_sandbox in qemu.conf;
    2) Restart libvirt daemon;
    3) Check if libvirtd successfully started;
    4) Check if qemu command line changed accordingly;
    """
    def get_qemu_command_sandbox_option(vm):
        """
        Get the sandbox option of qemu command line of a libvirt VM.

        :param vm: A libvirt_vm.VM class instance.
        :return :  A string containing '-sandbox' option of VM's qemu command
                   line or None if not found.
        """
        if vm.is_dead():
            vm.start()

        # Get qemu command line.
        pid = vm.get_pid()
        res = utils.run("ps -p %s -o cmd h" % pid)

        if res.exit_status == 0:
            match = re.search(r'-sandbox\s*(\S*)', res.stdout)
            if match:
                return match.groups()[0]

    vm_name = params.get("main_vm", "virt-tests-vm1")
    expected_result = params.get("expected_result", "not_set")
    seccomp_sandbox = params.get("seccomp_sandbox", "not_set")
    vm = env.get_vm(vm_name)

    # Get old qemu -sandbox option.
    orig_qemu_sandbox = get_qemu_command_sandbox_option(vm)
    logging.debug('Original "-sandbox" option of qemu command is '
                  '"%s".' % orig_qemu_sandbox)

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        if seccomp_sandbox == 'not_set':
            del config.seccomp_sandbox
        else:
            config.seccomp_sandbox = seccomp_sandbox

        # Restart libvirtd to make change valid.
        if not libvirtd.restart():
            if expected_result != 'unbootable':
                raise error.TestFail('Libvirtd is expected to be started '
                                     'with seccomp_sandbox = '
                                     '%s' % seccomp_sandbox)
            return
        if expected_result == 'unbootable':
            raise error.TestFail('Libvirtd is not expected to be started '
                                 'with seccomp_sandbox = '
                                 '%s' % seccomp_sandbox)

        # Restart VM to create a new qemu command line.
        if vm.is_alive():
            vm.destroy()
        vm.start()

        # Get new qemu -sandbox option.
        new_qemu_sandbox = get_qemu_command_sandbox_option(vm)
        logging.debug('New "-sandbox" option of qemu command is '
                      '"%s"' % new_qemu_sandbox)

        if new_qemu_sandbox is None:
            if expected_result != 'not_set':
                raise error.TestFail('Qemu sandbox option is expected to set '
                                     'but %s found', new_qemu_sandbox)
        else:
            if expected_result != new_qemu_sandbox:
                raise error.TestFail('Qemu sandbox option is expected to be '
                                     '%s, but %s found' % (
                                         expected_result, new_qemu_sandbox))
    finally:
        config.restore()
        libvirtd.restart()
