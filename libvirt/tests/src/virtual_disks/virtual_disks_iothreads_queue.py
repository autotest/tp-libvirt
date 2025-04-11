import os
import time

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_disk
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def prepare_vm_xml(params, vmxml, test):
    """
    Configure the XML definition of the VM

    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    :return: updated VMXML instance
    """
    vm_attrs = eval(params.get("vm_attrs"))
    plug_type = params.get("plug_type")
    vmxml.setup_attrs(**vm_attrs)
    if plug_type == 'coldplug':
        disk_dict = eval(params.get("disk_dict", "{}"))
        if disk_dict:
            libvirt_vmxml.modify_vm_device(vmxml,
                                           'disk',
                                           dev_dict=disk_dict,
                                           sync_vm=False)
    test.log.debug("Updated vm xml is %s", vmxml)
    return vmxml


def teardown_test(vm, params, vmxml, test):
    """
    Clean up the test

    :param params: dict, test parameters
    :param vm:     VM instance
    :param vmxml:  VMXML instance
    """
    if vm.is_alive():
        virsh.destroy(vm.name)
    vmxml.sync()
    disks = params.get('remove_disks', [])
    for a_disk in disks:
        libvirt.delete_local_disk("file", a_disk)
        test.log.debug("Remove disk %s", a_disk)


def run_common(params, vmxml, test):
    """
    Common test steps

    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    """
    vm_name = params.get("main_vm")
    virsh_dargs = {"debug": True, "ignore_status": False}

    test.log.debug("Step: define the vm")
    virsh.define(vmxml.xml, **virsh_dargs)
    test.log.debug("Step: start the vm")
    virsh.start(vm_name, **virsh_dargs)
    test.log.debug("After vm is started, "
                   "vm xml:%s\n", vm_xml.VMXML.new_from_dumpxml(vm_name))


def run_test_define_invalid(vm, params, vmxml, test):
    """
    Test vm definition with invalid configurations

    :param vm: vm instance
    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    """
    err_msg = params.get("err_msg")
    virsh_dargs = {"debug": True, "ignore_status": True}
    test.log.debug("Step: define the vm")
    ret = virsh.define(vmxml.xml, **virsh_dargs)
    libvirt.check_result(ret, expected_fails=err_msg)


def prepare_disk_for_hotplug(params):
    """
    Prepare a disk object for hotplugging

    :param params: dict, test parameters
    :return Disk: disk object
    """
    new_image_name = params.get("new_image_name")
    disk_source_path = \
        os.path.join(data_dir.get_data_dir(), new_image_name)
    libvirt.create_local_disk("file", path=disk_source_path,
                              size="1", disk_format='qcow2')
    params['remove_disks'] = [disk_source_path]
    new_disk_dict = eval(params.get("new_disk_dict") % disk_source_path)
    disk_obj = Disk(type_name='file')
    disk_obj.setup_attrs(**new_disk_dict)
    return disk_obj


def verify_hotplugged_disk(vm_name, params, session, old_part_list, test):
    """
    Verify the hotplugged disk within the vm and in vm xml

    :param vm_name: str, vm name
    :param params: dict, test parameters
    :param session: ShellSession instance
    :param old_part_list: list, disk partition list
    :param test: test object
    :return list: added partition list
    """
    new_driver_iothreads = eval(params.get("new_driver_iothreads"))
    added_part = utils_disk.get_added_parts(session, old_part_list)
    if not added_part:
        test.fail("Can not find the hotplugged disk in VM")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disks = vmxml.get_devices('disk')
    if len(disks) < 2:
        test.fail("Can not find the hotplugged disk in VM xml")
    plugged_disk = vmxml.get_devices('disk')[1]
    plugged_disk_driver_iothreads = plugged_disk.driver_iothreads.fetch_attrs()
    if plugged_disk_driver_iothreads != new_driver_iothreads:
        test.fail("Expect the plugged disk driver "
                  "iothreads to be '%s', "
                  "but found '%s'" % (new_driver_iothreads,
                                      plugged_disk_driver_iothreads))
    test.log.debug("Verify: the disk driver iothreads information "
                   "is as expected after hotplugging - PASS")
    return added_part


