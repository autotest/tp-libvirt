from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_vdpa

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base


def run(test, params, env):
    """
    Check mem_lock limit for vm with multiple vDPA interfaces.
    """
    libvirt_version.is_libvirt_feature_supported(params)
    # Variable assignment
    dev_type = params.get('dev_type', 'vdpa')
    iface_num = 3
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    try:
        pf_pci = utils_vdpa.get_vdpa_pci()
        test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci)
        test_env_obj.setup()
        qemu_log = "/var/log/libvirt/qemu/%s.log" % vm.name
        process.run("echo > {}".format(qemu_log), shell=True)

        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')

        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        test.log.debug("Updated VM xml: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))
        for idx in range(iface_num):
            default_iface = {'source': {'dev': '/dev/vhost-vdpa-%s' % str(idx)}}
            iface_dict = eval(params.get(
                'iface_dict%s' % str(idx+1), str(default_iface)))
            if iface_dict:
                iface_dev = interface_base.create_iface(dev_type, iface_dict)
                libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name),
                                      iface_dev)
        test.log.debug("VM xml after updating ifaces: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))

        test.log.info("TEST_STEP: Start the vm with 3 vDPA interfaces.")
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) * 3 + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after VM startup!")

        test.log.info("TEST_STEP: Check qemu log.")
        libvirt.check_logfile("fail|error", qemu_log, str_in_log=False)
    finally:
        backup_vmxml.sync()
        test_env_obj.cleanup()
