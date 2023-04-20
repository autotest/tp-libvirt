import logging as log
import os
import re
import shutil

from avocado.utils import process

from virttest import data_dir
from virttest import utils_split_daemons
from virttest import utils_libvirtd


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test the config in systemd config file of services

    1.Set the exec_args.
    2.Set LimitNOFILE.
    3.Set LimitMEMLOCK.
    """

    def set_config(ori_config_line, new_config_line, config_file):
        """
        Set config for the daemon systemd config

        :param ori_config_line: original config
        :param new_config_line: new config
        :param config_file: daemon systemd config file
        """
        with open(systemd_file, 'r') as fr:
            alllines = fr.readlines()
        with open(systemd_file, 'w+') as fw:
            for line in alllines:
                newline = re.sub(ori_config_line, new_config_line, line)
                fw.writelines(newline)

    def test_exec_config(daemon_name, new_config_line):
        """
        Set exec_args in the daemon systemd config and restart daemon,
        then confirm the config works

        :param daemon_name: daemon name
        :param new_config_line: new config
        """
        ori_config_line = process.getoutput("cat %s |grep timeout" % systemd_file, shell=True)
        ori_config_line = ori_config_line.split('\"')[1]
        new_config_line = ori_config_line.replace(ori_config_line, exec_args)
        set_config(ori_config_line, new_config_line, systemd_file)
        process.run("systemctl daemon-reload")
        utils_libvirtd.Libvirtd(daemon_name).restart()
        daemon_process = process.getoutput("ps aux |grep %s |grep -v grep" % daemon_name, shell=True)
        logging.debug("The process is started by:%s", daemon_process)
        daemon_exec_args = daemon_process.split(daemon_name)[-1]
        if not re.search(exec_args, daemon_exec_args):
            test.fail("The exec_args %s does not take effect" % exec_args)

    def test_limit_config(test_type, limit_config):
        """
        Set limitNOFILE and limitMEMLOCK in the daemon systemd config
        and restart daemon, then confirm the config works

        :param test_type: the type of limit
        :param limit_config: config of limit
        """
        process.run("prlimit -p `pidof %s` | grep %s" % (daemon_name, test_type), shell=True)
        ori_config_line = process.getoutput("cat %s |grep %s" % (systemd_file, test_type), shell=True)
        set_config(ori_config_line, limit_config, systemd_file)
        process.run("systemctl daemon-reload")
        utils_libvirtd.Libvirtd('virtqemud').restart()
        limit_info = process.getoutput("prlimit -p `pidof %s` | grep -i %s" % (daemon_name, test_type), shell=True)
        logging.debug("The limit resource for daemon is %s", limit_info)
        if not re.search(re.compile(r'[0-9]\d+').findall(limit_info)[0], limit_config):
            test.fail("The limit set %s does not take effect" % limit_config)

    daemons = params.get('daemons', "").split()
    require_modular_daemon = params.get('require_modular_daemon', "no") == "yes"
    config_dir = params.get('config_dir', "")
    daemon_name = params.get('daemon_name', "")
    test_type = params.get('test_type', "")
    exec_args = params.get('exec_args', "")
    limitNOFILE = params.get('limitNOFILE', "")
    limitMEMLOCK = params.get('limitMEMLOCK', "")

    utils_split_daemons.daemon_mode_check(require_modular_daemon)

    try:
        systemd_file = config_dir + daemon_name + ".service"
        backup_file = os.path.join(data_dir.get_tmp_dir(), systemd_file + "-bak")
        shutil.copy(systemd_file, backup_file)
        if test_type == "set_exec_args":
            test_exec_config(daemon_name, systemd_file)
        if test_type == "set_limitNOFILE" or test_type == "set_limitMEMLOCK":
            test_type = test_type.replace('set_limit', '')
            limit_config = limitNOFILE + limitMEMLOCK
            test_limit_config(test_type, limit_config)

    finally:
        if os.path.exists(backup_file):
            shutil.copy(backup_file, systemd_file)
            os.remove(backup_file)
        process.run("systemctl daemon-reload")
        utils_libvirtd.Libvirtd(daemon_name).restart()
