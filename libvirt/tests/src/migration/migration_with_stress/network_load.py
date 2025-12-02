# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from avocado.utils import process

from virttest import remote
from virttest import utils_iptables
from virttest import utils_package
from virttest import utils_net

from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps
from provider.virtual_network import network_base


def run(test, params, env):
    """
    To test that vm migration can succeed when there is heavy network load in
    vm.

    """
    def setup_test():
        """
        Setup steps

        """
        client_ip = params.get("client_ip")

        test.log.info("Setup steps.")
        utils_net.create_linux_bridge_tmux(bridge_name, iface_name=host_iface)
        remote_host_iface = utils_net.get_default_gateway(
            iface_name=True, session=remote_session, force_dhcp=False, json=True)
        params.update({"remote_host_iface": remote_host_iface})
        utils_net.create_linux_bridge_tmux(
            bridge_name, iface_name=remote_host_iface, session=remote_session)

        migration_obj.setup_connection()

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.remove_all_device_by_type('interface')
        vmxml.sync()
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name), 'interface',
            iface_dict)

        firewall_cmd.add_port(netperf_port, 'tcp', permanent=True)
        firewall_cmd.add_port(netperf_data_port, 'tcp', permanent=True)

        if not utils_package.package_install('netperf'):
            test.error('Failed to install netperf on host.')
        process.run(f"nohup netserver -p {netperf_port} -L {client_ip} -- -D -P {netperf_data_port} >/dev/null 2>&1 &", shell=True, verbose=True)

        vm.start()
        vm_session = vm.wait_for_serial_login()
        if not utils_package.package_install('netperf', session=vm_session):
            test.error('Failed to install netperf on VM.')

        utils_net.restart_guest_network(vm_session)
        vm_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
        test.log.debug("VM MAC: %s", vm_mac)
        vm_ip = network_base.get_vm_ip(vm_session, vm_mac)
        test.log.debug("VM IP Addr: %s", vm_ip)
        vm_session.cmd(f"nohup netperf -H {client_ip} -l 10000000 -L {vm_ip} -- -P {netperf_data_port} >/dev/null 2>&1 &")
        vm_session.close()
        test.log.debug("Guest xml:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.info("Cleanup steps.")
        utils_net.delete_linux_bridge_tmux(bridge_name, iface_name=host_iface)
        utils_net.delete_linux_bridge_tmux(
            bridge_name, iface_name=params.get("remote_host_iface"),
            session=remote_session)
        migration_obj.cleanup_connection()
        process.run("pkill netserver", shell=True, verbose=True, ignore_status=True)
        firewall_cmd.remove_port(netperf_port, 'tcp', permanent=True)
        firewall_cmd.remove_port(netperf_data_port, 'tcp', permanent=True)

    vm_name = params.get("migrate_main_vm")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    netperf_port = params.get("netperf_port")
    netperf_data_port = params.get("netperf_data_port")
    bridge_name = params.get("bridge_name")
    iface_dict = eval(params.get("iface_dict", "{}"))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=False, json=True)

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    remote_runner = remote.RemoteRunner(host=server_ip,
                                        username=server_user,
                                        password=server_pwd)
    remote_session = remote_runner.session
    firewall_cmd = utils_iptables.Firewall_cmd()

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
