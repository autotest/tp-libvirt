import logging

from virttest import libvirt_vm
from virttest import utils_test
from virttest import utils_misc
from virttest import utils_package
from virttest import libvirt_xml
from virttest import virsh
from virttest import test_setup
from virttest.staging import utils_memory


def do_stress_migration(vms, srcuri, desturi, migration_type, test, params,
                        thread_timeout=60):
    """
    Migrate vms with stress.

    :param vms: migrated vms.
    :param srcuri: connect uri for source machine
    :param desturi: connect uri for destination machine
    :param migration_type: type of migration to be performed
    :param params: Test dict params
    :param thread_timeout: default timeout for migration thread

    :raise: test.fail if migration fails
    """
    migrate_setup = utils_test.libvirt.MigrationTest()
    options = params.get("migrate_options")
    ping_count = int(params.get("ping_count", 10))
    migrate_times = 1
    migrate_back = params.get("virsh_migrate_back", "no") == "yes"
    if migrate_back:
        migrate_times = int(params.get("virsh_migrate_there_and_back", 1))
    uptime = {}

    for vm in vms:
        uptime[vm.name] = vm.uptime()
        logging.info("uptime of VM %s: %s", vm.name, uptime[vm.name])
        migrate_setup.ping_vm(vm, params, ping_count=ping_count)
    logging.debug("Starting migration...")
    migrate_options = ("%s --timeout %s"
                       % (options, params.get("virsh_migrate_timeout", 60)))
    for each_time in range(migrate_times):
        logging.debug("Migrating vms from %s to %s for %s time", srcuri,
                      desturi, each_time + 1)
        try:
            migrate_setup.do_migration(vms, srcuri, desturi, migration_type,
                                       options=migrate_options,
                                       thread_timeout=thread_timeout)
        except Exception, info:
            test.fail(info)

        uptime = migrate_setup.post_migration_check(vms, params, uptime,
                                                    uri=desturi)
        if migrate_back and "cross" not in migration_type:
            migrate_setup.migrate_pre_setup(srcuri, params)
            logging.debug("Migrating back to source from %s to %s for %s time",
                          desturi, srcuri, each_time + 1)
            try:
                migrate_setup.do_migration(vms, desturi, srcuri, migration_type,
                                           options=migrate_options,
                                           thread_timeout=thread_timeout,
                                           virsh_uri=desturi)
            except Exception, info:
                test.fail(info)
            uptime = migrate_setup.post_migration_check(vms, params, uptime)
            migrate_setup.migrate_pre_setup(srcuri, params, cleanup=True)


def macvtap_plug_unplug(test, vms, macvtap_xml, hotplug=False, unplug=False):
    """
    Method to perform macvtap hotplug/hotunplug and coldplug/coldunplug

    :param vms: VM objects
    :param macvtap_xml: macvtap xml dict of VMs
    :param hotplug: True to perform hotplug, False to perform coldplug
    :param unplug: True and hotplug as True to perform hotunplug
                   True and hotplug as False to perform coldunplug
    :raise: TestFail if the operation fails
    """
    for vm in vms:
        xml_list = macvtap_xml[vm.name]
        flag = "--live"
        func = virsh.attach_device
        # perform coldplug / coldunplug
        if not hotplug:
            flag = "--config"
            if vm.is_alive:
                vm.destroy()
        if unplug:
            func = virsh.detach_device
        # perform hotplug / hotunplug
        if hotplug:
            if not vm.is_alive():
                vm.start()
                vm.wait_for_login()
        for xml in xml_list:
            ret = func(vm.name, xml.xml, flagstr=flag, debug=True)
            utils_test.libvirt.check_result(ret)

        # start the VM for coldplug/coldunplug scenario
        if not hotplug:
            if not vm.is_alive():
                vm.start()
                vm.wait_for_login()


