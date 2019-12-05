import logging
import platform

from avocado.core import exceptions

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_net
from virttest import test_setup
from virttest.utils_test import libvirt
from virttest import libvirt_xml


def update_machinetype(test, vmxml, machine):
    """
    Method to update machine type of the VM

    :param test: KVM Test Object
    :param vmxml: guest VMXML object
    :param machine: machine type of the guest to be updated

    :raise: TestError if machine type is failed to be updated in guest xml
    """
    try:
        osxml = libvirt_xml.vm_xml.VMOSXML()
        osxml.type = vmxml.os.type
        osxml.arch = vmxml.os.arch
        osxml.machine = machine
        vmxml.os = osxml
        vmxml.sync()
    except Exception as info:
        test.error("Failed to update machine type: %s" % info)


def cleanup_vm(vm_list, vmxml_dict, migration_obj, src_uri, dest_uri):
    """
    Method to cleanup VMs in the environment

    :param vm_list: list of VM objects
    :param vmxml_dict: dict of vmxml object of VMs
    :param migration_obj: MigrationTest() object
    :param src_uri: virsh connect uri to source host
    :param dest_uri: virsh connect uri to target host
    """
    for vm in vm_list:
        migration_obj.cleanup_dest_vm(vm, src_uri, dest_uri)
        if vm.exists() and vm.is_persistent():
            vm.undefine()
        if vm.is_alive():
            vm.destroy()
    for key in vmxml_dict.keys():
        vmxml_dict[key].define()


def hotplug(vm, xml, uri=None, hotunplug=False):
    """
    Perform hotplug/hotunplug operation

    :param vm: VM Object
    :param xml: xml file used to hotplug/hotunplug
    :param uri: virsh connect uri
    :param hotunplug: True to hotunplug, False to hotplug

    :raise: checks the exit status and raise TestFail
    """
    func = virsh.attach_device
    if hotunplug:
        func = virsh.detach_device
    ret = func(vm.name, xml, flagstr="--live", uri=uri, debug=True)
    libvirt.check_exit_status(ret)


def check_interface(vm, nic_index, params, test, hotunplug=False, uri=None):
    """
    Checks the hotplugged/hotunplugged interface is visible to guest
    based upon the operation and performs ping test for hotplugged
    interface.

    :param vm: VM object
    :param nic_index: nic index of hotplugged/hotunplugged interface
    :param params: Test dict params
    :param test: Test Object
    :param hotunplug: set True to check for hotunplug and False for hotplug
    :param uri: virsh connect uri

    :raise: test.fail if interface is still available after hotunplug
    :raise: test.fail if interface is not available/pingable after hotplig
    """
    iface_name = ''
    remote_host = None
    if uri:
        vm.connect_uri = uri
        session = vm.wait_for_serial_login()
        remote_host = test_setup.remote_session(params)
    else:
        session = vm.wait_for_login()
    networks = params.get("vms_network")
    ping_test = int(params.get("ping_test_count", 5))
    mac = networks[vm.name][nic_index]['mac']
    try:
        iface_name = utils_net.get_linux_ifname(session, mac_address=mac)
        networks[vm.name][nic_index]['iface'] = iface_name
    except exceptions.TestError:
        if hotunplug:
            logging.debug("Expected the interface with mac %s on %s not to be "
                          "found, as it is hotunplugged", mac, vm.name)
        else:
            session.close()
            test.fail("Failed to retrieve hotplugged interface"
                      " with mac: %s in %s" % (mac, vm.name))
    if iface_name and hotunplug:
        test.fail("Interface: %s exists in %s even after hotunplug operation" %
                  (iface_name, vm.name))
    if not hotunplug:
        if session.cmd_status("dhclient %s" % iface_name) != 0:
            test.error("Failed to perform dhclient for %s in %s" %
                       (iface_name, vm.name))
        ip_address = utils_net.parse_arp()[mac]
        networks[vm.name][nic_index]['ip_address'] = ip_address
        s_ping, o_ping = utils_net.ping(ip_address,
                                        count=ping_test, output_func=logging.debug,
                                        session=remote_host)
        if s_ping != 0:
            test.fail("Interface: %s with IP: %s on %s failed to ping after network"
                      "Hotplug operation" % (iface_name, ip_address, vm.name))
    params['vms_network'] = networks
    session.close()
    if uri:
        vm.connect_uri = None


