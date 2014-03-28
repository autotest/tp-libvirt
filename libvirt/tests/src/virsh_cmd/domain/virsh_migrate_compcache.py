import logging
from virttest import virsh, utils_misc
from autotest.client.shared import error, utils


def get_page_size():
    """
    Get the current memory page size using getconf.
    If getconf doesn't exist, assume it's 4096.

    :return: An integer of current page size bytes.
    """
    try:
        getconf_path = utils_misc.find_command('getconf')
        return int(utils.run(getconf_path + ' PAGESIZE').stdout)
    except ValueError:
        logging.warning('getconf not found! Assuming 4K for PAGESIZE')
        return 4096


def run(test, params, env):
    """
    Test command: migrate-compcache <domain> [--size <number>]

    1) Run migrate-compcache command and check return code.
    """
    vm_ref = params.get("vm_ref", "name")
    vm_name = params.get('main_vm')
    start_vm = 'yes' == params.get('start_vm', 'yes')
    pause_vm = 'yes' == params.get('pause_after_start_vm', 'no')
    expect_succeed = 'yes' == params.get('expect_succeed', 'yes')
    size_option = params.get('size_option', 'valid')
    action = params.get('compcache_action', 'get')
    vm = env.get_vm(vm_name)

    # Check if the virsh command migrate-compcache is available
    if not virsh.has_help_command('migrate-compcache'):
        raise error.TestNAError("This version of libvirt does not support "
                                "virsh command migrate-compcache")

    # Prepare the VM state if it's not correct.
    if start_vm and not vm.is_alive():
        vm.start()
    elif not start_vm and vm.is_alive():
        vm.destroy()
    if pause_vm and not vm.is_paused():
        vm.pause()

    # Setup domain reference
    if vm_ref == 'domname':
        vm_ref = vm_name

    # Setup size according to size_option:
    # minimal: Same as memory page size
    # maximal: Same as guest memory
    # empty: An empty string
    # small: One byte less than page size
    # large: Larger than guest memory
    # huge : Largest int64
    page_size = get_page_size()
    if size_option == 'minimal':
        size = str(page_size)
    elif size_option == 'maximal':
        size = str(vm.get_max_mem() * 1024)
    elif size_option == 'empty':
        size = '""'
    elif size_option == 'small':
        size = str(page_size - 1)
    elif size_option == 'large':
        # Guest memory is larger than the max mem set,
        # add 50MB to ensure size exceeds guest memory.
        size = str(vm.get_max_mem() * 1024 + 50000000)
    elif size_option == 'huge':
        size = str(2**64 - 1)
    else:
        size = size_option

    # If we need to get, just omit the size option
    if action == 'get':
        size = None

    # Run testing command
    result = virsh.migrate_compcache(vm_ref, size=size)
    logging.debug(result)

    # Shut down the VM to make sure the compcache setting cleared
    if vm.is_alive():
        vm.destroy()

    # Check test result
    if expect_succeed:
        if result.exit_status != 0:
            raise error.TestFail(
                'Expected succeed, but failed with result:\n%s' % result)
    elif expect_succeed:
        if result.exit_status == 0:
            raise error.TestFail(
                'Expected fail, but succeed with result:\n%s' % result)
