import re
import logging as log

from avocado.utils import process
from virttest import virsh
from virttest import utils_misc
from virttest.staging import service
from virttest.libvirt_xml import network_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_network

logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    1) make sure the network is active in healthy state;
    2) delete the bridge of the network, which cause an abnormal status;
    3) restart the network daemon, check the network status is inactive and clean;
    4) start the network and check it's healthy again;
    """

    def delete_bridge(net_name):
        """
        Delete the bridge associated with the network created by libvirt to
        prepare an abnormal scenario

        :params net_name: the name of the network
        :return: None
        """
        xml = network_xml.NetworkXML.new_from_net_dumpxml(net_name)
        br_name = xml.bridge['name']
        logging.debug("Delete the bridge %s for network %s:", br_name, net_name)
        cmd = "ip l delete %s" % br_name
        process.run(cmd, shell=True)
        cmd1 = "ip l show %s" % br_name
        if not process.run(cmd1, shell=True, ignore_status=True).exit_status:
            test.cancel("The bridge can not be deleted: %s" % br_name)

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
        xml = network_xml.NetworkXML.new_from_net_dumpxml(net_name)
        br_ip = xml.ips[0]['address']
        logging.debug("br_ip is %s for network %s", br_ip, net_name)

        cmd = "ps aux|grep dnsmasq|grep -v grep | grep %s" % net_name
        dnsmasq_process = process.run(cmd, shell=True, ignore_status=True).stdout_text.strip()
        cmd1 = """ps aux|grep dnsmasq|grep -v grep | grep %s | awk '$1=="dnsmasq" {print $2}'""" % net_name
        dnsmasq_pid = process.run(cmd1, shell=True, ignore_status=True).stdout_text.strip()
        cmd2 = "lsof -i -P -n | grep %s" % dnsmasq_pid
        listen_port = process.run(cmd2, shell=True, ignore_status=True).stdout_text.strip()
        logging.debug("Check the dnsmasq process of the network: %s", dnsmasq_process)
        logging.debug("Check the listen_port associated with the dnsmasq process: %s", listen_port)
        if active:
            pid_num = len(dnsmasq_process.splitlines())
            pattern_list = [rf'UDP\s+{br_ip}:53',
                            rf'TCP\s+{br_ip}:53',
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
                test.fail("There should not be any dnsmasq process nor listening port!")

    try:
        net_name = utils_misc.generate_random_string(8)
        net_attrs = eval(params.get('net_attrs', '{}'))
        logging.debug("TEST_STEP1: Define and start a network")
        libvirt_network.create_or_del_network(net_attrs)
        virsh.net_start(net_name, ignore_status=True)
        check_net_states(net_name, True)
        logging.debug("TEST_STEP2: Delete the bridge to prepare an abnormal status")
        delete_bridge(net_name)
        logging.debug("TEST_STEP3: Restart virtnetworkd, check the network become inactive")
        service.Factory.create_service("virtnetworkd").restart()
        check_net_states(net_name, False)
        logging.debug("TEST_STEP4: Start the network again and ensure it works well")
        ret = virsh.net_start(net_name, ignore_status=False)
        libvirt.check_result(ret)
    finally:
        virsh.net_destroy(net_name, ignore_status=True)
        virsh.net_undefine(net_name, ignore_status=True)