def run(test, params, env):
    """
    Test KVM migration scenarios
    """
    migrate_options = params.get("migrate_options", "")
    migrate_postcopy = params.get("migrate_postcopy", "")
    migrate_dest_ip = params.get("migrate_dest_host")
    nfs_mount_path = params.get("nfs_mount_dir")
    migrate_start_state = params.get("migrate_start_state", "paused")
    machine_types = params.get("migrate_all_machine_types", "no") == "yes"
    migrate_back = params.get("migrate_back", "yes") == "yes"
    migrate_thread_timeout = int(params.get("migrate_thread_timeout", 60))
    postcopy_func = None
    if migrate_postcopy:
        postcopy_func = virsh.migrate_postcopy
    migrate_type = params.get("migrate_type", "orderly")
    vm_state = params.get("migrate_vm_state", "running")
    ping_count = int(params.get("ping_count", 10))
    thread_timeout = int(params.get("thread_timeout", 3600))
    hotplug_operation = params.get("hotplug_type", None)
    hotplug_times = int(params.get("hotplug_times", 1))
    hotplug_before_migration = "yes" == params.get("hotplug_before_migration",
                                                   "no")
    hotplug_after_migration = "yes" == params.get("hotplug_after_migration",
                                                  "no")
    hotunplug_before_migration = "yes" == params.get("hotunplug_before_migration",
                                                     "no")
    hotunplug_after_migration = "yes" == params.get("hotunplug_after_migration",
                                                    "no")
    vm_list = env.get_all_vms()

    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = nfs_mount_path

    src_uri = "qemu:///system"
    dest_uri = libvirt_vm.complete_uri(params["server_ip"])

    vmxml_dict = {}
    vmxml_machine = {}

    machine_list = params.get("machine_type").split()
    virt_type = params.get("hvm_or_pv", "hvm")
    arch = params.get("vm_arch_name", platform.machine())

    # Get all supported machine types in source
    if machine_types:
        machine_list = libvirt.get_machine_types(arch, virt_type)
        if not machine_list:
            test.cancel("Libvirt doesn't support %s virtualization on "
                        "arch %s in source host" % (virt_type, arch))
        logging.debug("Supported machine types in source: %s",
                      ", ".join(map(str, machine_list)))

        # Get all supported machine types in target host
        virsh_remote = virsh.Virsh(uri=dest_uri)
        remote_machine_list = libvirt.get_machine_types(arch, virt_type,
                                                        virsh_instance=virsh_remote)
        if not remote_machine_list:
            test.cancel("Libvirt doesn't support %s virtualization on "
                        "arch %s in target host" % (virt_type, arch))
        logging.debug("Supported machine types in target: %s",
                      ", ".join(map(str, remote_machine_list)))

        # use machine types supported by both source and target host
        machine_list = list(set(machine_list).intersection(remote_machine_list))
        if not machine_list:
            test.cancel("Migration not supported as source machine type and target "
                        "machine type doesn't match")
        logging.debug("Supported machine types that are common in  source and "
                      "target are: %s", ", ".join(map(str, machine_list)))

    if hotplug_operation and hotplug_operation == "network":
        networks = params.get("vms_network", {})

    migrate_setup = libvirt.MigrationTest()
    # Perform migration with each machine type
    try:
        for vm in vm_list:
            vmxml_dict[vm.name] = libvirt_xml.vm_xml.VMXML.new_from_dumpxml(vm.name)
            params["source_dist_img"] = "%s-nfs-img" % vm.name
            if vm.is_alive():
                vm.destroy()
            libvirt.set_vm_disk(vm, params)

            # Hotplug/Hotunplug PCI device before migration
            if hotplug_operation and hotplug_operation == "network":
                for nic_index in range(hotplug_times):
                    mac, iface = libvirt.create_network_vmxml(params)
                    networks[vm.name] = {nic_index: {'xml': iface, 'mac': mac}}
                    params["vms_network"] = networks
            if hotplug_before_migration:
                for nic_index in range(hotplug_times):
                    hotplug(vm, networks[vm.name][nic_index]['xml'])
                    check_interface(vm, nic_index, params, test)
                if hotunplug_before_migration:
                    for nic_index in range(hotplug_times):
                        hotplug(vm, networks[vm.name][nic_index]['xml'],
                                hotunplug=True)
                        check_interface(vm, nic_index, params, test,
                                        hotunplug=True)
        info_list = []
        for machine in machine_list:
            uptime = {}
            for vm in vm_list:
                vmxml_machine[vm.name] = libvirt_xml.vm_xml.VMXML.new_from_dumpxml(vm.name)
                # update machine type
                update_machinetype(test, vmxml_machine[vm.name], machine)
                if vm.is_alive():
                    vm.destroy()
                if "offline" not in migrate_options:
                    vm.start()
                    vm.wait_for_login()
                    uptime[vm.name] = vm.uptime()
                    logging.info("uptime of VM %s: %s", vm.name, uptime[vm.name])
                    migrate_setup.ping_vm(vm, params, ping_count=ping_count)
            try:
                logging.debug("Migrating source to target from %s to %s "
                              "with machine type: %s", src_uri, dest_uri,
                              machine)
                # Initialize it to avoid current iteration fail to not affect
                # next iteration
                migrate_setup.RET_MIGRATION = True
                migrate_setup.do_migration(vm_list, src_uri, dest_uri,
                                           migrate_type, migrate_options,
                                           func=postcopy_func,
                                           migrate_start_state=migrate_start_state,
                                           thread_timeout=thread_timeout)
            except Exception as info:
                info_list.append(info)
                logging.error("Failed to migrate VM from source to target "
                              "%s to %s with machine type: %s",
                              dest_uri, src_uri, machine)

            if migrate_setup.RET_MIGRATION:
                uptime = migrate_setup.post_migration_check(vm_list, params,
                                                            uptime,
                                                            uri=dest_uri)
                if migrate_back:
                    migrate_setup.migrate_pre_setup(src_uri, params)
                    logging.debug("Migrating back to source from %s to %s "
                                  "with machine type: %s", dest_uri, src_uri,
                                  machine)
                    try:
                        migrate_setup.do_migration(vm_list, dest_uri, src_uri,
                                                   migrate_type,
                                                   options=migrate_options,
                                                   func=postcopy_func,
                                                   migrate_start_state=migrate_start_state,
                                                   thread_timeout=thread_timeout,
                                                   virsh_uri=dest_uri)
                    except Exception as info:
                        logging.error("Failed to migrate back to source from "
                                      "%s to %s with machine type: %s",
                                      dest_uri, src_uri, machine)
                        info_list.append(info)
                        cleanup_vm(vm_list, vmxml_machine, migrate_setup,
                                   src_uri, dest_uri)
                        continue
                    uptime = migrate_setup.post_migration_check(vm_list, params,
                                                                uptime)
                    migrate_setup.migrate_pre_setup(src_uri, params, cleanup=True)

        # Hotplug/Hotunplug PCI device after migration
        for vm in vm_list:
            if hotplug_after_migration:
                for nic_index in range(hotplug_times):
                    hotplug(vm, networks[vm.name][nic_index]['xml'], uri=dest_uri)
                    check_interface(vm, nic_index, params, test, uri=dest_uri)
                if hotunplug_after_migration:
                    for nic_index in range(hotplug_times):
                        hotplug(vm, networks[vm.name][nic_index]['xml'],
                                uri=dest_uri, hotunplug=True)
                        check_interface(vm, nic_index, params, test,
                                        uri=dest_uri, hotunplug=True)
        if info_list:
            test.fail(" |".join(map(str, info_list)))
    finally:
        logging.debug("cleanup the migration setup in source/destination")
        cleanup_vm(vm_list, vmxml_dict, migrate_setup, src_uri, dest_uri)
        for source_file in params.get("source_file_list", []):
            libvirt.delete_local_disk("file", path=source_file)
