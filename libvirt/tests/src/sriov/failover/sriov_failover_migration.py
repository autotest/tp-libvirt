import os
import re
import shutil

from provider.migration import base_steps
from provider.sriov import check_points
from provider.sriov import sriov_base

from virttest import data_dir
from virttest import remote
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Migrate vm with failover settings.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def gen_hostdev_migratable(hostdev_dev):
        """
        Generate a migratable xml for the VM with hostdev device

        :param hostdev_dev: hostdev device object
        """
        remote_pwd = params.get("migrate_dest_pwd")
        remote_ip = params.get("migrate_dest_host")
        remote_user = params.get("remote_user", "root")

        guest_xml = vm_xml.VMXML.new_from_dumpxml(vm.name,
                                                  options="--migratable")
        remote.scp_to_remote(remote_ip, '22', remote_user, remote_pwd,
                             guest_xml.xml, guest_xml.xml, limit="",
                             log_filename=None, timeout=600,
                             interface=None)
        guest_xml.remove_all_device_by_type("hostdev")
        guest_xml.add_device(hostdev_dev)
        guest_xml.xmltreefile.write()
        xmlfile = os.path.join(data_dir.get_tmp_dir(), "xml_file")
        shutil.copyfile(guest_xml.xml, xmlfile)
        params["virsh_migrate_extra"] += "--xml %s" % xmlfile

    def setup_test():
        """
        Test setup
        """
        iface_dict = sriov_src_obj.parse_iface_dict()
        sriov_dest_obj.setup_failover_test(**test_dict)
        sriov_src_obj.setup_failover_test(**test_dict)
        iface_dev = sriov_src_obj.create_iface_dev(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)
        libvirt.set_vm_disk(vm, params)
        verify_network()

        if cmd_during_mig:
            vm_session = vm.wait_for_serial_login(timeout=240)
            vm_session.cmd(cmd_during_mig)
            vm_session.close()

        if dev_type == "hostdev_device":
            iface_dict = sriov_dest_obj.parse_iface_dict()
            hostdev_dev = sriov_dest_obj.create_iface_dev(dev_type, iface_dict)
            gen_hostdev_migratable(hostdev_dev)

    def verify_network():
        """
        Verify network function
        """
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=240)
        check_points.check_vm_iface_num(vm_session, expr_iface_no,
                                        timeout=40, first=15)

        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)

    br_name = params.get("br_name")
    dev_type = params.get("dev_type", "")
    cmd_during_mig = params.get("cmd_during_mig")
    expr_iface_no = int(params.get("expr_iface_no", '3'))
    vm_tmp_file = params.get("vm_tmp_file")
    status_error = "yes" == params.get("status_error", "no")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                           server_user, server_pwd,
                                           r"[\#\$]\s*$")
    migration_obj = base_steps.MigrationBase(test, vm, params)
    sriov_src_obj = sriov_base.SRIOVTest(vm, test, params)
    sriov_dest_obj = sriov_base.SRIOVTest(
        vm, test, params, session=remote_session)
    test_dict = sriov_src_obj.parse_iommu_test_params()

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
        verify_network()
        if not status_error:
            migration_obj.run_migration_back()
            verify_network()
            if vm_tmp_file:
                vm_session = vm.wait_for_serial_login(timeout=240)
                cmd_result = vm_session.cmd_output('cat %s' % vm_tmp_file)
                if re.findall('Destination Host Unreachable', cmd_result, re.M):
                    err_msg = ("The network does not work well during the "
                               "migration period. Ping output: %s" % cmd_result)
                    test.fail(err_msg)
    finally:
        migration_obj.cleanup_default()
        sriov_dest_obj.teardown_failover_test(**test_dict)
        sriov_src_obj.teardown_failover_test(**test_dict)
