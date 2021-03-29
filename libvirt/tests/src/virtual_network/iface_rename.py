import logging
import os
import time

from avocado.utils import process
from virttest import utils_libvirtd
from virttest import utils_config
from virttest import utils_net
from virttest import data_dir
from virttest import libvirt_version


def run(test, params, env):
    """
    Check libvirtd log after some operations about the interface
    1. Configure the libvirtd log;
    2. Do the operations about interface;
    3. Check the libvirtd log for errors;
    4. Clear the env;
    """
    name_1 = params.get("name_1")
    name_2 = params.get("name_2")
    config_libvirtd = "yes" == params.get("config_libvirtd")
    log_file = params.get("log_file", "libvirtd.log")
    iface_name = utils_net.get_net_if(state="UP")[0]

    try:
        # config libvirtd
        if config_libvirtd:
            config = utils_config.LibvirtdConfig()
            log_path = os.path.join(data_dir.get_tmp_dir(), log_file)
            log_outputs = "1:file:%s" % log_path
            config.log_outputs = log_outputs
            config.log_level = 1
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
        process.run("ip l add link {0} name {1} type macvlan; ip l set {1} name {2}".format(iface_name, name_1, name_2),
                    ignore_status=True, shell=True)
        logging.debug("Check the log, there should be no error")
        time.sleep(5)
        check_cmd = "grep -i error %s" % log_path
        out = process.run(check_cmd, ignore_status=True, shell=True).stdout_text.strip()
        logging.debug("the log error is %s", out)
        if 'virFileReadAll' in out or "virNetDevGetLinkInfo" in out:
            if libvirt_version.version_compare(6, 3, 0):
                test.fail("libvirtd.log get error: %s" % out)
            else:
                test.fail("The bug 1557902 is fixed since libvirt-6.3.0")

    finally:
        process.run("ip l delete %s; ip l delete %s" % (name_2, name_1), ignore_status=True, shell=True)
        if config_libvirtd:
            config.restore()
            libvirtd.restart()
