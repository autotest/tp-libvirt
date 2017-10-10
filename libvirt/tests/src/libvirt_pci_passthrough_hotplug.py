import os
import logging
import aexpect
from avocado.utils import process
from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.test_setup import PciAssignable
from virttest import utils_misc
from virttest import data_dir
from virttest.libvirt_xml.devices.controller import Controller
from virttest import utils_package
from provider import libvirt_version


def run(test, params, env):
    """
    Test for PCI single function device(NIC or Infiniband)
    passthrough to libvirt guest in hotplug mode.

    a). NIC Or Infiniband:
        1. Get params.
        2. Get the pci device function.
        3. Start guest
        4. prepare device xml to be attached
        5. hotplug the device
        6. check device hotplugged or not
        5. Ping to server_ip from guest
           to verify the new network device.
    """
    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device_name = params.get("libvirt_pci_net_dev_name", "ENTER_YOUR.DEV.NAME")
    pci_id = params.get("libvirt_pci_net_dev_label", "ENTER_YOUR.DEV.LABEL")
    net_ip = params.get("libvirt_pci_net_ip", "ENTER_YOUR.IP")
    server_ip = params.get("libvirt_pci_server_ip",
                           "ENTER_YOUR.SERVER.IP")
    netmask = params.get("libvirt_pci_net_mask", "ENTER_YOUR.MASK")
    stress = params.get("stress_val", "ENTER_YOUR.STRESS.VALUE")
    timeout = int(params.get("timeout", "ENTER_YOUR.TIMEOUT.VALUE"))
    # Check the parameters from configuration file.
    for each_param in params.itervalues():
        if "ENTER_YOUR" in each_param:
            test.cancel("Please enter the configuration details of %s."
                        % each_param)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devices = vmxml.get_devices()
    nic_id = None
    nic_name = None
    pci_devs = []
    controller = Controller("controller")
    controller.type = "pci"
    controller.index = params.get("index", "1")
    controller.model = params.get("model", "pci-root")
    devices.append(controller)
    vmxml.set_devices(devices)
    vmxml.sync()
    save_file = os.path.join(data_dir.get_tmp_dir(), "attach.xml")
    try:
        fp = open(save_file, "w")
    except IOError:
        logging.debug("Error: File does not appear to exist.")
    if not vm.is_alive():
        vm.start()
        session = vm.wait_for_login()
    if not utils_package.package_install(["ppc64-diag",
                                          "librtas", "powerpc-utils"],
                                         session, 360):
        test.error('Fail on dependencies installing ')
    output = session.cmd_output("lspci -nn")
    nic_list_before = output.splitlines()
    logging.debug("nic_list before hotplug %s", nic_list_before)
    obj = PciAssignable()
    # get all functions id's
    pci_ids = obj.get_same_group_devs(pci_id)
    for val in pci_ids:
        temp = val.replace(":", "_")
        pci_devs.extend(["pci_"+temp])
    if not libvirt_version.version_compare(3, 6, 0):
        # detaching the device from host
        for pci_val, pci_node in map(None, pci_devs, pci_ids):
            pci_val = pci_val.replace(".", "_")
            virsh.nodedev_detach(pci_val)
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.system_output(cmd, shell=True).strip()
            if driver_name != "vfio-pci":
                test.error("Hostdev node detach failed")
        pci_val = pci_devs[0].replace(".", "_")
        pci_xml = NodedevXML.new_from_dumpxml(pci_val)
        pci_address = pci_xml.cap.get_address_dict()
        dev = VMXML.get_device_class('hostdev')()
        dev.mode = 'subsystem'
        dev.hostdev_type = 'pci'
        dev.managed = 'no'
        dev.source = dev.new_source(**pci_address)
        dev = str(dev)
        temp, dev = dev.split("?>", 1)
        fp.write(dev)
    fp.close()
    try:
        for stress_value in range(0, int(stress)):
            # attach the device in hotplug mode
            result = virsh.attach_device(vm_name, save_file,
                                         flagstr="--live", debug=True)
            if result.exit_status:
                test.error(result.stdout)
            else:
                logging.debug(result.stdout)

            def check_attach_pci():
                output = session.cmd_output("lspci -nn")
                nic_list_after = output.splitlines()
                return nic_list_after != nic_list_before
            if not utils_misc.wait_for(check_attach_pci, timeout):
                test.fail("timeout value is not sufficient")
            output = session.cmd_output("lspci -nn")
            nic_list_after = output.splitlines()
            if nic_list_after == nic_list_before:
                test.fail("passthrough Adapter not found in guest.")
            logging.debug("nic_list after hotplug %s" % nic_list_after)
            # ping to server from passthrough adapter
            output = session.cmd_output("lspci -nn | grep %s" % device_name)
            nic_id = output.split(' ', 1)[0]
            nic_name = str(utils_misc.get_interface_from_pci_id(nic_id, session))
            try:
                session.cmd("ip addr flush dev %s" % nic_name)
                session.cmd("ip addr add %s/%s dev %s"
                            % (net_ip, netmask, nic_name))
                session.cmd("ip link set %s up" % nic_name)
                session.cmd("ping -I %s %s -c 5" % (nic_name, server_ip))
            except aexpect.ShellError, detail:
                test.error("Succeed to set ip on guest, but failed "
                           "to ping server ip from guest.\n"
                           "Detail: %s." % detail)
            # detach the device in hotplug mode
            result = virsh.detach_device(vm_name, save_file,
                                         flagstr="--live", debug=True)
            if result.exit_status:
                test.fail(result.stdout)
            else:
                logging.debug(result.stdout)
    finally:
        def check_detach_pci():
            return not virsh.nodedev_reattach(pci_val).exit_status
        if not libvirt_version.version_compare(3, 6, 0):
            pci_devs.sort()
            # reattach the device to host
            for pci_val, pci_node in map(None, pci_devs, pci_ids):
                pci_val = pci_val.replace(".", "_")
                if not utils_misc.wait_for(check_detach_pci, timeout):
                    test.fail("timeout value is not sufficient")
                cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                       awk '{print $5}'" % pci_node
                driver_name = process.system_output(cmd, shell=True).strip()
                if driver_name == "vfio-pci":
                    test.error("Hostdev node reattach failed")
        backup_xml.sync()
