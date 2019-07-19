import os
import logging
import aexpect
import shutil
import stat

from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.channel import Channel
from virttest.utils_test import libvirt as utlv
from virttest import data_dir


def run(test, params, env):
    """
    Test for basic channel device function.

    1) Define the VM with specified channel device and check result meets
       expectation.
    2) Start the guest and check if start result meets expectation
    3) Test the function of started channel device
    4) Shutdown the VM and clean up environment
    """

    def prepare_channel_xml(to_file, char_type, port_id=0):
        """
        Prepare pty or spicevmc channel devices file to do hotplug.

        :param to_file: the file generated
        :param char_type: the type of the channel
        :param port_id: the port id, if '0' specified, means auto gen
        """
        params = {'channel_type_name': char_type,
                  'target_type': 'virtio',
                  'target_state': 'disconnected',
                  'address_type': 'virtio-serial',
                  'address_controller': '0',
                  'address_bus': '0'}

        if char_type == "pty":
            channel_path = ("/dev/pts/%s" % port_id)
            params_tmp = {'source_path': channel_path,
                          'target_name': 'org.linux-kvm.port.%s' % port_id}
            params.update(params_tmp)
            if not port_id:
                params['address_port'] = '%s' % port_id
        elif char_type == "spicevmc":
            params['target_name'] = 'com.redhat.spice.0'
        channel_xml = utlv.create_channel_xml(params, alias=True, address=True)
        shutil.copyfile(channel_xml.xml, to_file)

    def _setup_pty_channel_xml(port_id=0):
        """
        Prepare pty channel devices of VM XML.

        :param port_id: the port id for the channel
        """
        channel_path = ("/dev/pts/%s" % port_id)
        target_name = ('org.linux-kvm.port.%s' % port_id)

        source_dict = {'path': channel_path}
        target_dict = {'type': 'virtio',
                       'name': target_name,
                       'state': 'disconnected'}
        address_dict = {'type': 'virtio-serial',
                        'controller': '0',
                        'bus': '0'}

        if not port_id:
            address_dict['port'] = '%s' % port_id

        channel_tmp = Channel(type_name=channel_type)
        channel_tmp.source = source_dict
        channel_tmp.target = target_dict
        channel_tmp.address = address_dict

        logging.debug("Channel XML is:%s", channel_tmp)
        vm_xml.add_device(channel_tmp)

    def _setup_channel_xml():
        """
        Prepare channel devices of VM XML according to params.
        """

        source_dict = {}
        target_dict = {}
        if source_mode is not None:
            source_dict['mode'] = source_mode
        if source_path is not None:
            source_dict['path'] = source_path
        if source_autopath is not None:
            source_dict['autopath'] = source_autopath
        if target_type is not None:
            target_dict['type'] = target_type
        if target_name is not None:
            target_dict['name'] = target_name
        if target_state is not None:
            target_dict['state'] = target_state
        if target_address is not None:
            target_dict['address'] = target_address
        if target_port is not None:
            target_dict['port'] = target_port

        if source_dict:
            channel.source = source_dict
        if target_dict:
            channel.target = target_dict

        logging.debug("Channel XML is:%s", channel)
        vm_xml.add_device(channel)

    def _define_and_check():
        """
        Predict the error message when defining and try to define the guest
        with testing XML.
        """
        fail_patts = []

        if not auto_gen_port and \
           target_type not in ['virtio', 'guestfwd', 'spicevmc']:
            fail_patts.append(
                r"target type must be specified for channel device")
            fail_patts.append(
                r"unknown target type '.*' specified for character device")
        if not auto_gen_port and target_type == 'guestfwd':
            if target_address is None:
                fail_patts.append(
                    r"guestfwd channel does not define a target address")
            if target_port is None:
                fail_patts.append(
                    r"guestfwd channel does not define a target port")
            if channel_type == 'unix' and source_path is None:
                fail_patts.append(
                    r"Missing source path attribute for char device")
        vm_xml.undefine()
        res = vm_xml.virsh.define(vm_xml.xml)
        libvirt.check_result(res, expected_fails=fail_patts)
        return not res.exit_status

    def _get_autopath():
        """
        Predict automatic generated source path
        """
        base_path = '/var/lib/libvirt/qemu/channel/target/%s.' % vm_name
        if target_name:
            return base_path + target_name
        else:
            return base_path + '(null)'

    def _check_xml(test):
        """
        Check defined XML against expectation
        """
        expected_channel = Channel(channel_type)

        try:
            source_dict = channel.source
        except LibvirtXMLNotFoundError:
            source_dict = {}

        if channel_type == 'pty':
            source_dict = {}
        elif channel_type == 'unix':
            if source_mode is None:
                if source_path:
                    source_dict['mode'] = 'connect'
                else:
                    source_dict['mode'] = 'bind'
            if source_path is None:
                source_dict['path'] = _get_autopath()
            if source_autopath:
                del source_dict['autopath']

        target_dict = {}
        if target_type == 'virtio':
            expected_channel.address = {
                'bus': '0',
                'controller': '0',
                'port': '1',
                'type': 'virtio-serial',
            }
            if 'type' in channel.target:
                target_dict['type'] = channel.target['type']
            if 'name' in channel.target:
                target_dict['name'] = channel.target['name']
        elif target_type == 'guestfwd':
            if 'type' in channel.target:
                target_dict['type'] = channel.target['type']
            if 'address' in channel.target:
                target_dict['address'] = channel.target['address']
            if 'port' in channel.target:
                target_dict['port'] = channel.target['port']

        if source_dict:
            expected_channel.source = source_dict
        if target_dict:
            expected_channel.target = target_dict

        current_xml = VMXML.new_from_dumpxml(vm_name)
        channel_elem = current_xml.xmltreefile.find('devices/channel')
        cur_channel = Channel.new_from_element(channel_elem)
        if not (expected_channel == cur_channel):
            test.fail("Expect generate channel:\n%s\nBut got:\n%s" %
                      (expected_channel, cur_channel))

    def _start_and_check():
        """
        Predict the error message when starting and try to start the guest.
        """
        fail_patts = []

        if channel_type == 'unix' and source_path and source_mode != 'bind':
            fail_patts.append(r"No such file or directory")

        res = virsh.start(vm_name)
        libvirt.check_result(res, expected_fails=fail_patts)
        return not res.exit_status

    def _check_virtio_guest_channel(test):
        """
        Check virtio guest channel state against expectation.
        """

        def _find_comm_paths(session):
            if source_path is None:
                host_path = _get_autopath()
            else:
                host_path = source_path

            name_port_map = {}
            base_path = '/sys/class/virtio-ports'
            vports = session.cmd_output('ls %s' % base_path).split()
            status = session.cmd_status('ls %s/*/name' % base_path)
            if status == 0:
                for vport in vports:
                    vport_path = os.path.join(base_path, vport)
                    name_path = os.path.join(vport_path, 'name')
                    name = session.cmd_output('cat %s' % name_path).strip()
                    name_port_map[name] = vport

                if expect_name not in name_port_map:
                    test.fail("Expect get vport name %s, got %s" %
                              (expect_name, name_port_map))
                vport = name_port_map[expect_name]
            else:
                active_xml = VMXML.new_from_dumpxml(vm_name)
                port_number = active_xml.xmltreefile.find(
                    '/devices/channel/address').get('port')
                vport = 'vport1p%s' % port_number
            guest_path = '/dev/%s' % vport
            return guest_path, host_path

        def _test_unix_communication(session, guest_path, host_path, test):
            """
            Test unix socket communication between host and guest through
            channel
            """
            nc_session = aexpect.Expect('nc -U "%s"' % host_path)
            try:
                # Test message passing from guest to host
                msg = "What hath God wrought"
                try:
                    session.cmd_status('echo "%s" > %s' % (msg, guest_path),
                                       timeout=1)
                except aexpect.ShellTimeoutError as detail:
                    pass
                nc_session.read_until_last_line_matches(msg, timeout=1)

                # Test message passing from host to guest
                nc_session.sendline(msg)
                try:
                    session.cmd_output('cat %s' % guest_path, timeout=1)
                except aexpect.ShellTimeoutError as detail:
                    if detail.output.strip() != msg:
                        test.fail("Expect receive '%s' in guest, "
                                  "But got %s" %
                                  (msg, detail.output))
            finally:
                nc_session.close()

        if target_name is None:
            expect_name = 'com.redhat.spice.0'
        else:
            expect_name = target_name

        if channel_type == 'unix':
            session = vm.wait_for_login()
            try:
                guest_path, host_path = _find_comm_paths(session)
                _test_unix_communication(session, guest_path, host_path, test)
            finally:
                session.close()

    def _verify_attach_channel_device(char_type, port_id):
        """
        Test unix socket communication between host and guest through
        channel
        :param char_type: the type of the channel
        :param port_id: the port id of the channel
        """
        result = virsh.attach_device(vm_name, xml_file)
        if result.stderr:
            test.fail('Failed to attach %s to %s. Result:\n %s'
                      % ('pty', vm_name, result))
        current_xml = VMXML.new_from_dumpxml(vm_name)
        channel_devices = current_xml.get_devices('channel')

        found_dev = False
        for channel_device in channel_devices:
            if channel_device.address['port'] == port_id:
                found_dev = True
                break
        if not found_dev:
            logging.debug("Failed to find channel with port %s", port_id)
        return found_dev

    channel_type = params.get('channel_type', None)
    source_mode = params.get('channel_source_mode', None)
    source_path = params.get('channel_source_path', None)
    source_autopath = params.get('channel_source_autopath', None)
    target_type = params.get('channel_target_type', None)
    target_name = params.get('channel_target_name', None)
    target_state = params.get('channel_target_state', None)
    target_address = params.get('channel_target_address', None)
    target_port = params.get('channel_target_port', None)
    auto_gen_port = params.get('auto_gen_port', 'no') == 'yes'

    channel = Channel(type_name=channel_type)

    vm_name = params.get("main_vm", "virt-tests-vm1")

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_device_by_type('channel')
        if auto_gen_port:
            _setup_pty_channel_xml(2)
            _setup_pty_channel_xml(8)

            tmp_dir = os.path.join(data_dir.get_tmp_dir(), "hotplug_serial")
            if not os.path.exists(tmp_dir):
                os.mkdir(tmp_dir)
            os.chmod(tmp_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

            xml_file = os.path.join(tmp_dir, "xml_%s" % 'pty')
            prepare_channel_xml(xml_file, 'pty')
        else:
            _setup_channel_xml()
        logging.debug("Test VM XML is %s" % vm_xml)

        if not _define_and_check():
            logging.debug("Can't define the VM, exiting.")
            return

        if not auto_gen_port:
            _check_xml(test)

        if not _start_and_check():
            logging.debug("Can't start the VM, exiting.")
            return

        if target_type == 'virtio':
            _check_virtio_guest_channel(test)

        if auto_gen_port:
            found_dev = _verify_attach_channel_device('pty', '1')
            if not found_dev:
                test.fail("Failed to attach a device with a fixed port id")

            xml_file = os.path.join(tmp_dir, "xml_%s" % 'spicevmc')
            prepare_channel_xml(xml_file, 'spicevmc')

            found_dev = _verify_attach_channel_device('spicevmc', '3')
            if not found_dev:
                test.fail("Failed to autogen port id during attach device")
    finally:
        if auto_gen_port and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        vm_xml_backup.sync()
