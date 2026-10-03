# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import re
import os
import threading
import time

from avocado.utils import process

from virttest import libvirt_remote
from virttest import remote
from virttest import virsh
from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps
from provider.migration import migration_base


def check_port(test, port_list):
    """
    Check port during migration

    """
    time.sleep(5)
    cmd = "netstat -tunap|grep qemu-kvm|grep 49"
    ret = process.run(cmd, shell=True, verbose=True, ignore_status=True).stdout_text.strip()
    for port in port_list:
        if not re.findall(port, ret):
            test.fail(f"Not found {port} in {ret}.")
        else:
            test.log.debug(f"Checked {port} successfully.")


def run(test, params, env):
    """
    This case is to verify that when migration port is not specified, libvirt
    will allocate a port in range [migration_port_min, migration_port_max]. 

    """
    def setup_test():
        """
        Setup steps

        """
        qemu_conf_path = params.get("qemu_conf_path")
        qemu_conf_dest = params.get("qemu_conf_dest", "{}")
        nfs_mount_dir = params.get("nfs_mount_dir")
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")

        migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))
        migration_obj.remote_add_or_remove_port(migrate_desturi_port)

        nonlocal remote_obj
        remote_obj = libvirt_remote.update_remote_file(params,
                                                       qemu_conf_dest,
                                                       qemu_conf_path)
        for vm in vms:
            disk_dict = {'source': {'attrs': {'file': os.path.join(nfs_mount_dir,
                         os.path.basename(vm.get_first_disk_devices()['source']))}}}
            libvirt_vmxml.modify_vm_device(
                    vm_xml.VMXML.new_from_inactive_dumpxml(vm.name),
                    'disk', disk_dict)
            vm.start()
            vm.wait_for_login()

    def run_test():
        """
        run test steps

        """
        src_uri = params.get("virsh_migrate_connect_uri")
        options = params.get("virsh_migrate_options", "")
        status_error = "yes" == params.get("status_error", "no")
        migrate_timeout = int(params.get("virsh_migrate_thread_timeout", 900))
        port_list = eval(params.get("port_list", "[]"))
        test_case = params.get("test_case")

        test.log.debug("run test.")
        p = threading.Thread(target=check_port, args=(test, port_list,))
        p.start()

        try:
            migration_obj.migration_test.do_migration(vms=vms, srcuri=src_uri,
                                                      desturi=dest_uri,
                                                      migration_type="simultaneous",
                                                      options=options,
                                                      thread_timeout=migrate_timeout,
                                                      ignore_status=False,
                                                      status_error=status_error)
        except Exception as info:
            if test_case == "min_49333_and_max_49334":
                err_msg = "Unable to find an unused port in range 'migration' (49333-49334)"
                if err_msg not in info:
                    test.fail("Migrate vms failed: %s", info)
                else:
                    migration_obj.migration_test.do_migration(vms=vm[2], srcuri=src_uri,
                                                              desturi=dest_uri,
                                                              migration_type="orderly",
                                                              options=options,
                                                              thread_timeout=migrate_timeout,
                                                              ignore_status=False,
                                                              status_error=status_error)
            else:
                test.fail("Migrate vms failed: %s", info)

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.info("Cleanup steps.")
        for vm in vms:
            virsh.destroy(vm.name, debug=True, uri=dest_uri)
        migration_obj.cleanup_connection()
        nonlocal remote_obj
        if remote_obj:
            del remote_obj

    dest_uri = params.get("virsh_migrate_desturi")
    vm_names = params.get("vms").split()
    remote_obj = None
    vms = []
    for vm_name in vm_names:
        vm = env.get_vm(vm_name)
        vms.append(vm)
        test.log.debug("vm name: %s", vm.name)

    migration_obj = base_steps.MigrationBase(test, vms[0], params)

    try:
        setup_test()
        run_test()
        migration_obj.verify_default()
    finally:
        cleanup_test()
