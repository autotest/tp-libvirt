# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.utils_libvirt import libvirt_network


def run(test, params, env):
    """
    Test virtnetworkd should not block host OS shutdown.
    """
    def setup_test():
        """
        Prepare network and its status.
        """
        test.log.info("TEST_SETUP: Prepare network and its status.")
        if preset_net == "define_net":
            libvirt_network.create_or_del_network(network_attrs)
            test.log.debug(f'{virsh.net_dumpxml(net_name).stdout_text}')

        if network_states == "inactive_net":
            virsh.net_destroy(net_name, debug=True)

    def run_test():
        """
        Check systemd-inhibit for virtnetworkd not existing in the list.
        """
        test.log.info("TEST_STEP1：Check systemd-inhibit")
        result = process.run(cmd, shell=True).stdout_text.strip()
        if re.findall(check_pattern, result):
            test.fail("Expected no '%s'", check_pattern)
        test.log.debug("Check systemd-inhibit PASS")

    def teardown_test():
        """
        Recover network.
        """
        test.log.info("TEST_TEARDOWN: Recover network.")
        if preset_net == "default_net":
            bk_net.sync()
            libvirt_network.ensure_default_network()
        else:
            libvirt_network.create_or_del_network(network_attrs, is_del=True)

    libvirt_version.is_libvirt_feature_supported(params)
    net_name = params.get("net_name")
    preset_net = params.get("preset_net")
    if preset_net == "default_net":
        default_net = NetworkXML.new_from_net_dumpxml(net_name)
        bk_net = default_net.copy()
    cmd = params.get("cmd")
    network_states = params.get("network_states")
    network_attrs = eval(params.get('network_attrs', '{}'))
    check_pattern = params.get("check_pattern")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
