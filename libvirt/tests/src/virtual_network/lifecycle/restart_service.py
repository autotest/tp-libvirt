import re
import logging as log

from avocado.utils import process
from virttest import virsh
from virttest.staging import service

logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    1) make sure the network is active in healthy state;
    2) delete the bridge of the network, which cause an abnormal status;
    3) restart the network daemon, check the network status is inactive and clean;
    4) start the network and check it's healthy again;
    """

    def check_net_states(net_name, active):
        """
        Check the network related status are expected,
        when it is running, there should be 2 dnsmasq process,
        and listening on specific ports. While inactive network
        should be clean without these.

        :params net_name: string, name of the network
        :params active: bool, True or False
        """
        s_active = virsh.net_state_dict()[net_name]["active"]
        cmd = "ps aux|grep dnsmasq|grep -v grep"
        cmd1 = "lsof -i -P -n | grep dnsmasq"
        dnsmasq_process = process.run(cmd, shell=True, ignore_status=True).stdout_text.strip()
        listen_port = process.run(cmd1, shell=True, ignore_status=True).stdout_text.strip()
        logging.debug(dnsmasq_process)
        logging.debug(listen_port)
        if active:
            pid_num = len(dnsmasq_process.splitlines())
            pattern_list = [r'UDP\s+192\.168\.\d{1,3}\.\d{1,3}:53',
                            r'TCP\s+192\.168\.\d{1,3}\.\d{1,3}:53',
                            r'UDP\s+\*:67']
            for pattern in pattern_list:
                if not re.search(pattern, listen_port):
                    test.fail("Can not find the listen port %s" % pattern)
            if (pid_num != 2) or not s_active:
                test.fail("The network should be active with 2 dnsmasq process!")
        if not active:
            if s_active:
                test.fail("The network %s should be inactive!" % net_name)
            if dnsmasq_process or listen_port:
                test.fail("There should not be any dnsmasq process or listening port!")

    try:
        virsh.net_start("default", ignore_status=True)
        check_net_states("default", True)
        net_xml = virsh.net_dumpxml("default", debug=True).stdout.strip()
        br_name = re.search(r"bridge name='(.*?)'", net_xml).group(1)
        cmd = "ip l delete %s" % br_name
        cmd1 = "ip l show %s" % br_name
        process.run(cmd, shell=True)
        process.run(cmd1, shell=True, ignore_status=True)
        service.Factory.create_service("virtnetworkd").restart()
        check_net_states("default", False)
        # make sure the network can start again successfully
        virsh.net_start("default", ignore_status=False)

    finally:
        virsh.net_start("default", ignore_status=True)
