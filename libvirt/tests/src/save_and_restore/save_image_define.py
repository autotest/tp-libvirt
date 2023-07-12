import logging
import os

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test virsh save-image-define with options
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    status_error = 'yes' == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    readonly = 'yes' == params.get('readonly', 'no')
    options = params.get('options', '')
    rand_id = utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm_name}_{rand_id}.save'
    timeout = params.get('timeout', 60)
    pre_state = params.get('pre_state', 'running')
    after_state = params.get('after_state', pre_state)

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vm.start()
        vm.wait_for_login().close()

        if pre_state == 'paused':
            virsh.suspend(vm_name, **VIRSH_ARGS)
            if not utils_misc.wait_for(lambda: vm.state() == pre_state, 30):
                test.error(f'VM should be {pre_state} before save')

        virsh.save(vm_name, save_path, **VIRSH_ARGS)
        save_xml_str = virsh.save_image_dumpxml(save_path,
                                                **VIRSH_ARGS).stdout_text

        save_xml = vm_xml.VMXML()
        save_xml.xml = save_xml_str
        pre_on_crash = save_xml.on_crash
        set_on_crash = 'restart' if pre_on_crash == 'destroy' else 'destroy'
        save_xml.on_crash = set_on_crash
        save_xml.xmltreefile.write()
        LOG.debug(f'The xml used for save-image-define from is:\n{save_xml}')

        result = virsh.save_image_define(save_path, save_xml.xml,
                                         options=options, debug=True,
                                         readonly=readonly)
        libvirt.check_exit_status(result, status_error)
        if status_error:
            libvirt.check_result(result, error_msg)
            return

        virsh.restore(save_path, **VIRSH_ARGS)

        if not utils_misc.wait_for(lambda: vm.state() == after_state,
                                   timeout=timeout):
            test.fail(f'VM should be {after_state} after restore, but current '
                      f'state is {vm.state()}')

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if vmxml.on_crash != set_on_crash:
            test.fail(f'Setting of on_crash is incorrect: should be '
                      f'{set_on_crash}, not {vmxml.on_crash}')

    finally:
        bkxml.sync()
        if os.path.exists(save_path):
            os.remove(save_path)
