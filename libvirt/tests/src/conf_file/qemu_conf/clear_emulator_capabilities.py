import logging

from virttest import utils_config
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test clear_emulator_capabilities parameter in qemu.conf.

    1) Change clear_emulator_capabilities in qemu.conf;
    2) Restart libvirt daemon;
    3) Check if libvirtd successfully started;
    4) Check if qemu process capabilities changed accordingly;
    """
    def get_qemu_process_caps(vm):
        """
        Get the capabilities sets of qemu process of a libvirt VM from proc.

        The raw format of capabilities sets in /proc/${PID}/status is:

        set_name   cap_str
          |           |
          V           V
        CapInh: 0000000000000000
        CapPrm: 0000001fffffffff
        CapEff: 0000001fffffffff
        CapBnd: 0000001fffffffff
        CapAmb: 0000000000000000

        :param vm: A libvirt_vm.VM class instance.
        :return :  A dict using set_name as keys and integer converted from
                   hex cap_str as values.
        :raise cancel: If can not get capabilities from proc.
        """
        if vm.is_dead():
            vm.start()

        pid = vm.get_pid()
        with open('/proc/%s/status' % pid) as proc_stat_fp:
            caps = {}
            for line in proc_stat_fp:
                if line.startswith('Cap'):
                    set_name, cap_str = line.split(':\t')
                    caps[set_name] = int(cap_str, 16)

        if len(caps) == 5:
            return caps
        else:
            test.cancel('Host do not support capabilities or '
                        'an error has occured.')

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    expected_result = params.get("expected_result", "name_not_set")
    user = params.get("qemu_user", "not_set")
    clear_emulator_capabilities = params.get(
        "clear_emulator_capabilities", "not_set")
    vm = env.get_vm(vm_name)

    # Get old qemu process cap sets.
    orig_qemu_caps = get_qemu_process_caps(vm)
    logging.debug('Original capabilities sets of qemu process is: ')
    for s in orig_qemu_caps:
        cap_int = orig_qemu_caps[s]
        logging.debug('%s: %s(%s)' % (s, hex(cap_int), cap_int))

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        if user == 'not_set':
            del config.user
        else:
            config.user = user

        if clear_emulator_capabilities == 'not_set':
            del config.clear_emulator_capabilities
        else:
            config.clear_emulator_capabilities = clear_emulator_capabilities

        # Restart libvirtd to make change valid.
        if not libvirtd.restart():
            if expected_result != 'unbootable':
                test.fail('Libvirtd is expected to be started '
                          'with clear_emulator_capabilities = '
                          '%s' % clear_emulator_capabilities)
            return
        if expected_result == 'unbootable':
            test.fail('Libvirtd is not expected to be started '
                      'with clear_emulator_capabilities = '
                      '%s' % clear_emulator_capabilities)

        # Restart VM to create a new qemu process.
        if vm.is_alive():
            vm.destroy()
        vm.start()

        # Get new qemu process cap sets
        new_qemu_caps = get_qemu_process_caps(vm)
        logging.debug('New capabilities sets of qemu process is: ')
        for s in new_qemu_caps:
            cap_int = new_qemu_caps[s]
            logging.debug('%s: %s(%s)' % (s, hex(cap_int), cap_int))

        eff_caps = new_qemu_caps['CapEff']
        if eff_caps == 0:
            if expected_result != 'dropped':
                test.fail(
                    'Qemu process capabilities is not expected to be dropped, '
                    'but CapEff == %s found' % hex(eff_caps))
        else:
            if expected_result == 'dropped':
                test.fail(
                    'Qemu process capabilities is expected to be dropped, '
                    'but CapEff == %s found' % hex(eff_caps))
    finally:
        config.restore()
        libvirtd.restart()
