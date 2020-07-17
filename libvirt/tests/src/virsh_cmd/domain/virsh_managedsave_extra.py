import logging
import os
import shutil

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

MANAGEDSAVE_FILE = '/var/lib/libvirt/qemu/save/%s.save'


def run(test, params, env):
    """
    Test command: virsh managedsave-xxx
    including virsh managedsave-edit
              virsh managedsave-dumpxml
              virsh managedsave-define
              ...
    """
    vm_name = params.get('main_vm')
    checkpoint = params.get('checkpoint', '')
    error_msg = params.get('error_msg', '')
    virsh_opt = params.get('virsh_opt', '')
    ms_extra_options = params.get('ms_extra_options', '')
    pre_state = params.get('pre_state', '')
    status_error = 'yes' == params.get('status_error', 'no')

    vm = env.get_vm(vm_name)
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': False}

    def start_and_login_vm():
        """
        Start vm and login, after which vm is accessible
        """
        vm.start()
        vm.wait_for_login().close()

    try:
        if checkpoint == 'dumpxml':
            # Check managedsave-dumpxml
            start_and_login_vm()
            virsh.managedsave(vm_name, **virsh_dargs)
            virsh.managedsave_dumpxml(vm_name, **virsh_dargs)
            tmp_dir = data_dir.get_tmp_dir()
            save_img_xml = os.path.join(tmp_dir, 'save_img.xml')
            managed_save_xml = os.path.join(tmp_dir, 'managed_save.xml')
            virsh.save_image_dumpxml(MANAGEDSAVE_FILE % vm_name, ' > %s'
                                     % save_img_xml, **virsh_dargs)
            virsh.managedsave_dumpxml(vm_name, ' > %s' % managed_save_xml,
                                      **virsh_dargs)
            result_need_check = process.run('diff %s %s' %
                                            (save_img_xml, managed_save_xml),
                                            shell=True, verbose=True)
        if checkpoint == 'secure_info':
            # Check managedsave-dumpxml with option --security-info
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vm_xml.VMXML.set_graphics_attr(vm_name, {'passwd': '123456'})
            start_and_login_vm()
            virsh.managedsave(vm_name, **virsh_dargs)
            default_xml = virsh.managedsave_dumpxml(vm_name, **virsh_dargs).stdout_text
            if 'passwd' in default_xml:
                test.fail('Found "passwd" in dumped vm xml. '
                          'Secure info like "passwd" should not be dumped.')
            secure_xml = virsh.managedsave_dumpxml(vm_name, '--security-info',
                                                   **virsh_dargs).stdout_text
            if 'passwd' not in secure_xml:
                test.fail('Not found "passwd" in dumped vm xml.'
                          'Secure info like "passwd" should be dumped '
                          'with option "--security-info"')
        if checkpoint == 'define':
            # Make change to a managedsave-dumped xml and redefine vm
            # and check if the change take effect
            start_option = '--paused' if pre_state == 'paused' else ''
            virsh.start(vm_name, start_option, **virsh_dargs)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            logging.debug(vmxml.devices)
            disk = vmxml.get_devices('disk')[0]
            img_path = disk.source.attrs['file']
            logging.info('Original image path: %s', img_path)
            # Copy old image to new image
            new_img_path = os.path.join(data_dir.get_tmp_dir(), 'test.img')
            shutil.copyfile(img_path, new_img_path)
            virsh.managedsave(vm_name, **virsh_dargs)
            xmlfile = os.path.join(data_dir.get_tmp_dir(), 'managedsave.xml')
            virsh.managedsave_dumpxml(vm_name, '>%s' % xmlfile, **virsh_dargs)
            # Make change to xmlfile and managedsave-define with it
            with open(xmlfile) as file_xml:
                updated_xml = file_xml.read().replace(img_path, new_img_path)
            with open(xmlfile, 'w') as file_xml:
                file_xml.write(updated_xml)
            virsh.managedsave_define(vm_name, xmlfile, ms_extra_options, **virsh_dargs)
            virsh.start(vm_name, **virsh_dargs)
            xml_after_define = virsh.dumpxml(vm_name, **virsh_dargs).stdout_text
            if 'test.img' not in xml_after_define:
                test.fail('Not found "test.img" in vm xml after managedsave-define.'
                          'Modification to xml did not take effect.')
        if checkpoint == 'no_save':
            # Start a guest but do not managedsave it
            start_and_login_vm()
            virsh.dom_list('--all --managed-save', **virsh_dargs)
        if checkpoint == 'rm_after_save':
            # Remove saved file after managedsave a vm
            start_and_login_vm()
            virsh.managedsave(vm_name, **virsh_dargs)
            os.remove(MANAGEDSAVE_FILE % vm_name)
        if checkpoint == 'not_saved_corrupt':
            # Do not managedsave a vm, but create a fake managedsaved file by
            # 'touch' a file
            start_and_login_vm()
            virsh.dom_list('--all --managed-save', **virsh_dargs)
            process.run('touch %s' % MANAGEDSAVE_FILE % vm_name, verbose=True)
            params['clean_managed_save'] = True
        if checkpoint == 'readonly':
            start_and_login_vm()
            virsh.managedsave(vm_name, **virsh_dargs)
        if checkpoint == 'exclusive_option':
            virsh.managedsave(vm_name, **virsh_dargs)

        # Test managedsave-edit, managedsave_dumpxml, managedsave-define
        if params.get('check_cmd_error', '') == 'yes':
            ms_command = params.get('ms_command', '')
            if ms_command == 'edit':
                result_need_check = virsh.managedsave_edit(vm_name,
                                                           ms_extra_options,
                                                           timeout=60,
                                                           virsh_opt=virsh_opt,
                                                           debug=True)
            if ms_command == 'dumpxml':
                result_need_check = virsh.managedsave_dumpxml(vm_name,
                                                              ms_extra_options,
                                                              virsh_opt=virsh_opt,
                                                              debug=True)
            if ms_command == 'define':
                result_need_check = virsh.managedsave_define(vm_name,
                                                             bkxml.xml,
                                                             ms_extra_options,
                                                             virsh_opt=virsh_opt,
                                                             debug=True)
        # If needs to check result, check it
        if 'result_need_check' in locals():
            logging.info('Check command result.')
            libvirt.check_exit_status(result_need_check, status_error)
            if error_msg:
                libvirt.check_result(result_need_check, [error_msg])

    finally:
        if params.get('clean_managed_save'):
            os.remove(MANAGEDSAVE_FILE % vm_name)
        utils_libvirtd.libvirtd_restart()
        virsh.managedsave_remove(vm_name, debug=True)
        bkxml.sync()
