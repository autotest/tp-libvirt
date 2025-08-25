import logging
import os
import re

from avocado.utils import process
from avocado.utils import software_manager
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.save import save_base

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def setup_bypass_cache(check_cmd, test):
    """
    Setup for restore --bypass-cache option

    :param check_cmd: command to check bypass cache
    :param test: test instance
    :return: subprocess instance to run check command
    """
    sm = software_manager.manager.SoftwareManager()
    if not sm.check_installed('lsof'):
        test.error("Need to install lsof on host")
    sp = process.SubProcess(check_cmd, shell=True)
    sp.start()

    return sp


def check_bypass_cache(sp, test):
    """
    Check bypass cache after restore

    :param sp: subprocess instance to run check command
    :param test: test instance
    """
    output = sp.get_stdout().decode()
    LOG.debug(f'bypass-cache check output:\n{output}')
    sp.terminate()
    flags = os.O_DIRECT
    lines = re.findall(r"flags:.(\d+)", output, re.M)
    LOG.debug(f"Find all fdinfo flags: {lines}")
    lines = [int(i, 8) & flags for i in lines]
    if flags not in lines:
        test.fail('bypass-cache check fail, The flag os.O_DIRECT is expected '
                  'in log, but not found, please check log')


def run(test, params, env):
    """
    Test "--bypass-cache" option used when starting VM after managedsave.
    Alternatively, set auto_start_bypass_cache in qemu.conf to test the --bypass-cache function.
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    set_qemu_conf = 'yes' == params.get('set_qemu_conf', '')
    auto_file_path = params.get('auto_file_path')
    check_cmd = params.get('check_cmd', '')
    save_path_dir = params.get('save_path', '')
    save_path = os.path.join(save_path_dir, f'{vm_name}.save')
    check_cmd = check_cmd.format(save_path, save_path) if check_cmd else check_cmd
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    try:
        if set_qemu_conf:
            LOG.debug("Prepare ENV: Set VM as autostart")
            virsh.autostart(vm_name, "")
            LOG.debug("Prepare ENV: Update qemu.conf with 'auto_start_bypass_cache = 1':")
            qemu_conf.auto_start_bypass_cache = 1
            libvirtd.restart()
        if not vm.is_alive():
            vm.start()
        # Record VM info before managedsave for comparison
        LOG.info("Record the info before managedsave:")
        pid_ping, upsince = save_base.pre_save_setup(vm)
        LOG.info("TEST STEP 1: Perform managedsave")
        virsh.managedsave(vm_name, **VIRSH_ARGS)
        if vm.is_alive():
            test.fail("VM should be shutdown after managedsave")
        if set_qemu_conf:
            libvirtd.stop()
            LOG.debug(f"Remove autostarted file at {auto_file_path}")
            os.remove(auto_file_path)
        # Setup and start checking bypass cache
        sp = setup_bypass_cache(check_cmd, test)
        LOG.info("TEST STEP 2: Start restore process")
        if set_qemu_conf:
            LOG.info('Start libvirtd to trigger VM startup')
            libvirtd.start()
        else:
            # Run restore with "--bypass-cache" option
            virsh.start(vm_name, "--bypass-cache", **VIRSH_ARGS)
        # Check if "--bypass-cache" works as expected
        LOG.info("TEST STEP 3: Verify host memory cache info")
        check_bypass_cache(sp, test)
        LOG.info("TEST STEP 4: Ensure VM is running after restore")
        if not vm.is_alive():
            test.fail(f"VM should be running after restore, instead of {vm.state()}")
        # Compare VM info with the state before managedsave
        LOG.info("TEST STEP 5: Verify VM time and ping status on VM")
        save_base.post_save_check(vm, pid_ping, upsince)

    finally:
        bkxml.sync()
        qemu_conf.restore()
