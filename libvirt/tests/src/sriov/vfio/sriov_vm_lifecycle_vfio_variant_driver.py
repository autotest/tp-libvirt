from provider.sriov import sriov_vfio

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_virtio
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Lifecycle testing of VM with vfio variant driver
    """
    hotplug = params.get("hotplug", "no") == "yes"
    err_msg = params.get("err_msg")
    managed = params.get("managed")
    iommu_dict = eval(params.get("iommu_dict", "{}"))
    virsh_args = {'ignore_status': False, 'debug': True}

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    rand_id = utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm_name}_{rand_id}.save'
    dev_names = []

    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    orig_vm_xml = new_xml.copy()
    try:
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'hostdev')
        if iommu_dict:
            libvirt_virtio.add_iommu_dev(vm, iommu_dict)

        if hotplug:
            vm.start()
            vm.wait_for_serial_login().close()
            dev_names = sriov_vfio.attach_dev(vm, params)
        else:
            dev_names = sriov_vfio.attach_dev(vm, params)
            vm.start()
            vm.wait_for_serial_login().close()

        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        test.log.info("TEST_STEP: Save and restore the VM.")
        result = virsh.save(vm.name, save_path, debug=True)
        libvirt.check_result(result, err_msg)
        if err_msg:
            return
        virsh.restore(save_path, **virsh_args)

        test.log.info("TEST_STEP: Suspend VM and check vm's state.")
        virsh.suspend(vm.name, debug=True, ignore_status=False)
        if not libvirt.check_vm_state(vm_name, "paused"):
            test.fail("The guest should be down after executing 'virsh suspend'.")

        test.log.info("TEST_STEP: Resume the VM and check vm's state.")
        virsh.resume(vm.name, debug=True, ignore_status=False)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("The guest should be down after executing 'virsh resume'.")

        test.log.info("TEST_STEP: Managedsave and Start the VM.")
        virsh.managedsave(vm.name, **virsh_args)
        virsh.start(vm.name, debug=True, ignore_status=False)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("The guest should be down after executing 'virsh managedsave'.")

        vm.shutdown()
        vm.start()
        session = vm.wait_for_serial_login(
            timeout=int(params.get('login_timeout')))
        session.sendline(params.get("shutdown_command"))
        if not vm.wait_for_shutdown():
            test.fail("VM %s failed to shut down" % vm.name)

        vm.cleanup_serial_console()
        vm.start()

        session = vm.wait_for_serial_login(
            timeout=int(params.get('login_timeout')))
        virsh.reset(vm.name, **virsh_args)
        _match, _text = session.read_until_last_line_matches(
                [r"[Ll]ogin:\s*"], timeout=240, internal_timeout=0.5)
        session.close()

        test.log.info("TEST_STEP: Reboot the VM using command inside the VM.")
        session = vm.wait_for_serial_login(
            timeout=int(params.get('login_timeout')))
        session.sendline(params.get("reboot_command"))
        _match, _text = session.read_until_last_line_matches(
            [r"[Ll]ogin:\s*"], timeout=240, internal_timeout=0.5)
        session.close()

    finally:
        orig_vm_xml.sync()
        if managed == "no":
            for dev in dev_names:
                virsh.nodedev_reattach(dev, debug=True)
