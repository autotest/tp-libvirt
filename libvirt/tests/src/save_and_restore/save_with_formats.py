import logging
import os

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.save import save_base

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test virsh save with different formats
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    log_file = params.get('libvirtd_debug_file')
    save_format = params.get('save_format')
    rand_id = '_' + utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm_name}_{rand_id}.save'
    check_cmd = params.get('check_cmd', '')
    check_cmd = check_cmd.format(save_path) if check_cmd else check_cmd
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        qemu_conf = utils_config.LibvirtQemuConfig()
        libvirtd = utils_libvirtd.Libvirtd()
        qemu_conf.save_image_format = save_format
        libvirtd.restart()

        vm.start()
        pid_ping, upsince = save_base.pre_save_setup(vm)

        save_result = virsh.save(vm_name, save_path, debug=True)
        libvirt.check_exit_status(save_result, status_error)
        if status_error:
            libvirt.check_result(save_result, error_msg)
            return

        duration = save_result.duration
        LOG.debug(f'Duration of {format}: {duration}')
        img_size = int(utils_misc.get_image_info(save_path)['dsize'])
        LOG.debug(f'Image size of {format}: {img_size}')

        if save_format != 'raw':
            log_pattern = f'virCommandRunAsync.*{save_format} -c'
            libvirt.check_logfile(log_pattern, log_file)

        virsh.restore(save_path, **VIRSH_ARGS)

        if save_format != 'raw':
            log_pattern = f'virCommandRunAsync.*{save_format} -dc'
            libvirt.check_logfile(log_pattern, log_file)

        save_base.post_save_check(vm, pid_ping, upsince)

    finally:
        bkxml.sync()
        if 'qemu_conf' in locals():
            qemu_conf.restore()
            libvirtd.restart()
        if os.path.exists(save_path):
            os.remove(save_path)
