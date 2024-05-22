from datetime import datetime

import logging as log
import os.path
import re
import threading

try:
    import queue as Queue
except ImportError:
    import Queue

from avocado.utils import process

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_split_daemons
from virttest import utils_misc
from virttest import utils_package
from virttest import virsh

from virttest.libvirt_xml import network_xml
from virttest.utils_test import libvirt


msg_queue = Queue.Queue()
daemon_conf = None
daemon = None

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def start_journal():
    """
    Track system journal
    """

    ret = process.run("journalctl -f", shell=True, verbose=True, ignore_status=True)
    msg_queue.put(ret.stdout_text)


def test_check_journal(params, test):
    """
    Test restart libvirtd with running guest.
    1) Start a guest;
    2) Start journal;
    3) Restart libvirtd;
    4) Check the output of `journalctl -f`;
    5) Check libvirtd log

    :param params: dict, test parameters
    :param test: test object
    """
    libvirtd_debug_file = params.get("libvirtd_debug_file")
    error_msg_in_journal = params.get("error_msg_in_journal")
    error_msg_in_log = params.get("error_msg_in_log")
    ignore_log_err_msg = params.get("ignore_log_err_msg", "")

    utils_libvirtd.Libvirtd("libvirtd-tls.socket").stop()
    utils_libvirtd.Libvirtd("libvirtd-tcp.socket").stop()

    # Start journal
    monitor_journal = threading.Thread(target=start_journal, args=())
    monitor_journal.start()

    # Restart libvirtd
    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd.restart()

    monitor_journal.join(2)

    # Stop journalctl command
    utils_misc.kill_process_by_pattern("journalctl")
    output = msg_queue.get()
    # Check error message in journal
    if re.search(error_msg_in_journal, output):
        test.fail("Found error message during libvirtd restarting: %s" % output)
    else:
        logging.info("Not found error message during libvirtd restarting.")

    # Check error messages in libvirtd log
    libvirt.check_logfile(error_msg_in_log, libvirtd_debug_file,
                          False, ignore_str=ignore_log_err_msg)


def get_installable_old_libvirt(installed_version, test):
    """
    Get old version of libvirt package available to be installed

    :param installed_version: str, version info
    :param test: test object

    :return: str, version installable
    """
    cmd = 'dnf list libvirt --showduplicates --available'
    cmd_ret = process.run(cmd,
                          shell=True,
                          ignore_status=False,
                          verbose=True)
    versions = re.findall(r"\s+(\d+.*\d+)\s+\w+", cmd_ret.stdout_text.strip())
    versions = versions[::-1]
    old_version = None
    for one_version in versions:
        if one_version < installed_version:
            old_version = one_version
            break
    if not old_version:
        test.cancel("There are no older libvirt packages "
                    "available in the repos")
    return old_version


def downgrade_libvirt(test):
    """
    Install older version of libvirt packages

    :param test: test object
    """
    cmd = 'yum list libvirt --showduplicates --installed'
    cmd_ret = process.run(cmd, shell=True, ignore_status=False,
                          verbose=True)
    installed_version = re.findall(r"libvirt.*\s+(\d.*\d)\s", cmd_ret.stdout_text.strip())
    if not installed_version:
        test.cancel("libvirt packages are not installed")
    else:
        test.log.debug("Step: Current installed libvirt version %s", installed_version)
    if not utils_package.package_remove('libvirt'):
        test.error("Fail to uninstall current libvirt packages")
    else:
        test.log.debug("Step: Installed libvirt packages are removed")
    old_version = get_installable_old_libvirt(installed_version[0], test)
    if not utils_package.package_install('libvirt-%s' % old_version):
        test.error("Fail to install libvirt package %s" % ('libvirt-%s' % old_version))
    else:
        test.log.debug("Step: Libvirt %s is installed", old_version)


