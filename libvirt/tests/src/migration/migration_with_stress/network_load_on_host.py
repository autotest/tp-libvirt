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
from virttest import utils_package

from provider.migration import base_steps


def run(test, params, env):
    """
    This case is to verify that migration can succeed in high-speed network env
    with heavy nework load

    """
    def setup_test():
        """
        Setup steps

        """
        client_ip = params.get("client_ip")

        test.log.info("Setup steps.")
        if not utils_package.package_install('netperf'):
            test.error('Failed to install netperf on source host.')
        if not utils_package.package_install('netperf', remote_session):
            test.error('Failed to install netperf on target host.')

        process.run("systemctl stop firewalld", shell=True, verbose=True)
        remote.run_remote_cmd("systemctl stop firewalld", params)

        src_netserver_cmd = f"nohup netserver >/dev/null 2>&1 &"
        process.run(src_netserver_cmd, shell=True, verbose=True)
        dst_netserver_cmd = f"nohup netserver >/dev/null 2>&1 &"
        remote.run_remote_cmd(dst_netserver_cmd, params, ignore_status=False)

        src_netperf_cmd = f"nohup netperf -H {server_ip} -l 600000000 >/dev/null 2>&1 &"
        process.run(src_netperf_cmd, shell=True, verbose=True)

        dst_netperf_cmd = f"nohup netperf -H {client_ip} -l 600000000 >/dev/null 2>&1 &"
        remote.run_remote_cmd(dst_netperf_cmd, params, ignore_status=False)

        migration_obj.setup_connection()

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.info("Cleanup steps.")
        migration_obj.cleanup_connection()
        process.run("pkill netserver", shell=True, verbose=True, ignore_status=True)
        remote.run_remote_cmd("pkill netserver", params, ignore_status=True)
        process.run("pkill netperf", shell=True, verbose=True, ignore_status=True)
        remote.run_remote_cmd("pkill netperf", params, ignore_status=True)
        process.run("systemctl start firewalld", shell=True, verbose=True)
        remote.run_remote_cmd("systemctl start firewalld", params)

    vm_name = params.get("migrate_main_vm")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    netperf_port = params.get("netperf_port")
    netperf_data_port = params.get("netperf_data_port")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    remote_runner = remote.RemoteRunner(host=server_ip,
                                        username=server_user,
                                        password=server_pwd)
    remote_session = remote_runner.session

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
