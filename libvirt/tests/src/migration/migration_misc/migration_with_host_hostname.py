from avocado.utils import process

from virttest import remote
from virttest import utils_libvirtd

from provider.migration import base_steps
from provider.migration import migration_base


def get_hostname(params=None, remote_host=False):
    """
    Get hostname from source or dest host

    :param params: dictionary with the test parameter
    :param remote_host: if True, will get the hostname of target host
    :return: the hostname of source/target host
    """
    cmd = "hostname"
    if remote_host:
        ret = remote.run_remote_cmd(cmd, params, ignore_status=False)
    else:
        ret = process.run(cmd, ignore_status=False, shell=True)
    return ret.stdout_text.strip()


def set_hostname(params, hostname, test, remote_host=False):
    """
    Set hostname for source or dest host

    :param params: dictionary with the test parameter
    :param hostname: string, hostname
    :param test: test object
    :param remote_host: if True, will set the hostname of target host
    """
    cmd = "hostnamectl set-hostname %s" % hostname
    if remote_host:
        ret = remote.run_remote_cmd(cmd, params, ignore_status=False)
    else:
        ret = process.run(cmd, ignore_status=False, shell=True)
    if ret.exit_status:
        test.fail("Failed to set hostname: %s" % ret.stdout_text.strip())


def run(test, params, env):
    """
    Test cases about host's hostname.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps for cases

        """
        test.log.info("Setup steps for cases.")
        server_ip = params.get("server_ip")
        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")

        migration_obj.setup_connection()

        src_hostname = get_hostname()
        hostname_dict.update({"src_hostname": src_hostname})
        dest_hostname = get_hostname(params, remote_host=True)
        hostname_dict.update({"dest_hostname": dest_hostname})

        if migration_base.check_NM(params):
            test.log.debug("Stop NM service on source.")
            NM_service_dict.update({"src_NM_service": migration_base.get_NM_service()})
            if NM_service_dict["src_NM_service"].status():
                NM_service_dict["src_NM_service"].stop()
        if migration_base.check_NM(params, remote_host=True):
            test.log.debug("Stop NM service on target.")
            NM_service_dict.update({"dest_NM_service": migration_base.get_NM_service(params, remote_host=True)})
            if NM_service_dict["dest_NM_service"].status():
                NM_service_dict["dest_NM_service"].stop()

        dest_session = remote.wait_for_login('ssh', server_ip, '22',
                                             server_user, server_pwd,
                                             r"[\#\$]\s*$")
        dest_libvirtd = utils_libvirtd.Libvirtd(session=dest_session)
        libvirtd_dict.update({"dest_libvirtd": dest_libvirtd})
        src_libvirtd = utils_libvirtd.Libvirtd()
        libvirtd_dict.update({"src_libvirtd": src_libvirtd})

        set_hostname(params, params.get("src_hostname"), test)
        src_libvirtd.restart()

        set_hostname(params, params.get("dest_hostname"), test, remote_host=True)
        dest_libvirtd.restart()

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        if NM_service_dict["src_NM_service"] and not NM_service_dict["src_NM_service"].status():
            NM_service_dict["src_NM_service"].start()
        if NM_service_dict["dest_NM_service"] and not NM_service_dict["dest_NM_service"].status():
            NM_service_dict["dest_NM_service"].start()

        migration_obj.cleanup_connection()
        set_hostname(params, hostname_dict["src_hostname"], test)
        libvirtd_dict["src_libvirtd"].restart()
        set_hostname(params, hostname_dict["dest_hostname"], test, remote_host=True)
        libvirtd_dict["dest_libvirtd"].restart()

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    hostname_dict = {}
    NM_service_dict = {}
    libvirtd_dict = {}

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        if migrate_again:
            migration_obj.run_migration_again()
        migration_obj.verify_default()
    finally:
        cleanup_test()
