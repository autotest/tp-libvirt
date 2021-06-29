import logging
import os
import re

from avocado.utils import process
from avocado.utils import path as utils_path
from virttest import virsh

from virttest import libvirt_version


def get_avail_caps(all_caps):
    """
    Get all available capabilities on the host.

    :param all_caps: A list contains all currently known capabilities.
    :return:         A list contains all available capabilities.
    """
    avail_caps = []
    for cap in all_caps:
        result = virsh.nodedev_list(cap=cap)
        if result.exit_status == 0:
            avail_caps.append(cap)
    return avail_caps


def get_storage_devices():
    """
    Retrieve storage devices list from sysfs.

    :return:      A list contains retrieved storage device names with
                  the same format in virsh.
    """
    devices = []
    try:
        utils_path.find_command('udevadm')
        storage_path = '/sys/class/block'
        if not os.path.exists(storage_path):
            logging.debug(
                'Storage device path %s doesn`t exists!', storage_path)
            return []
        for device in os.listdir(storage_path):
            info = process.run(
                               'udevadm info %s' % os.path.join(storage_path, device),
                               timeout=5, ignore_status=True, shell=True).stdout_text
            # Only disk devices are list, not partition
            dev_type = re.search(r'(?<=E: DEVTYPE=)\S*', info)
            dev_real_virtual = re.search(r'P: /devices/virtual', info)
            if dev_type and not dev_real_virtual:
                if dev_type.group(0) == 'disk':
                    # Get disk serial
                    dev_id = re.search(r'(?<=E: ID_SERIAL=)\S*', info)
                    if dev_id:
                        serial = dev_id.group(0)
                        dev_name = 'block_' + device.replace(':', '_')
                        dev_name = re.sub(
                            r'\W', '_', 'block_%s_%s' % (device, serial))
                        devices.append(dev_name)
                    else:
                        dev_name = 'block_' + device.replace(':', '_')
                        devices.append(dev_name)
    except utils_path.CmdNotFoundError:
        logging.warning('udevadm not found! Skipping storage test!')
        logging.warning('You can try install it using `yum install udev`')
    return devices


def get_net_devices():
    """
    Retrieve net devices list from sysfs.

    :return:      A list contains retrieved net device names with
                  the same format in virsh.
    """
    net_path = '/sys/class/net'
    devices = []
    if os.path.exists(net_path):
        for device in os.listdir(net_path):
            if device == 'bonding_masters':
                continue
            try:
                dev_dir = os.path.join('/sys/class/net', device)
                # Ignore bridge devices
                if os.path.exists(os.path.join(dev_dir, 'bridge')):
                    continue
                # Ignore bonding devices
                if os.path.exists(os.path.join(dev_dir, 'bonding')):
                    continue
                # Ignore vlan devices
                if [x for x in os.listdir(dev_dir) if x.startswith('lower')]:
                    continue
                address_file = os.path.join(dev_dir, 'address')
                mac = ''
                with open(address_file, 'r') as f_addr:
                    mac = f_addr.read().strip()
            except IOError:
                print('Cannot get address for device %s' % device)
            if mac:
                dev_name = re.sub(r'\W', '_', 'net_%s_%s' % (device, mac))
            else:
                dev_name = re.sub(r'\W', '_', 'net_%s' % device)
            devices.append(dev_name)
    return devices


def get_ccw_io_devices():
    """
    Retrieve only online ccw devices

    :return: A list of ccw devices in the same format as nodedev-list
    """
    ccw_base_path = '/sys/bus/ccw/devices'
    devices = []
    if not os.path.exists(ccw_base_path):
        return devices
    for device in os.listdir(ccw_base_path):
        online_attribute = os.path.join(ccw_base_path, device, "online")
        online_value = process.run("cat %s" % online_attribute,
                                   timeout=5, ignore_status=True,
                                   shell=True).stdout_text
        is_online = "1" == online_value.strip()
        if is_online:
            dev_name = re.sub(r'\W', '_', 'ccw_' + device)
            devices.append(dev_name)
    return devices


def get_css_io_devices():
    """
    Retrieve css devices apt for I/O passthrough.

    :return: A list of css devices in the same format as nodedev-list
    """
    css_base_path = '/sys/bus/css'
    io_subchannel_path = '/sys/bus/css/drivers/io_subchannel'
    vfio_ccw_path = '/sys/bus/css/drivers/vfio_ccw'
    devices = []
    if os.path.exists(css_base_path):
        for path in [io_subchannel_path, vfio_ccw_path]:
            if not os.path.exists(path):
                continue
            for device in os.listdir(path):
                if re.match(r'\d+\.\d+\.\d+', device):
                    dev_name = re.sub(r'\W', '_', 'css_' + device)
                    devices.append(dev_name)
    return devices


