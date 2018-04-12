import re
import logging

from avocado.utils import process

from virttest import utils_config
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test set_process_name parameter in qemu.conf.

    1) Change set_process_name in qemu.conf;
    2) Restart libvirt daemon;
    3) Check if libvirtd successfully started;
    4) Check if qemu command line changed accordingly;
    """
    def get_qemu_command_name_option(vm):
        """
        Get the name option of qemu command line of a libvirt VM.

        :param vm: A libvirt_vm.VM class instance.
        :return :  A string containing '-name' option of VM's qemu command
                   line or None if error.
        """
        if vm.is_dead():
            vm.start()

        # Get qemu command line.
        pid = vm.get_pid()
        res = process.run("ps -p %s -o cmd h" % pid, shell=True)

        if res.exit_status == 0:
            match = re.search(r'-name\s*(\S*)', res.stdout_text.strip())
            if match:
                return match.groups()[0]

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    expected_result = params.get("expected_result", "name_not_set")
    set_process_name = params.get("set_process_name", "not_set")
    vm = env.get_vm(vm_name)

    # Get old qemu -name option.
    orig_qemu_name = get_qemu_command_name_option(vm)
    logging.debug('Original "-name" option of qemu command is '
                  '"%s".' % orig_qemu_name)

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        if set_process_name == 'not_set':
            del config.set_process_name
        else:
            config.set_process_name = set_process_name

        # Restart libvirtd to make change valid.
        if not libvirtd.restart():
            if expected_result != 'unbootable':
                test.fail('Libvirtd is expected to be started '
                          'with set_process_name = '
                          '%s' % set_process_name)
            return
        if expected_result == 'unbootable':
            test.fail('Libvirtd is not expected to be started '
                      'with set_process_name = '
                      '%s' % set_process_name)

        # Restart VM to create a new qemu command line.
        if vm.is_alive():
            vm.destroy()
        vm.start()

        # Get new qemu -name option.
        new_qemu_name = get_qemu_command_name_option(vm)
        logging.debug('New "-name" option of qemu command is '
                      '"%s"' % new_qemu_name)

        if ',process=qemu:%s' % vm_name in new_qemu_name:
            if expected_result == 'name_not_set':
                test.fail('Qemu name is not expected to set, '
                          'but %s found' % new_qemu_name)
        else:
            if expected_result == 'name_set':
                test.fail('Qemu name is expected to set, '
                          'but %s found' % new_qemu_name)
    finally:
        config.restore()
        libvirtd.restart()
