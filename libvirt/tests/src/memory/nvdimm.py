import os
import logging
import time

from avocado.utils import process

from virttest import virsh
from virttest import utils_hotplug
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test memory management of nvdimm
    """
    vm_name = params.get('main_vm')

    nvdimm_file = params.get('nvdimm_file')
    check = params.get('check', '')
    qemu_checks = params.get('qemu_checks', '').split('`')
    test_str = 'This is a test!'

    def check_boot_config(session):
        """
        Check /boot/config-$KVER file
        """
        check_list = [
            'CONFIG_LIBNVDIMM=m',
            'CONFIG_BLK_DEV_PMEM=m',
            'CONFIG_ACPI_NFIT=m'
        ]
        current_boot = session.cmd('uname -r').strip()
        content = session.cmd('cat /boot/config-%s' % current_boot).strip()
        for item in check_list:
            if item in content:
                logging.info(item)
            else:
                logging.error(item)
                test.fail('/boot/config content not correct')

    def check_file_in_vm(session, path, expect=True):
        """
        Check whether the existance of file meets expectation
        """
        exist = session.cmd_status('ls %s' % path)
        logging.debug(exist)
        exist = True if exist == 0 else False
        status = '' if exist else 'NOT'
        logging.info('File %s does %s exist', path, status)
        if exist != expect:
            err_msg = 'Existance doesn\'t meet expectation: %s ' % path
            if expect:
                err_msg += 'should exist.'
            else:
                err_msg += 'should not exist'
            test.fail(err_msg)

    def create_cpuxml():
        """
        Create cpu xml for test
        """
        cpu_params = {k: v for k, v in params.items() if k.startswith('cpuxml_')}
        logging.debug(cpu_params)
        cpu_xml = vm_xml.VMCPUXML()
        cpu_xml.xml = "<cpu><numa/></cpu>"
        for attr_key in cpu_params:
            val = cpu_params[attr_key]
            logging.debug('Set cpu params')
            setattr(cpu_xml, attr_key.replace('cpuxml_', ''),
                    eval(val) if ':' in val else val)
        logging.debug(cpu_xml)
        return cpu_xml.copy()

    def create_nvdimm_xml(**mem_param):
        """
        Create xml of nvdimm memory device
        """
        mem_xml = utils_hotplug.create_mem_xml(
            tg_size=mem_param['target_size'],
            mem_addr={'slot': mem_param['address_slot']},
            tg_sizeunit=mem_param['target_size_unit'],
            tg_node=mem_param['target_node'],
            mem_model="nvdimm",
            lb_size=mem_param.get('label_size'),
            lb_sizeunit=mem_param.get('label_size_unit'),
            mem_access=mem_param['mem_access']
        )

        source_xml = memory.Memory.Source()
        source_xml.path = mem_param['source_path']
        mem_xml.source = source_xml
        logging.debug(mem_xml)

        return mem_xml.copy()

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vm = env.get_vm(vm_name)
        # Create nvdimm file on the host
        process.run('truncate -s 512M %s' % nvdimm_file, verbose=True)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Set cpu according to params
        cpu_xml = create_cpuxml()
        vmxml.cpu = cpu_xml

        # Update other vcpu, memory info according to params
        update_vm_args = {k: params[k] for k in params
                          if k.startswith('setvm_')}
        logging.debug(update_vm_args)
        for key, value in list(update_vm_args.items()):
            attr = key.replace('setvm_', '')
            logging.debug('Set %s = %s', attr, value)
            setattr(vmxml, attr, int(value) if value.isdigit() else value)
        logging.debug(virsh.dumpxml(vm_name))

        # Add an nvdimm mem device to vm xml
        nvdimm_params = {k.replace('nvdimmxml_', ''): v
                         for k, v in params.items() if k.startswith('nvdimmxml_')}
        nvdimm_xml = create_nvdimm_xml(**nvdimm_params)
        vmxml.add_device(nvdimm_xml)
        vmxml.sync()
        logging.debug(virsh.dumpxml(vm_name))

        virsh.start(vm_name, debug=True, ignore_status=False)

        # Check qemu command line one by one
        map(libvirt.check_qemu_cmd_line, qemu_checks)

        alive_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        # Check if the guest support NVDIMM:
        # check /boot/config-$KVER file
        vm_session = vm.wait_for_login()
        check_boot_config(vm_session)

        # check /dev/pmem0 existed inside guest
        check_file_in_vm(vm_session, '/dev/pmem0')

        if check == 'back_file':
            # Create an ext4 file system on /dev/pmem0
            cmd_list = [
                "mkfs.ext4 /dev/pmem0",
                "mount -o dax /dev/pmem0 /mnt",
                "echo '%s' >/mnt/foo" % test_str,
                "umount /mnt"
            ]
            map(vm_session.cmd, cmd_list)
            vm_session.close()

            # Shutdown the guest, then start it, remount /dev/pmem0,
            # check if the test file is still on the file system
            vm.destroy()
            vm.start()
            vm_session = vm.wait_for_login()

            vm_session.cmd('mount -o dax /dev/pmem0 /mnt')
            if test_str not in vm_session.cmd('cat /mnt/foo'):
                test.fail('"%s" should be in /mnt/foo')

            # From the host, check the file has changed:
            host_output = process.run('hexdump -C /tmp/nvdimm',
                                      shell=True, verbose=True).stdout_text
            if test_str not in host_output:
                test.fail('"%s" should be in output')

            # Shutdown the guest, and edit the xml,
            # include: access='private'
            vm_session.close()
            vm.destroy()
            vm_devices = vmxml.devices
            nvdimm_device = vm_devices.by_device_tag('memory')[0]
            nvdimm_index = vm_devices.index(nvdimm_device)
            vm_devices[nvdimm_index].mem_access = 'private'
            vmxml.devices = vm_devices
            vmxml.sync()

            # Login to the guest, mount the /dev/pmem0 and .
            # create a file: foo-private
            vm.start()
            vm_session = vm.wait_for_login()

            libvirt.check_qemu_cmd_line('mem-path=/tmp/nvdimm,share=no')

            private_str = 'This is a test for foo-private!'
            vm_session.cmd('mount -o dax /dev/pmem0 /mnt/')

            file_private = 'foo-private'
            vm_session.cmd("echo '%s' >/mnt/%s" % (private_str, file_private))
            if private_str not in vm_session.cmd('cat /mnt/%s' % file_private):
                test.fail('"%s" should be in output' % private_str)

            # Shutdown the guest, then start it,
            # check the file: foo-private is no longer existed
            vm_session.close()
            vm.destroy()

            vm.start()
            vm_session = vm.wait_for_login()
            vm_session.cmd('mount -o dax /dev/pmem0 /mnt/')
            if file_private in vm_session.cmd('ls /mnt/'):
                test.fail('%s should not exist, for it was '
                          'created when access=private' % file_private)

        if check == 'label_back_file':
            # Create an xfs file system on /dev/pmem0
            vm_session.cmd('mkfs.xfs -f -b size=4096 /dev/pmem0')
            # Mount the file system with DAX enabled for page cache bypass
            output = vm_session.cmd_output('mount -o dax /dev/pmem0 /mnt/')
            logging.info(output)

            # Create a file on the nvdimm device.
            test_str = 'This is a test with label'
            vm_session.cmd('echo "%s" >/mnt/foo-label' % test_str)
            if test_str not in vm_session.cmd('cat /mnt/foo-label '):
                test.fail('"%s" should be in output' % test_str)

            # Reboot the guest, and remount the nvdimm device in the guest.
            # Check the file foo-label is exited
            vm_session.close()
            virsh.reboot(vm_name, debug=True)
            vm_session = vm.wait_for_login()

            vm_session.cmd('mount -o dax /dev/pmem0  /mnt')
            if test_str not in vm_session.cmd('cat /mnt/foo-label '):
                test.fail('"%s" should be in output' % test_str)

        if check == 'hot_plug':
            # Create file for 2nd nvdimm device
            nvdimm_file_2 = params.get('nvdimm_file_2')
            process.run('truncate -s 512M %s' % nvdimm_file_2)

            # Add 2nd nvdimm device to vm xml
            nvdimm2_params = {k.replace('nvdimmxml2_', ''): v
                              for k, v in params.items() if k.startswith('nvdimmxml2_')}
            nvdimm2_xml = create_nvdimm_xml(**nvdimm2_params)

            ori_devices = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')
            logging.debug('Starts with %d memory devices', len(ori_devices))

            result = virsh.attach_device(vm_name, nvdimm2_xml.xml, debug=True)
            libvirt.check_exit_status(result)

            # After attach, there should be an extra memory device
            devices_after_attach = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')
            logging.debug('After detach, vm has %d memory devices',
                          len(devices_after_attach))
            if len(ori_devices) != len(devices_after_attach) - 1:
                test.fail('Number of memory devices after attach is %d, should be %d'
                          % (len(devices_after_attach), len(ori_devices) + 1))

            time.sleep(5)
            check_file_in_vm(vm_session, '/dev/pmem1')

            nvdimm_detach = alive_vmxml.get_devices('memory')[-1]
            logging.debug(nvdimm_detach)

            # Hot-unplug nvdimm device
            result = virsh.detach_device(vm_name, nvdimm_detach.xml, debug=True)
            libvirt.check_exit_status(result)

            vm_session.close()
            vm_session = vm.wait_for_login()

            virsh.dumpxml(vm_name, debug=True)

            left_devices = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')
            logging.debug(left_devices)

            if len(left_devices) != len(ori_devices):
                test.fail('Number of memory devices after detach is %d, should be %d'
                          % (len(left_devices), len(ori_devices)))

            time.sleep(5)
            check_file_in_vm(vm_session, '/dev/pmem1', expect=False)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()
        os.remove(nvdimm_file)
