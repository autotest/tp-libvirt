import os
import re
import logging as log
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
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_bios


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_boot_config(session, test):
    """
    Check /boot/config-$KVER file

    :param session: vm session
    :param test: test object
    """
    check_list = [
        'CONFIG_LIBNVDIMM=m',
        'CONFIG_BLK_DEV_PMEM=m',
        'CONFIG_ACPI_NFIT=m'
    ]
    libvirt_bios.check_boot_config(session, test, check_list)


def check_file_in_vm(session, path, test, expect=True):
    """
    Check whether the existence of file meets expectation

    :param session:  vm session
    :param path: str, file path to be checked
    :param test: test object
    :param expect: boolean, True to expect existence. False to not
    :raises: test.fail if checking fails
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


def create_cpuxml(params):
    """
    Create cpu xml for test

    :param params: dict, test parameters
    :return: VMCPUXML object
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

    :param mem_param: dict, test parameters
    :return: Memory object
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


def check_nvdimm_file(test_str, file_name, vm_session, test):
    """
    check if expected content exists in nvdimm memory file

    :param test_str: str, to be checked in nvdimm file
    :param file_name: str, the file name in nvdimm file
    :param vm_session: session to the vm
    :param test: test object
    :raises test.fail, if test_str is not in the file
    """
    if test_str not in vm_session.cmd('cat /mnt/%s ' % file_name):
        test.fail('"%s" should be in output' % test_str)


def is_pmem_supported(params):
    """
    check if pmem is supported by qemu-kvm

    :param params: dict, test parameters
    :return: whether pmem is supported
    """
    pmem_support_check_cmd = params.get('pmem_support_check_cmd')
    cmd_result = process.run(pmem_support_check_cmd, ignore_status=True, shell=True)
    return not cmd_result.exit_status


def setup_test_pmem_alignsize(guest_xml, params):
    """
    Setup steps for pmem and alignsize test

    :param guest_xml: guest xml
    :param params: dict, test parameters
    :return: the updated guest xml
    """
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    mem_device_attrs = eval(params.get('mem_device_attrs', '{}'))
    if not is_pmem_supported(params):
        mem_device_attrs['source']['pmem'] = False
        params.update({'qemu_checks': params.get('qemu_checks').replace('pmem', '')})

    if vm_attrs:
        guest_xml.setup_attrs(**vm_attrs)
    if mem_device_attrs:
        mem_device = memory.Memory()
        mem_device.setup_attrs(**mem_device_attrs)
        guest_xml.add_device(mem_device)
    guest_xml.sync()
    return guest_xml


def setup_test_default(guest_xml, params):
    """
    Setup steps by default

    :param guest_xml: guest xml
    :param params: dict, test parameters
    :return: the updated guest xml
    """
    # Create nvdimm file on the host
    nvdimm_file_size = params.get('nvdimm_file_size')
    nvdimm_file = params.get('nvdimm_file')
    process.run('truncate -s %s %s' % (nvdimm_file_size, nvdimm_file), verbose=True)

    if params.get('vm_attrs'):
        guest_xml = setup_test_pmem_alignsize(guest_xml, params)
        return guest_xml
    # Set cpu according to params
    cpu_xml = create_cpuxml(params)
    guest_xml.cpu = cpu_xml

    # Update other vcpu, memory info according to params
    update_vm_args = {k: params[k] for k in params
                      if k.startswith('setvm_')}
    logging.debug(update_vm_args)
    for key, value in list(update_vm_args.items()):
        attr = key.replace('setvm_', '')
        logging.debug('Set %s = %s', attr, value)
        setattr(guest_xml, attr, int(value) if value.isdigit() else value)

    # Add an nvdimm mem device to vm xml
    nvdimm_params = {k.replace('nvdimmxml_', ''): v
                     for k, v in params.items() if k.startswith('nvdimmxml_')}
    nvdimm_xml = create_nvdimm_xml(**nvdimm_params)
    guest_xml.add_device(nvdimm_xml)
    error_msg = params.get('error_msg')
    check = params.get('check')
    check_define_list = ['ppc_no_label', 'discard']
    if libvirt_version.version_compare(7, 0, 0):
        check_define_list.append('less_than_256')

    if check in check_define_list:
        result = virsh.define(guest_xml.xml, debug=True)
        libvirt.check_result(result, expected_fails=[error_msg])
        return None

    guest_xml.sync()
    logging.debug(virsh.dumpxml(params.get('main_vm')).stdout_text)
    return guest_xml


