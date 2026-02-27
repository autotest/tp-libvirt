# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import logging

import aexpect.remote

from avocado.utils import process

from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_unprivileged

from provider.migration import base_steps
from provider.virtual_network import network_base

virsh_dargs = {"debug": True, "ignore_status": False}


def run(test, params, env):
    """
    Test migration with ethernet type interface managed=no

    This case tests migration of vm with ethernet type interface to and back,
    checking that network functions work well after migration.

    Test matrix:
    - precopy migration
    - postcopy migration
    - cancel migration

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def _prepare_tap_for_vm():
        remote_host_iface = params.get("remote_host_iface")
        if not remote_host_iface:
            remote_host_iface = utils_net.get_default_gateway(
                iface_name=True, session=remote_session, force_dhcp=False, json=True
            )
        params.update({"remote_host_iface": remote_host_iface})

        utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
        network_base.create_tap(
            tap_name, bridge_name, unpr_user, tap_mtu=tap_mtu, flag=tap_flag
        )

        utils_net.create_linux_bridge_tmux(
            bridge_name, remote_host_iface, session=remote_session
        )
        network_base.create_tap(
            tap_name,
            bridge_name,
            unpr_user,
            session=remote_session,
            tap_mtu=tap_mtu,
            flag=tap_flag,
        )

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
            add_user, add_pwd = (
                f"useradd -m {user_name}",
                f'echo "{user_name}:{user_passwd}" | chpasswd',
            )
            process.run(add_user, shell=True)
            process.run(add_pwd, shell=True)
            if remote_session:
                remote_session.cmd(add_user)
                remote_session.cmd(add_pwd)

        except Exception as e:
            test.log.warn(f"User {user_name} may already exist: {e}")

        _prepare_tap_for_vm()
        root_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(main_vm_name)
        logging.debug("Remove 'dac' security driver for unprivileged user")
        root_vmxml.del_seclabel(by_attr=[("model", "dac")])

        # Define VM XML for unprivileged user
        unpr_vmxml = network_base.prepare_vmxml_for_unprivileged_user(
            user_name, root_vmxml
        )
        libvirt_vmxml.modify_vm_device(unpr_vmxml, "interface", iface_attrs)
        new_vm_name = unpr_vmxml.vm_name
        test.log.info(f"Defining VM for unprivileged user: {user_name}")
        network_base.define_vm_for_unprivileged_user(user_name, unpr_vmxml)

        # Get unprivileged VM object
        unpr_vm = libvirt_unprivileged.get_unprivileged_vm(
            new_vm_name, user_name, user_passwd, **unpr_vm_args
        )
        params.update({"migrate_main_vm": new_vm_name})

        return unpr_vm

    def setup_test():
        """
        Setup test environment for migration with ethernet interface
        """
        test.log.info("TEST_SETUP: Setting up test environment")
        virsh.start(unpr_vm_obj.name, debug=True, unprivileged_user=unpr_user)
        test.log.debug(
            "Test with guest xml:%s", vm_xml.VMXML.new_from_dumpxml(unpr_vm_obj.name)
        )
        unpr_vm_obj.wait_for_login().close()

    def run_test():
        """
        Run the main test: migration and verification
        """
        test.log.info("TEST_STEP: Migrating VM to target host")
        migration_obj.setup_connection()

        # Use virsh migrate cmd to avoid reset uri params for the whole migration structure.
        virsh.migrate(
            unpr_vm_obj.name,
            dest_uri=desturi,
            uri=migrate_uri,
            option=virsh_migrate_options,
            extra=virsh_migrate_extra,
            unprivileged_user=unpr_user,
            **virsh_dargs,
        )

        if not cancel_migration:
            test.log.info("TEST_STEP: Checking VM network connectivity on target host")
            session = network_base.unprivileged_user_login(
                unpr_vm_obj.name,
                unpr_user,
                params.get("username"),
                params.get("password"),
            )
            ips = {"outside_ip": outside_ip}
            network_base.ping_check(params, ips, session)

        if migrate_vm_back:
            test.log.info("TEST_STEP: Migrating VM back to source host")
            migration_obj.run_migration_back()

    def teardown_test():
        """
        Cleanup test environment
        """
        test.log.info("TEST_TEARDOWN: Cleaning up test environment")
        network_base.delete_tap(tap_name)
        network_base.delete_tap(tap_name, session=remote_session)
        utils_net.delete_linux_bridge_tmux(bridge_name, iface_name=host_iface)
        utils_net.delete_linux_bridge_tmux(
            bridge_name,
            iface_name=params.get("remote_host_iface"),
            session=remote_session,
        )

        if unpr_vm_obj.is_alive():
            virsh.destroy(unpr_vm_obj.name, unprivileged_user=unpr_user, debug=True)
        virsh.undefine(
            unpr_vm_obj.name, options="--nvram", unprivileged_user=unpr_user, debug=True
        )

        migration_obj.cleanup_connection()
        if remote_session:
            remote_session.cmd(f"pkill -u {unpr_user};userdel -f -r {unpr_user}")
            remote_session.close()
        process.run(
            f"pkill -u {unpr_user};userdel -f -r {unpr_user}",
            shell=True,
            ignore_status=True,
        )

    server_ip = params.get("migrate_dest_host")
    server_user = params.get("server_user")
    server_pwd = params.get("migrate_dest_pwd")
    outside_ip = params.get("outside_ip")
    bridge_name = params.get("bridge_name", "br0")
    tap_name = params.get("tap_name", "mytap0")
    tap_mtu = params.get("tap_mtu")
    tap_flag = params.get("tap_flag")
    cancel_migration = params.get_boolean("cancel_migration")
    migrate_vm_back = params.get_boolean("migrate_vm_back", True)
    iface_attrs = eval(params.get("iface_attrs", "{}"))
    host_iface = params.get("host_iface")
    host_iface = (
        host_iface
        if host_iface
        else utils_net.get_default_gateway(iface_name=True, json=True)
    )

    rand_id = utils_misc.generate_random_string(3)
    vm_name = params.get("main_vm")
    unpr_user = params.get("unpr_user", "test_unpr") + rand_id
    unpr_passwd = params.get("test_passwd", params.get("password"))
    desturi = params.get("virsh_migrate_desturi") % unpr_user
    migrate_source_host = params.get("migrate_source_host")
    migrate_uri = "qemu+ssh://%s@%s/session" % (unpr_user, migrate_source_host)

    virsh_migrate_options = params.get("virsh_migrate_options")
    virsh_migrate_extra = params.get("virsh_migrate_extra")
    unpr_vm_args = {
        "username": params.get("username"),
        "password": params.get("password"),
    }

    remote_session = aexpect.remote.remote_login(
        "ssh", server_ip, "22", server_user, server_pwd, r"[$#%]"
    )
    unpr_vm_obj = create_unpr_user_and_vm(
        unpr_user, unpr_passwd, vm_name, remote_session
    )
    migration_obj = base_steps.MigrationBase(test, unpr_vm_obj, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
