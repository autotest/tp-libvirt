import os
import re
import logging
import netaddr
import time

from virttest import virsh
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest import utils_net
from virttest.utils_test import libvirt
from virttest import utils_hotplug
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError
from virttest.libvirt_xml.devices import memory
from virttest import utils_test
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.test_setup import PciAssignable
from virttest import utils_misc

from provider import libvirt_version


def run(test, params, env):
    """
    Test for PCI device passthrough to libvirt guest.

    a). NIC:
        1. Get params.
        2. Get the pci device for specific net_name.
        3. Attach pci device to guest.
        4. Start guest and set the ip to all the physical functions.
        5. Ping to server_ip from each physical function
           to verify the new network device.
    b). STORAGE:
        1. Get params.
        2. Get the pci device for specific storage_dev_name.
        3. Store the result of 'fdisk -l' on guest.
        3. Attach pci device to guest.
        4. Start guest and get the result of 'fdisk -l' on guest.
        5. Compare the result of 'fdisk -l' before and after
            attaching storage pci device to guest.
    """
    def create_mem_hotplug_xml(mem_size, mem_unit, numa_node='',
                               mem_model='dimm'):
        """
        Forms and return memory device xml for hotplugging
        :param mem_size: memory to be hotplugged
        :param mem_unit: unit for memory size
        :param numa_node: numa node to which memory is hotplugged
        :param mem_model: memory model to be hotplugged
        :return: xml with memory device
        """
        mem_xml = memory.Memory()
        mem_xml.mem_model = mem_model
        target_xml = memory.Memory.Target()
        target_xml.size = mem_size
        target_xml.size_unit = mem_unit
        if numa_node:
            target_xml.node = int(numa_node)
        mem_xml.target = target_xml
        logging.debug(mem_xml)
        mem_xml_file = os.path.join(data_dir.get_tmp_dir(),
                                    'memory_hotplug.xml')
        try:
            fp = open(mem_xml_file, 'w')
        except Exception, info:
            test.error(info)
        fp.write(str(mem_xml))
        fp.close()
        return mem_xml_file

    def memory_hotplug_hotunplug(vm, mem_hotplug_size, mem_size_unit,
                                 mem_hotplug_count, uri=None, unplug=False,
                                 params=None, vm_ip=None, session_timeout=600):
        """
        Performs Memory Hotplug or Hotunplug based on the hotplug count given.
        :param vm: vm object
        :param mem_hotplug_size: memory size to be hotplugged
        :param mem_size_unit: unit of memory size
        :param mem_hotplug_count: no of times hotplug/hotunplug operation
        :param uri: virsh connect uri if operation to be performed remotely
        :param unplug: if False perform hotplug, if True perform hotunplug
        :param params: Test dict params
        :param vm_ip: vm's ip address
        :param session_timeout: time out for shell session to return
        :raise: test.fail if memory hotplug or hotunplug doesn't work
        """

        mem_xml_list = []
        # For all numa node related scenarios, we create and use node 0 and
        # node 1 for guest, perform hotplug/hotunplug to node 0 if hotplug
        # count is 1 and perform hotplug/hotunplug to node 0 and node 1
        # alternatively if hotplug count > 1
        numa_node = '0'
        cmd = "cat /proc/meminfo | grep 'MemTotal'"
        dmesg_cmd = "dmesg -c"
        session = vm.wait_for_login()
        # clear the dmesg log
        if session.cmd(dmesg_cmd) != 0:
            logging.error("Failed to clear the dmesg logs")
        act_mem = session.cmd_output(cmd, timeout=session_timeout)
        act_mem = int(act_mem.split()[-2].strip())
        virsh_inst = virsh.Virsh(uri=uri)
        vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm.name,
                                                     virsh_instance=virsh_inst)
        vm_current_mem = int(vmxml_backup.current_mem)
        if mem_hotplug_count == 1:
            mem_xml = create_mem_hotplug_xml(mem_hotplug_size,
                                             mem_size_unit, numa_node)
            if not unplug:
                logging.info("Trying to hotplug memory after"
                             " device pass-through")
                ret_attach = virsh.attach_device(vm.name, mem_xml,
                                                 flagstr="--live",
                                                 debug=True, uri=uri)
                if ret_attach.exit_status != 0:
                    test.fail("Hotplug memory failed: %s", ret_attach.stderr)
            else:
                logging.info("Trying to hotunplug memory after"
                             " device pass-through")
                ret_attach = virsh.detach_device(vm.name, mem_xml,
                                                 flagstr="--live",
                                                 debug=True, uri=uri)
                if ret_attach.exit_status != 0:
                    test.fail("Hotunplug memory failed: %s", ret_attach.stderr)
            mem_xml_list.append(mem_xml)
        elif mem_hotplug_count > 1:
            for each_count in range(mem_hotplug_count):
                mem_xml = create_mem_hotplug_xml(mem_hotplug_size,
                                                 mem_size_unit,
                                                 numa_node)
                if not unplug:
                    logging.info("Trying to hotplug memory count: %s",
                                 each_count + 1)
                    ret_attach = virsh.attach_device(vm.name, mem_xml,
                                                     flagstr="--live",
                                                     debug=True, uri=uri)
                    if ret_attach.exit_status != 0:
                        test.fail("Hotplug memory failed at count %s: %s",
                                  each_count + 1, ret_attach.stderr)
                else:
                    logging.info("Trying to hotunplug memory count: %s",
                                 each_count + 1)
                    ret_attach = virsh.detach_device(vm.name, mem_xml,
                                                     flagstr="--live",
                                                     debug=True, uri=uri)
                    if ret_attach.exit_status != 0:
                        test.fail("Hotunplug memory failed at count %s: %s",
                                  each_count + 1, ret_attach.stderr)
                    # Hotplug memory to numa node alternatively if
                    # there are 2 nodes
                    if len(numa_dict_list) == 2:
                        if numa_node == '0':
                            numa_node = '1'
                        else:
                            numa_node = '0'
                mem_xml_list.append(mem_xml)
        if unplug:
            session = vm.wait_for_login()
            err_msg = "pseries-hotplug-mem: Memory indexed-count-remove failed"
            cmd = "shutdown -r now"
            dmesg_cmd = "dmesg"
            if err_msg in str(session.cmd_output(dmesg_cmd)).strip():
                # if hotplugged memory is in use then mem hotunplug might fail
                # so guest is reboot necessary for mem hotunplug to take effect
                try:
                    session.cmd(cmd)
                except Exception:
                    logging.info("guest reboot command triggered")
                    if hotunplug_mem and not params:
                        session = vm.wait_for_login()
            cmd = "cat /proc/meminfo | grep 'MemTotal'"
            # check hotplugged memory is reflected
            status = -1
            while(True):
                status, output = session.cmd_status_output(cmd,
                                                           timeout=session_timeout)
                if status == 0:
                    vm_new_mem = int(str(output).split()[-2].strip())
                    break
        if not unplug:
            session = vm.wait_for_login()
            vm_new_mem = session.cmd_output(cmd, timeout=session_timeout)
            vm_new_mem = int(vm_new_mem.split()[-2].strip())
        vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm.name,
                                                     virsh_instance=virsh_inst)
        vm_new_current_mem = int(vmxml_backup.current_mem)
        logging.debug("Old current memory %d", vm_current_mem)
        logging.debug("New current memory %d", vm_new_current_mem)
        logging.debug("Old current memory inside guest %d", act_mem)
        logging.debug("New current memory inside guest %d", vm_new_mem)
        if not unplug:
            expected_mem = vm_current_mem + vm_hotplug_mem
            expected_vm_mem = act_mem + vm_hotplug_mem
            logging.debug("Hot plug mem %d", vm_hotplug_mem)
            logging.debug("old mem + hotplug = %d", expected_mem)
            logging.debug("old mem + hotplug inside guest = %d",
                          expected_vm_mem)
            if not vm_new_mem > act_mem:
                test.fail("Memory hotplug failed with the device pass-through")
        else:
            expected_mem = vm_current_mem - vm_hotplug_mem
            expected_vm_mem = act_mem - vm_hotplug_mem
            logging.debug("Hot unplug mem %d", vm_hotplug_mem)
            logging.debug("old mem - hotunplug = %d", expected_mem)
            logging.debug("old mem - hotunplug inside guest = %d",
                          expected_vm_mem)
            if not vm_new_mem < act_mem:
                test.fail("Memory hotunplug failed with device passthrough")
        if not vm_new_current_mem == expected_mem:
            test.fail("Memory hotplug/unplug failed with device pass-through")

        logging.debug("Memory hotplug/hotunplug successful"
                      " along device pass-through!!!")
        if session:
            session.close()
        return mem_xml_list

    def create_numa(vcpu, max_mem, max_mem_unit):
        """
        creates list of dictionaries of numa
        :param vcpu: vcpus of existing guest
        :param max_mem: max_memory of existing guest
        :param max_mem_unit: unit of max_memory
        :return: numa dictionary list
        """
        numa_dict = {}
        numa_dict_list = []
        if vcpu == 1:
            numa_dict['id'] = '0'
            numa_dict['cpus'] = '0'
            numa_dict['memory'] = str(max_mem)
            numa_dict['unit'] = str(max_mem_unit)
            numa_dict_list.append(numa_dict)
        else:
            for index in range(2):
                numa_dict['id'] = str(index)
                numa_dict['memory'] = str(max_mem / 2)
                numa_dict['unit'] = str(max_mem_unit)
                if vcpu == 2:
                    numa_dict['cpus'] = "%s" % str(index)
                else:
                    if index == 0:
                        if vcpu == 3:
                            numa_dict['cpus'] = "%s" % str(index)
                        if vcpu > 3:
                            numa_dict['cpus'] = "%s-%s" % (str(index),
                                                           str(vcpu / 2 - 1))
                    else:
                        numa_dict['cpus'] = "%s-%s" % (str(vcpu / 2),
                                                       str(vcpu - 1))
                numa_dict_list.append(numa_dict)
                numa_dict = {}
        return numa_dict_list

    def online_new_vcpu(vm, vcpu_plug_num):
        """
        :params vm: VM object
        :params vcpu_plug_num: Hotplugged vcpu count
        """
        cpu_is_online = []
        session = vm.wait_for_login()
        for i in range(1, int(vcpu_plug_num)):
            cpu_is_online.append(False)
            cpu = "/sys/devices/system/cpu/cpu%s/online" % i
            cmd_s, cmd_o = session.cmd_status_output("cat %s" % cpu)
            logging.debug("cmd exist status: %s, cmd output %s", cmd_s, cmd_o)
            if cmd_s != 0:
                logging.error("Can not find cpu %s in domain", i)
            else:
                if cmd_o.strip() == "0":
                    if session.cmd_status("echo 1 > %s" % cpu) == 0:
                        cpu_is_online[i-1] = True
                    else:
                        logging.error("Fail to enable cpu %s online"
                                      " after hot-plugging", i)
                else:
                    cpu_is_online[i-1] = True
        session.close()
        return False not in cpu_is_online

    def check_setvcpus_result(cmd_result, expect_error):
        """
        Check command result.

        For setvcpus, pass unsupported commands(plug or unplug vcpus) by
        checking command stderr.

        :params cmd_result: Command result
        :params expect_error: Whether to expect error True or False
        """
        if cmd_result.exit_status != 0:
            if expect_error:
                logging.debug("Expect fail: %s", cmd_result.stderr)
                return
            if re.search("unable to execute QEMU command 'cpu-add'",
                         cmd_result.stderr):
                test.cancel("guest <os> machine property may be too"
                            "  old to allow hotplug")

            if re.search("cannot change vcpu count of this domain",
                         cmd_result.stderr):
                test.cancel("Unsupport virsh setvcpu hotplug")

            # Maybe QEMU doesn't support unplug vcpu
            if re.search("Operation not supported: qemu didn't"
                         " unplug the vCPUs", cmd_result.stderr):
                test.cancel("Your qemu unsupport unplug vcpu")

            # Attempting to enable more vCPUs in the guest than is currently
            # enabled in the guest but less than the maximum count for the VM
            if re.search("requested vcpu count is greater than the count of "
                         "enabled vcpus in the domain",
                         cmd_result.stderr):
                logging.debug("Expect fail: %s", cmd_result.stderr)
                return

            # Otherwise, it seems we have a real error
            test.fail("Run failed with right command: %s"
                      % cmd_result.stderr)
        else:
            if expect_error:
                test.fail("Expect fail but run successfully")

    def create_iface_xml(mac):
        """
        Create interface xml file
        """
        iface = Interface(type_name=iface_type)
        iface.source = iface_source
        iface.model = iface_model if iface_model else "virtio"
        if iface_target:
            iface.target = {'dev': iface_target}
        iface.mac_address = mac
        logging.debug("Create new interface xml: %s", iface)
        return iface

    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    # Memory Hotplug/unplug specific attributes
    mem_hotplug = "yes" == params.get("mem_hotplug", "no")
    mem_hotplug_size = int(params.get("hotplug_mem", "262144"))
    mem_hotplug_count = int(params.get("mem_hotplug_count", "1"))
    mem_size_unit = params.get("hotplug_mem_unit", "KiB")
    enable_numa = "yes" == params.get("with_numa", "no")
    hotunplug_mem = "yes" == params.get("hotunplug_mem", "no")
    mem_operation = params.get("mem_operation", "no")
    session_timeout = int(params.get("guest_session_timeout", 600))
    # vCPU Hotplug/unplug specific attributes
    vcpu_operation = params.get("vcpu_operation", "no")
    vcpu_max_num = int(params.get("vcpu_max_num", "4"))
    vcpu_current_num = int(params.get("vcpu_current_num", "2"))
    vcpu_plug = "yes" == params.get("vcpu_plug", "no")
    vcpu_plug_num = int(params.get("vcpu_plug_num", "4"))
    vcpu_unplug = "yes" == params.get("vcpu_unplug", "no")
    vcpu_unplug_num = int(params.get("vcpu_unplug_num", "2"))
    setvcpu_option = params.get("setvcpu_option", "")
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    check_after_plug_fail = "yes" == params.get("check_after_plug_fail", "no")
    # Network Hotplug/unplug specific attributes.
    network_operation = params.get("network_operation", "no")
    iface_num = params.get("iface_num", '1')
    iface_type = params.get("iface_type", "network")
    iface_source = eval(params.get("iface_source",
                                   "{'network':'default'}"))
    iface_model = params.get("iface_model")
    iface_target = params.get("iface_target")
    iface_mac = params.get("iface_mac")
    attach_iface = "yes" == params.get("attach_iface", "no")
    attach_option = params.get("attach_option", "")
    detach_device = "yes" == params.get("detach_device")
    username = params.get("username")
    password = params.get("password")
    poll_timeout = int(params.get("poll_timeout", 10))
    detach_option = attach_option
    status_error = "yes" == params.get("status_error", "no")
    # PCI Pass-through specific attributes
    sriov = ('yes' == params.get("libvirt_pci_SRIOV", 'no'))
    device_type = params.get("libvirt_pci_device_type", "NIC")
    pci_dev = None
    device_name = None
    pci_address = None
    bus_info = []
    if device_type == "NIC":
        pci_dev = params.get("libvirt_pci_net_dev_label")
        device_name = params.get("libvirt_pci_net_dev_name", "None")
    else:
        pci_dev = params.get("libvirt_pci_storage_dev_label")

    net_ip = params.get("libvirt_pci_net_ip", "ENTER.YOUR.IP")
    server_ip = params.get("libvirt_pci_server_ip",
                           "ENTER.YOUR.SERVER.IP")
    netmask = params.get("libvirt_pci_net_mask", "ENTER.YOUR.Mask")

    # Back up domain XML
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    if mem_hotplug:
        # To check memory hotplug is supported by libvirt, memory hotplug
        # support QEMU/KVM driver was added in 1.2.14 version of libvirt
        if not libvirt_version.version_compare(1, 2, 14):
            test.cancel("Memory Hotplug is not supported")

        xml_list = []
        # hotplug memory in KiB
        vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_max_dimm_slots = int(params.get("max_dimm_slots",
                                           "32"))
        vm_hotplug_mem = mem_hotplug_size * mem_hotplug_count
        vm_current_mem = int(vmxml_backup.current_mem)
        vm_max_mem = int(vmxml_backup.max_mem)

        vm_max_mem_rt_limit = 256 * 1024 * vm_max_dimm_slots
        # configure Maxmem in guest xml for memory hotplug to work
        try:
            vm_max_mem_rt = int(vmxml_backup.max_mem_rt)
            if(vm_max_mem_rt <= vm_max_mem_rt_limit):
                vmxml_backup.max_mem_rt = (vm_max_mem_rt_limit +
                                           vm_max_mem)
                vmxml_backup.max_mem_rt_slots = vm_max_dimm_slots
                vmxml_backup.max_mem_rt_unit = mem_size_unit
                vmxml_backup.sync()
                vm_max_mem_rt = int(vmxml_backup.max_mem_rt)
        except LibvirtXMLNotFoundError:
            vmxml_backup.max_mem_rt = (vm_max_mem_rt_limit +
                                       vm_max_mem)
            vmxml_backup.max_mem_rt_slots = vm_max_dimm_slots
            vmxml_backup.max_mem_rt_unit = mem_size_unit
            vmxml_backup.sync()
            vm_max_mem_rt = int(vmxml_backup.max_mem_rt)
        logging.debug("Hotplug mem = %d %s", mem_hotplug_size, mem_size_unit)
        logging.debug("Hotplug count = %d", mem_hotplug_count)
        logging.debug("Current mem = %d", vm_current_mem)
        logging.debug("VM maxmem = %d", vm_max_mem_rt)
        if((vm_current_mem + vm_hotplug_mem) > vm_max_mem_rt):
            test.cancel("Cannot hotplug memory more than max dimm slots "
                        "supported")
        if mem_hotplug_count > vm_max_dimm_slots:
            test.cancel("Cannot hotplug memory more than %d times" %
                        vm_max_dimm_slots)

    if enable_numa:
        numa_dict_list = []
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        vcpu = vmxml.vcpu
        max_mem = vmxml.max_mem
        max_mem_unit = vmxml.max_mem_unit
        if vcpu < 1:
            test.error("%s not having even 1 vcpu" % vm.name)
        else:
            numa_dict_list = create_numa(vcpu, max_mem, max_mem_unit)
        vmxml_cpu = vm_xml.VMCPUXML()
        vmxml_cpu.xml = "<cpu><numa/></cpu>"
        logging.debug(vmxml_cpu.numa_cell)
        vmxml_cpu.numa_cell = numa_dict_list
        logging.debug(vmxml_cpu.numa_cell)
        vmxml.cpu = vmxml_cpu
        vmxml.sync()

    # Init expect vcpu count values
    expect_vcpu_num = {'max_config': vcpu_max_num, 'max_live': vcpu_max_num,
                       'cur_config': vcpu_current_num,
                       'cur_live': vcpu_current_num,
                       'guest_live': vcpu_current_num}
    if check_after_plug_fail:
        expect_vcpu_num_bk = expect_vcpu_num.copy()

    # Check the parameters from configuration file.
    if (pci_dev.count("ENTER")):
        test.cancel("Please enter your device name for test.")
    if (device_type == "NIC" and (net_ip.count("ENTER") or
                                  server_ip.count("ENTER") or
                                  netmask.count("ENTER"))):
        test.cancel("Please enter the ips and netmask for NIC "
                    "test in config file")
    fdisk_list_before = None

    # Back up domain XML
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    if device_type == "NIC":
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        output = session.cmd_output("lspci -nn")
        nic_list_before = output.splitlines()
        if sriov:
            # The SR-IOV setup of the VF's should be done by test_setup
            # based on the driver options.
            # Usage of the PciAssignable for setting up of the VF's
            # is generic, and eliminates the need to hardcode the driver
            # and number of VF's to be created.

            sriov_setup = PciAssignable(
                driver=params.get("driver"),
                driver_option=params.get("driver_option"),
                host_set_flag=params.get("host_set_flag", 1),
                vf_filter_re=params.get("vf_filter_re"),
                pf_filter_re=params.get("pf_filter_re"),
                pa_type=params.get("pci_assignable"))

            cont = sriov_setup.get_controller_type()
            logging.info("The Controller type is '%s'", cont)
            if cont == "Infiniband controller":
                sriov_setup.set_linkvf_ib()

            # Based on the PF Device specified, all the VF's
            # belonging to the same iommu group, will be
            # pass-throughed to the guest.
            pci_id = pci_dev.replace("_", ".").strip("pci.").replace(".", ":", 2)
            pci_ids = sriov_setup.get_same_group_devs(pci_id)
            pci_devs = []
            for val in pci_ids:
                temp = val.replace(":", "_")
                pci_devs.extend(["pci_"+temp])
            pci_id = re.sub('[:.]', '_', pci_id)
            for val in pci_devs:
                val = val.replace(".", "_")
                # Get the virtual functions of the pci devices
                # which was generated above.
                pci_xml = NodedevXML.new_from_dumpxml(val)
                virt_functions = pci_xml.cap.virt_functions
                if not virt_functions:
                    test.fail("No Virtual Functions found.")
                for val in virt_functions:
                    pci_dev = utils_test.libvirt.pci_label_from_address(val,
                                                                        radix=16)
                    pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
                    pci_address = pci_xml.cap.get_address_dict()
                    vmxml.add_hostdev(pci_address)
        else:
            pci_id = pci_dev.replace("_", ".").strip("pci.").replace(".", ":", 2)
            obj = PciAssignable()
            # get all functions id's
            pci_ids = obj.get_same_group_devs(pci_id)
            pci_devs = []
            for val in pci_ids:
                temp = val.replace(":", "_")
                pci_devs.extend(["pci_"+temp])
            pci_id = re.sub('[:.]', '_', pci_id)
            for val in pci_devs:
                val = val.replace(".", "_")
                pci_xml = NodedevXML.new_from_dumpxml(val)
                pci_address = pci_xml.cap.get_address_dict()
                vmxml.add_hostdev(pci_address)

    elif device_type == "STORAGE":
        # Store the result of "fdisk -l" in guest.
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
        fdisk_list_before = output.splitlines()

        pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
        pci_address = pci_xml.cap.get_address_dict()
        vmxml.add_hostdev(pci_address)

    def test_vcpu_plug():
        result_vcpu = utils_hotplug.check_vcpu_value(vm, expect_vcpu_num)
        if vcpu_plug:
            logging.info("Hot-plugging the vcpu after device pass-through")
            result = virsh.setvcpus(vm_name, vcpu_plug_num, setvcpu_option,
                                    ignore_status=True, debug=True)
            check_setvcpus_result(result, status_error)

            if not status_error:
                if not utils_misc.wait_for(lambda: utils_misc.check_if_vm_vcpu_match(vcpu_plug_num, vm),
                                           120, text="wait for vcpu online") or not online_new_vcpu(vm, vcpu_plug_num):
                    test.fail("Fail to enable new added cpu")
            if status_error and check_after_plug_fail:
                result_vcpu = utils_hotplug.check_vcpu_value(vm,
                                                             expect_vcpu_num_bk,
                                                             {},
                                                             setvcpu_option)

                check_setvcpus_result(result_vcpu, status_error)
        # Unplug vcpu:
        if vcpu_unplug:
            logging.info("Hot-unplugging the vcpu after device pass-through")
            result = virsh.setvcpus(vm_name, vcpu_unplug_num, '--live',
                                    debug=True)
            check_setvcpus_result(result, status_error)
            libvirt.check_exit_status(result)
        if not result_vcpu:
            test.fail("Test Failed")

    def test_mem_plug():
        if mem_hotplug:
            logging.debug("Performing Memory Hotplug after device pass-through")
            mem_xml = memory_hotplug_hotunplug(vm,
                                               mem_hotplug_size,
                                               mem_size_unit,
                                               mem_hotplug_count,
                                               session_timeout=session_timeout)
            xml_list.extend(mem_xml)
        if hotunplug_mem:
            logging.debug("Performing Memory Hotunplug after device pass-through")
            mem_xml = memory_hotplug_hotunplug(vm,
                                               mem_hotplug_size,
                                               mem_size_unit,
                                               mem_hotplug_count,
                                               unplug=True,
                                               session_timeout=session_timeout)
            xml_list.extend(mem_xml)

    def test_nw_plug():
        # Check virsh command option
        check_cmds = []
        if not status_error:
            if attach_iface and attach_option:
                check_cmds.append(('attach-interface', attach_option))
            if detach_device and detach_option:
                check_cmds.append(('detach-device', detach_option))
        for cmd, option in check_cmds:
            libvirt.virsh_cmd_has_option(cmd, option)
        # Attach an interface when vm is running
        iface_list = []
        err_msgs = ("No more available PCI slots",
                    "No more available PCI addresses")
        if attach_iface:
            logging.info("Hot-plugging n/w after device pass-through")
            for i in range(int(iface_num)):
                logging.info("Try to attach interface loop %s" % i)
                mac = utils_net.generate_mac_address_simple()
                options = ("%s %s --model %s --mac %s %s" %
                           (iface_type, iface_source['network'],
                            iface_model, mac, attach_option))
                ret = virsh.attach_interface(vm_name, options,
                                             ignore_status=True)
                libvirt.check_exit_status(ret)
                if ret.exit_status:
                    if any([msg in ret.stderr for msg in err_msgs]):
                        logging.debug("No more pci slots, can't"
                                      " attach more devices")
                        break
                    elif (ret.stderr.count("doesn't support option %s"
                                           % attach_option)):
                        test.cancel(ret.stderr)
                    elif status_error:
                        continue
                    else:
                        logging.error("Command output %s" %
                                      ret.stdout.strip())
                        test.fail("Failed to attach-interface")
                else:
                    iface_list.append({'mac': mac})

        # Check if interface was attached
        for iface in iface_list:
            if 'mac' in iface:
                # Check interface in dumpxml output
                if_attr = vm_xml.VMXML.get_iface_by_mac(vm_name,
                                                        iface['mac'])
                logging.info("The interface is '%s'", if_attr)
                if not if_attr:
                    test.fail("Can't see interface in dumpxml")
                if (if_attr['type'] != iface_type or
                        if_attr['source'] != iface_source['network']):
                    test.fail("Interface attribute doesn't"
                              " match attachment opitons")

                # Check interface on guest
                def get_iface():
                    try:
                        ni = utils_net.get_linux_ifname(session, iface['mac'])
                        logging.info("The interface name is '%s'", ni)
                        return True
                    except Exception:
                        return False
                if not utils_misc.wait_for(get_iface, timeout=60):
                    test.fail("Can't see interface on guest")

        # Detach hot/cold-plugged interface at last
        if detach_device:
            logging.info("Hot-unplugging n/w after device pass-through")
            for iface in iface_list:
                if 'iface_xml' in iface:
                    ret = virsh.detach_device(vm_name,
                                              iface['iface_xml'].xml,
                                              flagstr="",
                                              ignore_status=True)
                    libvirt.check_exit_status(ret)
                else:
                    options = ("%s --mac %s" %
                               (iface_type, iface['mac']))
                    ret = virsh.detach_interface(vm_name, options,
                                                 ignore_status=True)
                    libvirt.check_exit_status(ret)
            # Check if interface was detached
            for iface in iface_list:
                if 'mac' in iface:
                    polltime = time.time() + poll_timeout
                    while True:
                        # Check interface in dumpxml output
                        if not vm_xml.VMXML.get_iface_by_mac(vm_name,
                                                             iface['mac']):
                            break
                        else:
                            time.sleep(2)
                            if time.time() > polltime:
                                test.fail("Interface still "
                                          "exists after detachment")

    try:
        vmxml.sync()
        vmxml.set_vm_vcpus(vm_name, vcpu_max_num, vcpu_current_num)

        vm.start()
        session = vm.wait_for_login()
        # The Network configuration is generic irrespective of PF or SRIOV VF
        if device_type == "NIC":
            output = session.cmd_output("lspci -nn")
            nic_list_after = output.splitlines()
            net_ip = netaddr.IPAddress(net_ip)
            if nic_list_after == nic_list_before:
                test.fail("passthrough Adapter not found in guest.")
            else:
                logging.debug("Adapter passthorughed to guest successfully")
            output = session.cmd_output("lspci -nn | grep %s" % device_name)
            nic_list = output.splitlines()
            for val in range(len(nic_list)):
                bus_info.append(str(nic_list[val]).split(' ', 1)[0])
                nic_list[val] = str(nic_list[val]).split(' ', 1)[0][:-2]
            bus_info.sort()
            if not sriov:
                # check all functions get same iommu group
                if len(set(nic_list)) != 1:
                    test.fail("Multifunction Device passthroughed but "
                              "functions are in different iommu group")
            # ping to server from each function
            for val in bus_info:
                nic_name = str(utils_misc.get_interface_from_pci_id(val, session))
                session.cmd("ip addr flush dev %s" % nic_name)
                session.cmd("ip addr add %s/%s dev %s"
                            % (net_ip, netmask, nic_name))
                session.cmd("ip link set %s up" % nic_name)
                # Pinging using nic_name is having issue,
                # hence replaced with IPAddress
                s_ping, o_ping = utils_test.ping(server_ip, count=5,
                                                 interface=net_ip, timeout=30,
                                                 session=session)
                logging.info(o_ping)
                if s_ping != 0:
                    err_msg = "Ping test fails, error info: '%s'"
                    test.fail(err_msg % o_ping)
                # Each interface should have unique IP
                net_ip = net_ip + 1

            # Device Hot-plugging after pass-through
            # Run vcpu hotplug/un-plug test
            if vcpu_operation == "yes":
                test_vcpu_plug()
            # Run Memory hotplug/unplug test
            if mem_operation == "yes":
                test_mem_plug()
            # Run Network hotplug/unplug test
            if network_operation == "yes":
                test_nw_plug()

        elif device_type == "STORAGE":
            # Get the result of "fdisk -l" in guest, and
            # compare the result with fdisk_list_before.
            output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
            fdisk_list_after = output.splitlines()
            if fdisk_list_after == fdisk_list_before:
                test.fail("Didn't find the disk attached to guest.")
    finally:
        backup_xml.sync()
        # For SR-IOV , VF's should be cleaned up in the post-processing.
        if sriov:
            sriov_setup.release_devs()
