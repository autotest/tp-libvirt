import logging as log
import re

from avocado.utils import process
from avocado.utils import service
from avocado.utils.software_manager.backends import rpm
from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.utils_libvirt import libvirt_network
from virttest.utils_test import libvirt

DEFAULT_NET = 'default'
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


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
    ran_id = utils_misc.generate_random_string(3)
    net_name = 'net_' + ran_id
    net_attrs = eval(params.get('net_attrs', '{}'))
    status_error = 'yes' == params.get('status_error', 'no')
    expect_str = params.get('expect_str')
    bridge_name = 'br_' + ran_id

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

        def setup_test_zone_default():
            # Setup zone for bridge
            netxml = NetworkXML.new_from_net_dumpxml(net)
            if case == 'info':
                netxml.bridge = {'name': bridge_name}
            else:
                test.error('Test "%s" is not supported.' % case)
            netxml.forward = {'mode': 'nat'}
            netxml.sync()
            logging.debug('Updated net:\n%s',
                          virsh.net_dumpxml(net).stdout_text)

        def setup_test_zone_libvirt_managed():
            if params.get('forward_mode') == 'isolated':
                net_attrs.pop('forward')
            libvirt_network.create_or_del_network(net_attrs)

        def setup_test_zone_public():
            libvirt_network.create_or_del_network(net_attrs)

        setup_zone = eval('setup_test_zone_%s' % case) if \
            'setup_test_zone_%s' % case in locals() else setup_test_zone_default
        setup_zone()

    def run_test_zone(case):
        """
        Test steps for network zone test cases

        :param case: test case
        """
        def check_zone(iface, expect_zone):
            """
            Check whether zone info meets expectation
            """
            check_cmd = 'firewall-cmd --get-zone-of-interface=%s' % iface
            cmd_result = process.run(check_cmd, ignore_status=True)
            output = cmd_result.stdout_text.strip() + cmd_result.stderr_text.strip()
            if output == expect_zone:
                logging.info('Zone check of interface PASSED')
                return output
            else:
                test.fail(
                    f'Zone check of interface FAILED, should be {expect_zone}, but got %s' % output)

        def check_zone_info(br, zone):
            """
            Check zone info with firewalld-cmd
            """
            check_cmd = f'firewall-cmd --info-zone={zone}'
            output = process.run(check_cmd, verbose=True).stdout_text.strip()
            for item in eval(expect_str):
                if item in output or re.search(item, output):
                    logging.debug('Found "%s" in firewalld-cmd output' % item)
                else:
                    test.fail('Not found "%s" in firewalld-cmd output' % item)

        def run_test_zone_public():
            """
            Check zone of interface with firewalld-cmd
            """
            net_attrs = NetworkXML.new_from_net_dumpxml(net_name).fetch_attrs()
            net_br_attrs = net_attrs['bridge']
            if net_attrs['bridge']['zone'] != 'public':
                test.fail(f"Network {net_name} zone should be 'public',"
                          f"not {net_attrs['bridge']['zone']}")

            check_zone(net_br_attrs['name'], 'public')
            process.run('firewall-cmd --reload')
            utils_misc.wait_for(
                lambda: check_zone(net_br_attrs['name'], 'public'),
                timeout=10, ignore_errors=True)

        def run_test_zone_info():
            """
            Check zone info with firewalld-cmd
            """
            check_zone_info(bridge_name, 'libvirt')

        def run_test_zone_libvirt_managed():
            br_attrs = eval(params.get('br_attrs', {}))
            default_zone = params.get('default_zone')

            virsh.net_dumpxml(net_name, **VIRSH_ARGS)
            net_attrs = NetworkXML.new_from_net_dumpxml(net_name).fetch_attrs()
            net_br_attrs = net_attrs['bridge']
            if not set(br_attrs.items()).issubset(set(net_br_attrs.items())):
                test.fail(
                    f'libvirt should create a linux bridge named '
                    f'{net_br_attrs["name"]}, with stp=on, delay=0')

            check_zone(net_br_attrs['name'], default_zone)
            if default_zone in ('no zone', 'libvirt-routed'):
                logging.info(f'Skip checking zone info for "{default_zone}"')
            else:
                check_zone_info(net_br_attrs['name'], default_zone)
            process.run('firewall-cmd --reload')
            utils_misc.wait_for(
                lambda: check_zone(net_br_attrs['name'], default_zone),
                timeout=10, ignore_errors=True)

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

        # Create interface with acpi setting
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

        # Test boundary/negative values of acpi setting
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

        # Check acpi inside vm
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
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        bk_xml.sync()
