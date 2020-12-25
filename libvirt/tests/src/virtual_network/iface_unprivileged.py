import logging
import shutil
import aexpect

from avocado.utils import process

from virttest import libvirt_version
from virttest import remote
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test interface with unprivileged user
    """

    def create_bridge(br_name, iface_name):
        """
        Create bridge attached to physical interface
        """
        # Make sure the bridge not exist
        if libvirt.check_iface(br_name, "exists", "--all"):
            test.cancel("The bridge %s already exist" % br_name)

        # Create bridge
        utils_package.package_install('tmux')
        cmd = 'tmux -c "ip link add name {0} type bridge; ip link set {1} up;' \
              ' ip link set {1} master {0}; ip link set {0} up; pkill dhclient; ' \
              'sleep 6; dhclient {0}; ifconfig {1} 0"'.format(br_name, iface_name)
        process.run(cmd, shell=True, verbose=True)

    def check_ping(dest_ip, ping_count, timeout, src_ip=None, session=None,
                   expect_success=True):
        """
        Check if ping result meets expectaion
        """
        status, output = utils_net.ping(dest=dest_ip, count=ping_count,
                                        interface=src_ip, timeout=timeout,
                                        session=session, force_ipv4=True)
        success = True if status == 0 else False

        if success != expect_success:
            test.fail('Ping result not met expectation, '
                      'actual result is {}'.format(success))

    if not libvirt_version.version_compare(5, 6, 0):
        test.cancel('Libvirt version is too low for this test.')

    vm_name = params.get('main_vm')
    rand_id = '_' + utils_misc.generate_random_string(3)

    upu_vm_name = 'upu_vm' + rand_id
    user_vm_name = params.get('user_vm_name', 'non_root_vm')
    bridge_name = params.get('bridge_name', 'test_br0') + rand_id
    device_type = params.get('device_type', '')
    iface_name = utils_net.get_net_if(state="UP")[0]
    tap_name = params.get('tap_name', 'mytap0') + rand_id
    macvtap_name = params.get('macvtap_name', 'mymacvtap0') + rand_id
    remote_ip = params.get('remote_ip')
    up_user = params.get('up_user', 'test_upu') + rand_id
    case = params.get('case', '')

    # Create unprivileged user
    logging.info('Create unprivileged user %s', up_user)
    process.run('useradd %s' % up_user, shell=True, verbose=True)
    root_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    upu_args = {
        'unprivileged_user': up_user,
        'ignore_status': False,
        'debug': True,
    }

    try:
        # Create vm as unprivileged user
        logging.info('Create vm as unprivileged user')
        upu_vmxml = root_vmxml.copy()

        # Prepare vm for unprivileged user
        xml_devices = upu_vmxml.devices
        disks = xml_devices.by_device_tag("disk")
        for disk in disks:
            ori_path = disk.source['attrs'].get('file')
            if not ori_path:
                continue

            file_name = ori_path.split('/')[-1]
            new_disk_path = '/home/{}/{}'.format(up_user, file_name)
            logging.debug('New disk path:{}'.format(new_disk_path))

            # Copy disk image file and chown to make sure that
            # unprivileged user has access
            shutil.copyfile(ori_path, new_disk_path)
            shutil.chown(new_disk_path, up_user, up_user)

            # Modify xml to set new path of disk
            disk_index = xml_devices.index(disk)
            source = xml_devices[disk_index].source
            new_attrs = source.attrs
            new_attrs['file'] = new_disk_path
            source.attrs = new_attrs
            xml_devices[disk_index].source = source
            logging.debug(xml_devices[disk_index].source)

        upu_vmxml.devices = xml_devices

        new_xml_path = '/home/{}/upu.xml'.format(up_user)
        shutil.copyfile(upu_vmxml.xml, new_xml_path)

        # Define vm for unprivileged user
        virsh.define(new_xml_path, **upu_args)
        virsh.domrename(vm_name, upu_vm_name, **upu_args)
        logging.debug(virsh.dumpxml(upu_vm_name, **upu_args))
        upu_vmxml = vm_xml.VMXML()
        upu_vmxml.xml = virsh.dumpxml(upu_vm_name, **upu_args).stdout_text

        if case == 'precreated':
            if device_type == 'tap':
                # Create bridge
                create_bridge(bridge_name, iface_name)

                # Create tap device
                tap_cmd = 'ip tuntap add mode tap user {user} group {user} ' \
                          'name {tap};ip link set {tap} up;ip link set {tap} ' \
                          'master {br}'.format(tap=tap_name, user=up_user,
                                               br=bridge_name)

                # Execute command as root
                process.run(tap_cmd, shell=True, verbose=True)

            if device_type == 'macvtap':
                # Create macvtap device
                mac_addr = utils_net.generate_mac_address_simple()
                macvtap_cmd = 'ip link add link {iface} name {macvtap} address' \
                              ' {mac} type macvtap mode bridge;' \
                              'ip link set {macvtap} up'.format(
                               iface=iface_name,
                               macvtap=macvtap_name,
                               mac=mac_addr)
                process.run(macvtap_cmd, shell=True, verbose=True)
                cmd_get_tap = 'ip link show {} | head -1 | cut -d: -f1'.format(macvtap_name)
                tap_index = process.run(cmd_get_tap, shell=True, verbose=True).stdout_text.strip()
                device_path = '/dev/tap{}'.format(tap_index)
                logging.debug('device_path: {}'.format(device_path))
                # Change owner and group for device
                process.run('chown {user} {path};chgrp {user} {path}'.format(
                    user=up_user, path=device_path),
                    shell=True, verbose=True)
                # Check if device owner is changed to unprivileged user
                process.run('ls -l %s' % device_path, shell=True, verbose=True)

            # Modify interface
            all_devices = upu_vmxml.devices
            iface_list = all_devices.by_device_tag('interface')
            if not iface_list:
                test.error('No iface to modify')
            iface = iface_list[0]

            # Remove other interfaces
            for ifc in iface_list[1:]:
                all_devices.remove(ifc)

            if device_type == 'tap':
                dev_name = tap_name
            elif device_type == 'macvtap':
                dev_name = macvtap_name
            else:
                test.error('Invalid device type: {}'.format(device_type))

            if_index = all_devices.index(iface)
            iface = all_devices[if_index]
            iface.type_name = 'ethernet'
            iface.target = {
                'dev': dev_name,
                'managed': 'no'
            }

            if device_type == 'macvtap':
                iface.mac_address = mac_addr
            logging.debug(iface)

            upu_vmxml.devices = all_devices
            logging.debug(upu_vmxml)

            # Define updated xml
            shutil.copyfile(upu_vmxml.xml, new_xml_path)
            upu_vmxml.xml = new_xml_path
            virsh.define(new_xml_path, **upu_args)

            # Switch to unprivileged user and modify vm's interface
            # Start vm as unprivileged user and test network
            virsh.start(upu_vm_name, debug=True, ignore_status=False,
                        unprivileged_user=up_user)
            cmd = ("su - %s -c 'virsh console %s'"
                   % (up_user, upu_vm_name))
            session = aexpect.ShellSession(cmd)
            session.sendline()
            remote.handle_prompts(session, params.get("username"),
                                  params.get("password"), r"[\#\$]\s*$", 60)
            logging.debug(session.cmd_output('ifconfig'))
            check_ping(remote_ip, 5, 10, session=session)
            session.close()

    finally:
        if 'upu_virsh' in locals():
            virsh.destroy(upu_vm_name, unprivileged_user=up_user)
            virsh.undefine(upu_vm_name, unprivileged_user=up_user)
        if case == 'precreated':
            try:
                if device_type == 'tap':
                    process.run('ip tuntap del mode tap {}'.format(tap_name), shell=True, verbose=True)
                elif device_type == 'macvtap':
                    process.run('ip l del {}'.format(macvtap_name), shell=True, verbose=True)
            except Exception:
                pass
            finally:
                cmd = 'tmux -c "ip link set {1} nomaster;  ip link delete {0};' \
                      'pkill dhclient; sleep 6; dhclient {1}"'.format(bridge_name, iface_name)
                process.run(cmd, shell=True, verbose=True, ignore_status=True)
        process.run('pkill -u {0};userdel -f -r {0}'.format(up_user), shell=True, verbose=True, ignore_status=True)
