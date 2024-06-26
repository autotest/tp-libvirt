import os
from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_disk
from virttest import utils_vdpa
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Verify vm lifecycle operations
    """
    def check_disk_io(vm_session, disk, cmd_in_vm, mount_dst="/mnt", is_mount=True):
        """
        Check disk I/O in guest

        :param vm_session: vm session
        :param disk: disk name
        :param cmd_in_vm: cmd to check disk io
        :param mount_dst: mount destination
        :param is_mount: Whether the disk is mounted
        """
        if is_mount:
            if not utils_disk.is_mount(f"/dev/{disk}", mount_dst, session=vm_session):
                test.fail("%s should be mounted!" % disk)
        else:
            utils_disk.mount(f"/dev/{disk}", mount_dst, session=vm_session)
        vm_session.cmd(cmd_in_vm)

    libvirt_version.is_libvirt_feature_supported(params)
    disk_attrs = eval(params.get("disk_attrs", "{}"))
    cmd_in_vm = "d=`date +%s`; echo $d >> /mnt/test; sync; grep $d /mnt/test"
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_STEP: Define a VM with vhostvdpa disk.")
        test_env_obj = utils_vdpa.VDPASimulatorTest(
            sim_dev_module="vdpa_sim_blk", mgmtdev="vdpasim_blk")
        test_env_obj.setup()
        vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False)
        libvirt_vmxml.modify_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), "disk", disk_attrs, 1)

        test.log.info("TEST_STEP: Start the VM, and check vhostvdpa disk r/w.")
        vm.start()
        vm_session = vm.wait_for_login()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        vm_session.cmd(f"mkfs.ext4 /dev/{new_disk}")
        check_disk_io(vm_session, new_disk, cmd_in_vm, is_mount=False)

        test.log.info("TEST_STEP: Save and restore VM, check vhostvdpa disk r/w.")
        save_path = os.path.join(data_dir.get_tmp_dir(), vm.name + '.save')
        virsh.save(vm.name, save_path, **VIRSH_ARGS)
        virsh.restore(save_path, **VIRSH_ARGS)
        check_disk_io(vm_session, new_disk, cmd_in_vm)

        test.log.info("TEST_STEP: ManagedSave and Start VM, check vhostvdpa disk r/w.")
        virsh.managedsave(vm.name, **VIRSH_ARGS)
        virsh.start(vm.name, **VIRSH_ARGS)
        check_disk_io(vm_session, new_disk, cmd_in_vm)

        test.log.info("TEST_STEP: Reboot VM, check vhostvdpa disk r/w.")
        virsh.reboot(vm.name, **VIRSH_ARGS)
        vm_session = vm.wait_for_login()
        check_disk_io(vm_session, new_disk, cmd_in_vm, is_mount=False)

        test.log.info("TEST_STEP: Shutdown and start VM, check vhostvdpa disk r/w.")
        virsh.shutdown(vm.name, wait_for_event=True,
                       event_type=".*Shutdown Finished.*(\n.*)*Stopped",
                       **VIRSH_ARGS)
        virsh.start(vm.name, **VIRSH_ARGS)
        vm_session = vm.wait_for_login()
        check_disk_io(vm_session, new_disk, cmd_in_vm, is_mount=False)
        utils_disk.umount(f"/dev/{new_disk}", "/mnt", session=vm_session)
    finally:
        bkxml.sync()
        test_env_obj.cleanup()
