from virttest import libvirt_version
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml

from provider.interface import check_points
from provider.interface import interface_base
from provider.interface import vdpa_base


def run(test, params, env):
    """
    Test Hotplug/unplug vDPA interface device(s)
    """

    libvirt_version.is_libvirt_feature_supported(params)
    supported_qemu_ver = eval(params.get('func_supported_since_qemu_kvm_ver', '()'))
    if supported_qemu_ver:
        if not utils_misc.compare_qemu_version(*supported_qemu_ver, False):
            test.cancel("Current qemu version doesn't support this test!")

    # Variable assignment
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', 'vdpa')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    test_obj = None
    try:
        # Execute test
        test_obj, _ = vdpa_base.setup_vdpa(vm, params)
        if 'hotplug' in params.get('test_scenario'):
            vm.start()
            vm_session = vm.wait_for_serial_login(timeout=240)
        else:
            params["flagstr"] = "--config"
        interface_base.attach_iface_device(vm_name, dev_type, params)
        if not vm.is_alive():
            vm.start()
            vm_session = vm.wait_for_serial_login(timeout=240)
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, config_vdpa=False, **params)
        check_points.check_vm_iface_queues(vm_session, params)
        interface_base.detach_iface_device(vm_name, dev_type)

    finally:
        backup_vmxml.sync()
        vdpa_base.cleanup_vdpa(test_target, test_obj)
