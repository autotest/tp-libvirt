# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os
import re
import json

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base


def check_nvdimm_config(params, test, mem_index=0):
    """
    Check nvdimm memory devices xml config.

    :param params: Optional new VM creation parameters.
    :param test: test object.
    :param mem_index: memory index in memory list, default 0.
    """
    address_config = params.get("address_config")
    addr_slot = [params.get("addr_slot"), params.get("addr_slot_attach")]
    addr_base = [params.get("addr_base"), params.get("addr_base_attach")]
    alias_name = eval(params.get("alias_name", "[]"))
    target = [eval(params.get("target_attrs")[10:]),
              eval(params.get("target_attach_attrs")[10:])]
    source = [eval(params.get("source_attrs")[10:]),
              eval(params.get("source_attach_attrs")[10:])]

    vmxml = vm_xml.VMXML.new_from_dumpxml(params.get("main_vm"))
    actual_attrs = vmxml.devices.by_device_tag('memory')[mem_index].fetch_attrs()
    test.log.debug("Get actual memory attrs: %s", actual_attrs)

    def _compare_values(expected, actual):
        if expected != actual:
            test.fail("Expect to get '%s' instead of '%s' " % (expected, actual))
        else:
            test.log.debug("Check %s PASS", actual)
    _compare_values(source[mem_index], actual_attrs['source'])
    _compare_values(target[mem_index], actual_attrs['target'])
    _compare_values(alias_name[mem_index], actual_attrs['alias']['name'])
    if address_config != "addr_undefined":
        _compare_values(addr_slot[mem_index], actual_attrs['address']['attrs']['slot'])
        _compare_values(addr_base[mem_index], actual_attrs['address']['attrs']['base'])


def check_nvdimm_device_content(test, params, session, expected_content,
                                existed=True):
    """
    Check the nvdimm device content exist or not.

    :param test: test object.
    :param params: Optional new VM creation parameters.
    :param session: guest session.
    :param expected_content: expected file content.
    :param existed: bool type, True if expected_content existed.
    """
    mount_file_1 = params.get("mount_file_1")
    mount_file_2 = params.get("mount_file_2")
    nvdimm_devices = params.get("nvdimm_devices").split(" ")
    paths = eval(params.get("paths"))

    def _get_uuid(vm_session):
        uuids = []
        for dev in nvdimm_devices:
            uuid_out = vm_session.cmd_output('blkid %s' % dev)
            uuid = re.findall(r' UUID="(\S+)"', uuid_out)[0]
            if not uuid:
                test.fail("Expect to get uuid in '%s'" % uuid_out)
            uuids.append(uuid)
        params.update({'uuids': uuids})

    if existed:
        # Check if mounted and remount
        for index in range(len(paths)):
            if not session.cmd_output("mount | grep '%s'" % paths[index]):
                if not params.get("uuids"):
                    _get_uuid(session)
                session.cmd_output('mount -o dax -U {} {}'.format(
                    params.get('uuids')[index], paths[index]))

        # Check file content
        for item in [mount_file_1, mount_file_2]:
            output = session.cmd_output('cat %s ' % item).strip()
            if expected_content not in output:
                test.fail('"%s" should be in output:"%s"' % (expected_content, output))
        test.log.debug("Check '%s' in '%s' is '%s' PASS",
                       expected_content, output, [mount_file_1, mount_file_2])
    else:
        # Check blkid is not existed.
        for dev in nvdimm_devices:
            output = session.cmd_output('blkid %s ' % dev).strip()
            if output:
                test.fail("Expect to get no blkid, but got:'%s'" % output)


def check_alignment_value(params, test, check_cmd):
    """
    Check guest alignment value by virsh qemu_monitor_command

    :param params: dictionary with the test parameters
    :param test: test object
    :param check_cmd: the cmd option of virsh qemu_monitor_command
    """
    vm_name = params.get("main_vm")
    expected_align = params.get("expected_align")

    result = virsh.qemu_monitor_command(vm_name, check_cmd, debug=True).stdout_text
    actual_align = str(json.loads(result)['return'])
    if actual_align != expected_align:
        test.fail("Expected alignment value '%s' instead of '%s'" % (
            expected_align, actual_align))


def do_domain_lifecyle(params, vm, test):
    """
    Do lifecycle for guest.

    :param params: Dictionary with the test parameters
    :param vm: vm object
    :param test: test object
    """
    virsh_dargs = {'debug': True, 'ignore_status': False}
    target_config = params.get("target_config")
    file_content = params.get("file_content")

    def _check_after_operation():
        session = vm.wait_for_login()
        if target_config != "target_readonly":
            check_nvdimm_device_content(test, params, session, file_content)
        check_nvdimm_config(params, test)
        check_nvdimm_config(params, test, mem_index=1)
        session.close()

    virsh.suspend(vm.name, **virsh_dargs)
    virsh.resume(vm.name, **virsh_dargs)
    _check_after_operation()

    virsh.reboot(vm.name, **virsh_dargs)
    _check_after_operation()

    save_file = params.get("save_file")
    if os.path.exists(save_file):
        os.remove(save_file)
    virsh.save(vm.name, save_file, **virsh_dargs)
    virsh.restore(save_file, **virsh_dargs)
    _check_after_operation()

    virsh.managedsave(vm.name, **virsh_dargs)
    vm.start()
    _check_after_operation()

    virsh.reboot(vm.name, **virsh_dargs)
    _check_after_operation()

    Libvirtd().restart()
    _check_after_operation()


