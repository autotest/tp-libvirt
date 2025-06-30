from virttest import libvirt_version
from virttest import virsh
from virttest import utils_disk

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import controller
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test driver queues attributes of scsi controller

    1. Start the guest with scsi controller whose multiqueue configured.
    2. Check the guest xml.
    3. Check the qemu command line.
    4. Write datas and check the result.
    """
    def prepare_vm_xml():
        """
        Prepare VM XML for test.
        """
        vmxml.setup_attrs(**vm_attrs)
        test.log.debug("Remove the existing scsi controller.")
        vmxml.del_controller("scsi")
        controller_obj = controller.Controller(type_name="controller")
        controller_obj.setup_attrs(**controller_dict)
        vmxml.add_device(controller_obj)
        vmxml.sync()

        if disk_type == "block":
            new_image_path = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        else:
            new_image_path = ""
        disk_obj.add_vm_disk(disk_type, disk_dict, new_image_path)
        test.log.info("TEST_STEP1: start the guest.")
        virsh.start(vm_name, debug=True, ignore_status=False)
        test.log.debug("The current guest xml is: %s" % virsh.dumpxml(vm_name).stdout_text)

    def check_result():
        """
        Check the scsi controller xml and qemu command line.
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        scsi_controller = vmxml.get_devices("controller")
        expect_value = driver_dict.get("queues")
        test.log.info("TEST_STEP2: check the scsi controller xml.")
        for dev in scsi_controller:
            if dev.type == "scsi":
                actual_driver_iothreads = dev.driver_iothreads.fetch_attrs()
                if dev.driver.get("queues") != expect_value:
                    test.fail("Expect the scsi controller queues to be '%s',"
                              "but found '%s'." % (expect_value, dev.driver.get("queues")))
                if actual_driver_iothreads != driver_iothreads:
                    test.fail("Expect the scsi controller iothread to be '%s',"
                              "but found '%s'." % (driver_iothreads, actual_driver.iothreads))
        test.log.info("TEST_STEP3: check the qemu command line.")
        if check_qemu_pattern:
            libvirt.check_qemu_cmd_line(check_qemu_pattern)

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    disk_type = params.get("disk_type")
    vm_attrs = eval(params.get("vm_attrs"))
    disk_dict = eval(params.get("disk_dict", "{}"))
    driver_dict = eval(params.get("driver_dict"))
    driver_iothreads = eval(params.get("driver_iothreads", "{}"))
    controller_dict = eval(params.get("controller_dict", "{}"))
    check_qemu_pattern = params.get("check_qemu_pattern")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        prepare_vm_xml()
        check_result()
        test.log.info("TEST_STEP4: writes datas to guest scsi disk.")
        vm_session = vm.wait_for_login()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, "/dev/%s" % new_disk, bs="1M", count="10")
        vm_session.close()
    finally:
        bkxml.sync()
        if disk_type == "file":
            disk_obj.cleanup_disk_preparation(disk_type)
        else:
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
