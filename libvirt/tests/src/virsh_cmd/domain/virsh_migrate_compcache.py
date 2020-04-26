import logging
import subprocess
import time

from avocado.utils import process
from avocado.utils import path as utils_path

from virttest import virsh
from virttest import ssh_key
from virttest import migration


def get_page_size():
    """
    Get the current memory page size using getconf.
    If getconf doesn't exist, assume it's 4096.

    :return: An integer of current page size bytes.
    """
    try:
        getconf_path = utils_path.find_command('getconf')
        return int(process.run(getconf_path + ' PAGESIZE', shell=True).stdout_text.strip())
    except utils_path.CmdNotFoundError:
        logging.warning('getconf not found! Assuming 4K for PAGESIZE')
        return 4096


def run(test, params, env):
    """
    Test command: migrate-compcache <domain> [--size <number>]

    1) Run migrate-compcache command and check return code.
    """
    vm_ref = params.get("vm_ref", "name")
    vm_name = params.get("migrate_main_vm")
    start_vm = 'yes' == params.get('start_vm', 'yes')
    pause_vm = 'yes' == params.get('pause_after_start_vm', 'no')
    expect_succeed = 'yes' == params.get('expect_succeed', 'yes')
    size_option = params.get('size_option', 'valid')
    action = params.get('compcache_action', 'get')
    vm = env.get_vm(vm_name)

    # Check if the virsh command migrate-compcache is available
    if not virsh.has_help_command('migrate-compcache'):
        test.cancel("This version of libvirt does not support "
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
        size = str(2 ** 64 - 1)
    else:
        size = size_option

    # If we need to get, just omit the size option
    if action == 'get':
        size = None

    # Run testing command
    result = virsh.migrate_compcache(vm_ref, size=size)
    logging.debug(result)

    remote_uri = params.get("compcache_remote_uri")
    remote_host = params.get("migrate_dest_host")
    remote_user = params.get("migrate_dest_user", "root")
    remote_pwd = params.get("migrate_dest_pwd")
    check_job_compcache = False
    compressed_size = None
    if not remote_host.count("EXAMPLE") and size is not None and expect_succeed:
        # Config ssh autologin for remote host
        ssh_key.setup_ssh_key(remote_host, remote_user,
                              remote_pwd, port=22)
        if vm.is_dead():
            vm.start()
        if vm.is_paused():
            vm.resume()
        vm.wait_for_login()
        # Do actual migration to verify compression cache of migrate jobs
        command = ("virsh migrate %s %s --compressed --unsafe --verbose"
                   % (vm_name, remote_uri))
        logging.debug("Start migrating: %s", command)
        p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)

        # Give enough time for starting job
        t = 0
        while t < 5:
            jobinfo = virsh.domjobinfo(vm_ref, debug=True,
                                       ignore_status=True).stdout
            jobtype = "None"
            for line in jobinfo.splitlines():
                key = line.split(':')[0]
                if key.count("type"):
                    jobtype = line.split(':')[-1].strip()
                elif key.strip() == "Compression cache":
                    compressed_size = line.split(':')[-1].strip()
            if "None" == jobtype or compressed_size is None:
                t += 1
                time.sleep(1)
                continue
            else:
                check_job_compcache = True
                logging.debug("Job started: %s", jobtype)
                break

        if p.poll():
            try:
                p.kill()
            except OSError:
                pass

        # Cleanup in case of successful migration
        migration.MigrationTest().cleanup_dest_vm(vm, None, remote_uri)

    # Shut down the VM to make sure the compcache setting cleared
    if vm.is_alive():
        vm.destroy()

    # Check test result
    if expect_succeed:
        if result.exit_status != 0:
            test.fail('Expected succeed, but failed with result:\n%s' % result)
        if check_job_compcache:
            value = compressed_size.split()[0].strip()
            unit = compressed_size.split()[-1].strip()
            value = int(float(value))
            if unit == "KiB":
                size = int(int(size) / 1024)
            elif unit == "MiB":
                size = int(int(size) / 1048576)
            elif unit == "GiB":
                size = int(int(size) / 1073741824)
            if value != size:
                test.fail("Compression cache is not match"
                          " with setted")
            else:
                return
            test.fail("Get compression cache in job failed.")
        else:
            logging.warn("The compressed size wasn't been verified "
                         "during migration.")
    elif not expect_succeed:
        if result.exit_status == 0:
            test.fail('Expected fail, but succeed with result:\n%s' % result)