def run(test, params, env):
    """
    Test migration under stress.
    """
    vm_names = params.get("vms").split()
    if len(vm_names) < 2:
        test.cancel("Provide enough vms for migration")

    src_uri = "qemu:///system"
    dest_uri = libvirt_vm.complete_uri(params.get("migrate_dest_host",
                                                  "EXAMPLE"))
    if dest_uri.count('///') or dest_uri.count('EXAMPLE'):
        test.cancel("The dest_uri '%s' is invalid" % dest_uri)

    # Migrated vms' instance
    vms = env.get_all_vms()
    params["load_vms"] = list(vms)

    memory = int(params.get("mem")) * 1024
    stress_tool = params.get("stress_tool", "")
    remote_stress = params.get("migration_stress_remote", "no") == "yes"
    host_stress = params.get("migration_stress_host", "no") == "yes"
    vms_stress = params.get("migration_stress_vms", "no") == "yes"
    vm_bytes = params.get("stress_vm_bytes", "128M")
    stress_args = params.get("%s_args" % stress_tool)
    migration_type = params.get("migration_type")
    start_migration_vms = params.get("start_migration_vms", "yes") == "yes"
    thread_timeout = int(params.get("thread_timeout", 120))
    host_pf_filter = params.get("host_pf_filter", "Mellanox Technologies")
    host_vf_filter = params.get("host_vf_filter", "Mellanox Technologies")
    # No of macvtap interfaces required per VM
    vm_ifaces = int(params.get("vm_macvtap_interfaces_per_pf", 1))
    macvtap_migration = params.get("plug_operation", "")
    plug_before = params.get("virsh_plug_before", "no") == "yes"
    unplug_before = params.get("virsh_unplug_before", "no") == "yes"
    plug_after = params.get("virsh_plug_after", "no") == "yes"
    unplug_after = params.get("virsh_unplug_after", "no") == "yes"

    ubuntu_dep = ['build-essential', 'git']
    hstress = rstress = None
    source_assignable = target_assignable = None
    vstress = {}
    vmxml_dict = {}

    # backup vm xml
    for vm in vms:
        vmxml_dict[vm.name] = libvirt_xml.vm_xml.VMXML.new_from_dumpxml(vm.name)
        params["source_dist_img"] = "%s-nfs-img" % vm.name

    # Set vm_bytes for start_cmd
    mem_total = utils_memory.memtotal()
    vm_reserved = len(vms) * memory
    if vm_bytes == "half":
        vm_bytes = (mem_total - vm_reserved) / 2
    elif vm_bytes == "shortage":
        vm_bytes = mem_total - vm_reserved + 524288
    if "vm-bytes" in stress_args:
        params["%s_args" % stress_tool] = stress_args % vm_bytes

    target_session = test_setup.remote_session(params)
    cmd = "ip link show | grep \"^[0-9]:\" | awk \"{print $2}\""
    remote_ifaces = target_session.cmd_output(cmd).split()

    if macvtap_migration:
        iface_dict = {}
        iface_list = []
        macvtap_xml = {}
        source_assignable = test_setup.PciAssignable(pf_filter_re=host_pf_filter,
                                                     vf_filter_re=host_vf_filter)
        target_assignable = test_setup.PciAssignable(pf_filter_re=host_pf_filter,
                                                     vf_filter_re=host_vf_filter,
                                                     session=target_session)
        source_pfs = map(str, source_assignable.get_pf_ids())
        target_pfs = map(str, target_assignable.get_pf_ids())
        logging.debug("source PFs are: %s", ' '.join(map(str, source_pfs)))
        logging.debug("target PFs are: %s", ' '.join(map(str, target_pfs)))
        if source_pfs.sort() != target_pfs.sort():
            test.cancel("For migration to work PFs should be in same slot "
                        "in source and target so that VFs created out of "
                        "it will be same")

        # create VFs in source and target based on no of VMs and no of
        # interfaces required for each VM
        nr_vfs = len(vms) * vm_ifaces
        for pf in source_pfs:
            # initialize it to 0
            if source_assignable.get_vfs_count() != 0:
                source_assignable.set_vf(pf)
            if target_assignable.get_vfs_count() != 0:
                target_assignable.set_vf(pf)
            # set actual vfs
            source_assignable.set_vf(pf, nr_vfs)
            target_assignable.set_vf(pf, nr_vfs)
        pf_vf_info = source_assignable.get_pf_vf_info()
        # map vf from each pf to every VM
        for vm_index in range(len(vms)):
            iface_list = []
            for pf in source_pfs:
                for each in pf_vf_info:
                    if pf == str(each['pf_id']):
                        vf_pci = str(each['vf_ids'][vm_index]['vf_id'])
                        iface_list.append(utils_misc.get_interface_from_pci_id(vf_pci))
                        iface_dict[vms[vm_index].name] = iface_list
        # create xml for vfs associated with VM
        for vm in vms:
            macvtap_xml_list = []
            for iface in iface_dict[vm.name]:
                xml = utils_test.libvirt.create_macvtap_vmxml(iface, params)
                macvtap_xml_list.append(xml)
                macvtap_xml[vm.name] = macvtap_xml_list

    # Ensure stress tool is available in host
    if host_stress:
        # remove package manager installed tool to avoid conflict
        if not utils_package.package_remove(stress_tool):
            logging.error("Existing %s is not removed")
        if "stress-ng" in stress_tool and 'Ubuntu' in utils_misc.get_distro():
            params['stress-ng_dependency_packages_list'] = ubuntu_dep
        try:
            hstress = utils_test.HostStress(stress_tool, params)
            hstress.load_stress_tool()
        except utils_test.StressError, info:
            test.error(info)

    if remote_stress:
        try:
            remote_session = test_setup.remote_session(params)
            # remove package manager installed tool to avoid conflict
            if not utils_package.package_remove(stress_tool, session=remote_session):
                logging.error("Existing %s is not removed")
            if("stess-ng" in stress_tool and
               'Ubuntu' in utils_misc.get_distro(session=remote_session)):
                params['stress-ng_dependency_packages_list'] = ubuntu_dep

            rstress = utils_test.HostStress(stress_tool, params, remote_server=True)
            rstress.load_stress_tool()
            remote_session.close()
        except utils_test.StressError, info:
            remote_session.close()
            test.error(info)

    try:
        if start_migration_vms:
            for vm in vms:
                if not vm.is_alive():
                    vm.start()
                session = vm.wait_for_login()
                # configure stress in VM
                if vms_stress:
                    try:
                        vstress[vm.name] = utils_test.VMStress(vm, stress_tool, params)
                        vstress[vm.name].load_stress_tool()
                    except utils_test.StressError, info:
                        session.close()
                        test.error(info)
                session.close()

        if macvtap_migration:
            # perform hotplug/hotunplug before migration
            if macvtap_migration == "hotplug":
                if plug_before:
                    macvtap_plug_unplug(test, vms, macvtap_xml, hotplug=True)
                if unplug_before:
                    macvtap_plug_unplug(test, vms, macvtap_xml, hotplug=True,
                                        unplug=True)
            # perform coldplug/coldunplug before migration
            elif macvtap_migration == "coldplug":
                if plug_before:
                    macvtap_plug_unplug(test, vms, macvtap_xml)
                if unplug_before:
                    macvtap_plug_unplug(test, vms, macvtap_xml, unplug=True)

        do_stress_migration(vms, src_uri, dest_uri, migration_type, test,
                            params, thread_timeout)

        if macvtap_migration:
            # perform hotplug/coldplug after migration
            if macvtap_migration == "hotplug":
                if plug_after:
                    macvtap_plug_unplug(test, vms, macvtap_xml, hotplug=True)
                if unplug_after:
                    macvtap_plug_unplug(test, vms, macvtap_xml, hotplug=True,
                                        unplug=True)
            # perform coldplug/coldunplug after migration
            elif macvtap_migration == "coldplug":
                if plug_after:
                    macvtap_plug_unplug(test, vms, macvtap_xml)
                if unplug_after:
                    macvtap_plug_unplug(test, vms, macvtap_xml, unplug=True)

    finally:
        logging.debug("Cleanup vms...")
        for vm in vms:
            utils_test.libvirt.MigrationTest().cleanup_dest_vm(vm, None,
                                                               dest_uri)
            # bring down the VMs
            if vm.is_alive():
                vm.destroy()

        if rstress:
            rstress.unload_stress()

        if hstress:
            hstress.unload_stress()

        for source_file in params.get("source_file_list", []):
            utils_test.libvirt.delete_local_disk("file", path=source_file)

        # define the backup xml
        if vmxml_dict:
            for key in vmxml_dict.keys():
                vmxml_dict[key].define()

        # clean up VFs
        if source_assignable:
            if source_assignable.get_vfs_count() != 0:
                source_assignable.set_vf(pf)
        if target_assignable:
            if target_assignable.get_vfs_count() != 0:
                target_assignable.set_vf(pf)