def verify_hotunplugged_disk(vm_name, params, added_part, session, test):
    """
    Verify the hotplugged disk information within vm and vm xml

    :param vm_name: str, vm name
    :param params: dict, test parameters
    :param added_part: str, disk newly added, like vdb
    :param session: ShellSession instance
    :param test: test object
    """
    new_target_dev = params.get("new_target_dev")
    non_root_disks = libvirt_disk.get_non_root_disk_names(session, ignore_status=True)
    if non_root_disks and added_part[0] in non_root_disks:
        test.fail("The unplugged disk '%s' is still found in VM" % added_part)
    test.log.debug("Verify: Check lsblk in VM after hotunlug the disk - PASS")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    current_disks = vmxml.get_devices('disk')
    for a_disk in current_disks:
        if a_disk.target['dev'] == new_target_dev:
            test.fail("The hotunplugged disk '%s' is "
                      "still found in VM XML" % new_target_dev)
    test.log.debug("Verify: Check hotplugged disk in "
                   "VM xml after unhotplug the disk - PASS")


def test_with_hotplug(vm, params, vmxml, test):
    """
    Test hotplug unplug a disk with driver iothreads to the vm

    :param vm: vm instance
    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    """
    virsh_dargs = {"debug": True, "ignore_status": False}
    vm_name = params.get("main_vm")
    check_libvirtd_log = params.get("check_libvirtd_log")
    new_target_dev = params.get("new_target_dev")

    run_common(params, vmxml, test)
    session = vm.wait_for_login()
    old_part_list = utils_disk.get_parts_list(session)

    test.log.debug("Step: prepare a disk")
    disk_obj = prepare_disk_for_hotplug(params)

    test.log.debug("Step: hotplug the disk to the vm")
    virsh.attach_device(vm_name, disk_obj.xml, **virsh_dargs)
    time.sleep(20)
    added_part = verify_hotplugged_disk(vm_name,
                                        params,
                                        session,
                                        old_part_list,
                                        test)
    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    libvirt.check_logfile(check_libvirtd_log, libvirtd_log_file)
    test.log.debug("Verify: Check libvirtd log - PASS")

    test.log.debug("Step: unhotplug the disk from the vm")
    virsh.detach_disk(vm_name, new_target_dev, wait_for_event=True, **virsh_dargs)
    verify_hotunplugged_disk(vm_name, params, added_part, session, test)


def test_with_coldplug(vm, params, vmxml, test):
    """
    Test coldplug a disk (define) with driver iothreads to the vm

    :param vm: vm instance
    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    """
    driver_conf = eval(params.get("driver", "{}"))
    driver_iothreads_conf = eval(params.get("driver_iothreads", "{}"))
    check_qemu_pattern = params.get("check_qemu_pattern")

    run_common(params, vmxml, test)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    first_disk = vmxml.get_devices('disk')[0]
    expect_value = driver_conf.get("queues", '')
    if expect_value and first_disk.driver.get("queues") != expect_value:
        test.fail("Expect the disk driver "
                  "'queues' to be '%s', "
                  "but found '%s'" % (expect_value,
                                      first_disk.driver.get("queues")))
    test.log.debug("Verify: Check disk driver queues number - PASS")
    actual_driver_iothreads = first_disk.driver_iothreads.fetch_attrs()
    if driver_iothreads_conf and actual_driver_iothreads != driver_iothreads_conf:
        test.fail("Expect the disk "
                  "driver iothreads to be '%s', "
                  "but found '%s'" % (driver_iothreads_conf,
                                      actual_driver_iothreads))
    test.log.debug("Verify: Check disk driver iothreads - PASS")
    if check_qemu_pattern:
        libvirt.check_qemu_cmd_line(check_qemu_pattern)


