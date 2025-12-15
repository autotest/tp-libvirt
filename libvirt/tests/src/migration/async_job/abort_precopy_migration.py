# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import re
import time

from avocado.utils import process

from virttest import remote
from virttest import utils_misc
from virttest import virsh
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def check_job_type_list(job_type_list, domjobinfo_log, test):
    """
    Check job type list

    :param job_type_list: list, job type list
    :param domjobinfo_log: domain job info log
    :param test: test object
    """

    found = [False] * len(job_type_list)
    i = 0
    with open(domjobinfo_log) as f:
        for line in f:
            if i < len(job_type_list) and job_type_list[i] in line:
                found[i] = True
                i += 1
    if all(found):
        test.log.info(f"Got all expected job type: {job_type_list}")
    else:
        test.fail(f"Not get all expected job type: {found}")


def run(test, params, env):
    """
    Abort migration job when vm memory is being transferred at qemu layer,
    then migrate vm again.

    """
    def setup_test():
        """
        Setup steps

        """
        abort_mig = params.get("abort_mig")

        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        if abort_mig == "abort_during_vm_downtime":
            virsh.migrate_setmaxdowntime(vm_name, '1000000', debug=True)
        process.run("echo > {}".format(qemu_log), shell=True)
        if os.path.exists(domjobinfo_log):
            os.remove(domjobinfo_log)
        cmd = f"nohup bash -c 'while true; do virsh domjobinfo {vm_name}; sleep 0.1; done' > {domjobinfo_log} 2>&1 &"
        process.run(cmd, shell=True, verbose=True)

    def run_test():
        abort_method = params.get("abort_method")

        test.log.info("Run test steps.")
        if abort_method == "ctrl_c":
            dest_uri = params.get("virsh_migrate_desturi")
            option = params.get("virsh_migrate_options", "--live --verbose")
            extra = params.get("virsh_migrate_extra")
            err_msg = params.get("err_msg")
            after_event = params.get("after_event")

            event_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
            event_cmd = "event --all --loop"
            test.log.debug("event cmd: %s", event_cmd)
            event_session.sendline(event_cmd)

            mig_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
            mig_cmd = f"migrate {vm_name} {dest_uri} {extra} {option}"
            test.log.debug("migration cmd: %s", mig_cmd)
            mig_session.sendline(mig_cmd)

            if not utils_misc.wait_for(
                lambda: re.findall(after_event, event_session.get_stripped_output()),
                150,
            ):
                test.fail("Unable to find event {}".format(after_event))

            mig_session.sendcontrol("c")
            time.sleep(3)
            mig_output = mig_session.get_stripped_output()
            test.log.debug("migration output: %s", mig_output)
            if err_msg not in mig_output:
                test.fail(f"Unexpected error message: {mig_output}")
        else:
            migration_obj.run_migration()

    def verify_test():
        """
        Verify steps

        """
        with_multifd = params.get("with_multifd")
        expected_dest_state = params.get("expected_dest_state")
        expected_src_state = params.get("expected_src_state")
        dest_uri = params.get("virsh_migrate_desturi")
        job_type_list = eval(params.get("job_type_list", "[]"))

        test.log.info("Verify steps.")
        if not libvirt.check_vm_state(vm_name, expected_src_state, uri=migration_obj.src_uri, debug=True):
            test.fail(f"Failed to get VM state '{expected_src_state}' on source host.")
        if expected_src_state == "paused":
            vm.resume()
        dest_vm_list = virsh.dom_list(options="--all --persistent", debug=True, uri=dest_uri)
        if expected_dest_state == "nonexist":
            if vm_name in dest_vm_list.stdout.strip():
                test.fail(f"Unexpected {vm_name} found on the dest host.")
        if with_multifd:
            ret = virsh.domstate(vm_name, extra="--reason", uri=migration_obj.src_uri, debug=True)
            libvirt.check_result(ret, expected_fails="failed to get domain")
            libvirt.check_logfile("shutting down, reason=failed", qemu_log)
            ret = remote.run_remote_cmd("coredumpctl list", params)
            libvirt.check_result(ret, expected_fails='No coredumps found', check_both_on_error=True)
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)
        check_job_type_list(job_type_list, domjobinfo_log, test)

    vm_name = params.get("migrate_main_vm")
    qemu_log = f"/var/log/libvirt/qemu/{vm_name}.log"
    domjobinfo_log = params.get("domjobinfo_log")
    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        run_test()
        verify_test()
        migration_obj.run_migration_again()
        migration_obj.verify_default()
    finally:
        cmd = "pkill -f 'while true; do virsh domjobinfo'"
        process.run(cmd, shell=True, verbose=True)
        if os.path.exists(domjobinfo_log):
            os.remove(domjobinfo_log)
        migration_obj.cleanup_connection()