def update_conf_files(params, test):
    """
    Update configuration files

    :param params: dict, test parameters
    :param test: test object
    """
    def _customize_conf(conf_dict, config_type):
        if config_type == 'sysconfig':
            sysconfig_conf_path = params.get('sysconfig_conf_path')
            if not os.path.exists(sysconfig_conf_path):
                process.run('touch %s' % sysconfig_conf_path)
                params['delete_file'] = sysconfig_conf_path
        is_force = True if config_type == 'libvirtd' else False
        conf_obj = libvirt.customize_libvirt_config(conf_dict,
                                                    config_type=config_type,
                                                    restart_libvirt=False,
                                                    force=is_force)
        test.log.debug("%s is updated", conf_obj.conf_path)
        return conf_obj

    libvirt_conf_dict = eval(params.get("libvirt_conf_dict"))
    sysconfig_conf_dict = eval(params.get("sysconfig_conf_dict"))
    libvirtd_conf_dict = eval(params.get("libvirtd_conf_dict"))
    config_dict = {}
    config_dict.update({'libvirt': _customize_conf(libvirt_conf_dict, 'libvirt')})
    config_dict.update({'sysconfig': _customize_conf(sysconfig_conf_dict, 'sysconfig')})
    config_dict.update({'libvirtd': _customize_conf(libvirtd_conf_dict, 'libvirtd')})
    params['config_dict'] = config_dict


def setup_default(params, test):
    pass


def setup_upgrade_with_legacy_mode(params, test):
    """
    Setup for libvirt package upgrade test with non-socket activation mode

    :param params: dict, test parameters
    :param test: test object
    """
    downgrade_libvirt(test)
    update_conf_files(params, test)
    daemons_masked = params.get('daemons_masked')
    process.run("systemctl mask %s" % daemons_masked, shell=True, ignore_status=False, verbose=True)
    test.log.debug("Step: The socket files were masked")
    process.run("systemctl restart libvirtd", shell=True, ignore_status=False)
    test.log.debug("Step: The libvirtd daemon is restarted")
    # In 'systemctl status libvirtd output, there might be below error:
    #   -- libvirtd[xxx]: operation failed: network 'default' already exists with uuid xxx
    # The error might impact on following cases because it exists in journal file.
    # Below two steps are to eliminate the bad impact.
    net_obj = network_xml.NetworkXML()
    if net_obj.get_active():
        virsh.net_destroy('default', ignore_status=False, debug=True)
        test.log.debug("Step: The default network is destroyed "
                       "in order to avoid the systemd error")
    process.run("systemctl restart libvirtd", shell=True, ignore_status=False)


def get_libvirtd_pid(test):
    """
    Get the PID of libvirtd process

    :param test: test object
    :return： tuple, (pid, stdout)
    """
    ret = process.run("systemctl status libvirtd", shell=True, verbose=True)
    pat = r"Main PID:\s+(\d+)\s+\(libvirtd\)"
    match_obj = re.findall(pat, ret.stdout_text.strip())
    if not match_obj:
        test.error("libvirtd daemon is not started correctly and no PID is found")
    return match_obj[0], ret.stdout_text.strip()


def test_upgrade_with_legacy_mode(params, test):
    """
    Test for upgrading libvirt package in non-socket activation mode

    :param params: dict, test parameters
    :param test: test object
    """
    old_dt = datetime.now()
    old_timestamp = int(round(old_dt.timestamp()))
    old_libvirtd_pid, status_output = get_libvirtd_pid(test)
    pat = r"%s\s+/usr/sbin/libvirtd\s+--listen" % old_libvirtd_pid
    if not re.search(pat, status_output):
        test.error("libvirtd daemon is not started "
                   "correctly as the pattern '%s' is not found" % pat)
    else:
        test.log.debug("Step: The libvirt daemon is started in "
                       "traditional mode(non-socket activation mode)")
    if not utils_package.package_upgrade('libvirt'):
        test.error("Fail to upgrade libvirt packages")
    else:
        test.log.debug("Step: The libvirt packages were upgraded")
    new_libvirtd_pid, status_output = get_libvirtd_pid(test)
    if old_libvirtd_pid == new_libvirtd_pid:
        test.fail("libvirtd pid '%s' is expected to change after "
                  "libvirt packages are upgraded")
    else:
        test.log.debug("Step: libvirtd PID changes as expected")
    pat = r"active\s+\(running\).*(\d{4}.*\d{2})\s+"
    matches = re.findall(pat, status_output)
    if not matches:
        test.error("The pattern '%s' did not match anything" % pat)
    dt_output = datetime.strptime(matches[0], "%Y-%m-%d %H:%M:%S")
    if int(round(dt_output.timestamp())) < old_timestamp:
        test.fail("The libvirtd's started time should be after %s" % old_dt)
    else:
        test.log.debug("The libvirtd's started time is new as expected")


