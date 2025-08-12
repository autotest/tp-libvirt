import logging
import os
import time

from avocado.utils import process
from avocado.utils import service

from virttest import data_dir
from virttest import utils_libvirtd
from virttest import utils_split_daemons
from virttest import virt_vm

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
    def config_libvirtd_log(params):
        """
        Configure audit log level"

        :param params: one dict wrapping parameters
        """
        # enable/disable audit_logging
        enable_audit_logging = "yes" == params.get("enable_audit", "no")
        # set audit level or not
        set_audit_level = "yes" == params.get("set_audit_level", "yes")
        log_filters = params.get('log_filters')
        libvirtd_config.log_outputs = "1:file:%s" % log_config_path
        libvirtd_config.log_level = 1
        if set_audit_level:
            libvirtd_config.audit_level = 1
        if enable_audit_logging:
            libvirtd_config.audit_logging = 1
        if log_filters:
            libvirtd_config.log_filters = log_filters
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
        Clean up audit message under /var/log/audit/.
        """
        cmd = "truncate -s 0  /var/log/audit/*"
        process.run(cmd, ignore_status=False,
                    shell=True)

    def check_msg_in_libvirtd_log(str_to_grep):
        """
        Check audit message in libvirtd log file

        param: str_to_grep: captured message in string
        """
        if not libvirt.check_logfile(str_to_grep, log_config_path, str_in_log=enable_audit_logging):
            test.fail("Check message log:%s failed in log file:%s" % (str_to_grep, log_config_path))

    def ausearch_audit_log():
        """
        Check ausearch output related to audit

        """
        ausearch_type_list = params.get("ausearch_type_list").split()
        for type_item in ausearch_type_list:
            cmd = "ausearch -m %s -ts recent" % type_item
            type_message = "type=%s msg=audit" % type_item
            cmd_result = process.run(cmd, shell=True).stdout_text.strip()
            if type_message not in cmd_result:
                test.fail("Check ausearch type:%s log failed in output:%s" % (type_item, cmd_result))

    def check_concurrent_filters():
        """
        Check concurrent filters

        """
        log_for_util = params.get("log_for_util")
        if not libvirt.check_logfile(log_for_util, log_config_path,
                                     ignore_status=True, str_in_log=True):
            test.fail("Can not find expected message :%s in log file:%s" % (log_for_util, log_config_path))
        log_for_filter_list = eval(params.get("log_for_filter_list"))
        for filter in log_for_filter_list:
            str_to_grep = "%s" % filter
            if libvirt.check_logfile(str_to_grep, log_config_path,
                                     ignore_status=True, str_in_log=True):
                test.fail("Find unexpected message :%s in log file:%s" % (str_to_grep, log_config_path))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    enable_audit_logging = "yes" == params.get("enable_audit", "no")
    set_audit_level = "yes" == params.get("set_audit_level", "yes")

    # Get LibvirtdConfig or VirtQemuConfig
    log_config_path = os.path.join(data_dir.get_tmp_dir(), "libvirtd.log")
    libvirtd_config = VirtQemudConfig() if utils_split_daemons.is_modular_daemon() else LibvirtdConfig()

    test_scenario = params.get("test_scenario")

    try:
        # Need stop VM first
        if vm.is_alive():
            vm.destroy()
        config_libvirtd_log(params)
        # Clean up old audit message from log file
        clean_up_audit_log_file()
        # Check and start audit daemon service if necessary
        ensure_auditd_started()
        vm.start()
    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    else:
        if test_scenario in ["disable_audit_log", "enable_audit_log"]:
            check_virt_type_from_audit_log()
            check_msg_in_libvirtd_log("virDomainAudit")
        elif test_scenario == "default_audit_log":
            vm.wait_for_login().close()
            time.sleep(10)
            ausearch_audit_log()
        elif test_scenario == "concurrent_filters":
            check_concurrent_filters()
    finally:
        libvirtd_config.restore()
        utils_libvirtd.Libvirtd('virtqemud').restart()
        if log_config_path and os.path.exists(log_config_path):
            os.remove(log_config_path)
