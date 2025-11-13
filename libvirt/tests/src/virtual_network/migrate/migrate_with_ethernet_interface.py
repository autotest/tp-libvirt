# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import time

import aexpect.remote
from avocado.utils import process

from virttest import virsh
from virttest import utils_net
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_unprivileged

from provider.migration import base_steps
from provider.virtual_network import network_base


def run(test, params, env):
    """
    Test migration with ethernet type interface managed=no

    This case tests migration of vm with ethernet type interface to and back,
    checking that network functions work well after migration.

    Test matrix:
    - precopy migration
    - postcopy migration
    - cancel migration

    Setup:
    1. Prepare migration environment
    2. Prepare pre-created tap device and connect it to linux bridge on both hosts
    3. Start VM with ethernet type interface pointing to the tap device

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def create_unpr_user_and_vm(user_name, user_passwd, main_vm_name, remote_session):
        """
        Create unprivileged user and setup VM for that user

        :param user_name: unprivileged user name
        :param user_passwd: unprivileged user password
        :param main_vm_name: main vm name in env
        :param remote_session: remote machine session
        :return: VM object for unprivileged user
        """
        test.log.info(f"Creating unprivileged user for local and remote: {user_name}")
        try:
            add_user, add_pwd = (f'useradd -m {user_name}',
                                 f'echo "{user_name}:{user_passwd}" | chpasswd')
            process.run(add_user, shell=True)
            process.run(add_pwd, shell=True)
            if remote_session:
                remote_session.cmd(add_user)
                remote_session.cmd(add_pwd)

        except Exception as e:
            test.log.warn(f"User {user_name} may already exist: {e}")

        # Get original VM XML from root user
        root_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(main_vm_name)

        # Prepare VM XML for unprivileged user
        test.log.info(f"Preparing VM XML for unprivileged user: {user_name}")
        unpr_vmxml = network_base.prepare_vmxml_for_unprivileged_user(user_name, root_vmxml)

        # Remove existing interfaces and add ethernet interface
        unpr_vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(unpr_vmxml, 'interface', iface_attrs)

        # Remove DAC security label for unprivileged user
        unpr_vmxml.del_seclabel(by_attr=[('model', 'dac')])

        # Define VM for unprivileged user
        test.log.info(f"Defining VM for unprivileged user: {user_name}")
        network_base.define_vm_for_unprivileged_user(user_name, unpr_vmxml)

        # Get unprivileged VM object
        unpr_vm_args = {
            'username': params.get('username'),
            'password': params.get('password'),
        }
        unpr_vm = libvirt_unprivileged.get_unprivileged_vm(
            unpr_vmxml.vm_name, user_name, user_passwd, **unpr_vm_args)

        test.log.info(f"Successfully created unprivileged VM: {unpr_vmxml.vm_name}")
        return unpr_vm

    def setup_test():
        """
        Setup test environment for migration with ethernet interface
        """
        test.log.info("Setting up test environment")
        remote_host_iface = utils_net.get_default_gateway(
            iface_name=True, session=remote_session, force_dhcp=True, json=True)
        params.update({"remote_host_iface": remote_host_iface})

        utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
        network_base.create_tap(tap_name, bridge_name, unpr_user, tap_mtu=tap_mtu)

        utils_net.create_linux_bridge_tmux(bridge_name, remote_host_iface, session=remote_session)
        network_base.create_tap(tap_name, bridge_name, unpr_user,
                                session=remote_session, tap_mtu=tap_mtu)

    def run_test():
        """
        Run the main test: migration and verification
        """
        test.log.info("TEST_STEP: Migrating VM to target host")
        migration_obj.setup_connection()
        virsh_ins.start(unpr_vm_obj.name, debug=True, ignore_status=False)
        # test.log.debug("------------------------------------------")
        # virsh_ins.dom_list(" --all", debug=True)
        # time.sleep(420)

        migration_obj.run_migration()

        if not cancel_migration:
            test.log.info("TEST_STEP: Checking VM network connectivity on target host")
            if unpr_vm_obj.serial_console is not None:
                unpr_vm_obj.cleanup_serial_console()
            unpr_vm_obj.create_serial_console()
            vm_session_after_mig = unpr_vm_obj.wait_for_serial_login(timeout=240)
            vm_session_after_mig.cmd("dhclient -r; dhclient", timeout=120)

            test.log.info("TEST_STEP: Testing guest ping to outside")
            ips = {'outside_ip': outside_ip}
            network_base.ping_check(params, ips, vm_session_after_mig)

        if migrate_vm_back:
            test.log.info("TEST_STEP: Migrating VM back to source host")
            migration_obj.run_migration_back()


    def teardown_test():
        """
        Cleanup test environment
        """
        test.log.info("Cleaning up test environment")
        network_base.delete_tap(tap_name)
        network_base.delete_tap(tap_name, session=remote_session)

        utils_net.delete_linux_bridge_tmux(bridge_name, iface_name=host_iface)
        utils_net.delete_linux_bridge_tmux(
            bridge_name, iface_name=params.get("remote_host_iface"),
            session=remote_session)

        bk_xml.sync()
        virsh.destroy(unpr_vm_obj.name, unprivileged_user=unpr_user, debug=True)
        virsh.undefine(unpr_vm_obj.name, options='--nvram',
                       unprivileged_user=unpr_user, debug=True)
        migration_obj.cleanup_connection()
        if remote_session:
            remote_session.close()

    # Parse test parameters
    server_ip = params["server_ip"] = params.get("migrate_dest_host")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd") or params.get("migrate_dest_pwd")
    outside_ip = params.get("outside_ip")
    bridge_name = params.get("bridge_name", "br0")
    tap_name = params.get("tap_name", "mytap0")
    tap_mtu = params.get("tap_mtu")
    cancel_migration = params.get_boolean("cancel_migration")
    migrate_vm_back = params.get_boolean("migrate_vm_back", True)
    iface_attrs = eval(params.get("iface_attrs", "{}"))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True)

    vm_name = params.get('main_vm')
    unpr_user = params.get('test_user', 'test')
    unpr_passwd = params.get('test_passwd', 'test')
    dest_uri = params.get("virsh_migrate_desturi") % unpr_user
    params.update({"virsh_migrate_desturi": dest_uri})
    connect_uri = f'qemu+ssh://{unpr_user}@localhost/session'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    remote_session = aexpect.remote.remote_login(
        "ssh", server_ip, "22", server_user, server_pwd, r'[$#%]')

    # virsh_ins = virsh.Virsh(uri=connect_uri)
    # params["connect_uri"] = connect_uri
    # params.update({"customized_uri": connect_uri})
    # unpr_vm_obj = create_unpr_user_and_vm(unpr_user, unpr_passwd, vm_name, remote_session)
    # unpr_vm_obj.connect_uri = connect_uri
    # migration_obj = base_steps.MigrationBase(test, unpr_vm_obj, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()

