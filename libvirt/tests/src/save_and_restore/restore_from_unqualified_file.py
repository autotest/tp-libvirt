import logging
import os

from avocado.utils import process
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def modify_save_image_disk_source(save_path, vm_disk_source):
    """
    Modify disk source of saved file

    :param save_path: path of saved file
    :param vm_disk_source: new disk source file to be set
    """
    save_xml = vm_xml.VMXML()
    save_xml.xml = virsh.save_image_dumpxml(
        save_path, **VIRSH_ARGS).stdout_text
    disk_attrs = {'source': {'attrs': {'file': vm_disk_source}}}
    disk_xml, xml_devices = libvirt.get_vm_device(save_xml, 'disk')
    disk_xml.setup_attrs(**disk_attrs)
    save_xml.devices = xml_devices
    virsh.save_image_define(save_path, save_xml.xml, **VIRSH_ARGS)


def run(test, params, env):
    """
    Test restoring vm from unqualified file
    """
    vm_name = params.get('main_vm')
    vm_2nd = params.get('vm_2nd')
    vm = env.get_vm(vm_name)

    scenario = params.get('scenario', '')
    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    rand_id = utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm_name}_{rand_id}.save'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    try:
        process.run('chcon -t qemu_var_run_t "/var/tmp"', ignore_status=True, shell=True)
        if scenario == 'invalid':
            process.run(f'echo > {save_path}', shell=True)

        if scenario == 'to_running_vm':
            virsh.save(vm_name, save_path, **VIRSH_ARGS)
            virsh.start(vm_name, **VIRSH_ARGS)

        if scenario == 'image_running_by_another_vm':
            vm_disk_source = vmxml.get_devices('disk')[0].source.attrs['file']
            virsh.save(vm_2nd, save_path, **VIRSH_ARGS)
            modify_save_image_disk_source(save_path, vm_disk_source)

        restore_result = virsh.restore(save_path, debug=True)
        libvirt.check_exit_status(restore_result, status_error)
        libvirt.check_result(restore_result, error_msg)

    finally:
        bkxml.sync()
        if os.path.exists(save_path):
            os.remove(save_path)
