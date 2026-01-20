import logging as log
import re

from virttest import virsh
from virttest import remote
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test virsh migrate command with different VCPU configurations.

    This test verifies that migration works correctly with VMs configured
    with different numbers of VCPUs (1, 4, 8, 16).

    Steps:
    1. Set up source and destination hosts for migration
    2. Configure VM with specified number of VCPUs
    3. Perform live migration from source to destination
    4. Verify VCPU count in destination VM XML
    5. Migrate back to source (if migrate_vm_back=yes)
    6. Verify VCPU count after back-migration

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    # Get standard migration parameters
    vm_name = params.get("migrate_main_vm", params.get("main_vm"))
    server_ip = params.get("remote_ip")
    server_user = params.get("remote_user", "root")
    server_pwd = params.get("remote_pwd")
    virsh_options = params.get("virsh_options", "")
    options = params.get("virsh_migrate_options", "--live --verbose")
    extra = params.get("virsh_migrate_extra", "")
    dest_uri = params.get("virsh_migrate_desturi")

    # VCPU-specific parameters
    vcpu_num = params.get("vcpu_num")
    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")
    xml_check_after_mig = params.get("guest_xml_check_after_mig", "")

    # Get VM object
    vm = env.get_vm(vm_name)

    # Remote virsh session parameters
    remote_virsh_dargs = {
        'remote_ip': server_ip,
        'remote_user': server_user,
        'remote_pwd': server_pwd,
        'unprivileged_user': None,
        'ssh_remote_auth': True
    }

    # Backup original VM XML
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Configure VM with specified number of VCPUs
        if vcpu_num:
            logging.info("Setting VM %s to use %s VCPUs", vm_name, vcpu_num)
            vm_xml.VMXML.set_vm_vcpus(vm_name, int(vcpu_num))

        # Make sure VM is running
        if not vm.is_alive():
            vm.start()

        # Wait for VM to be ready
        vm.wait_for_login()

        logging.info("Migrating VM %s from source to destination", vm_name)
        logging.debug("Migration command: virsh %s migrate %s %s %s %s",
                      virsh_options, options, vm_name, dest_uri, extra)

        # Perform migration
        result = virsh.migrate(vm_name, dest_uri, options=options,
                               extra=extra, virsh_opt=virsh_options,
                               debug=True, ignore_status=True)

        # Check migration result
        libvirt.check_exit_status(result)

        logging.info("Migration completed successfully")

        # Verify VCPU count on destination
        remote_virsh_session = None
        try:
            remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
            target_guest_dumpxml = remote_virsh_session.dumpxml(
                vm_name, debug=True, ignore_status=True).stdout_text.strip()

            if vcpu_num:
                check_str = vcpu_num
                xml_check = "%s%s</vcpu>" % (xml_check_after_mig, check_str)

                logging.info("Checking for '%s' in target guest XML", xml_check)
                if not re.search(xml_check, target_guest_dumpxml):
                    test.fail("Fail to search '%s' in target guest XML:\n%s"
                              % (xml_check, target_guest_dumpxml))

                logging.info("VCPU count verified successfully on destination")
        finally:
            if remote_virsh_session:
                remote_virsh_session.close_session()

        # Migrate back to source if requested
        if migrate_vm_back:
            logging.info("Migrating VM %s back to source", vm_name)

            # Get source URI
            src_uri = params.get("virsh_migrate_connect_uri", "qemu:///system")

            # Prepare remote command to migrate back
            cmd = "virsh %s migrate %s %s %s %s" % (
                virsh_options, options, vm_name, src_uri, extra)

            logging.debug("Back-migration command: %s", cmd)

            # Execute migration back from remote host
            runner = remote.RemoteRunner(host=server_ip,
                                         username=server_user,
                                         password=server_pwd)
            ret = runner.run(cmd, ignore_status=False)

            if ret.exit_status != 0:
                test.fail("Failed to migrate VM back to source: %s" % ret.stderr)

            logging.info("Back-migration completed successfully")

            # Verify VCPU count after back-migration
            source_guest_dumpxml = virsh.dumpxml(vm_name, debug=True).stdout_text.strip()

            if vcpu_num:
                xml_check = "%s%s</vcpu>" % (xml_check_after_mig, vcpu_num)
                logging.info("Checking for '%s' in source guest XML after back-migration",
                             xml_check)
                if not re.search(xml_check, source_guest_dumpxml):
                    test.fail("Fail to search '%s' in source guest XML after back-migration:\n%s"
                              % (xml_check, source_guest_dumpxml))

                logging.info("VCPU count verified successfully after back-migration")

        logging.info("Test completed successfully")

    finally:
        # Cleanup: restore original VM configuration
        logging.info("Restoring original VM configuration")

        # If VM is on remote, migrate it back first
        if migrate_vm_back or libvirt.check_vm_state(vm_name, "running", uri=dest_uri):
            try:
                runner = remote.RemoteRunner(host=server_ip,
                                             username=server_user,
                                             password=server_pwd)
                src_uri = params.get("virsh_migrate_connect_uri", "qemu:///system")
                cmd = "virsh migrate %s %s --live" % (vm_name, src_uri)
                runner.run(cmd, ignore_status=True)
            except Exception as e:
                logging.warning("Failed to migrate VM back during cleanup: %s", e)

        # Restore original XML
        if vm.is_alive():
            vm.destroy(gracefully=False)

        vmxml_backup.sync()
        logging.info("Original VM configuration restored")
