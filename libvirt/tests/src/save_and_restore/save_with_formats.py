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


def save_with_format(save_format, test, params, env):
    """
    Run virsh save with given format

    :param save_format: format to save with
    :param test: test instance
    :param params: test params
    :param env: test env
    :return: dict-type data of duration and image size
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    log_file = params.get('libvirtd_debug_file')
    rand_id = '_' + utils_misc.generate_random_string(3)
    save_path = f'/root/{vm_name}_{save_format}_{rand_id}.save'

    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    qemu_conf.save_image_format = save_format
    libvirtd.restart()

    try:
        pid_ping, upsince = save_base.pre_save_setup(vm)
        save_result = virsh.save(vm_name, save_path, debug=True)
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

        return {'duration': duration, 'img_size': img_size}

    finally:
        if os.path.exists(save_path):
            os.remove(save_path)
        qemu_conf.restore()
        libvirtd.restart()


def run(test, params, env):
    """
    Test virsh save with different formats and compare duration and image size
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    save_formats = params.get('save_formats', '').split()
    rand_id = '_' + utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm_name}_{rand_id}.save'
    check_cmd = params.get('check_cmd', '')
    check_cmd = check_cmd.format(save_path) if check_cmd else check_cmd

    try:
        vm.start()
        if not status_error:
            save_info = {}
            for save_format in save_formats:
                save_info[save_format] = save_with_format(save_format, test,
                                                          params, env)
            [LOG.debug(f"Duration of format {f} is {save_info[f]['duration']}")
             for f in save_formats]
            [LOG.debug(f"Image size of format {f} is {save_info[f]['img_size']}"
                       ) for f in save_formats]
            duration_list = [save_info[f]['duration'] for f in save_formats]
            if sorted(duration_list) != duration_list:
                test.fail(
                    'The order of time cost is not correct, please check log')
            img_size_list = [save_info[f]['img_size'] for f in save_formats]
            if sorted(img_size_list, reverse=True) != img_size_list:
                test.fail(
                    'The order of image size is not correct, please check log')
        else:
            save_format = params.get('save_format')
            qemu_conf = utils_config.LibvirtQemuConfig()
            libvirtd = utils_libvirtd.Libvirtd()
            qemu_conf.save_image_format = save_format
            libvirtd.restart()
            save_result = virsh.save(vm_name, save_path, debug=True)
            libvirt.check_result(save_result, error_msg)

    finally:
        bkxml.sync()
        if 'qemu_conf' in locals():
            qemu_conf.restore()
            libvirtd.restart()
        if os.path.exists(save_path):
            os.remove(save_path)