def create_file_within_nvdimm_disk(vm_session, test_file, test_str, test, block_size=0):
    """
    Create a test file in the nvdimm file disk

    :param vm_session: VM session
    :param test_file: str, file name to be used
    :param test_str: str to be written into the nvdimm file disk
    :param test: test object
    :param block_size: int, block size for mkfs.xfs -b
    """
    # Create a file system on /dev/pmem0
    bsize_str = '-b size={}'.format(block_size) if block_size != 0 else ''
    if any(platform.platform().find(ver) for ver in ('el8', 'el9')):
        cmd = 'mkfs.xfs -f {} /dev/pmem0 -m reflink=0'.format(bsize_str)
    else:
        cmd = 'mkfs.xfs -f {} /dev/pmem0'.format(bsize_str)
    output = vm_session.cmd_output(cmd)
    test.log.debug("Command '%s' output:%s", cmd, output)
    # Mount the file system with DAX enabled for page cache bypass
    cmd = 'mount -o dax /dev/pmem0 /mnt'
    output = vm_session.cmd_output(cmd)
    test.log.debug("Mount output:%s", output)

    cmd = 'echo \"%s\" >/mnt/%s' % (test_str, test_file)
    vm_session.cmd(cmd)

    check_nvdimm_file(test_str, test_file, vm_session, test)

    vm_session.cmd('umount /mnt')


def test_no_label(vm, params, test):
    """
    Test nvdimm without label setting

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    :raises: test.fail if checkpoints fail
    """
    test_str = params.get('test_str')
    vm_session = vm.wait_for_login()
    # Create a file system on /dev/pmem0
    create_file_within_nvdimm_disk(vm_session, 'foo', test_str, test)
    vm_session.close()

    # Shutdown the guest, then start it, remount /dev/pmem0,
    # check if the test file is still on the file system
    vm.destroy()
    vm.start()
    vm_session = vm.wait_for_login()

    vm_session.cmd('mount -o dax /dev/pmem0 /mnt')
    check_nvdimm_file(test_str, 'foo', vm_session, test)
    # From the host, check the file has changed:
    host_output = process.run('hexdump -C /tmp/nvdimm',
                              shell=True, verbose=True).stdout_text
    if test_str not in host_output:
        test.fail('\"%s\" should be in output' % test_str)

    # Shutdown the guest, and edit the xml,
    # include: access='private'
    vm_session.close()
    vm.destroy()
    guest_xml = vm_xml.VMXML.new_from_dumpxml(params.get('main_vm'))
    vm_devices = guest_xml.devices
    nvdimm_device = vm_devices.by_device_tag('memory')[0]
    nvdimm_index = vm_devices.index(nvdimm_device)
    vm_devices[nvdimm_index].mem_access = 'private'
    guest_xml.devices = vm_devices
    guest_xml.sync()

    # Login to the guest, mount the /dev/pmem0 and .
    # create a file: foo-private
    vm.start()
    vm_session = vm.wait_for_login()
    IS_PPC_TEST = 'ppc64le' in platform.machine().lower()
    if IS_PPC_TEST:
        libvirt.check_qemu_cmd_line('mem-path=/tmp/nvdimm,share=no')

    private_str = 'This is a test for foo-private'
    vm_session.cmd('mount -o dax /dev/pmem0 /mnt/')

    file_private = 'foo-private'
    vm_session.cmd("echo '%s' >/mnt/%s" % (private_str, file_private))
    check_nvdimm_file(private_str, file_private, vm_session, test)
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


def test_with_label(vm, params, test):
    """
    Test nvdimm with label setting

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    :raises: test.fail if checkpoints fail
    """
    test_str = params.get('test_str')
    test_file = params.get('test_file')
    vm_name = params.get('main_vm')
    vm_session = vm.wait_for_login()
    # Create a file on the nvdimm device.
    create_file_within_nvdimm_disk(vm_session, test_file, test_str, test, block_size=4096)

    # Reboot the guest, and remount the nvdimm device in the guest.
    # Check the file foo-label is exited
    vm_session.close()
    virsh.reboot(vm_name, debug=True)
    vm_session = vm.wait_for_login()

    vm_session.cmd('mount -o dax /dev/pmem0  /mnt')
    if test_str not in vm_session.cmd('cat /mnt/foo-label '):
        test.fail('"%s" should be in output' % test_str)
    vm_session.close()
    if params.get('check_life_cycle', 'no') == 'yes':
        virsh.managedsave(vm_name, ignore_status=False, debug=True)
        vm.start()
        vm_session = vm.wait_for_login()
        check_nvdimm_file(test_str, test_file, vm_session, test)
        vm_session.close()
        vm_s1 = vm_name + ".s1"
        virsh.save(vm_name, vm_s1, ignore_status=False, debug=True)
        virsh.restore(vm_s1, ignore_status=False, debug=True)
        vm_session = vm.wait_for_login()
        check_nvdimm_file(test_str, test_file, vm_session, test)
        vm_session.close()
        virsh.snapshot_create_as(vm_name, "%s --disk-only" % vm_s1,
                                 ignore_status=False, debug=True)
        revert_result = virsh.snapshot_revert(vm_name, vm_s1, debug=True)
        if libvirt_version.version_compare(9, 9, 0):
            libvirt.check_exit_status(revert_result)
        else:
            libvirt.check_result(
                revert_result,
                expected_fails=[
                    params.get('error_msg_1'),
                    params.get('error_msg_2')
                ]
            )

        if libvirt_version.version_compare(9, 9, 0):
            virsh.snapshot_delete(vm_name, vm_s1,
                                  ignore_status=False, debug=True)
        else:
            virsh.snapshot_delete(vm_name,
                                  "%s --metadata" % vm_s1,
                                  ignore_status=False, debug=True)
            snap_file_path = libvirt_disk.get_first_disk_source(vm)
            if os.path.exists(snap_file_path):
                os.remove(snap_file_path)


