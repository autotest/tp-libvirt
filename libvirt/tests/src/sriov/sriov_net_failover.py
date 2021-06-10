import logging
import os
import time

from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_sriov
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml.devices import interface
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def exec_function(test_func):
    """
    A wrapper function to run 'setup/teardown' function

    :param test_func: The name setup/tearndown function
    """
    if not callable(test_func):
        logging.warning('Function "%s" is not implemented yet.', test_func)
        return
    test_func()


def run(test, params, env):
    """
    Sriov net failover related test.
    """
    def setup_hotplug_hostdev_iface_with_teaming():
        logging.info("Create hostdev network.")
        net_hostdev_fwd = params.get("net_hostdev_fwd",
                                     '{"mode": "hostdev", "managed": "yes"}')
        net_hostdev_dict = {"net_name": net_hostdev_name,
                            "net_forward": net_hostdev_fwd,
                            "net_forward_pf": '{"dev": "%s"}' % pf_name}
        libvirt_network.create_or_del_network(net_hostdev_dict)

        logging.info("Clear up VM interface.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        vm.start()
        vm.wait_for_serial_login(timeout=180).close()

    def teardown_hotplug_hostdev_iface_with_teaming():
        logging.info("Delete hostdev network.")
        net_hostdev_dict = {"net_name": net_hostdev_name}
        libvirt_network.create_or_del_network(net_hostdev_dict, is_del=True)

    def test_hotplug_hostdev_iface_with_teaming():
        logging.info("Attach the bridge and hostdev interfaces.")
        iface = interface.Interface("network")
        iface.xml = create_bridge_iface_xml(vm, mac_addr, params)
        virsh.attach_device(vm_name, iface.xml, debug=True,
                            ignore_status=False)
        hostdev_iface_xml = create_hostdev_iface_xml(vm, mac_addr, params)
        virsh.attach_device(vm_name, hostdev_iface_xml, debug=True,
                            ignore_status=False)
        check_ifaces(vm_name, expected_ifaces={"bridge", "hostdev"})

        vm_session = vm.wait_for_serial_login(timeout=240)
        check_vm_network_accessed(vm_session)

        logging.info("Detach the hostdev interface.")
        hostdev_iface = interface.Interface("network")
        for ifc in vm_xml.VMXML.new_from_dumpxml(vm_name).devices.by_device_tag(
           "interface"):
            if ifc.type_name == "hostdev":
                ifc.del_address()
                hostdev_iface = ifc
        virsh.detach_device(vm_name, hostdev_iface.xml, wait_remove_event=True,
                            debug=True, ignore_status=False)
        check_ifaces(vm_name, expected_ifaces={"hostdev"}, status_error=True)

        check_vm_network_accessed(vm_session, 2)

        libvirt_vfio.check_vfio_pci(vf_pci, status_error=True)
        logging.info("Re-attach the hostdev interface.")
        virsh.attach_device(vm_name, hostdev_iface.xml, debug=True,
                            ignore_status=False)
        check_vm_network_accessed(vm_session)

    def setup_hotplug_hostdev_device_with_teaming():
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

    def test_hotplug_hostdev_device_with_teaming():
        default_vf_mac = utils_sriov.get_vf_mac(pf_name)
        utils_sriov.set_vf_mac(pf_name, mac_addr)
        logging.info("Attach the bridge interface.")
        brg_iface_xml = create_bridge_iface_xml(vm, mac_addr, params)
        virsh.attach_device(vm_name, brg_iface_xml, debug=True,
                            ignore_status=False)
        # Wait for 10s before attaching the hostdev device
        time.sleep(10)
        logging.info("Attach the hostdev device.")
        hostdev_dev = libvirt.create_hostdev_xml(vf_pci,
                                                 teaming=hostdev_teaming_dict)
        virsh.attach_device(vm_name, hostdev_dev.xml, debug=True,
                            ignore_status=False)
        vm_session = vm.wait_for_serial_login(timeout=240)
        check_vm_network_accessed(vm_session)

        logging.info("Detach the hostdev device.")
        virsh.detach_device(vm_name, hostdev_dev.xml, wait_remove_event=True,
                            debug=True, ignore_status=False)
        logging.debug("Recover vf's mac to %s.", default_vf_mac)
        utils_sriov.set_vf_mac(pf_name, default_vf_mac)

        check_hostdev = vm_xml.VMXML.new_from_dumpxml(vm_name)\
            .devices.by_device_tag('hostdev')
        if check_hostdev:
            test.fail("The hostdev device exists after detaching %s."
                      % check_hostdev)
        libvirt_vfio.check_vfio_pci(vf_pci, status_error=True)
        check_vm_network_accessed(vm_session, 2)

    def setup_save_restore_hostdev_device_with_teaming():
        logging.info("Start a VM with bridge iface and hostdev device.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface = interface.Interface("network")
        iface.xml = create_bridge_iface_xml(vm, mac_addr, params)
        vmxml.add_device(iface)

        hostdev_dev = libvirt.create_hostdev_xml(vf_pci,
                                                 teaming=hostdev_teaming_dict)
        vmxml.add_device(hostdev_dev)
        vmxml.sync()
        vm.start()
        utils_sriov.set_vf_mac(pf_name, mac_addr)
        vm.wait_for_serial_login(timeout=240).close()

    def test_save_restore_hostdev_device_with_teaming():
        logging.info("Save/restore VM.")
        save_file = os.path.join(data_dir.get_tmp_dir(), "save_file")
        virsh.save(vm_name, save_file, debug=True, ignore_status=False, timeout=10)
        if not libvirt.check_vm_state(vm_name, "shut off"):
            test.fail("The guest should be down after executing 'virsh save'.")
        virsh.restore(save_file, debug=True, ignore_status=False)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("The guest should be running after executing 'virsh restore'.")

        vm_session = vm.wait_for_serial_login()
        check_vm_network_accessed(vm_session)

        logging.info("Detach the hostdev device.")
        hostdev_dev = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name).devices.\
            by_device_tag("hostdev")
        virsh.detach_device(vm_name, hostdev_dev.xml, wait_remove_event=True,
                            debug=True, ignore_status=False)
        check_hostdev = vm_xml.VMXML.new_from_dumpxml(vm_name)\
            .devices.by_device_tag('hostdev')
        if check_hostdev:
            test.fail("The hostdev device exists after detaching %s."
                      % check_hostdev)

        check_vm_network_accessed(vm_session, 2)
        libvirt_vfio.check_vfio_pci(vf_pci, status_error=True)
        logging.info("Attach the hostdev device.")
        virsh.attach_device(vm_name, hostdev_dev.xml, debug=True,
                            ignore_status=False)
        check_vm_network_accessed(vm_session)

    def check_vm_iface_num(session, exp_num=3):
        """
        Check he number of interfaces

        :param session: The session to the guest
        :param exp_num: The expected number
        :return: True when interfaces' number is equal to exp_num
        """
        p_iface = utils_net.get_remote_host_net_ifs(session)[0]
        logging.debug("Ifaces in VM: %s", p_iface)

        return len(p_iface) == exp_num

    def check_vm_network_accessed(vm_session, expected_iface_no=3,
                                  ping_dest="8.8.8.8", timeout=30):
        """
        Test VM's network by checking ifaces' number and the accessibility

        :param vm_session: The session object to the guest
        :param expected_iface_no: The expected number of ifaces
        :param ping_dest: The destination to be ping
        :param timeout: The timeout of the checking.
        :raise: test.fail when ifaces' number is incorrect or ping fails
        """
        if not utils_misc.wait_for(lambda: check_vm_iface_num(
           vm_session, expected_iface_no), first=3, timeout=timeout):
            test.fail("%d interfaces should be found on the vm."
                      % expected_iface_no)
        if not utils_misc.wait_for(lambda: not utils_test.ping(
           ping_dest, count=3, timeout=5, output_func=logging.debug,
           session=vm_session)[0], first=5, timeout=timeout):
            test.fail("Failed to ping %s." % ping_dest)

    def create_bridge_iface_xml(vm, mac_addr, params):
        """
        Create xml of bridge interface

        :param vm: The vm object
        :param mac_address: The mac address
        :param params: Dictionary with the test parameters
        :return: The interface xml
        """
        net_bridge_name = params.get("net_bridge_name", "host-bridge")
        iface_bridge_dict = {"type": "network",
                             "source": "{'network': '%s'}" % net_bridge_name,
                             "mac": mac_addr, "model": "virtio",
                             "teaming": '{"type":"persistent"}',
                             "alias": '{"name": "ua-backup0"}'}
        return libvirt.modify_vm_iface(vm.name, "get_xml", iface_bridge_dict)

    def create_hostdev_iface_xml(vm, mac_addr, params):
        """
        Create xml of hostdev interface

        :param vm: The vm object
        :param mac_address: The mac address
        :param params: Dictionary with the test parameters
        :return: The interface xml
        """
        net_hostdev_name = params.get("net_hostdev_name", "hostdev-net")
        hostdev_iface_dict = {"type": "network",
                              "source": "{'network': '%s'}" % net_hostdev_name,
                              "mac": mac_addr,
                              "teaming": '{"type":"transient", "persistent": "ua-backup0"}'}
        return libvirt.modify_vm_iface(vm.name, "get_xml", hostdev_iface_dict, 4)

    def check_ifaces(vm_name, expected_ifaces={"bridge", "hostdev"},
                     status_error=False):
        """
        Check VM's interfaces

        :param vm_name: The name of VM
        :param expected_ifaces: The expected interfaces
        :param status_error: Whether the ifaces should be same with the expected_ifaces
        :raise: test.fail if the interface(s) is(are) as expected
        """
        if not expected_ifaces:
            return
        else:
            expected_ifaces = set(expected_ifaces)
        vm_ifaces = [iface for iface in vm_xml.VMXML.new_from_dumpxml(vm_name).
                     devices.by_device_tag("interface")]
        ifaces_net = {iface.get_type_name() for iface in vm_ifaces}
        if expected_ifaces.issubset(ifaces_net) == status_error:
            test.fail("Unable to get the expected interfaces %s, "
                      "it should%s be %s."
                      % (expected_ifaces,  ' not' if status_error else '',
                         ifaces_net))
        else:
            logging.debug("{}Found iface(s) as expected: {}."
                          .format('Not ' if status_error else '',
                                  expected_ifaces))

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else "setup_%s" % test_case
    teardown_test = eval("teardown_%s" % test_case) if "teardown_%s" % \
        test_case in locals() else "teardown_%s" % test_case
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])

    driver = params.get("driver", "ixgbe")
    bridge_name = params.get("bridge_name", "br0")
    net_bridge_name = params.get("net_bridge_name", "host-bridge")
    net_bridge_fwd = params.get("net_bridge_fwd", '{"mode": "bridge"}')
    net_hostdev_name = params.get("net_hostdev_name", "hostdev-net")
    bridge_name = params.get("bridge_name", "br0")
    hostdev_teaming_dict = params.get("hostdev_device_teaming_dict", '{}')

    default_vf = 0
    try:
        vf_no = int(params.get("vf_no", "4"))
    except ValueError as e:
        test.error(e)

    libvirt_version.is_libvirt_feature_supported(params)

    mac_addr = utils_net.generate_mac_address_simple()
    pf_name, pf_pci = utils_sriov.find_pf(driver)
    brg_dict = {'pf_name': pf_name, 'bridge_name': bridge_name}
    bridge_dict = {"net_name": net_bridge_name,
                   "net_forward": net_bridge_fwd,
                   "net_bridge": '{"name": "%s"}' % bridge_name}
    pf_pci_path = utils_misc.get_pci_path(pf_pci)
    cmd = "cat %s/sriov_numvfs" % (pf_pci_path)
    default_vf = process.run(cmd, shell=True, verbose=True).stdout_text

    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        if not utils_sriov.set_vf(pf_pci_path, vf_no):
            test.error("Failed to set vf.")
        utils_sriov.add_or_del_connection(brg_dict, is_del=False)
        libvirt_network.create_or_del_network(bridge_dict)

        vf_pci = utils_sriov.get_vf_pci_id(pf_pci)
        exec_function(setup_test)
        run_test()

    finally:
        logging.info("Recover test enviroment.")
        utils_sriov.add_or_del_connection(brg_dict, is_del=True)
        libvirt_network.create_or_del_network(bridge_dict, is_del=True)
        if 'pf_pci_path' in locals() and default_vf != vf_no:
            utils_sriov.set_vf(pf_pci_path, default_vf)

        if vm.is_alive():
            vm.destroy(gracefully=False)

        try:
            orig_config_xml.sync()
        except:
            # FIXME: Workaround for 'save'/'managedsave' hanging issue
            utils_libvirtd.Libvirtd().restart()
            orig_config_xml.sync()

        exec_function(teardown_test)
