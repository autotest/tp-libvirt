import os
import re
import logging
from virttest import virsh
from avocado.utils import process
from avocado.utils import software_manager
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.save import save_base


LOG = logging.getLogger('avocado.' + __name__)


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


def setup_getdomjobinfo(check_cmd_jobinfo):
    """
    Get the domjobinfo output during managedsave
    :param check_cmd_jobinfo: command to check domjobinfo
    return: subprocess instance to run check command
    """
    st = process.SubProcess(check_cmd_jobinfo, shell=True)
    st.start()
    return st


def check_domjobinfo(st, test):
    """
    Check the domjobinfo during managedsave
    :param st: subprocess instance to run check command
    :param test: test instance
    """
    output = st.get_stdout().decode()
    LOG.debug(f'domjobinfo check output:\n{output}')
    st.terminate()

    expected_patterns = [
        ('job type', 'unbounded'),
        ('operation', 'save')
    ]

    for pattern in expected_patterns:
        if not re.search(f'{pattern[0]}:.*{pattern[1]}', output, flags=re.IGNORECASE):
            test.fail(f'Failed to find "{pattern[1]}" under "{pattern[0]}"')

    completed_output = virsh.domjobinfo('avocado-vt-vm1', extra='--completed').stdout_text.strip()
    LOG.info(f"The domjobinfo with '--completed' outputs:\n {completed_output}")
    if not re.search(r'job type:.*completed.*operation:.*save', completed_output, flags=re.IGNORECASE | re.DOTALL):
        test.fail("Failed to find 'completed' job type and 'save' operation in domjobinfo")


def setup(vm, vm_name, is_persistent, expect_state):
    """"
    Prepare the pre-conditions
    1. Ensure VM is in expected state: paused, shutoff or running
    2. Ensure VM is in expected state: persistent or transient
    3. If VM is running, get the pid of ping and uptime
    :params vm: vm instance
    :params vm_name: string, name of the vm
    :params persistent: boolean, whether the expected status is persistent, true or false
    :params expect_state: string, the expected vm state, like "shut off", "running" or "paused"
    :params return: if the vm is running, login vm to run cmd and return the outputs
                    pid_ping, get the pid of the ping process
                    upsince, get the uptime
                    if the vm is not running, return None, None
    """
    if not vm.is_alive():
        vm.start()
    if not is_persistent:
        virsh.undefine(vm_name, options="--nvram", debug=True)
    if expect_state.lower() == "shut off":
        virsh.destroy(vm_name, debug=True)
    elif expect_state.lower() == "running":
        pid_ping, upsince = save_base.pre_save_setup(vm, serial=True)
        return pid_ping, upsince
    elif expect_state.lower() == "paused":
        virsh.suspend(vm_name, debug=True)
    return None, None


def do_managedsave(vm_name, virsh_opt, readonly, expected_match, error_msg):
    """
    do managedsave and check the result
    """
    ret = virsh.managedsave(vm_name, options=virsh_opt, expected_match=expected_match, readonly=readonly)
    libvirt.check_result(ret, expected_fails=error_msg)


def check_state_after_managedsave(vm_name, status_error, test, pre_vm_state, managed_save_file):
    """
    Check the status after managedsave
    1. Managedsave succeed: VM should be shut off, and there is saved file
    2. Managedsave fail: VM should be in previous status, and no saved file
    """
    LOG.info("Check VM status:")
    post_vm_state = virsh.domstate(vm_name).stdout_text.strip()
    LOG.debug("VM state is %s", post_vm_state)
    if not status_error:
        if post_vm_state != 'shut off':
            test.fail(f"VM should be shut off after managedsave succeed, but got {post_vm_state}")
    else:
        if post_vm_state != pre_vm_state:
            test.fail("VM state should not change after managedsave fail")
    LOG.info("Check saved file existence:")
    file_exists = os.path.exists(managed_save_file)
    LOG.info("file exists: %s" % file_exists)
    if not status_error:
        if not file_exists:
            test.fail("There should be saved file!")
    else:
        if file_exists:
            test.fail("There should not be saved file!")


def run(test, params, env):
    """
    Test command: virsh managedsave

    Test the various options of this cmd, combined with different vm states
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    managed_save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name

    virsh_opt = params.get("virsh_opt", '')
    pre_vm_state = params.get("pre_vm_state")
    persistent = 'yes' == params.get('persistent')
    readonly = 'yes' == params.get('readonly', 'no')
    save_path_dir = params.get('save_path', '')
    save_path = os.path.join(save_path_dir, f'{vm_name}.save')
    check_cmd_ = params.get('check_cmd', '')
    check_cmd = check_cmd_.format(save_path, save_path) if check_cmd_ else check_cmd_
    check_cmd_jobinfo_ = params.get('check_cmd_jobinfo', '')
    check_cmd_jobinfo = check_cmd_jobinfo_.format(vm_name)
    expected_match = params.get('expected_match', '')
    error_msg = str(params.get('error_msg', ''))
    status_error = 'yes' == params.get('status_error', '')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    try:
        LOG.info("TEST STEP1: Set up the pre-conditions:")
        pid_ping, upsince = setup(vm, vm_name, persistent, pre_vm_state)
        st = virsh.dominfo(vm_name, debug=True).stdout_text.strip()
        LOG.info("VM info after setup: %s" % st)
        LOG.info("TEST STEP2: Do managedsave:")
        if virsh_opt == '--bypass-cache':
            sp = setup_bypass_cache(check_cmd, test)
            do_managedsave(vm_name, virsh_opt, readonly, expected_match, error_msg)
            check_bypass_cache(sp, test)
        elif virsh_opt == '' and not status_error:
            st = setup_getdomjobinfo(check_cmd_jobinfo)
            do_managedsave(vm_name, virsh_opt, readonly, expected_match, error_msg)
            check_domjobinfo(st, test)
        else:
            do_managedsave(vm_name, virsh_opt, readonly, expected_match, error_msg)
        LOG.info("TEST STEP3: Check the status after managedsave:")
        check_state_after_managedsave(vm_name, status_error, test, pre_vm_state, managed_save_file)
        LOG.info("TEST STEP4: for positive scenarios, restore and check status after it:")
        if not status_error:
            virsh.start(vm_name)
            LOG.info("Check the saved file is removed after restore:")
            if os.path.exists(managed_save_file):
                test.fail("There should not be save file after restore the vm")
            LOG.info("Check the vm state after restore:")
            restore_vm_state = virsh.domstate(vm_name).stdout_text.strip()
            LOG.info("VM status is %s", restore_vm_state)
            if virsh_opt == '--paused' or (pre_vm_state == 'paused' and virsh_opt != '--running'):
                if restore_vm_state == "running":
                    test.fail("VM should be paused, but it's %s!" % restore_vm_state)
            else:
                if restore_vm_state == "paused":
                    test.fail("VM should be running, but is's %s!" % restore_vm_state)
            LOG.info("Login vm and check ping and time consistence")
            if restore_vm_state == "paused":
                virsh.resume(vm_name, debug=True)
            if pid_ping:
                save_base.post_save_check(vm, pid_ping, upsince)
    finally:
        virsh.destroy(vm_name, debug=True)
        if not persistent:
            virsh.define(bk_xml.xml, debug=True)
        virsh.managedsave_remove(vm_name, debug=True)
        if os.path.exists(managed_save_file):
            process.run("rm -f %s" % managed_save_file, shell=True, ignore_status=True)
        bk_xml.sync()
