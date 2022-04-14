import uuid

from virttest import libvirt_version
from virttest import utils_net
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import hostdev
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.sriov import check_points
from provider.sriov import sriov_base


def get_hostdev_dict(vf_pci, params):
    """
    Get the updated hostdev dict

    :param vf_pci: VF's pci
    :param params: the parameters dict
    :return: The updated hostdev dict
    """
    pci_to_addr = utils_sriov.pci_to_addr(vf_pci)
    if params.get('hostdev_iface_dict'):
        return eval(params.get('hostdev_iface_dict') % pci_to_addr)
    else:
        del pci_to_addr['type']
        return eval(params.get('hostdev_dict') % pci_to_addr)


def create_dev(params, dev_dict):
    """
    Wrapper function for creating a device

    :param params: the parameters dict
    :param hostdev_dict: Dict, attrs of the device
    :return: new created device
    """
    if params.get('hostdev_iface_dict'):
        return interface_base.create_iface('hostdev', dev_dict)
    else:
        return create_hostdev_device(dev_dict)


def create_hostdev_device(hostdev_dict):
    """
    Create Hostdev device

    :param hostdev_dict: Dict, attrs of Hostdev
    :return: Object of Hostdev device
    """
    host_dev = hostdev.Hostdev()
    host_dev.setup_attrs(**hostdev_dict)

    return host_dev


def run(test, params, env):
    """
    Test hostdev attach/detach
    """
    def setup_default():
        """
        Default setup
        """
        test.log.info("TEST_SETUP: Clear up VM interface(s).")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')

    def teardown_default():
        """
        Default cleanup
        """
        test.log.info("TEST_TEARDOWN: Recover test enviroment.")
        orig_config_xml.sync()
        sriov_base.recover_vf(pf_pci, params, 0)

    def exec_test(vm, hostdev_dict, params):
        """
        Execute basic test

        :param vm: VM object
        :param hostdev_dict: Hostdev attrs
        :param params: Test parameters
        """
        start_vm = "yes" == params.get("start_vm")
        options = '' if vm.is_alive() else '--config'

        host_dev = create_dev(params, hostdev_dict)
        test.log.debug("Hostdev XML: %s.", host_dev)
        test.log.info("TEST_STEP1: Attach hostdev interface.")
        virsh.attach_device(vm_name, host_dev.xml, flagstr=options,
                            debug=True, ignore_status=False)
        if not start_vm:
            vm.start()

        test.log.info("TEST_STEP2: Check VM XML.")
        device_type = "interface" if params.get('hostdev_iface_dict') else 'hostdev'
        check_points.comp_hostdev_xml(vm, device_type, hostdev_dict)

    def test_unassigned_address():
        """
        Cold/Hot plug hostdev interface with 'unassigned' address type
        """
        hostdev_dict = get_hostdev_dict(vf_pci, params)
        exec_test(vm, hostdev_dict, params)
        test.log.info("Check if the VM is not using VF.")
        libvirt_vfio.check_vfio_pci(vf_pci)
        vm_session = vm.wait_for_serial_login(timeout=240)
        p_iface = utils_net.get_remote_host_net_ifs(vm_session)[0]
        if p_iface:
            test.fail("There should be no interface, but got %s." % p_iface)

    def test_duplicated_cust_alias():
        """
        Hotplug hostdev interface with duplicate custom alias
        """
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm.wait_for_serial_login(timeout=240).close()
        alias_name = 'ua-' + str(uuid.uuid4())
        hostdev_dict = eval(params.get('hostdev_iface_dict')
                            % (utils_sriov.pci_to_addr(vf_pci), alias_name))
        exec_test(vm, hostdev_dict, params)

        host_dev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("interface")[0]
        test.log.info("TEST_STEP3: Hotplug another hostdev interface with the"
                      "same alias name")
        vf2_pci = utils_sriov.get_vf_pci_id(pf_pci, vf_index=1)
        hostdev_dict['hostdev_address']['attrs'] = utils_sriov.pci_to_addr(vf2_pci)
        host_dev2 = interface_base.create_iface('hostdev', hostdev_dict)
        result = virsh.attach_device(vm_name, host_dev2.xml, debug=True)
        libvirt.check_exit_status(result, True)

        test.log.info("TEST_STEP4: Detach the first hostdev interface.")
        virsh.detach_device(vm_name, host_dev.xml, wait_for_event=True,
                            debug=True, ignore_status=False)

        test.log.info("TEST_STEP5: Attach the second hostdev interface again.")
        virsh.attach_device(vm_name, host_dev2.xml, debug=True,
                            ignore_status=False)
        check_points.comp_hostdev_xml(vm, "interface", hostdev_dict)

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    pf_pci = utils_sriov.get_pf_pci()
    if not pf_pci:
        test.cancel("NO available pf found.")
    sriov_base.setup_vf(pf_pci, params)

    vf_pci = utils_sriov.get_vf_pci_id(pf_pci)

    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else setup_default
    teardown_test = eval("teardown_%s" % test_case) if "teardown_%s" % \
        test_case in locals() else teardown_default
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        test.log.info("TEST_CASE: %s", run_test.__doc__.lstrip().split('\n\n')[0])
        setup_test()
        run_test()

    finally:
        teardown_test()
