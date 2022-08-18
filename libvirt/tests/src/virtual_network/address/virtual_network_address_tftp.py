import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import network_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Enable tftp service for the network without dhcp
    """
    def run_test():
        """
        Test tftp server function via libvirt network
        1. Prepare a network xml with tftp setting
        2. Define and start the network when there is no tftp_root dir
        3. Create tftp_root dir and start the network again
        4. Check dnsmasq config file and tftp server
        """
        test.log.info("TEST_STEP1: Prepare a network xml with tftp settting.")
        net_obj = network_xml.NetworkXML()
        net_obj.setup_attrs(**network_dict)
        test.log.debug("Network created: %s.", net_obj)
        net_obj.define()

        test.log.info("TEST_STEP2: Remove tftp_root dir and start the network.")
        if os.path.exists(tftp_root):
            utils_misc.safe_rmdir(tftp_root)
        result = virsh.net_start(net_name, debug=True)
        libvirt.check_exit_status(result, True)
        libvirt.check_result(result, error_msg)

        test.log.info("TEST_STEP3: Create tftp_root dir and start the network.")
        os.makedirs(tftp_root)
        net_obj.start()

        test.log.info("TEST_STEP4: Check dnsmasq config.")
        net_conf = '/var/lib/libvirt/dnsmasq/%s.conf' % net_name
        with open(net_conf, 'r') as fd:
            lines = fd.read()
        dnsmasq_setting = params.get("dnsmasq_setting", "enable-tftp")
        if not lines.count(dnsmasq_setting):
            test.fail("Unable to get '%s' from %s." % (dnsmasq_setting, lines))

        test.log.info("TEST_STEP5: Check tftp service is running.")
        process.run('netstat -anu | grep ":69 "', shell=True)

    libvirt_version.is_libvirt_feature_supported(params)

    error_msg = params.get("error_msg")
    tftp_root = '/var/lib/tftpboot'
    network_dict = eval(params.get("network_dict"))
    net_name = network_dict.get('name')

    try:
        run_test()
    finally:
        if os.path.exists(tftp_root):
            utils_misc.safe_rmdir(tftp_root)
        virsh.net_destroy(net_name, debug=True)
        virsh.net_undefine(net_name, debug=True)
