import logging as log
import re

from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps

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
    # VCPU-specific parameters
    vcpu_num = params.get("vcpu_num")
    xml_check_after_mig = params.get("guest_xml_check_after_mig", "")
    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")

    # Check for required VCPU configuration
    if not vcpu_num:
        test.fail("Missing required configuration: vcpu_num must be specified")

    # Get VM object
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)

    # Initialize migration base helper
    migration_base = base_steps.MigrationBase(test, vm, params)

    try:
        # Configure VM with specified number of VCPUs
        logging.info("Setting VM %s to use %s VCPUs", vm_name, vcpu_num)
        vm_xml.VMXML.set_vm_vcpus(vm_name, int(vcpu_num))

        # Setup and start VM
        migration_base.setup_default()

        # Perform migration from source to destination
        logging.info("Migrating VM %s from source to destination", vm_name)
        migration_base.run_migration()

        # Verify VCPU count on destination
        verify_vcpu_count_on_destination(test, params, vm_name, vcpu_num, xml_check_after_mig)

        # Verify migration success
        migration_base.verify_default()

        # Migrate back to source if requested
        if migrate_vm_back:
            logging.info("Migrating VM %s back to source", vm_name)
            migration_base.run_migration_back()

            # Verify VCPU count after back-migration
            verify_vcpu_count_on_source(test, vm_name, vcpu_num, xml_check_after_mig)

        logging.info("Test completed successfully")

    finally:
        # Cleanup: restore original VM configuration
        logging.info("Restoring original VM configuration")
        migration_base.cleanup_default()


def verify_vcpu_count_on_destination(test, params, vm_name, vcpu_num, xml_check_after_mig):
    """
    Verify VCPU count on destination host after migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param vm_name: Name of the VM
    :param vcpu_num: Expected number of VCPUs
    :param xml_check_after_mig: XML prefix for verification
    """
    server_ip = params.get("remote_ip", params.get("server_ip"))
    server_user = params.get("remote_user", params.get("server_user", "root"))
    server_pwd = params.get("remote_pwd", params.get("server_pwd"))

    remote_virsh_dargs = {
        'remote_ip': server_ip,
        'remote_user': server_user,
        'remote_pwd': server_pwd,
        'unprivileged_user': None,
        'ssh_remote_auth': True
    }

    remote_virsh_session = None
    try:
        remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
        target_guest_dumpxml = remote_virsh_session.dumpxml(
            vm_name, debug=True, ignore_status=True).stdout_text.strip()

        xml_check = "%s%s</vcpu>" % (xml_check_after_mig, vcpu_num)

        logging.info("Checking for '%s' in target guest XML", xml_check)
        if not re.search(xml_check, target_guest_dumpxml):
            test.fail("Fail to search '%s' in target guest XML:\n%s"
                      % (xml_check, target_guest_dumpxml))

        logging.info("VCPU count verified successfully on destination")
    finally:
        if remote_virsh_session:
            remote_virsh_session.close_session()


def verify_vcpu_count_on_source(test, vm_name, vcpu_num, xml_check_after_mig):
    """
    Verify VCPU count on source host after back-migration.

    :param test: test object
    :param vm_name: Name of the VM
    :param vcpu_num: Expected number of VCPUs
    :param xml_check_after_mig: XML prefix for verification
    """
    source_guest_dumpxml = virsh.dumpxml(vm_name, debug=True).stdout_text.strip()

    xml_check = "%s%s</vcpu>" % (xml_check_after_mig, vcpu_num)
    logging.info("Checking for '%s' in source guest XML after back-migration", xml_check)

    if not re.search(xml_check, source_guest_dumpxml):
        test.fail("Fail to search '%s' in source guest XML after back-migration:\n%s"
                  % (xml_check, source_guest_dumpxml))

    logging.info("VCPU count verified successfully after back-migration")