def run(test, params, env):
    """
    Verify various configs of file backed nvdimm memory device take effect
    during the life cycle of guest vm.
    """

    def setup_test():
        """
        Create file backend for nvdimm device
        """
        test.log.info("Setup env.")
        for path in [source_path, source_path_attach]:
            process.run('truncate -s %s %s' % (nvdimm_file_size, path),
                        verbose=True, shell=True)

    def run_test():
        """
        1. Define a vm with a nvdimm memory device and check the nvdimm configuration.
        2. Hotplug an nvdimm memory device and check the nvdimm configuration.
        3. Check the alignment with the virsh qemu-monitor-command.
        4. Create a file system on two nvdimm devices in the guest.
        6. Lifecycle checks for a guest with an attached device.
        7. Hotplug another nvdimm device.
         """
        test.log.info("TEST_STEP1: Define vm with nvdimm memory device")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        mem_obj = libvirt_vmxml.create_vm_device_by_type('memory', nvdimm_dict)
        vmxml.devices = vmxml.devices.append(mem_obj)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start guest")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Check nvdimm memory device xml")
        check_nvdimm_config(params, test)

        test.log.info("TEST_STEP4: Hot plug all memory device")
        attach_mem = libvirt_vmxml.create_vm_device_by_type('memory', nvdimm_attach_dict)
        virsh.attach_device(vm_name, attach_mem.xml, **virsh_dargs)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug('Get xml after attaching memory is "%s"', vmxml)

        test.log.info("TEST_STEP5: Check nvdimm memory device xml")
        check_nvdimm_config(params, test)
        check_nvdimm_config(params, test, mem_index=1)

        test.log.info("TEST_STEP6:Check alignment by virsh.qemu-monitor-command")
        check_alignment_value(params, test, check_alignment % alias_name[0])
        check_alignment_value(params, test, check_alignment % alias_name[1])

        test.log.info("TEST_STEP7: Login guest and check nvdimm memory device")
        session = vm.wait_for_login()
        for dev in nvdimm_devices:
            status, output = session.cmd_status_output("ls %s" % dev)
            if status:
                test.fail("Expect nvdimm device in guest, but got: %s" % output)

        test.log.info("TEST_STEP8: Create file system on two nvdimme device")
        memory_base.create_file_within_nvdimm_disk(
            test, session, test_device=nvdimm_devices[0],
            mount_point=nvdimm_path, test_file=mount_file_1,
            test_str=file_content, error_msg=error_msg)
        memory_base.create_file_within_nvdimm_disk(
            test, session, test_device=nvdimm_devices[1],
            mount_point=nvdimm_path_attach, test_file=mount_file_2,
            test_str=file_content, error_msg=error_msg)
        session.close()

        test.log.info("TEST_STEP9: Do lifecycle")
        do_domain_lifecyle(params, vm, test)

        if target_config != "target_readonly":
            test.log.info("TEST_STEP10: Attach device again")
            virsh.destroy(vm_name)
            virsh.start(vm_name)
            virsh.attach_device(vm_name, attach_mem.xml, **virsh_dargs)

            test.log.info("TEST_STEP11: Login the guest to check the file")
            session = vm.wait_for_login()
            check_nvdimm_device_content(test, params, session, file_content,
                                        existed=content_existed)
            session.close()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        for file in [mount_file_1, mount_file_2]:
            session.cmd("rm -f %s " % file)
        session.close()

        bkxml.sync()

        for file in [source_path, source_path_attach]:
            if os.path.exists(file):
                os.remove(file)

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    vm_attrs = eval(params.get("vm_attrs", "{}"))
    nvdimm_dict = eval(params.get("nvdimm_dict"))
    nvdimm_attach_dict = eval(params.get("nvdimm_attach_dict"))
    error_msg = params.get("error_msg")
    nvdimm_path = params.get("nvdimm_path")
    nvdimm_path_attach = params.get("nvdimm_path_attach")
    source_path = params.get("source_path")
    source_path_attach = params.get("source_path_attach")
    mount_file_1 = params.get("mount_file_1")
    mount_file_2 = params.get("mount_file_2")
    file_content = params.get("file_content")
    nvdimm_file_size = params.get("nvdimm_file_size")
    nvdimm_devices = params.get("nvdimm_devices").split(" ")
    target_config = params.get("target_config",)
    content_existed = bool(params.get("content_existed"))
    check_alignment = params.get("check_alignment")
    alias_name = eval(params.get("alias_name", "[]"))
    virsh_dargs = {'debug': True, 'ignore_status': False}

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
