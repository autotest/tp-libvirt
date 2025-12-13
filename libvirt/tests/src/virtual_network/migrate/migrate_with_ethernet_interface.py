# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import logging
import time

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
        logging.debug("Remove 'dac' security driver for unprivileged user")
        root_vmxml.del_seclabel(by_attr=[('model', 'dac')])

        # Prepare VM XML for unprivileged user
        test.log.info(f"Preparing VM XML for unprivileged user: {user_name}")
        unpr_vmxml = network_base.prepare_vmxml_for_unprivileged_user(user_name, root_vmxml)
        logging.debug("00000000000000055555555555:%s", unpr_vmxml.vm_name)

        libvirt_vmxml.modify_vm_device(unpr_vmxml, 'interface', iface_attrs)
        new_vm_name = unpr_vmxml.vm_name
        logging.debug("0000000000000000006666666666666:%s", new_vm_name)

        # Define VM for unprivileged user
        test.log.info(f"Defining VM for unprivileged user: {user_name}")
        if new_vm_name in virsh.dom_list('--all', shell=True, unprivileged_user=unpr_user).stdout_text:
            virsh.undefine(new_vm_name, options='--nvram', unprivileged_user=unpr_user, debug=True)
        network_base.define_vm_for_unprivileged_user(user_name, unpr_vmxml)

        # Get unprivileged VM object
        unpr_vm_args = {
            'username': params.get('username'),
            'password': params.get('password'),
        }
        unpr_vm = libvirt_unprivileged.get_unprivileged_vm(
            new_vm_name, user_name, user_passwd, **unpr_vm_args)

        test.log.info(f"Successfully created unprivileged VM: {new_vm_name}")

        return unpr_vm

    def setup_test():
        """
        Setup test environment for migration with ethernet interface
        """
        test.log.info("Setting up test environment")
        remote_host_iface = utils_net.get_default_gateway(
            iface_name=True, session=remote_session, force_dhcp=False, json=True)
        params.update({"remote_host_iface": remote_host_iface})

        utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
        network_base.create_tap(tap_name, bridge_name, unpr_user, tap_mtu=tap_mtu, flag=tap_flag)

        utils_net.create_linux_bridge_tmux(bridge_name, remote_host_iface, session=remote_session)
        network_base.create_tap(tap_name, bridge_name, unpr_user,
                                session=remote_session, tap_mtu=tap_mtu, flag=tap_flag)
        virsh.start(unpr_vm_obj.name, debug=True, unprivileged_user=unpr_user)


    def run_test():
        """
        Run the main test: migration and verification
        """
        test.log.info("TEST_STEP: Migrating VM to target host")
        test.log.debug("eeeeeeeeeeeeeeeeeeee:%s", params.get("customized_uri"))

        migration_obj.setup_connection()
        test.log.debug("fffffffffffffffffff:%s", params.get("customized_uri"))

        virsh.dom_list(" --all", debug=True, unprivileged_user=unpr_user)
        test.log.debug("gggggggggggggggg:%s", params.get("customized_uri"))

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
        params.update({"customized_uri": "default"})
        test.log.debug("11111111111111111111")

        network_base.delete_tap(tap_name)
        network_base.delete_tap(tap_name, session=remote_session)
        test.log.debug("2222222222222222222222222222")

        utils_net.delete_linux_bridge_tmux(bridge_name, iface_name=host_iface)
        utils_net.delete_linux_bridge_tmux(
            bridge_name, iface_name=params.get("remote_host_iface"),
            session=remote_session)
        test.log.debug("3333333333333333333333")
        if unpr_vm_obj.is_alive():
            virsh.destroy(unpr_vm_obj.name, unprivileged_user=unpr_user, debug=True)
        virsh.undefine(unpr_vm_obj.name, options='--nvram',
                       unprivileged_user=unpr_user, debug=True)
        test.log.debug("444444444444444444444444444444")

        migration_obj.cleanup_connection()
        test.log.debug("555555555555555")

        if remote_session:
            remote_session.close()
        test.log.debug("6666666666666666")

        process.run(f'pkill -u {unpr_user};userdel -f -r {unpr_user}',
                    shell=True, ignore_status=True)
        test.log.debug("777777777777777777")

    # Parse test parameters
    server_ip = params["server_ip"] = params.get("migrate_dest_host")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd") or params.get("migrate_dest_pwd")
    outside_ip = params.get("outside_ip")
    bridge_name = params.get("bridge_name", "br0")
    tap_name = params.get("tap_name", "mytap0")
    tap_mtu = params.get("tap_mtu")
    tap_flag = params.get("tap_flag")
    cancel_migration = params.get_boolean("cancel_migration")
    migrate_vm_back = params.get_boolean("migrate_vm_back", True)
    iface_attrs = eval(params.get("iface_attrs", "{}"))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, json=True)
    rand_id = utils_misc.generate_random_string(3)

    vm_name = params.get('main_vm')
    unpr_user = params.get('unpr_user', 'test_unpr') + rand_id
    unpr_passwd = params.get('test_passwd', 'test')

    params.update({"virsh_migrate_desturi": params.get("virsh_migrate_desturi") % unpr_user})
    # update uri in case file to avoid env_process skip case.
    migrate_source_host = params.get("migrate_source_host")
    params.update({"customized_uri": "qemu+ssh://%s@%s/session" % (unpr_user, migrate_source_host)})

    test.log.debug("xxxxxxxxxxxxxxx:%s", params.get("customized_uri"))

    remote_session = aexpect.remote.remote_login(
        "ssh", server_ip, "22", server_user, server_pwd, r'[$#%]')
    unpr_vm_obj = create_unpr_user_and_vm(unpr_user, unpr_passwd, vm_name, remote_session)
    migration_obj = base_steps.MigrationBase(test, unpr_vm_obj, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
