import uuid

from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.interface import check_points as iface_check
from provider.sriov import check_points
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Attach/detach-interface of hostdev type to/from guest
    """
    def check_hostdev_xml(vmxml, device_dict=None):
        """
        Check VM hostdev xml info

        :param vmxml: The vmxml object
        :param device_dict: Expected device settings
        """
        vm_hostdevs = vmxml.devices.by_device_tag("interface")
        if device_dict:
            iface_check.comp_interface_xml(vmxml, device_dict)
        else:
            if len(vm_hostdevs):
                test.fail("Got incorrect hostdev device/interface number: %d!"
                          % len(vm_hostdevs))

    def check_vm_xml(vm, params, device_dict=None):
        """
        Check VM xml info

        :param vm: VM object
        :param params: Dictionary with the test parameters
        :param device_dict: Expected device settings
        """
        if params.get('expr_active_xml_changes', 'no') == "yes":
            test.log.debug("checking active vm xml after attaching hostdev.")
            check_hostdev_xml(
                vm_xml.VMXML.new_from_dumpxml(vm.name), device_dict)
        if params.get('expr_inactive_xml_changes', 'no') == "yes":
            test.log.debug("checking inactive vm xml after attaching hostdev.")
            check_hostdev_xml(
                vm_xml.VMXML.new_from_inactive_dumpxml(vm.name), device_dict)

    def run_test():
        """
        Attach hostdev type interface to a guest and detach it.
        """
        if start_vm and not vm.is_alive():
            vm.start()
            vm_session = vm.wait_for_serial_login(timeout=240)

        mac_addr = utils_net.generate_mac_address_simple()
        alias_name = 'ua-' + str(uuid.uuid4())
        iface_dict = {'alias': {'name': alias_name}, 'mac_address': mac_addr,
                      'managed': 'yes'}

        test.log.info("TEST_STEP1: Attach-interface with '--print-xml' option.")
        opts = "hostdev --source {0} --mac {1} --alias {2} --managed {3}".format(
            sriov_test_obj.vf_pci, mac_addr, alias_name, flagstr)

        test.log.info("TEST_STEP2: Attach-interface to the VM.")
        result = virsh.attach_interface(vm.name, opts, debug=True)
        libvirt.check_exit_status(result, status_error)
        if status_error:
            return

        test.log.info("TEST_STEP3: Check VM xml after attaching a host device.")
        check_vm_xml(vm, params, iface_dict)

        if 'vm_session' in locals():
            check_points.check_vm_network_accessed(vm_session)

        test.log.info("TEST_STEP4: Detach the hostdev interface.")
        wait_for_event = True if vm.is_alive() and not flagstr.count('config') \
            else False
        virsh.detach_interface(vm.name, "hostdev %s" % flagstr,
                               debug=True, ignore_status=False,
                               wait_for_event=wait_for_event)
        test.log.info("TEST_STEP5: Check VM xml after detaching the device.")
        check_vm_xml(vm, params)

    if params.get('flagstr', 'live') == "no_option":
        flagstr = ""
    else:
        flagstr = "--%s" % params.get('flagstr', 'live')
    start_vm = "yes" == params.get("start_vm")
    status_error = "yes" == params.get("status_error")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    try:
        sriov_test_obj.setup_default()
        run_test()

    finally:
        sriov_test_obj.teardown_default()
