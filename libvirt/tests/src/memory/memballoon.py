import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test memballoon device
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    case = params.get('case', '')
    status_error = "yes" == params.get('status_error', 'no')

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if case == 'alias':
            model = params.get('model')
            alias_name = params.get('alias_name')
            has_alias = 'yes' == params.get('has_alias', 'no')

            # Update memballoon device
            balloon_dict = {
                'membal_model': model,
                'membal_alias_name': alias_name
            }

            libvirt.update_memballoon_xml(vmxml, balloon_dict)
            logging.debug(virsh.dumpxml(vm_name).stdout_text)

            # Get memballoon device after vm define and check
            balloons = vmxml.get_devices('memballoon')
            if len(balloons) == 0:
                test.error('Memballoon device was not added to vm.')
            new_balloon = balloons[0]
            if has_alias:
                logging.debug('Expected alias: %s\nActual alias: %s',
                              alias_name, new_balloon.alias_name)
                if new_balloon.alias_name == alias_name:
                    logging.info('Memballon alias check PASS.')
                else:
                    test.fail('Memballon alias check FAIL.')

            # Check vm start
            cmd_result = virsh.start(vm_name)
            libvirt.check_result(cmd_result, status_error)

    finally:
        if params.get('destroy_after') == 'yes':
            vm.destroy()
        bkxml.sync()
