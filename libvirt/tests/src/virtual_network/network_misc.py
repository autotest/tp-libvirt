import logging
import re

from avocado.utils.software_manager.backends import rpm
from avocado.utils import process
from avocado.utils import service

from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.utils_test import libvirt

DEFAULT_NET = 'default'
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


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
    machine_type = params.get('machine_type')
    net = params.get('net', DEFAULT_NET)
    status_error = 'yes' == params.get('status_error', 'no')
    expect_str = params.get('expect_str')
    bridge_name = 'br_' + utils_misc.generate_random_string(3)

    test_group = params.get('test_group', 'default')
    case = params.get('case', '')

    def setup_test_default(case):
        """
        Default setup for test cases

        :param case: test case
        """
        libvirt_version.is_libvirt_feature_supported(params)

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

    def run_test_iface_acpi(case):
        """
        Test acpi setting of interface

        :param case: test case
        """
        if not utils_misc.compare_qemu_version(6, 1, 0, is_rhev=False) and \
                machine_type == 'q35':
            test.cancel('This test is not supported on q35 '
                        'by qemu until v6.1.0')

        acpi_index = params.get('acpi_index')
        iface_in_vm = params.get('iface_in_vm')
        iface_attrs = eval(params.get('iface_attrs'))

        # Create interface with apci setting
        iface_acpi = interface.Interface('network')
        iface_acpi.setup_attrs(**iface_attrs)
        logging.debug('New interface with acpi: %s', iface_acpi)

        # Setup vm features with acpi if there is not
        features = vmxml.features
        feature_acpi = 'acpi'
        if not features.has_feature(feature_acpi):
            features.add_feature(feature_acpi)
            vmxml.features = features

        # Prepare vmxml for further test
        vmxml.remove_all_device_by_type('interface')
        vmxml.sync()

        # Test vm with iface with acpi
        if case == 'inplace':
            libvirt.add_vm_device(vmxml, iface_acpi)
            vm.start()

        # Test hotplug iface with acpi
        elif case == 'hotplug':
            vm.start()
            virsh.attach_device(vm_name, iface_acpi.xml, **VIRSH_ARGS)

        # Test boundry/negative values of acpi setting
        elif case == 'value_test':
            vm.start()
            vm.wait_for_serial_login().close()
            at_result = virsh.attach_device(vm_name, iface_acpi.xml, debug=True)
            libvirt.check_exit_status(at_result, status_error)
            if status_error:
                libvirt.check_result(at_result, expect_str)
            return

        # Check acpi setting in iface after adding/attaching to vm
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        new_iface = new_vmxml.get_devices('interface')[0]
        logging.debug('Interface with acpi in vm: %s', new_iface)

        if new_iface.xmltreefile.find('acpi') is None:
            test.fail('acpi not found in Interface')
        if new_iface.acpi.get('index') != acpi_index:
            test.fail('Index of acpi check failed, should be %s' % acpi_index)

        # Chech acpi inside vm
        session = vm.wait_for_serial_login()
        ip_output = session.cmd_output('ip l')
        logging.debug(ip_output)

        vm_ifaces = utils_net.get_linux_ifname(session)
        if iface_in_vm in vm_ifaces:
            logging.info('Found Interface %s in vm', iface_in_vm)
        else:
            test.fail('Interface %s not found in vm' % iface_in_vm)

        # Hotunplug iface
        virsh.detach_device(vm_name, new_iface.xml, **VIRSH_ARGS)
        vm_ifaces = utils_net.get_linux_ifname(session)
        if iface_in_vm in vm_ifaces:
            test.fail('Interface %s should be removed from vmxml' % iface_in_vm)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if new_vmxml.get_devices('interface'):
            test.fail('Interface should be removed.')

    bk_netxml = NetworkXML.new_from_net_dumpxml(net)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

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
