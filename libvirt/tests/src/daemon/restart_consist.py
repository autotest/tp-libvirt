import logging
import difflib

from aexpect import ExpectTimeoutError
from aexpect import ShellTimeoutError

from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import remote
from virttest.libvirt_xml import VMXML
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError


def run(test, params, env):
    """
    Restart libvirtd and check the consistent of VM states.
    """

    def agent_connected():
        """
        Callback function to check if agent channel connected.
        """
        ga_tgt = VMXML.new_from_dumpxml(vm_name).get_section_string(
            '/devices/channel/target')
        return 'state="connected"' in ga_tgt

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    username = params.get("username")
    password = params.get("password")

    # Wait guest agent channel to be connected to avoid XML differ
    try:
        if not utils_misc.wait_for(agent_connected, 60):
            logging.warning('Agent channel not connected')
    except LibvirtXMLNotFoundError:
        pass

    vm_xml = VMXML.new_from_dumpxml(vm_name)
    logging.debug(vm_xml)

    # Skip the test when serial login is not available
    try:
        session = vm.wait_for_serial_login(60, username=username,
                                           password=password)
    except remote.LoginError:
        test.cancel('Serial console might needed to be '
                    'configured before test.')
    # Send a command line without waiting result
    try:
        session.cmd('sleep 30; echo hello', timeout=0)
    except ShellTimeoutError:
        pass

    # Restart libvirtd
    utils_libvirtd.Libvirtd().restart()

    # Check whether guest is still working
    vm.cleanup_serial_console()
    vm.create_serial_console()
    try:
        vm.serial_console.read_until_any_line_matches(['hello'], timeout=60)
    except ExpectTimeoutError:
        test.fail('Timeout when waiting for command output. '
                  'Maybe your guest is refreshed.')

    # Wait guest agent channel to be reconnected to avoid XML differ
    try:
        if not utils_misc.wait_for(agent_connected, 30):
            logging.warning('Agent channel not recovered after libvirtd '
                            'restart')
    except LibvirtXMLNotFoundError:
        pass

    # Check whether domain XML changed
    vm_xml_new = VMXML.new_from_dumpxml(vm_name)
    if str(vm_xml) != str(vm_xml_new):
        diff_txt = '\n'.join(
            difflib.unified_diff(
                str(vm_xml).splitlines(),
                str(vm_xml_new).splitlines(),
                lineterm='',
            )
        )
        test.fail("XML changed after libvirtd restart:\n%s"
                  % diff_txt)
