import uuid

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_libvirt import libvirt_vfio

from provider.sriov import sriov_base
from provider.sriov import check_points


def run(test, params, env):
    """
    Attach/detach-interface of hostdev type to/from guest
    """
    def run_test():
        """
        Attach hostdev type interface to a guest and detach it.
        """
        test.log.info("TEST_STEP1: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        mac_addr = utils_net.generate_mac_address_simple()
        alias_name = 'ua-' + str(uuid.uuid4())
        iface_dict = {'alias': {'name': alias_name}, 'mac_address': mac_addr}

        test.log.info("TEST_STEP2: Attach-interface with '--print-xml' option.")
        opts = "hostdev --source {0} --mac {1} --alias {2} {3}".format(
            sriov_test_obj.vf_pci, mac_addr, alias_name, attach_opt)
        iface = interface.Interface()
        iface.xml = virsh.attach_interface(
            vm.name, opts + " --print-xml", debug=True,
            ignore_status=False).stdout_text.strip()

        test.log.info("TEST_STEP3: Attach-interface to the VM.")
        virsh.attach_interface(vm.name, opts, debug=True,
                               ignore_status=False)

        test.log.info("TEST_STEP4: Check the network connectivity, mac and xml info.")
        check_points.check_vm_network_accessed(vm_session)
        check_points.check_mac_addr(
            vm_session, vm.name, "interface", iface_dict)
        check_points.comp_hostdev_xml(vm, "interface", iface.fetch_attrs())
        check_points.comp_hostdev_xml(vm, "interface", iface_dict)

        test.log.info("TEST_STEP5: Detach the hostdev interface.")
        virsh.detach_interface(vm.name, "hostdev", debug=True,
                               ignore_errors=False, wait_for_event=True)
        cur_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("interface")
        if cur_hostdevs:
            test.fail("Got hostdev interface(%s) after detaching the "
                      "device!" % cur_hostdevs)

        test.log.info("TEST_STEP6: Check driver and mac address recovery.")
        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                sriov_test_obj.vf_pci, not managed_disabled, True), 10, 5):
            test.fail("Got incorrect driver!")
        if managed_disabled:
            virsh.nodedev_reattach(sriov_test_obj.vf_dev_name, debug=True,
                                   ignore_status=False)
            libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci, True)
        check_points.check_mac_addr_recovery(
            sriov_test_obj.pf_name, "interface", iface_dict)

    attach_opt = params.get("attach_opt", "")
    managed_disabled = "managed" not in attach_opt
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    try:
        sriov_test_obj.setup_default(dev_name=sriov_test_obj.vf_dev_name,
                                     managed_disabled=managed_disabled)
        run_test()

    finally:
        sriov_test_obj.teardown_default(dev_name=sriov_test_obj.vf_dev_name,
                                        managed_disabled=managed_disabled)
