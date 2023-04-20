import logging
import os

from avocado.utils import process
from avocado.utils import service

from virttest import data_dir
from virttest import utils_libvirtd
from virttest import utils_split_daemons

from virttest.utils_test import libvirt

from virttest.utils_config import LibvirtdConfig
from virttest.utils_config import VirtQemudConfig

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test Disable/enable audit_logging in libvirtd.log.

    1) Enable/disable audit_logging in libvirtd.conf;
    2) Restart libvirtd daemon;
    3) Check if audit logging as expected;
    """
    # Here it needs manipulate libvird config as test step, so it doesn't use default config
    # enabled by avocado-vt framework at start
    def config_libvirtd_log(enable_audit_logging=False):
        """
        Configure audit log level"

        :param enable_audit_logging: bool indicating enable/disable audit_logging
        """
        libvirtd_config.log_outputs = "1:file:%s" % log_config_path
        libvirtd_config.log_level = 1
        libvirtd_config.audit_level = 1
        if enable_audit_logging:
            libvirtd_config.audit_logging = 1
        utils_libvirtd.Libvirtd('virtqemud').restart()

    def ensure_auditd_started():
        """
        Check audit service status and start it if it's not running
        """
        service_name = 'auditd'
        service_mgr = service.ServiceManager()
        status = service_mgr.status(service_name)
        LOG.debug('Service status is %s', status)
        if not status:
            service_mgr.start(service_name)

    def check_virt_type_from_audit_log():
        """
        Check whether virt type: VIRT_CONTROL|VIRT_MACHINE_ID|VIRT_RESOURCE in audit.log
        """
        cmd = 'cat /var/log/audit/audit.log |grep -E "type=VIRT_CONTROL|VIRT_MACHINE_ID|VIRT_RESOURCE"'
        if process.system(cmd, ignore_status=True, shell=True):
            test.fail("Check virt type failed with %s" % cmd)

    def clean_up_audit_log_file():
        """
        Clean up audit message in log file.
        """
        cmd = "> /var/log/audit/audit.log; > %s" % log_config_path
        process.run(cmd, ignore_status=False,
                    shell=True)

    def check_msg_in_libvirtd_log(str_to_grep):
        """
        Check audit message in libvirtd log file

        param: str_to_grep: captured message in string
        """
        if not libvirt.check_logfile(str_to_grep, log_config_path, str_in_log=enable_audit_logging):
            test.fail("Check message log:%s failed in log file:%s" % (str_to_grep, log_config_path))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    enable_audit_logging = "yes" == params.get("enable_audit", "no")

    # Get LibvirtdConfig or VirtQemuConfig
    log_config_path = os.path.join(data_dir.get_tmp_dir(), "libvirtd.log")
    libvirtd_config = VirtQemudConfig() if utils_split_daemons.is_modular_daemon() else LibvirtdConfig()

    try:
        # Need stop VM first
        if vm.is_alive():
            vm.destroy()
        config_libvirtd_log(enable_audit_logging)
        # Clean up old audit message from log file
        clean_up_audit_log_file()

        # Check and start audit daemon service if necessary
        ensure_auditd_started()

        vm.start()
        check_virt_type_from_audit_log()
        check_msg_in_libvirtd_log("virDomainAudit")
    finally:
        libvirtd_config.restore()
        utils_libvirtd.Libvirtd('virtqemud').restart()
