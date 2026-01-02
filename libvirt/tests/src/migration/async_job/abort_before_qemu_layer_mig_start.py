# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import queue
import threading
import time

from virttest import remote
from virttest import virsh
from virttest.utils_test import libvirt

from provider.migration import base_steps

msg_queue = queue.Queue()


def run_migration(vm_name, dest_uri, option, extra):
    """
    Run migration

    :param vm_name: VM name
    :param dest_uri: virsh uri
    :param option: virsh migrate option parameters
    :param extra: virsh migrate extra parameters
    """
    ret = virsh.migrate(vm_name, dest_uri, option, extra, ignore_status=True, debug=True)
    msg_queue.put(ret)


def run(test, params, env):
    """
    Abort migration job before qemu layer migration starts, then migrate vm
    again.

    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        remote.run_remote_cmd("pkill -19 virtqemud", params)

    def run_test():
        """
        Run test

        """
        dest_uri = params.get("virsh_migrate_desturi")
        option = params.get("virsh_migrate_options", "--live --verbose")
        extra = params.get("virsh_migrate_extra")
        err_msg = params.get("err_msg")

        mig_t = threading.Thread(target=run_migration, args=(vm_name, dest_uri, option, extra))
        mig_t.start()
        time.sleep(3)

        virsh.domjobabort(vm_name, debug=True)
        remote.run_remote_cmd("pkill -18 virtqemud", params)
        mig_t.join()

        output = msg_queue.get()
        libvirt.check_result(output, err_msg)

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        run_test()
        migration_obj.run_migration_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
