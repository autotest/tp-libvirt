# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# Author: Tasmiya.Nalatwad<tasmiya@linux.vnet.ibm.com>
# EEH Test on pci passthrough devices


import logging as log
import ipaddress
import platform
import time

from avocado.utils import process
from virttest import virsh
from virttest import utils_test
from virttest import utils_misc
from virttest import utils_sys
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.test_setup import PciAssignable
from virttest.libvirt_xml.devices.controller import Controller     
from virttest import utils_package
from virttest import libvirt_version

logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test EEH functionality on PCI Passthrough device
    of a libvirt guest

    a). NIC:
        1. Get params.
        2. Get the pci device function.
        3. Start guest
        4. prepare device xml to be attached
        5. hotplug the device
        6. check device hotplugged or not
        7. Ping to server_ip from guest
        8. Get location code for the pci device
        9. Trigger EEH on pci device
        10. Check EEH recovery on the device
        11. check device availability inside guest
    b). STORAGE:
        1. Get params.
        2. Get the pci device function.
        3. Start guest
        4. prepare device xml to be attached
        5. hotplug the device
        6. check device hotplugged or not
        7. check STORAGE device inside guest
        8. Get location code for the pci device
        9. mount file (/dev/nvme*) on /mnt
        9. Trigger EEH on pci device
        10. Check EEH recovery on the device
        11. check device availability inside guest
    """
    
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device_name = params.get("libvirt_pci_net_dev_name", "None")
    device_type = params.get("libvirt_pci_device_type", "None")
    pci_id = params.get("libvirt_pci_net_dev_label", "None")
    sriov = ('yes' == params.get("libvirt_pci_SRIOV", 'no'))
    net_ip = params.get("libvirt_pci_net_ip", "None")
    server_ip = params.get("libvirt_pci_server_ip",
                           "None")
    netmask = params.get("libvirt_pci_net_mask", "None")
    timeout = int(params.get("timeout", "None"))
    cntlr_index = params.get("index", "1")
    cntlr_model = params.get("model", "pci-root")
    cntlr_type = "pci"
    ioa_system_info = params.get("ioa_system_info", "ioa-bus-error")
    func = params.get("function", "6")
    max_freeze = params.get("max_freeze", "5")
    eeh_host = params.get("eeh_host", "no")
    arch = platform.machine()
    dargs = {'debug': True, 'ignore_status': True}

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devices = vmxml.get_devices()
    pci_devs = []
    
    def create_controller_with_next_index(vmxml, cntlr_type, cntlr_model, cntlr_index):
        """
        Create a new controller with the next available index based on existing controllers.
   
        :param vmxml: VM XML object
        :param cntlr_type: Controller type (e.g., 'pci')
        :param cntlr_model: Controller model (e.g., 'pci-root')
        :param cntlr_index: Controller index value
  
        :returns : Returns A new Controller object with updated type, model, and index
        """
        controllers = vmxml.get_controllers(cntlr_type, cntlr_model)
        index_list = []
        for controller in controllers:
            index_value = controller.get("index")
            if index_value is not None:
                index_list.append(int(index_value))
        if index_list:
            next_index = max(index_list) + 1
        else:
            next_index = int(cntlr_index)

        controller = Controller("controller")
        controller.type = cntlr_type
        controller.index = str(next_index)
        controller.model = cntlr_model
        
        return controller

    def get_nic_list_before_hotplug(session):
        """
        Get the NIC interface list from the guest before hotplug operation
        
        :param session: session object for guest ssh
        :return : Returns a list of interfaces present inside guest 
        """
        output = session.cmd_output("ip link")
        logging.debug("checking for output - %s", output)
        nic_list_before = str(output.splitlines())
        logging.debug("nic_list before hotplug %s", nic_list_before)
        
        return nic_list_before

    controller = create_controller_with_next_index(vmxml, cntlr_type, cntlr_model, cntlr_index)
    devices.append(controller)
    vmxml.set_devices(devices)
    vmxml.sync()
    if not vm.is_alive():
        vm.start()
    session = vm.wait_for_login()
    if not utils_package.package_install(["ppc64-diag",
                                          "powerpc-utils"],
                                         session, 360):
        test.cancel('Fail on dependencies installing')

    nic_list_before = get_nic_list_before_hotplug(session)
    obj = PciAssignable()
    # get all functions id's
    pci_ids = obj.get_same_group_devs(pci_id)
    for val in pci_ids:
        temp = val.replace(":", "_")
        pci_devs.extend(["pci_"+temp])
    pci_val = pci_devs[0].replace(".", "_")
    if device_type == "NIC":
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        nic_list_before = vm.get_pci_devices()
    pci_xml = NodedevXML.new_from_dumpxml(pci_val)
    pci_address = pci_xml.cap.get_address_dict()
    dev = VMXML.get_device_class('hostdev')()
    dev.mode = 'subsystem'
    dev.type = 'pci'
    dev.managed = 'no'
    dev.source = dev.new_source(**pci_address)
    arch = platform.machine()

    def test_ping(net_ip, server_ip, netmask):
        """
        Function is to perform ping test on a passthrough device

        :param net_ip: IP address assigned to the passthrough device interface
        :param server_ip: IP address used to test connectivity via ping
        :param netmask: Network mask value (e.g., "255.255.255.0")

        :raise : Test fails and raises exception if ping fails
        """
        try:
            bus_info = []
            nic_devices = []
            # The Network configuration is generic irrespective of PF or SRIOV VF
            if sorted(vm.get_pci_devices()) != sorted(nic_list_before):
                logging.debug("Adapter successfully passed through to guest")
            else:
                test.fail("Passthrough adapter not found in guest.")
            net_ip = ipaddress.ip_address(net_ip)
            nic_list_after = vm.get_pci_devices()
            nic_list = list(set(nic_list_after).difference(set(nic_list_before)))
            for val in range(len(nic_list)):
                bus_info.append(str(nic_list[val]).split(' ', 1)[0])
                nic_devices.append(str(nic_list[val]).split(' ', 1)[0][:-2])
            bus_info.sort()
            if not sriov:
                # check all functions get same iommu group
                # arch ppc64 gets different iommu group when attached to VM
                if arch != "ppc64le":
                    if len(set(nic_devices)) != 1:
                        test.fail("Multifunction Device is passed through but "
                                  "functions are in different iommu group")
            # ping to server from each function
            for val in bus_info:
                nic_name = utils_misc.get_interface_from_pci_id(val, session)
                session.cmd("ip addr flush dev %s" % nic_name)
                session.cmd("ip addr add %s/%s dev %s" % (net_ip, netmask, nic_name))
                session.cmd("ip link set %s up" % nic_name)
                s_ping, o_ping = utils_test.ping(server_ip, count=5,
                                                 interface=net_ip, timeout=30,
                                                 session=session)
                logging.info(o_ping)
                if s_ping != 0:
                    err_msg = "Ping test fails, error info: '%s'"
                    test.fail(err_msg % o_ping)
                # Each interface should have unique IP
                # For ppc64 arch let's test using one ip only
                if arch != "ppc64le":
                    net_ip = net_ip + 1
        except Exception as e:
            test.error("An unexpected error occurred: %s" % str(e))
            raise

    def detach_device(pci_devs, pci_ids):
        """
        Function is to detach a pci device from host
        :param pci_devs : pci device functions example "xx:xx:xx:xx"
        :param pci_ids : pci device funstion id's example ".x"
        
        Test fails if the device detach is failed.
        """
        for pci_value, pci_node in zip(pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.run(cmd, shell=True).stdout_text.strip()
            if driver_name == "vfio-pci":
                logging.debug("device already detached")
            else:
                if virsh.nodedev_detach(pci_value).exit_status:
                    test.error("Hostdev node detach failed")
                driver_name = process.run(cmd, shell=True).stdout_text.strip()
                if driver_name != "vfio-pci":
                    test.error("driver bind failed after detach")

    def reattach_device(pci_devs, pci_ids):
        """
        Function is to re-attach a pci device to host
        :param pci_devs : pci device functions example "xx:xx:xx:xx"
        :param pci_ids : pci device funstion id's example ".x"

        Test fails if the device re-attach is failed.
        """
        for pci_value, pci_node in zip(pci_devs, pci_ids):
            pci_value = pci_value.replace(".", "_")
            cmd = "lspci -ks %s | grep 'Kernel driver in use' |\
                   awk '{print $5}'" % pci_node
            driver_name = process.run(cmd, shell=True).stdout_text.strip()
            if driver_name != "vfio-pci":
                logging.debug("device already attached")
            else:
                if virsh.nodedev_reattach(pci_value).exit_status:
                    test.fail("Hostdev node reattach failed")
                driver_name = process.run(cmd, shell=True).stdout_text.strip()
                if driver_name == "vfio-pci":
                    test.error("driver bind failed after reattach")

    def check_attach_pci():
        """
        Function is to verify if the pci device is available in the guest
        
        :return : Returns True if the pci device is found in the guest interface list
                : Return False if the pci device is not found in the guest interface list
        """
        session = vm.wait_for_login()
        output = session.cmd_output("ip link")
        nic_list_after = str(output.splitlines())
        logging.debug(nic_list_after)
        return nic_list_after != nic_list_before

    def device_hotplug():
        """
        Function performs attach/hotplug of a pci device to the guest

        Test fails when hotplug of pci device fails
        """
        if arch == "ppc64le":
            if libvirt_version.version_compare(3, 10, 0):
                detach_device(pci_devs, pci_ids)
        else:
            if not libvirt_version.version_compare(3, 10, 0):
                detach_device(pci_devs, pci_ids)
        # attach the device in hotplug mode
        result = virsh.attach_device(vm_name, dev.xml,
                                     flagstr="--live", debug=True)
        time.sleep(timeout)
        if result.exit_status:
            test.error(result.stdout.strip())
        else:
            logging.debug(result.stdout.strip())
        if not utils_misc.wait_for(check_attach_pci, timeout):
            test.fail("timeout value is not sufficient")

    def device_hotunplug():
        """
        Function performs detach/hot-unplug of a pci device to the guest

        Test fails when hot unplug of pci device fails
        """
        result = virsh.detach_device(vm_name, dev.xml,
                                     flagstr="--live", debug=True)
        if result.exit_status:
            test.fail(result.stdout.strip())
        else:
            logging.debug(result.stdout.strip())
        # Fix me
        # the purpose of waiting here is after detach the device from
        # guest it need time to perform any other operation on the device
        time.sleep(timeout)
        if not libvirt_version.version_compare(3, 10, 0):
            pci_devs.sort()
            reattach_device(pci_devs, pci_ids)

    def get_new_dmesg_logs(last_dmesg_line):
        """
        Fetch new `dmesg` logs since the last pointer.
        
        :param last_dmesg_line: pointer to last dmesg line from dmeg logs
        :return : Returns dmesg logs from pointer line onwards. 
                  Return None if no new logs available in dmesg
        """
        cmd = "dmesg"
        res_status, res_output = session.cmd_status_output(cmd)
        if res_status != 0:
            test.fail(f"Failed to fetch dmesg logs, status: {res_status}, output: {res_output}")
        logs = res_output.splitlines()
        if last_dmesg_line is not None:
            try:
                idx = logs.index(last_dmesg_line)
                new_logs = logs[idx + 1:]
            except ValueError:
                new_logs = logs
        else:
            new_logs = logs
        return new_logs, logs[-1] if logs else None

    def test_eeh_nic():
        """
        Function is to perform EEH injection operation on a network device 
        by running eerinjct command
        
        """
        cmd = f"echo {max_freeze} > /sys/kernel/debug/powerpc/eeh_max_freezes"
        process.run(cmd, shell=True)
        loc_code = utils_sys.get_location_code(pci_id)
        num_of_miss = 0
        last_dmesg_line = None
        pass_hit = 0
        for num_of_hit in range(1, int(max_freeze) + 2):
            if num_of_miss < 5:
                # Inject EEH error using below command
                eeh_cmd = f"errinjct {ioa_system_info} -f {func} -p {loc_code} -m 0"
                if eeh_host == "yes":
                    utils_misc.cmd_status_output(eeh_cmd, shell=True, verbose=True, ignore_status=False, session=None)
                is_hit, last_dmesg_line = check_eeh_hit(last_dmesg_line)
                if not is_hit:
                    num_of_miss += 1
                    if num_of_hit >= 1 and pass_hit != 0:
                        test.fail(f"Failed to inject EEH after {pass_hit} sucessfull attempt for {pci_ids}. Please check dmesg logs")
                    logging.debug(f"PCI Device {pci_ids} EEH hit failed")
                    continue
                is_recovered, last_dmesg_line, status = check_eeh_pci_device_recovery(last_dmesg_line)
                if status == "PERMANENT FAILURE":
                    logging.debug(f"PCI device Permanently Failed after EEH attempt {num_of_hit}")
                else:
                    if not is_recovered:
                        test.fail(f"PCI device {pci_ids} recovery failed after {num_of_hit} EEH")
                    else:
                        logging.debug(f"PCI device recovery successfull after EEH attempt {num_of_hit}")
            else:
                test.fail("Failed to Inject EEH for 5 times")
            pass_hit += 1
        is_removed, last_dmesg_line = check_eeh_removed(last_dmesg_line)
        if is_removed:
            logging.debug(f"PCI Device {pci_ids} removed successfully")
        else:
            test.fail(f"PCI Device {pci_ids} failed to permanetly disable after max hit")

    def check_eeh_pci_device_recovery(last_dmesg_line):
        """
        Check if the pci device is recovered successfully after injecting EEH
        
        :param last_dmesg_line: this is a pointer to last dmesg line read.
        :return : Returns True/False, last_dmesg_line pointer and Success/Failure 
                  string to identify the EEH operation was successfull or not
        """
        tries = 60
        for _ in range(0, tries):
            logs, last_dmesg_line = get_new_dmesg_logs(last_dmesg_line)
            if any('permanent failure' in log for log in logs):
                logging.debug("PERMANENT FAILURE IS SEEN")
                return False, last_dmesg_line, "PERMANENT FAILURE"
            elif any('EEH: Recovery successful.' in log for log in logs):
                logging.debug("waiting for pci device to recover %s", pci_ids)
                break
            time.sleep(10)
        else:
            logging.debug("EEH recovery failed for pci device %s" % pci_ids)
        tries = 30
        for _ in range(0, tries):
            if sorted(vm.get_pci_devices()) != sorted(nic_list_before):
                logging.debug("Adapter found after EEH was injection successfully")
                return True, last_dmesg_line, "SUCCESS"
            time.sleep(1)
        return False, last_dmesg_line, "RECOVERY FAILED"

    def check_eeh_hit(last_dmesg_line):
        """
        Function to check if EEH is successfully hit
        
        :param last_dmesg_line : this is a pointer to last dmesg line read 
        :return : Returns True/False and last_dmesg_line pointer
        """
        tries = 30
        for _ in range(0, tries):
            logs, last_dmesg_line = get_new_dmesg_logs(last_dmesg_line)
            if any('EEH: Frozen' in log for log in logs):
                return True, last_dmesg_line
            time.sleep(1)
        return False, last_dmesg_line

    def check_eeh_removed(last_dmesg_line):
        """
        Function to check if PCI PT is recovered successfully

        :param last_dmesg_line : this is a pointer to last dmesg line read
        :return : Returns True/False and last_dmesg_line pointer
        """
        tries = 30
        for _ in range(0, tries):
            time.sleep(10)
            cmd = "dmesg"
            res_status, res_output = session.cmd_status_output(cmd)
            if 'permanent failure' in res_output and res_status == 0:
                return True, last_dmesg_line
        return False, last_dmesg_line

    try:
        device_hotplug()
        if device_type == "NIC":
            test_ping(net_ip, server_ip, netmask)
            test_eeh_nic()
        device_hotunplug()

    finally:
        cmd = "dmesg"
        res_output = session.cmd_output(cmd)
        logging.debug("complete dmesg Logs:: %s", res_output)
        backup_xml.sync()
        if session:
            session.close()
        if arch == "ppc64le":
            if libvirt_version.version_compare(3, 10, 0):
                pci_devs.sort()
                reattach_device(pci_devs, pci_ids)
        else:
            if not libvirt_version.version_compare(3, 10, 0):
                pci_devs.sort()
                reattach_device(pci_devs, pci_ids)
