import re
import logging

from virttest import virsh
from virttest import utils_net
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

driver_dict = {'virtio': 'virtio_net', '-': '8139cp', 'e1000': 'e1000',
               'rtl8139': '8139cp', 'spapr-vlan': 'ibmveth',
               'virtio-net-pci': 'virtio_net'}

# Regular expression for the below output
#      vnet0      bridge     virbr0     virtio      52:54:00:b2:b3:b4
rg = re.compile(r"^(\w+)\s+(\w+)\s+(\w+)\s+(\S+)\s+(([a-fA-F0-9]{2}:?){6})")


def run(test, params, env):
    """
    Step 1: Get the virsh domiflist value.
    Step 2: Check for interface in xml file.
    Step 3: Check for type in xml file.
    Step 4: Check for model inside the guest and xml file.
    Step 5: Check for mac id inside the guest and xml file
    """

    def parse_interface_details(output):
        """
        To parse the interface details from virsh command output
        """
        iface_cmd = {}
        ifaces_cmd = []
        for line in output.split('\n'):
            match_obj = rg.search(line)
            # Due to the extra space in the list
            if match_obj is not None:
                iface_cmd['interface'] = match_obj.group(1)
                iface_cmd['type'] = match_obj.group(2)
                iface_cmd['source'] = match_obj.group(3)
                iface_cmd['model'] = match_obj.group(4)
                iface_cmd['mac'] = match_obj.group(5)
                ifaces_cmd.append(iface_cmd)
                iface_cmd = {}
        return ifaces_cmd

    def check_output(ifaces_actual, vm, login_nic_index=0):
        """
        1. Get the interface details of the command output
        2. Get the interface details from xml file
        3. Check command output against xml and guest output
        """
        vm_name = vm.name

        try:
            session = vm.wait_for_login(nic_index=login_nic_index)
        except Exception as detail:
            test.fail("Unable to login to VM:%s" % detail)
        iface_xml = {}
        error_count = 0
        # Check for the interface values
        for item in ifaces_actual:
            # Check for mac and model
            model = item['model']
            iname = utils_net.get_linux_ifname(session, item['mac'])
            if iname is not None:
                cmd = 'ethtool -i %s | grep driver | awk \'{print $2}\'' % iname
                drive = session.cmd_output(cmd).strip()
                if driver_dict[model] != drive:
                    error_count += 1
                    logging.error("Mismatch in the model for the interface %s\n"
                                  "Expected Model:%s\nActual Model:%s",
                                  item['interface'], driver_dict[model],
                                  item['model'])
            else:
                error_count += 1
                logging.error("Mismatch in the mac for the interface %s\n",
                              item['interface'])
            iface_xml = vm_xml.VMXML.get_iface_by_mac(vm_name, item['mac'])
            if iface_xml is not None:
                if iface_xml['type'] != item['type']:
                    error_count += 1
                    logging.error("Mismatch in the network type for the "
                                  "interface %s \n Type in command output: %s\n"
                                  "Type in xml file: %s",
                                  item['interface'],
                                  item['type'],
                                  iface_xml['type'])
                if iface_xml['source'] != item['source']:
                    error_count += 1
                    logging.error("Mismatch in the network source for the"
                                  " interface %s \n Source in command output:"
                                  "%s\nSource in xml file: %s",
                                  item['interface'],
                                  item['source'],
                                  iface_xml['source'])

            else:
                error_count += 1
                logging.error("There is no interface in the xml file "
                              "with the below specified mac address\n"
                              "Mac:%s", item['mac'])
            iface_xml = {}
        if error_count > 0:
            test.fail("The test failed, consult previous error logs")

    def add_iface(vm, at_option=""):
        """
        Attach interface for the vm
        """
        if vm.is_alive() and "--inactive" not in additional_options:
            vm.destroy(gracefully=False)
        iface_source = params.get("iface_source", "default")
        iface_type = params.get("iface_type", "network")
        iface_model = params.get("iface_model", "virtio")
        iface_mac = utils_net.generate_mac_address_simple()
        at_options = (" --type %s --source %s --model %s --mac %s --config %s"
                      % (iface_type, iface_source, iface_model,
                         iface_mac, at_option))
        ret = virsh.attach_interface(vm.name, at_options, ignore_status=True)
        libvirt.check_exit_status(ret)
        nic_params = {'mac': iface_mac, 'nettype': 'bridge'}
        vm.add_nic(**nic_params)
        if not vm.is_alive():
            vm.start()
        # Return the new attached interface index
        return vm.get_nic_index_by_mac(iface_mac)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    domid = vm.get_id()
    domuuid = vm.get_uuid()
    attach_iface = "yes" == params.get("attach_iface", "no")
    attach_option = params.get("attach_option", "")
    additional_options = params.get("domiflist_extra_options", "")
    vm_backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    login_nic_index = 0
    new_nic_index = 0
    status_error = "yes" == params.get("status_error", "no")

    # Check virsh command option
    if attach_iface and attach_option and not status_error:
        libvirt.virsh_cmd_has_option('attach-interface', attach_option)

    try:
        # Get the virsh domiflist
        options = params.get("domiflist_domname_options", "id")

        if options == "id":
            options = domid
        elif options == "uuid":
            options = domuuid
        elif options == "name":
            options = vm_name

        result = virsh.domiflist(vm_name, "", ignore_status=True)
        libvirt.check_exit_status(result)
        old_iflist = parse_interface_details(result.stdout.strip())
        logging.debug("Old interface list: %s", old_iflist)
        # Attach interface for testing.
        if attach_iface:
            new_nic_index = add_iface(vm, attach_option)
            # Basically, after VM started, the new attached interface will get
            # IP address, so we should use this interface to login
            if '--inactive' not in additional_options:
                if new_nic_index > 0:
                    login_nic_index = new_nic_index

        vm.verify_alive()
        result = virsh.domiflist(options, additional_options, ignore_status=True)
        new_iflist = parse_interface_details(result.stdout.strip())
        logging.debug("New interface list: %s", new_iflist)

        if status_error:
            if result.exit_status == 0:
                test.fail("Run passed for incorrect command \nCommand: "
                          "virsh domiflist %s\nOutput Status:%s\n"
                          % (options, result.exit_status))
        else:
            if 'print-xml' in attach_option:
                if len(old_iflist) != len(new_iflist):
                    test.fail("Interface attached with"
                              " '--print-xml' option")
            else:
                check_output(new_iflist, vm, login_nic_index)

    finally:
        # Delete the new attached interface
        if new_nic_index > 0:
            vm.del_nic(new_nic_index)
        # Destroy vm after test.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vm_backup_xml.sync()