def get_devices_by_cap(cap):
    """
    Retrieve devices list from sysfs for specific capability.

    Implemented capabilities are:
    'system', 'pci', 'usb_device', 'usb', 'net', 'scsi_host',
    'scsi_target', 'scsi', 'storage', 'scsi_generic', 'ccw', 'css'.

    Not implemented capabilities are:
    'vports', 'fc_host'.

    :params cap:  The capability of devices to be retrieve.
    :return:      A list contains retrieved device names with
                  the same format in virsh. If the cap is not
                  implemented, an empty list is returned.
    """
    cap_map = {
        'pci': ('/sys/bus/pci/devices/', 'pci_', '.*'),
        'scsi_host': ('/sys/class/scsi_host', 'scsi_', '.*'),
        'scsi': ('/sys/class/scsi_device', 'scsi_', '.*'),
        'scsi_generic': ('/sys/class/scsi_generic', 'scsi_generic_', '.*'),
        'scsi_target': ('/sys/bus/scsi/devices', 'scsi_', r'target.*'),
        'usb': ('/sys/bus/usb/devices', 'usb_',
                # Match any string that does not have :X.X at the end
                r'\S+:\d+\.\d+'),
        'usb_device': ('/sys/bus/usb/devices', 'usb_',
                       # Match any string that does not have :X.X at the end
                       r'^((?!\S+:\d+\.\d+).)*$')
    }
    if cap in cap_map:
        devices = []
        path, header, pattern = cap_map[cap]
        if os.path.exists(path):
            for device in os.listdir(path):
                if re.match(pattern, device):
                    dev_name = re.sub(r'\W', '_', header + device)
                    devices.append(dev_name)
    elif cap == 'system':
        devices = ['machine']
    elif cap == 'net':
        devices = get_net_devices()
    elif cap == 'storage':
        devices = get_storage_devices()
    elif cap == 'css':
        devices = get_css_io_devices()
    elif cap == 'ccw':
        devices = get_ccw_io_devices()
    else:
        devices = []
    return devices


def run(test, params, env):
    """
    Test command: nodedev-list [--tree] [--cap <string>]

    1) Run nodedev-list command and check return code.
    2) If `cap_option == one`, results are also compared
       with devices get from sysfs.
    """
    def _check_result(cap, ref_list, result, mode):
        """
        Check test result agains a device list retrived from sysfs.

        :param cap:        Capability being checked, current available caps are
                           defined in variable `caps`.
        :param ref_list:   Reference device list retrived from sysfs.
        :param result:     Stdout returned from virsh nodedev-list command.
        :param mode:       How to compare sysfs info with command output:
                           "exact" or "similar".
        """
        check_list = result.strip().splitlines()
        are_not_equivalent = True
        if mode == "similar":
            listed = [x for x in ref_list if x in result]
            all_sysfs_info_listed = len(ref_list) == len(listed)
            same_number_of_devices = len(ref_list) == len(check_list)
            are_not_equivalent = (not all_sysfs_info_listed or
                                  not same_number_of_devices)
        elif mode == "exact":
            are_not_equivalent = set(ref_list) != set(check_list)
        else:
            logging.error("Unknown comparison mode in result check: %s",
                          mode)
            return False

        uavail_caps = ['system', 'vports', 'fc_host']

        if are_not_equivalent and cap not in uavail_caps:
            logging.error('Difference in capability %s:', cap)
            logging.error('Expected devices: %s', ref_list)
            logging.error('Result devices  : %s', check_list)
            return False
        return True

    mode = params.get("comparison_mode", "exact")
    all_caps = ['system', 'pci', 'usb_device', 'usb', 'net', 'scsi_host',
                'scsi_target', 'scsi', 'storage', 'fc_host', 'vports',
                'scsi_generic', 'ccw', 'css']
    expect_succeed = params.get('expect_succeed', 'yes')
    tree_option = params.get('tree_option', 'off')
    cap_option = params.get('cap_option', 'off')
    caps = get_avail_caps(all_caps)
    check_failed = False

    # acl polkit params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    virsh_dargs = {}
    if params.get('setup_libvirt_polkit') == 'yes':
        virsh_dargs['unprivileged_user'] = unprivileged_user
        virsh_dargs['uri'] = uri

    tree = (tree_option == 'on')
    if cap_option == 'one':
        devices = {}
        for cap in caps:
            devices[cap] = get_devices_by_cap(cap)

        for cap in devices:
            logging.debug(cap + ':')
            for device in devices[cap]:
                logging.debug('    ' + device)

        for cap in caps:
            result = virsh.nodedev_list(tree=tree, cap=cap, **virsh_dargs)
            if result.exit_status != 0 and expect_succeed == 'yes':
                break
            elif result.exit_status == 0 and expect_succeed == 'no':
                break
            if not _check_result(cap, devices[cap], result.stdout.strip(), mode):
                check_failed = True
                break
    else:
        cap = ''
        if cap_option != 'off':
            if cap_option == 'multi':
                cap = ','.join(caps)
            elif cap_option == 'long':
                cap = ','.join(['pci', 'usb', 'net', 'storage', 'scsi'] * 5000)
            else:
                cap = cap_option
        result = virsh.nodedev_list(tree=tree, cap=cap, **virsh_dargs)

    logging.debug(result)
    if expect_succeed == 'yes':
        if result.exit_status != 0:
            test.fail(
                'Expected succeed, but failed with result:\n%s' % result)
    elif expect_succeed == 'no':
        if result.exit_status == 0:
            test.fail(
                'Expected fail, but succeed with result:\n%s' % result)
    if check_failed:
        test.fail('Check failed. result:\n%s' % result)