def run_test_define_start(vm, params, vmxml, test):
    """
    Test define and start vm with specified driver iothreads configuration

    :param vm: vm instance
    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    """
    plug_type = params.get("plug_type")
    if plug_type == "coldplug":
        test_with_coldplug(vm, params, vmxml, test)
    else:
        test_with_hotplug(vm, params, vmxml, test)


def run_test_update_delete_iothread(vm, params, vmxml, test):
    """
    Test to delete an iothread being used by a disk

    :param vm: vm instance
    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    """
    vm_name = params.get("main_vm")
    err_msg = params.get("err_msg")
    del_iothread_id = params.get("del_iothread_id")
    virsh_dargs = {"debug": True, "ignore_status": True}

    run_common(params, vmxml, test)
    test.log.debug("Step: delete an iothread")
    ret = virsh.iothreaddel(vm_name, del_iothread_id, **virsh_dargs)
    libvirt.check_result(ret, expected_fails=err_msg)


def run_test_live_update_disk(vm, params, vmxml, test):
    """
    Test to live update iothread_vq_mapping of a disk.

    :param vm: vm instance
    :param params: dict, test parameters
    :param vmxml:  VMXML instance
    :param test:   test object
    """

    def _get_driver_iothreads(vmxml):
        """
        Utility function for getting the device object and driver iothreads

        :param vmxml: VMXML instance
        :return tuple: device xml object, dict of driver_iothreads
        """
        dev_obj, _ = libvirt.get_vm_device(vmxml, "disk")
        iothreads = None
        if dev_obj.fetch_attrs().get("driver_iothreads") is not None:
            iothreads = dev_obj.driver_iothreads
        return dev_obj, iothreads

    vm_name = params.get("main_vm")
    status_error = "yes" == params.get("status_error", "no")
    err_msg = params.get("err_msg")
    old_driver_iothreads = eval(params.get("driver_iothreads"))
    new_driver_iothreads = eval(params.get("new_driver_iothreads"))
    virsh_dargs = {"debug": True, "ignore_status": True}

    run_common(params, vmxml, test)
    test.log.debug("Step: update the disk with new driver iothreads")
    dev_obj, iothreads = _get_driver_iothreads(vmxml)
    iothreads.update(**new_driver_iothreads)
    dev_obj.driver_iothreads = iothreads
    test.log.debug("The new disk xml is:\n%s", dev_obj)
    ret = virsh.update_device(vm_name, dev_obj.xml, **virsh_dargs)
    updated_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    _, new_iothreads = _get_driver_iothreads(updated_vmxml)
    if new_iothreads is None and old_driver_iothreads != {}:
        test.fail(
            "Expect driver iothreads in the disk "
            "to be '%s', but found None" % (old_driver_iothreads)
        )
    elif (
        new_iothreads is not None
        and new_iothreads.fetch_attrs() != old_driver_iothreads
    ):
        test.fail(
            "Expect driver iothreads in the disk "
            "to be '%s', but found '%s'"
            % (old_driver_iothreads, new_iothreads.fetch_attrs())
        )
    else:
        test.log.debug("Verify: the disk's driver iothreads is not changed - PASS")
    libvirt.check_exit_status(ret, status_error)
    if err_msg:
        libvirt.check_result(ret, expected_fails=err_msg)


def run(test, params, env):
    """
    Test disk driver iothreads

    1.Update vm xml using given parameters
    2.Perform test operation and verify checkpoints
    3.Recover test environment
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    test_scenario = params.get("scenario")
    case_name = "run_test_%s" % test_scenario
    run_test_case = eval(case_name)
    try:
        if vm.is_alive():
            vm.destroy()
        test.log.info("Step: Prepare VM XML")
        vmxml = prepare_vm_xml(params, vmxml, test)
        run_test_case(vm, params, vmxml, test)

    finally:
        teardown_test(vm, params, vmxml_backup, test)