def test_hotplug(vm, params, test):
    """
    Test nvdimm device hotplug

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    :raises: test.fail if checkpoints fail
    """
    vm_name = params.get('main_vm')
    nvdimm_file_2 = params.get('nvdimm_file_2')
    process.run('truncate -s 512M %s' % nvdimm_file_2)
    vm_session = vm.wait_for_login()
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
    IS_PPC_TEST = 'ppc64le' in platform.machine().lower()
    if IS_PPC_TEST:
        logging.debug(vm_session.cmd_output(
            'ndctl create-namespace --mode=fsdax --region=region1'))

    time.sleep(int(params.get('wait_sec', 5)))
    check_file_in_vm(vm_session, '/dev/pmem1', test)
    alive_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
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

    time.sleep(int(params.get('wait_sec', 5)))
    check_file_in_vm(vm_session, '/dev/pmem1', test, expect=False)


def test_pmem_alignsize(vm, params, test):
    """
    Test nvdimm with pmem and alignsize setting

    :param vm: vm object
    :param params: dict, test parameters
    :param test: test object
    :raises: test.fail if checkpoints fail
    """
    error_msg = eval(params.get('error_msg'))
    libvirt.check_qemu_cmd_line(params.get('qemu_checks'))

    vm_session = vm.wait_for_login()
    # Create a file on the nvdimm device.
    output = vm_session.cmd_output('mkfs.xfs -f /dev/pmem0'.format())

    if not re.search(error_msg[0], output):
        test.fail("The error '{}' should be in the output \n'{}'".format(error_msg[0], output))
    output = vm_session.cmd_output('mount /dev/pmem0 /mnt')
    if not re.search(error_msg[1], output):
        test.fail("The error '{}' should be in the output \n'{}'".format(error_msg[1], output))

    vm_session.close()


def cleanup_test_default(params):
    """
    Cleanup steps for the test by default

    :param params: dict, test parameters
    :return:
    """
    for one_file in [params.get('nvdimm_file_2'), params.get('nvdimm_file')]:
        if one_file:
            os.remove(one_file)


def run(test, params, env):
    """
    Test memory management of nvdimm
    """
    vm_name = params.get('main_vm')
    check = params.get('check', '')
    status_error = "yes" == params.get('status_error', 'no')
    error_msg = params.get('error_msg', '')
    qemu_checks = params.get('qemu_checks', '').split('`')

    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    IS_PPC_TEST = 'ppc64le' in platform.machine().lower()
    if IS_PPC_TEST:
        if not libvirt_version.version_compare(6, 2, 0):
            test.cancel('Libvirt version should be > 6.2.0'
                        ' to support nvdimm on pseries')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        vmxml = setup_test_default(vmxml, params)
        if not vmxml:
            return

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

        # Check if the guest support NVDIMM:
        # check /boot/config-$KVER file
        vm_session = vm.wait_for_login()
        if not IS_PPC_TEST:
            check_boot_config(vm_session, test)

        # ppc test requires ndctl
        if IS_PPC_TEST:
            if not utils_package.package_install('ndctl', session=vm_session):
                test.error('Cannot install ndctl to vm')
            logging.debug(vm_session.cmd_output(
                'ndctl create-namespace --mode=fsdax --region=region0'))

        # check /dev/pmem0 existed inside guest
        check_file_in_vm(vm_session, '/dev/pmem0', test)

        if check == 'back_file':
            test_no_label(vm, params, test)
        if check == 'pmem_alignsize':
            test_pmem_alignsize(vm, params, test)
        if check == 'label_back_file':
            test_with_label(vm, params, test)
        if check == 'hot_plug':
            test_hotplug(vm, params, test)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()
        cleanup_test_default(params)