def teardown_upgrade_with_legacy_mode(params, test):
    """
    Teardown for test with upgrading libvirt packages in
    non-socket activation mode

    :param params: dict, test parameters
    :param test: test object
    """
    net_obj = network_xml.NetworkXML()
    if not net_obj.get_active():
        virsh.net_start('default', ignore_status=False, debug=True)
        test.log.debug("Step: default network is restarted")
    daemons_masked = params.get('daemons_masked')
    process.run("systemctl unmask %s" % daemons_masked,
                shell=True, ignore_status=False, verbose=True)
    test.log.debug("Step: Socket files were unmasked")
    config_dict = params.get('config_dict', {})
    for config_type, config_obj in config_dict.items():
        is_force = True if config_type == 'libvirtd' else False
        libvirt.customize_libvirt_config(None,
                                         config_type=config_type,
                                         config_object=config_obj,
                                         is_recover=True,
                                         restart_libvirt=False,
                                         force=is_force)
    test.log.debug("Step: Config files were recovered")
    utils_libvirtd.Libvirtd().restart()
    delete_file = params['delete_file']
    if delete_file and os.path.exists(delete_file):
        os.remove(delete_file)
        test.log.debug("Step: Files were deleted")


def teardown_default(params, test):
    """
    Default teardown

    :param params: dict, test parameters
    :param test: test object
    """
    pass


def test_check_max_anonymous_clients(params, test):
    """
    Test the default value of max_anonymous_clients.

    :param params: dict, test parameters
    :param test: test object
    """
    check_string_in_log = params.get("check_string_in_log")
    daemon_name = params.get("daemon_name")
    conf_list = eval(params.get("conf_list"))
    log_level = params.get("log_level")
    log_file = params.get("log_file")
    log_filters = params.get("log_filters")
    virsh_uri = params.get("virsh_uri")
    require_modular_daemon = params.get('require_modular_daemon', "no") == "yes"
    vm_name = params.get("main_vm", "avocado-vt-vm1")

    utils_split_daemons.daemon_mode_check(require_modular_daemon)
    global daemon
    global daemon_conf
    daemon = utils_libvirtd.Libvirtd(daemon_name)
    daemon_conf = utils_config.get_conf_obj(daemon_name)

    for item in conf_list:
        try:
            del daemon_conf[item]
        except utils_config.ConfigNoOptionError:
            test.log.info("No '%s' in config file.", item)

    daemon_conf.log_level = log_level
    daemon_conf.log_outputs = "1:file:%s" % log_file
    daemon_conf.log_filters = log_filters
    daemon.restart()

    if daemon_name == "virtproxyd":
        virtproxy_tcp = utils_libvirtd.DaemonSocket("virtproxyd-tcp.socket")
        virtproxy_tcp.restart()

    virsh.start(vm_name, uri=virsh_uri, debug=True, ignore_status=True)
    libvirt.check_logfile(check_string_in_log, log_file, str_in_log=True)


def teardown_check_max_anonymous_clients(params, test):
    """
    Teardown for test the default value of max_anonymous_clients.

    :param params: dict, test parameters
    :param test: test object
    """
    global daemon
    global daemon_conf
    if daemon_conf:
        daemon_conf.restore()
    if daemon:
        daemon.restart()


def run(test, params, env):
    """
    libvirtd daemon related tests.
    """
    case = params.get('case', '')
    run_test = eval("test_%s" % case)
    setup_test = eval("setup_%s" % case) if "setup_%s" % case \
                                            in globals() else setup_default
    teardown_test = eval("teardown_%s" % case) if "teardown_%s" % case \
                                                  in globals() else teardown_default

    try:
        setup_test(params, test)
        run_test(params, test)
    finally:
        teardown_test(params, test)
