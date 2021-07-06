import logging
import re

from avocado.utils.software_manager.backends import rpm
from avocado.utils import process
from avocado.utils import service

from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML

DEFAULT_NET = 'default'


def setup_firewalld():
    """
    Setup firewalld service for test
    """
    firewalld = 'firewalld'
    service_mgr = service.ServiceManager()
    status = service_mgr.status(firewalld)
    logging.debug('Service status is %s', status)
    if not status:
        service_mgr.start(firewalld)


def run(test, params, env):
    """
    Test virtual network
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    net = params.get('net', DEFAULT_NET)
    expect_str = params.get('expect_str')
    bridge_name = 'br_' + utils_misc.generate_random_string(3)

    test_group = params.get('test_group', 'default')
    case = params.get('case', '')

    def setup_test_default(case):
        """
        Default setup for test cases

        :param case: test case
        """
        logging.info('No specific setup step for %s', case)

    def cleanup_test_default(case):
        """
        Default cleanup for test cases

        :param case: test case
        """
        logging.info('No specific cleanup step for %s', case)

    def setup_test_zone(case):
        """
        Test setup for network zone test cases

        :param case: test case
        """

        if not libvirt_version.version_compare(6, 0, 0):
            test.cancel('Version of libvirt is too low for this test.')

        # Make sure firewalld version supports current test
        rpmb = rpm.RpmBackend()
        if rpmb.check_installed('firewalld', '0.6.0'):
            logging.info('Version of firewalld meets expectation')
        else:
            test.cancel('Version of firewalld is too low to finish this test.')

        setup_firewalld()

        # Setup zone for bridge
        netxml = NetworkXML.new_from_net_dumpxml(net)
        if case == 'public':
            netxml.bridge = {'name': bridge_name, 'zone': 'public'}
        elif case == 'info':
            netxml.bridge = {'name': bridge_name}
        else:
            test.error('Test "%s" is not supported.' % case)
        netxml.forward = {'mode': 'nat'}
        netxml.sync()
        logging.debug('Updated net:\n%s', virsh.net_dumpxml(net).stdout_text)

    def run_test_zone(case):
        """
        Test steps for network zone test cases

        :param case: test case
        """

        def run_test_zone_public():
            """
            Check zone of interface with firewalld-cmd
            """
            check_cmd = 'firewall-cmd --get-zone-of-interface=%s' % bridge_name
            output = process.run(check_cmd, verbose=True).stdout_text.strip()
            if output == 'public':
                logging.info('Zone check of interface PASSED')
            else:
                test.fail('Zone check of interface FAILED, should be public, but got %s' % output)

        def run_test_zone_info():
            """
            Check zone info with firewalld-cmd
            """
            check_cmd = 'firewall-cmd --info-zone=libvirt'
            output = process.run(check_cmd, verbose=True).stdout_text.strip()
            br = bridge_name
            for item in eval(expect_str):
                if item in output or re.search(item, output):
                    logging.debug('Found "%s" in firewalld-cmd output' % item)
                else:
                    test.fail('Not found "%s" in firewalld-cmd output' % item)

        test_zone = eval('run_test_zone_%s' % case)
        test_zone()

    bk_netxml = NetworkXML.new_from_net_dumpxml(net)
    bk_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    try:
        # Get setup function
        setup_test = eval('setup_test_%s' % test_group) \
            if 'setup_test_%s' % test_group in locals() else setup_test_default
        # Get runtest function
        run_test = eval('run_test_%s' % test_group)
        # Get cleanup function
        cleanup_test = eval('cleanup_test_%s' % test_group) \
            if 'cleanup_test_%s' % test_group in locals() else cleanup_test_default

        # Execute test
        setup_test(case)
        run_test(case)
        cleanup_test(case)

    finally:
        bk_netxml.sync()
        bk_xml.sync()
