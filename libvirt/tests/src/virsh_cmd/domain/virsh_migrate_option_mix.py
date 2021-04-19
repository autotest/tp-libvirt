import logging
import time
import re

from six import itervalues, string_types
from avocado.utils import process

from virttest import utils_selinux
from virttest import virsh
from virttest import utils_package
from virttest import migration
from virttest.utils_conn import TLSConnection
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import graphics
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError


def run(test, params, env):
    """
    Test virsh migrate command.
    """

    def cleanup_vm(vm, vm_name='', uri=''):
        """
        Clean up vm in the src or destination host environment
        when doing the uni-direction migration.
        """
        # Backup vm name and uri
        uri_bak = vm.connect_uri
        vm_name_bak = vm.name

        # Destroy and undefine vm
        vm.connect_uri = uri if uri else uri_bak
        vm.name = vm_name if vm_name else vm_name_bak
        logging.info("Cleaning up VM %s on %s", vm.name, vm.connect_uri)
        if vm.is_alive():
            vm.destroy()
        if vm.is_persistent():
            vm.undefine()

        # Restore vm connect_uri
        vm.connect_uri = uri_bak
        vm.name = vm_name_bak

    # Check whether there are unset parameters
    for v in list(itervalues(params)):
        if isinstance(v, string_types) and v.count("EXAMPLE"):
            test.cancel("Please set real value for %s" % v)

    # Params for virsh migrate options:
    live_migration = params.get("live_migration") == "yes"
    offline_migration = params.get("offline_migration") == "yes"
    persistent = params.get("persistent") == "yes"
    undefinesource = params.get("undefinesource") == "yes"
    p2p = params.get("p2p") == "yes"
    tunnelled = params.get("tunnelled") == "yes"
    postcopy = params.get("postcopy") == "yes"
    dname = params.get("dname")
    xml_option = params.get("xml_option") == "yes"
    persistent_xml_option = params.get("persistent_xml_option") == "yes"
    extra_options = params.get("virsh_migrate_extra", "")

    if live_migration and not extra_options.count("--live"):
        extra_options = "%s --live" % extra_options
    if offline_migration and not extra_options.count("--offline"):
        extra_options = "%s --offline" % extra_options
    if persistent and not extra_options.count("--persistent"):
        extra_options = "%s --persistent" % extra_options
    if undefinesource and not extra_options.count("--undefinesource"):
        extra_options = "%s --undefinesource" % extra_options
    if p2p and not extra_options.count("--p2p"):
        extra_options = "%s --p2p" % extra_options
    if tunnelled and not extra_options.count("--tunnelled"):
        extra_options = "%s --tunnelled" % extra_options
    if tunnelled and not extra_options.count("--p2p"):
        extra_options = "%s --p2p" % extra_options
    if postcopy and not extra_options.count("--postcopy"):
        extra_options = "%s --postcopy" % extra_options
    if dname and not extra_options.count("--dname"):
        extra_options = "%s --dname %s" % (extra_options, dname)
    if xml_option:
        pass
    if persistent_xml_option and not extra_options.count("--persistent"):
        extra_options = "%s --persistent" % extra_options
    if persistent_xml_option:
        pass

    # Set param migrate_options in case it is used somewhere:
    params.setdefault("migrate_options", extra_options)

    # Params for postcopy migration
    postcopy_timeout = int(params.get("postcopy_migration_timeout", "180"))

    # Params for migrate hosts:
    server_cn = params.get("server_cn")
    client_cn = params.get("client_cn")
    migrate_source_host = client_cn if client_cn else params.get("migrate_source_host")
    migrate_dest_host = server_cn if server_cn else params.get("migrate_dest_host")

    # Params for migrate uri
    transport = params.get("transport", "tls")
    transport_port = params.get("transport_port")
    uri_port = ":%s" % transport_port if transport_port else ''
    hypervisor_driver = params.get("hypervisor_driver", "qemu")
    hypervisor_mode = params.get("hypervisor_mode", 'system')
    if "virsh_migrate_desturi" not in list(params.keys()):
        params["virsh_migrate_desturi"] = "%s+%s://%s%s/%s" % (hypervisor_driver,
                                                               transport,
                                                               migrate_dest_host,
                                                               uri_port,
                                                               hypervisor_mode)
    if "virsh_migrate_srcuri" not in list(params.keys()):
        params["virsh_migrate_srcuri"] = "%s:///%s" % (hypervisor_driver,
                                                       hypervisor_mode)
    dest_uri = params.get("virsh_migrate_desturi")
    src_uri = params.get("virsh_migrate_srcuri")

    # Params for src vm cfg:
    src_vm_cfg = params.get("src_vm_cfg")
    src_vm_status = params.get("src_vm_status")
    with_graphic_passwd = params.get("with_graphic_passwd")
    graphic_passwd = params.get("graphic_passwd")

    # For test result check
    cancel_exception = False
    fail_exception = False
    exception = False
    result_check_pass = True

    # Objects(SSH, TLS and TCP, etc) to be cleaned up in finally
    objs_list = []

    # VM objects for migration test
    vms = []

    try:
        # Get a MigrationTest() Object
        logging.debug("Get a MigrationTest()  object")
        obj_migration = migration.MigrationTest()

        # Setup libvirtd remote connection TLS connection env
        if transport == "tls":
            tls_obj = TLSConnection(params)
            # Setup CA, server(on dest host) and client(on src host)
            tls_obj.conn_setup()
            # Add tls_obj to objs_list
            objs_list.append(tls_obj)

        # Enable libvirtd remote connection transport port
        if transport == 'tls':
            transport_port = '16514'
        elif transport == 'tcp':
            transport_port = '16509'
        obj_migration.migrate_pre_setup(dest_uri, params, ports=transport_port)

        # Back up vm name for recovery in finally
        vm_name_backup = params.get("migrate_main_vm")

        # Get a vm object for migration
        logging.debug("Get a vm object for migration")
        vm = env.get_vm(vm_name_backup)

        # Back up vm xml for recovery in finally
        logging.debug("Backup vm xml before migration")
        vm_xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        if not vm_xml_backup:
            test.error("Backing up xmlfile failed.")

        # Prepare shared disk in vm xml for live migration:
        # Change the source of the first disk of vm to shared disk
        if live_migration:
            logging.debug("Prepare shared disk in vm xml for live migration")
            storage_type = params.get("storage_type")
            if storage_type == 'nfs':
                logging.debug("Prepare nfs shared disk in vm xml")
                nfs_mount_dir = params.get("nfs_mount_dir")
                libvirt.update_vm_disk_source(vm.name, nfs_mount_dir)
                libvirt.update_vm_disk_driver_cache(vm.name, driver_cache="none")
            else:
                # TODO:Other storage types
                test.cancel("Other storage type is not supported for now")
                pass

        # Prepare graphic password in vm xml
        if with_graphic_passwd in ["yes", "no"]:
            logging.debug("Set VM graphic passwd in vm xml")
            # Get graphics list in vm xml
            vmxml_tmp = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
            graphics_list = vmxml_tmp.get_graphics_devices

            if not graphics_list:
                # Add spice graphic with passwd to vm xml
                logging.debug("Add spice graphic to vm xml")
                graphics.Graphics.add_graphic(vm.name, graphic_passwd, "spice")
            elif graphic_passwd:
                # Graphics already exist in vm xml and passwd is required
                # Add passwd to the first graphic device in vm xml
                logging.debug("Add graphic passwd to vm xml")
                vm_xml.VMXML.add_security_info(vmxml_tmp, graphic_passwd)
                vmxml_tmp.sync()
            else:
                # Graphics already exist in vm xml and non-passwd is required
                # Do nothing here as passwd has been removed by new_from_inactive_dumpxml()
                pass

        # Prepare for required src vm status.
        logging.debug("Turning %s into certain state.", vm.name)
        if src_vm_status == "running" and not vm.is_alive():
            vm.start()
        elif src_vm_status == "shut off" and not vm.is_dead():
            vm.destroy()

        # Prepare for required src vm persistency.
        logging.debug("Prepare for required src vm persistency")
        if src_vm_cfg == "persistent" and not vm.is_persistent():
            logging.debug("Make src vm persistent")
            vm_xml_backup.define()
        elif src_vm_cfg == "transient" and vm.is_persistent():
            logging.debug("Make src vm transient")
            vm.undefine()

        # Prepare for postcopy migration: install and run stress in VM
        if postcopy and src_vm_status == "running":
            logging.debug("Install and run stress in vm for postcopy migration")
            pkg_name = 'stress'

            # Get a vm session
            logging.debug("Get a vm session")
            vm_session = vm.wait_for_login()
            if not vm_session:
                test.error("Can't get a vm session successfully")

            # Install package stress if it is not installed in vm
            logging.debug("Check if stress tool is installed for postcopy migration")
            pkg_mgr = utils_package.package_manager(vm_session, pkg_name)
            if not pkg_mgr.is_installed(pkg_name):
                logging.debug("Stress tool will be installed")
                if not pkg_mgr.install():
                    test.error("Package '%s' installation fails" % pkg_name)

            # Run stress in vm
            logging.debug("Run stress in vm")
            stress_args = params.get("stress_args")
            vm_session.cmd('stress %s' % stress_args)

        # Prepare for --xml <updated_xml_file>.
        if xml_option:
            logging.debug("Preparing new xml file for --xml option.")

            # Get the vm xml
            vmxml_tmp = vm_xml.VMXML.new_from_dumpxml(vm.name,
                                                      "--security-info --migratable")

            # Update something in the xml file: e.g. title
            # Note: VM ABI shall not be broken when migrating with updated_xml
            updated_title = "VM Title in updated xml"
            vmxml_tmp.title = updated_title

            # Add --xml to migrate extra_options
            extra_options = ("%s --xml=%s" % (extra_options, vmxml_tmp.xml))

        # Prepare for --persistent-xml <updated_xml_file>.
        if persistent_xml_option:
            logging.debug("Preparing new xml file for --persistent-xml option.")

            # Get the vm xml
            vmxml_persist_tmp = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name,
                                                                       "--security-info")

            # Update something in the xml file: e.g. title
            # Note: VM ABI shall not be broken when migrating with updated_xml
            updated_persist_title = "VM Title in updated persist xml"
            vmxml_persist_tmp.title = updated_persist_title

            # Add --persistent-xml to migrate extra_options
            extra_options = ("%s --persistent-xml=%s" % (extra_options, vmxml_persist_tmp.xml))

        # Prepare host env: clean up vm on dest host
        logging.debug("Clean up vm on dest host before migration")
        if dname:
            cleanup_vm(vm, dname, dest_uri)
        cleanup_vm(vm, vm.name, dest_uri)

        # Prepare host env: set selinux state before migration
        logging.debug("Set selinux to enforcing before migration")
        utils_selinux.set_status(params.get("selinux_state", "enforcing"))

        # Check vm network connectivity by ping before migration
        logging.debug("Check vm network before migration")
        if src_vm_status == "running":
            obj_migration.ping_vm(vm, params)

        # Get VM uptime before migration
        if src_vm_status == "running":
            vm_uptime = vm.uptime()
            logging.info("Check VM uptime before migration: %s", vm_uptime)

        # Print vm active xml before migration
        process.system_output("virsh dumpxml %s --security-info" %
                              vm.name, shell=True)

        # Print vm inactive xml before migration
        process.system_output("virsh dumpxml %s --security-info --inactive" %
                              vm.name, shell=True)

        # Do uni-direction migration.
        # NOTE: vm.connect_uri will be set to dest_uri once migration is complete successfully
        logging.debug("Start to do migration test.")
        vms.append(vm)
        if postcopy:
            # Monitor the qemu monitor event of "postcopy-active" for postcopy migration
            logging.debug("Monitor the qemu monitor event for postcopy migration")
            virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
            cmd = "qemu-monitor-event --loop --domain %s --event MIGRATION" % vm.name
            virsh_session.sendline(cmd)

            # Do live migration and switch to postcopy by "virsh migrate-postcopy"
            logging.debug("Start to do postcopy migration")
            obj_migration.do_migration(vms, src_uri, dest_uri, "orderly",
                                       options="",
                                       thread_timeout=postcopy_timeout,
                                       ignore_status=True,
                                       func=virsh.migrate_postcopy,
                                       extra_opts=extra_options,
                                       shell=True)
            # Check migration result
            obj_migration.check_result(obj_migration.ret, params)

            # Check "postcopy-active" event after postcopy migration
            logging.debug("Check postcopy-active event after postcopy migration")
            virsh_session.send_ctrl("^C")
            events_output = virsh_session.get_stripped_output()
            logging.debug("events_output are %s", events_output)
            pattern = "postcopy-active"
            if not re.search(pattern, events_output):
                test.fail("Migration didn't switch to postcopy mode")
                virsh_session.close()
            virsh_session.close()

        else:
            logging.debug("Start to do precopy migration")
            obj_migration.do_migration(vms, src_uri, dest_uri, "orderly",
                                       options="",
                                       ignore_status=True,
                                       extra_opts=extra_options)

        """
        # Check src vm after migration
        # First, update vm name and connect_uri to src vm's
        """
        vm.name = vm_name_backup
        vm.connect_uri = src_uri
        logging.debug("Start to check %s state on src %s after migration.",
                      vm.name, src_uri)

        # Check src vm status after migration: existence, running, shutoff, etc
        logging.debug("Check vm status on source after migration")
        if offline_migration:
            if src_vm_status == "shut off" and undefinesource:
                if vm.exists():
                    result_check_pass = False
                    logging.error("Src vm should not exist after offline migration"
                                  " with --undefinesource")
                    logging.debug("Src vm state is %s" % vm.state())
            elif not libvirt.check_vm_state(vm.name, src_vm_status, uri=vm.connect_uri):
                result_check_pass = False
                logging.error("Src vm should be %s after offline migration" % src_vm_status)
                logging.debug("Src vm state is %s" % vm.state())

        if live_migration:
            if not undefinesource and src_vm_cfg == "persistent":
                if not libvirt.check_vm_state(vm.name, "shut off", uri=vm.connect_uri):
                    result_check_pass = False
                    logging.error("Src vm should be shutoff after live migration")
                    logging.debug("Src vm state is %s" % vm.state())
            elif vm.exists():
                result_check_pass = False
                logging.error("Src vm should not exist after live migration")
                logging.debug("Src vm state is %s" % vm.state())

        # Check src vm status after migration: persistency
        logging.debug("Check vm persistency on source after migration")
        if src_vm_cfg == "persistent" and not undefinesource:
            if not vm.is_persistent():
                # Src vm should be persistent after migration without --undefinesource
                result_check_pass = False
                logging.error("Src vm should be persistent after migration")
        elif vm.is_persistent():
            result_check_pass = False
            logging.error("Src vm should be not be persistent after migration")

        """
        # Check dst vm after migration
        # First, update vm name and connect_uri to dst vm's
        """
        vm.name = dname if dname else vm.name
        vm.connect_uri = dest_uri
        logging.debug("Start to check %s state on target %s after migration.",
                      vm.name, vm.connect_uri)

        # Check dst vm status after migration: running, shutoff, etc
        logging.debug("Check vm status on target after migration")
        if live_migration:
            if not libvirt.check_vm_state(vm.name, src_vm_status, uri=vm.connect_uri):
                result_check_pass = False
                logging.error("Dst vm should be %s after live migration", src_vm_status)
        elif vm.is_alive():
            result_check_pass = False
            logging.error("Dst vm should not be alive after offline migration")

        # Print vm active xml after migration
        process.system_output("virsh -c %s dumpxml %s --security-info" %
                              (vm.connect_uri, vm.name), shell=True)

        # Print vm inactive xml after migration
        process.system_output("virsh -c %s dumpxml %s --security-info --inactive" %
                              (vm.connect_uri, vm.name), shell=True)

        # Check dst vm xml after migration
        logging.debug("Check vm xml on target after migration")
        remote_virsh = virsh.Virsh(uri=vm.connect_uri)
        vmxml_active_tmp = vm_xml.VMXML.new_from_dumpxml(vm.name, "--security-info",
                                                         remote_virsh)
        vmxml_inactive_tmp = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name,
                                                                    "--security-info",
                                                                    remote_virsh)
        # Check dst vm xml after migration: --xml <updated_xml_file>
        if xml_option and not offline_migration:
            logging.debug("Check vm active xml for --xml")
            if not vmxml_active_tmp.title == updated_title:
                print("vmxml active tmp title is %s" % vmxml_active_tmp.title)
                result_check_pass = False
                logging.error("--xml doesn't take effect in migration")

        if xml_option and offline_migration:
            logging.debug("Check vm inactive xml for --xml")
            if not vmxml_active_tmp.title == updated_title:
                result_check_pass = False
                logging.error("--xml doesn't take effect in migration")

        # Check dst vm xml after migration: --persistent-xml <updated_xml_file>
        if persistent_xml_option:
            logging.debug("Check vm inactive xml for --persistent-xml")
            if not offline_migration and not vmxml_inactive_tmp.title == updated_persist_title:
                print("vmxml inactive tmp title is %s" % vmxml_inactive_tmp.title)
                result_check_pass = False
                logging.error("--persistent-xml doesn't take effect in live migration")
            elif offline_migration and vmxml_inactive_tmp.title == updated_persist_title:
                result_check_pass = False
                logging.error("--persistent-xml should not take effect in offline "
                              "migration")

        # Check dst vm xml after migration: graphic passwd
        if with_graphic_passwd == "yes":
            logging.debug("Check graphic passwd in vm xml after migration")
            graphic_active = vmxml_active_tmp.devices.by_device_tag('graphics')[0]
            graphic_inactive = vmxml_inactive_tmp.devices.by_device_tag('graphics')[0]
            try:
                logging.debug("Check graphic passwd in active vm xml")
                if graphic_active.passwd != graphic_passwd:
                    result_check_pass = False
                    logging.error("Graphic passwd in active xml of dst vm should be %s",
                                  graphic_passwd)

                logging.debug("Check graphic passwd in inactive vm xml")
                if graphic_inactive.passwd != graphic_passwd:
                    result_check_pass = False
                    logging.error("Graphic passwd in inactive xml of dst vm should be %s",
                                  graphic_passwd)
            except LibvirtXMLNotFoundError:
                result_check_pass = False
                logging.error("Graphic passwd lost in dst vm xml")

        # Check dst vm uptime, network, etc after live migration
        if live_migration:
            # Check dst VM uptime after migration
            # Note: migrated_vm_uptime should be greater than the vm_uptime got
            # before migration
            migrated_vm_uptime = vm.uptime(connect_uri=dest_uri)
            logging.info("Check VM uptime in destination after "
                         "migration: %s", migrated_vm_uptime)
            if not migrated_vm_uptime:
                result_check_pass = False
                logging.error("Failed to check vm uptime after migration")
            elif vm_uptime > migrated_vm_uptime:
                result_check_pass = False
                logging.error("VM went for a reboot while migrating to destination")

            # Check dst VM network connectivity after migration
            logging.debug("Check VM network connectivity after migrating")
            obj_migration.ping_vm(vm, params, uri=dest_uri)

            # Restore vm.connect_uri as it is set to src_uri in ping_vm()
            logging.debug("Restore vm.connect_uri as it is set to src_uri in ping_vm()")
            vm.connect_uri = dest_uri

        # Check dst vm status after migration: persistency
        logging.debug("Check vm persistency on target after migration")
        if persistent:
            if not vm.is_persistent():
                result_check_pass = False
                logging.error("Dst vm should be persistent after migration "
                              "with --persistent")
                time.sleep(10)
            # Destroy vm and check vm state should be shutoff. BZ#1076354
            vm.destroy()
            if not libvirt.check_vm_state(vm.name, "shut off", uri=vm.connect_uri):
                result_check_pass = False
                logging.error("Dst vm with name %s should exist and be shutoff", vm.name)
        elif vm.is_persistent():
            result_check_pass = False
            logging.error("Dst vm should not be persistent after migration "
                          "without --persistent")

    finally:
        logging.debug("Start to clean up env")
        # Clean up vm on dest and src host
        for vm in vms:
            cleanup_vm(vm, vm_name=dname, uri=dest_uri)
            cleanup_vm(vm, vm_name=vm_name_backup, uri=src_uri)

        # Recover source vm defination (just in case).
        logging.info("Recover vm defination on source")
        if vm_xml_backup:
            vm_xml_backup.define()

        # Clean up SSH, TCP, TLS test env
        if objs_list and len(objs_list) > 0:
            logging.debug("Clean up test env: SSH, TCP, TLS, etc")
            for obj in objs_list:
                obj.auto_recover = True
                obj.__del__()

        # Disable libvirtd remote connection transport port
        obj_migration.migrate_pre_setup(dest_uri, params, cleanup=True, ports=transport_port)

        # Check test result.
        if not result_check_pass:
            test.fail("Migration succeed, but some check points didn't pass."
                      "Please check the error log for details")
