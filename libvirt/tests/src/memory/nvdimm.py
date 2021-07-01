import os
import logging
import time
import platform

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest import utils_hotplug
from virttest import utils_package
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
    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    qemu_checks = params.get('qemu_checks', '').split('`')
    wait_sec = int(params.get('wait_sec', 5))
    test_str = 'This is a test'

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
        if 'cpuxml_numa_cell' in cpu_params:
            cpu_params['cpuxml_numa_cell'] = cpu_xml.dicts_to_cells(
                eval(cpu_params['cpuxml_numa_cell']))
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
            mem_discard=mem_param.get('discard'),
            mem_model="nvdimm",
            lb_size=mem_param.get('label_size'),
            lb_sizeunit=mem_param.get('label_size_unit'),
            mem_access=mem_param['mem_access'],
            uuid=mem_param.get('uuid')
        )

        source_xml = memory.Memory.Source()
        source_xml.path = mem_param['source_path']
        mem_xml.source = source_xml
        logging.debug(mem_xml)

        return mem_xml.copy()

    def check_nvdimm_file(file_name):
        """
        check if the file exists in nvdimm memory device

        :param file_name: the file name in nvdimm device
        """
        vm_session = vm.wait_for_login()
        if test_str not in vm_session.cmd('cat /mnt/%s ' % file_name):
            test.fail('"%s" should be in output' % test_str)

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    IS_PPC_TEST = 'ppc64le' in platform.machine().lower()
    if IS_PPC_TEST:
        if not libvirt_version.version_compare(6, 2, 0):
            test.cancel('Libvirt version should be > 6.2.0'
                        ' to support nvdimm on pseries')

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
        logging.debug(virsh.dumpxml(vm_name).stdout_text)

        # Add an nvdimm mem device to vm xml
        nvdimm_params = {k.replace('nvdimmxml_', ''): v
                         for k, v in params.items() if k.startswith('nvdimmxml_')}
        nvdimm_xml = create_nvdimm_xml(**nvdimm_params)
        vmxml.add_device(nvdimm_xml)
        check_define_list = ['ppc_no_label', 'discard']
        if libvirt_version.version_compare(7, 0, 0):
            check_define_list.append('less_than_256')
        if check in check_define_list:
            result = virsh.define(vmxml.xml, debug=True)
            libvirt.check_result(result, expected_fails=[error_msg])
            return
        vmxml.sync()
        logging.debug(virsh.dumpxml(vm_name).stdout_text)

        if IS_PPC_TEST:
            # Check whether uuid is automatically created
            new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if not new_xml.xml.find('/devices/memory/uuid'):
                test.fail('uuid should be generated automatically.')
            vm_nvdimm_xml = new_xml.get_devices('memory')[0]
            qemu_checks.append('uuid=%s' % vm_nvdimm_xml.uuid)

            # Check memory target size
            target_size = vm_nvdimm_xml.target.size
            logging.debug('Target size: %s', target_size)

            if check == 'less_than_256':
                if not libvirt_version.version_compare(7, 0, 0):
                    result = virsh.start(vm_name, debug=True)
                    libvirt.check_exit_status(result, status_error)
                    libvirt.check_result(result, error_msg)
                    return

        virsh.start(vm_name, debug=True, ignore_status=False)

        # Check qemu command line one by one
        if IS_PPC_TEST:
            list(map(libvirt.check_qemu_cmd_line, qemu_checks))

        alive_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        # Check if the guest support NVDIMM:
        # check /boot/config-$KVER file
        vm_session = vm.wait_for_login()
        if not IS_PPC_TEST:
            check_boot_config(vm_session)

        # ppc test requires ndctl
        if IS_PPC_TEST:
            if not utils_package.package_install('ndctl', session=vm_session):
                test.error('Cannot install ndctl to vm')
            logging.debug(vm_session.cmd_output(
                'ndctl create-namespace --mode=fsdax --region=region0'))

        # check /dev/pmem0 existed inside guest
        check_file_in_vm(vm_session, '/dev/pmem0')

        if check == 'back_file':
            # Create a file system on /dev/pmem0
            if any(platform.platform().find(ver) for ver in ('el8', 'el9')):
                vm_session.cmd('mkfs.xfs -f /dev/pmem0 -m reflink=0')
            else:
                vm_session.cmd('mkfs.xfs -f /dev/pmem0')

            vm_session.cmd('mount -o dax /dev/pmem0 /mnt')
            vm_session.cmd('echo \"%s\" >/mnt/foo' % test_str)
            vm_session.cmd('umount /mnt')
            vm_session.close()

            # Shutdown the guest, then start it, remount /dev/pmem0,
            # check if the test file is still on the file system
            vm.destroy()
            vm.start()
            vm_session = vm.wait_for_login()

            vm_session.cmd('mount -o dax /dev/pmem0 /mnt')
            if test_str not in vm_session.cmd('cat /mnt/foo'):
                test.fail('\"%s\" should be in /mnt/foo' % test_str)

            # From the host, check the file has changed:
            host_output = process.run('hexdump -C /tmp/nvdimm',
                                      shell=True, verbose=True).stdout_text
            if test_str not in host_output:
                test.fail('\"%s\" should be in output' % test_str)

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

            if IS_PPC_TEST:
                libvirt.check_qemu_cmd_line('mem-path=/tmp/nvdimm,share=no')

            private_str = 'This is a test for foo-private'
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
            if any(platform.platform().find(ver) for ver in ('el8', 'el9')):
                vm_session.cmd('mkfs.xfs -f -b size=4096 /dev/pmem0 -m reflink=0')
            else:
                vm_session.cmd('mkfs.xfs -f -b size=4096 /dev/pmem0')

            # Mount the file system with DAX enabled for page cache bypass
            output = vm_session.cmd_output('mount -o dax /dev/pmem0 /mnt/')
            logging.info(output)

            # Create a file on the nvdimm device.
            test_str = 'This is a test with label'
            vm_session.cmd('echo "%s" >/mnt/foo-label' % test_str)
            if test_str not in vm_session.cmd('cat /mnt/foo-label '):
                test.fail('"%s" should be in the output of cat cmd' % test_str)

            vm_session.cmd('umount /mnt')
            # Reboot the guest, and remount the nvdimm device in the guest.
            # Check the file foo-label is exited
            vm_session.close()
            virsh.reboot(vm_name, debug=True)
            vm_session = vm.wait_for_login()

            vm_session.cmd('mount -o dax /dev/pmem0  /mnt')
            if test_str not in vm_session.cmd('cat /mnt/foo-label '):
                test.fail('"%s" should be in output' % test_str)

            if params.get('check_life_cycle', 'no') == 'yes':
                virsh.managedsave(vm_name, ignore_status=False, debug=True)
                vm.start()
                check_nvdimm_file('foo-label')

                vm_s1 = vm_name + ".s1"
                virsh.save(vm_name, vm_s1, ignore_status=False, debug=True)
                virsh.restore(vm_s1, ignore_status=False, debug=True)
                check_nvdimm_file('foo-label')

                virsh.snapshot_create_as(vm_name, vm_s1, ignore_status=False, debug=True)
                virsh.snapshot_revert(vm_name, vm_s1, ignore_status=False, debug=True)
                virsh.snapshot_delete(vm_name, vm_s1, ignore_status=False, debug=True)

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

            # Create namespace for ppc tests
            if IS_PPC_TEST:
                logging.debug(vm_session.cmd_output(
                    'ndctl create-namespace --mode=fsdax --region=region1'))

            time.sleep(wait_sec)
            check_file_in_vm(vm_session, '/dev/pmem1')

            nvdimm_detach = alive_vmxml.get_devices('memory')[-1]
            logging.debug(nvdimm_detach)

            # Hot-unplug nvdimm device
            result = virsh.detach_device(vm_name, nvdimm_detach.xml, debug=True)
            libvirt.check_exit_status(result)

            vm_session.close()
            vm_session = vm.wait_for_login()

            logging.debug(virsh.dumpxml(vm_name).stdout_text)

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
        if 'nvdimm_file_2' in locals():
            os.remove(nvdimm_file_2)
