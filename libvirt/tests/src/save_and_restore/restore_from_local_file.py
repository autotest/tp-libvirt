import logging
import os
import re

from avocado.utils import process
from avocado.utils import software_manager
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.save import save_base

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def setup_restore_xml(params, save_path):
    """
    Setup xml for restore --xml option

    :param params: test params
    :param save_path: path of saved file
    :return: xml instance of altered xml
    """
    alter_xml = vm_xml.VMXML()
    xml_str = virsh.save_image_dumpxml(save_path).stdout_text
    alter_xml.xml = xml_str
    description = params.get('description', 'test-description')
    alter_xml.description = description
    LOG.debug(f"Modify description to {description}")
    LOG.debug(f'XML for restoring:\n{alter_xml}')

    return alter_xml


def check_restore_xml(vm_name, params, test):
    """
    Check vmxml after restore

    :param vm_name: name of vm
    :param params: test params
    :param test: test instance
    """
    vmxml_after = vm_xml.VMXML.new_from_dumpxml(vm_name)
    LOG.debug(
        f'VM description after restore: {vmxml_after.description}')
    if vmxml_after.description != params.get('description', 'test-description'):
        test.fail('VM description after restore is incorrect')


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
    Test virsh restore with various options
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    scenario = params.get('scenario', '')
    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    timeout = params.get('timeout', 60)
    virsh_options = params.get('virsh_options', '')
    options = params.get('options', '')
    save_opt = params.get('save_opt', '')
    pre_path_options = params.get('pre_path_options', '')
    after_state = params.get('after_state', 'running')
    rand_id = utils_misc.generate_random_string(3)
    save_path = f'/var/lib/libvirt/qemu/save/{vm_name}_{rand_id}.save'
    save_args = pre_path_options + ' ' + save_path
    check_cmd = params.get('check_cmd', '')
    check_cmd = check_cmd.format(
        save_path, save_path) if check_cmd else check_cmd

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        pid_ping, upsince = save_base.pre_save_setup(vm)

        virsh.save(vm_name, save_path, options=save_opt, **VIRSH_ARGS)

        if scenario == 'xml_opt':
            alter_xml = setup_restore_xml(params, save_path)
            options += ' ' + alter_xml.xml

        if scenario == 'bypass_cache_opt':
            sp = setup_bypass_cache(check_cmd, test)

        restore_result = virsh.restore(save_args, options=options, debug=True,
                                       virsh_opt=virsh_options)

        if after_state and not utils_misc.wait_for(
                lambda: vm.state() == after_state, timeout=timeout):
            test.fail(f'VM should be {after_state} after restore, but current '
                      f'state is {vm.state()}')

        LOG.debug(f'Current vm state after restore: {vm.state()}')

        if status_error:
            libvirt.check_result(restore_result, error_msg)
            return

        if scenario == 'bypass_cache_opt':
            check_bypass_cache(sp, test)

        if vm.state() == 'paused':
            virsh.resume(vm_name, **VIRSH_ARGS)

        if scenario == 'xml_opt':
            check_restore_xml(vm_name, params, test)

        save_base.post_save_check(vm, pid_ping, upsince)

    finally:
        bkxml.sync()
        if os.path.exists(save_path):
            os.remove(save_path)
