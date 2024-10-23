import os
import shutil

from virttest import libvirt_remote

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_config

from provider.migration import base_steps


def update_qemu_conf_on_src(params, test, local_obj):
    """
    Update qemu configure file on source

    :param params: Dictionary with the test parameter
    :param test: Test object
    :param local_obj: Local qemu conf object
    """
    qemu_conf_src = eval(params.get("qemu_conf_src", "{}"))

    local_obj.append(libvirt.customize_libvirt_config(qemu_conf_src,
                                                      "qemu",
                                                      remote_host=False,
                                                      extra_params=params))


def update_qemu_conf_on_target(params, test, remote_obj):
    """
    Update qemu configure file on target

    :param params: Dictionary with the test parameter
    :param test: Test object
    :param remote_obj: Remote qemu conf object
    """
    default_qemu_conf = params.get("default_qemu_conf")
    qemu_conf_path = params.get("qemu_conf_path")
    qemu_conf_dest = params.get("qemu_conf_dest", "{}")

    if default_qemu_conf:
        params['file_path'] = qemu_conf_path
        remote_obj.append(libvirt_config.remove_key_in_conf(eval(default_qemu_conf),
                                                            "qemu",
                                                            remote_params=params,
                                                            restart_libvirt=True))
    if qemu_conf_dest:
        remote_obj.append(libvirt_remote.update_remote_file(params,
                                                            qemu_conf_dest,
                                                            qemu_conf_path))


def run(test, params, env):
    """
    Test default_tls_x509_verify/migrate_tls_x509_verify on source/target host.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_migrate_tls_x509_verify_on_target():
        """
        Setup steps for migrate_tls_x509_verify_on_target

        """
        test.log.info("Setup steps for migrate_tls_x509_verify_on_target.")
        cert_path = params.get("cert_path")
        tmp_cert_path = params.get("tmp_cert_path")

        update_qemu_conf_on_target(params, test, remote_obj)
        migration_obj.setup_connection()
        if os.path.exists(tmp_cert_path):
            os.remove(tmp_cert_path)
        shutil.move(cert_path, tmp_cert_path)

    def setup_migrate_tls_x509_verify_on_src():
        """
        Setup steps for migrate_tls_x509_verify_on_src

        """
        test.log.info("Setup steps for migrate_tls_x509_verify_on_src.")

        update_qemu_conf_on_src(params, test, local_obj)
        migration_obj.setup_connection()

    def run_migration_again_migrate_tls_x509_verify_on_target():
        """
        Run migration again for migrate_tls_x509_verify_on_target

        """
        test.log.info("Run migration again for migrate_tls_x509_verify_on_target.")
        cert_path = params.get("cert_path")
        tmp_cert_path = params.get("tmp_cert_path")

        if os.path.exists(cert_path):
            os.remove(cert_path)
        shutil.move(tmp_cert_path, cert_path)
        migration_obj.run_migration_again()

    def cleanup_migrate_tls_x509_verify_on_target():
        """
        Cleanup steps for migrate_tls_x509_verify_on_target

        """
        test.log.info("Cleanup steps migrate_tls_x509_verify_on_target.")
        migration_obj.cleanup_connection()
        if remote_obj:
            for obj in remote_obj:
                del obj

    def cleanup_migrate_tls_x509_verify_on_src():
        """
        Cleanup steps for migrate_tls_x509_verify_on_src

        """
        test.log.info("Cleanup steps migrate_tls_x509_verify_on_src.")
        migration_obj.cleanup_connection()
        if local_obj:
            for obj in local_obj:
                libvirt.customize_libvirt_config(None,
                                                 config_type="qemu",
                                                 remote_host=False,
                                                 is_recover=True,
                                                 extra_params=params,
                                                 config_object=obj)

    migrate_again = "yes" == params.get("migrate_again", "no")
    vm_name = params.get("migrate_main_vm")
    test_case = params.get('test_case', '')

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    run_migration_again_test = eval("run_migration_again_%s" % test_case) if "run_migration_again_%s" % test_case in \
        locals() else migration_obj.run_migration_again
    cleanup_test = eval("cleanup_%s" % test_case) if "cleanup_%s" % test_case in \
        locals() else migration_obj.cleanup_connection

    remote_obj = []
    local_obj = []

    try:
        setup_test()
        migration_obj.run_migration()
        if migrate_again:
            run_migration_again_test()
        migration_obj.verify_default()
    finally:
        cleanup_test()
