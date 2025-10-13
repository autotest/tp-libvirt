"""
This module provides the interfaces to do some operations only in windows guest.

"""

import re

from virttest import utils_misc


def pscp_file(guest_session, remote_file, local_path, remote_ip, username,
              password, port=22):
    """
    Pscp file from remote linux host to local windows guest.

    :param guest_session: The guest session object.
    :param remote_file: The file transferred from linux host to windows guest.
    :param local_path: The path that transferred file will be in.
    :param remote_ip: The linux host ip.
    :param username: The linux host username.
    :param password: The linux host password
    :param port: The port that pscp process default use
    :return: The pscp result.
    """
    guest_session.guest_cmd_output(cmd='cd C:\\')
    status, pscp_path = guest_session.guest_cmd_status_output(
        cmd='dir pscp.exe /s /b')
    if pscp_path == 'File Not Found' or status:
        guest_session.test_fail('There is no pscp to transfer files '
                                'from host to win guest')
    cmd = 'echo y | "%s" -P %s -pw %s %s@%s:"%s" "%s"' \
          % (pscp_path, port, password, username, remote_ip, remote_file, local_path)
    return guest_session.guest_cmd_status(cmd)


def windrv_verify_running(serial, driver_name, timeout=300):
    """
    Check if driver is running for windows guest within a period time.

    :param serial: The guest serial object.
    :param driver_name: The driver which needs to check.
    :param timeout: Timeout in seconds.
    """

    def _check_driver_stat():
        """
        Check if driver is in Running status.

        """
        output = serial.serial_cmd_output(driver_check_cmd, timeout=timeout)
        if "Running" in output:
            return True
        return False

    serial.test_print("Check %s driver state." % driver_name)
    driver_check_cmd = (r'wmic sysdriver where PathName="C:\\Windows\\System32'
                        r'\\drivers\\%s.sys" get State /value') % driver_name

    if not utils_misc.wait_for_output(_check_driver_stat, timeout, 0, 5):
        serial.test_error("%s driver is not running" % driver_name)


def _check_driver_verifier(serial, driver_name, timeout=300,
                           serial_debug=False):
    """
    Check driver verifier status

    :param serial: The guest serial object.
    :param driver_name: The driver which needs to check.
    :param timeout: Timeout in seconds.
    :param serial_debug: print only in serial log if True, otherwise print in
                        short and long logs
    :return driver_exist, output: True if driver_name enabled, otherwise False;
                                  return all enabled drivers via output
    """
    serial.test_print("Check %s driver verifier status" % driver_name)
    query_cmd = "verifier /querysettings"
    o = serial.serial_cmd_output(query_cmd, timeout=timeout,
                                 serial_debug=serial_debug).splitlines()
    for i in o:
        if re.search(r'Verified Drivers:', i, re.I):
            id = o.index(i)
            output = o[-(len(o)-id-1):]
            if re.search(r'\w:\\.*>', output[-1].strip()):
                output = output[0:-1]
            break
    else:
        serial.test_error('No matched str "Verified Drivers" under %s' % o)
    for i in output:
        if driver_name in i.strip():
            driver_exist = True
            break
    else:
        driver_exist = False
    return driver_exist, output


def setup_win_driver_verifier(serial, driver_name, timeout=300,
                              serial_debug=False):
    """
    Enable driver verifier for windows guest.

    :param serial: The guest serial object.
    :param driver_name: The driver which needs to check.
    :param timeout: Timeout in seconds.
    :param serial_debug: print only in serial log if True, otherwise print in
                        short and long logs
    """
    verifier_status = _check_driver_verifier(serial, driver_name,
                                             serial_debug=serial_debug)[0]
    if not verifier_status:
        serial.test_print("Enable %s driver verifier" % driver_name)
        verifier_setup_cmd = "verifier /standard /driver %s.sys" % driver_name
        serial.serial_cmd_output(verifier_setup_cmd, timeout=timeout,
                                 serial_debug=serial_debug)
        serial.serial_reboot_vm()
        verifier_status, output = _check_driver_verifier(serial, driver_name,
                                                     serial_debug=serial_debug)
        if not verifier_status:
            msg = "%s verifier is not enabled, details: %s" % (driver_name,
                                                               output)
            serial.test_error(msg)
    serial.test_print("%s verifier is enabled already" % driver_name)


def windrv_check_running_verifier(serial, driver_name, timeout=300):
    """
    Check whether the windows driver is running, then enable driver verifier.

    :param serial: The guest serial object.
    :param driver_name: The name of windows driver.
    :param timeout: Timeout in seconds.
    :return: The new serial
    """
    windrv_verify_running(serial, driver_name, timeout)
    return setup_win_driver_verifier(serial, driver_name, timeout)


def get_win_disk_vol(session, condition="VolumeName='WIN_UTILS'"):
    """
    Getting logicaldisk drive letter in windows guest.

    :param session: The guest session object.
    :param condition: supported condition via cmd "wmic logicaldisk list".

    :return: volume ID.
    """
    cmd = "wmic logicaldisk where (%s) get DeviceID" % condition
    output = session.cmd_output(cmd, timeout=120)
    device = re.search(r'(\w):', output, re.M)
    if not device:
        return ""
    return device.group(1)


def get_winutils_vol(session, label="WIN_UTILS"):
    """
    Return Volume ID of winutils CDROM ISO file should be create via command
    ``mkisofs -V $label -o winutils.iso``.

    :param session: The guest session object.
    :param label: volume label of WIN_UTILS.iso.

    :return: volume ID.
    """
    return utils_misc.wait_for_output(
            lambda: get_win_disk_vol(session, "VolumeName='%s'" % label), 240)


def set_winutils_letter(session, cmd, label="WIN_UTILS"):
    """
    Replace label in command to real winutils CDROM drive letter.

    :param session: The guest session object.
    :param cmd: cmd path in winutils.iso
    :param label: volume label of WIN_UTILS.iso
    """
    if label in cmd:
        return cmd.replace(label, get_winutils_vol(session))
    return cmd


def generate_random_data(session, timeout=360):
    """
    Generate random data for windows.

    :param session: The guest session.
    :param timeout: Timeout in seconds.
    """
    read_rng_cmd = "WIN_UTILS:\\random_%PROCESSOR_ARCHITECTURE%.exe"
    read_rng_cmd = set_winutils_letter(session, read_rng_cmd)
    output = session.guest_cmd_output(read_rng_cmd, timeout=timeout)
    if len(re.findall(r'0x\w', output, re.M)) < 2:
        session.test_error("Unable to read random numbers "
                           "from guest: %s" % output)


def get_product_dirname_iso(session):
    """
    Get product directory's name.

    :param session: Session object.
    :return: Directory's name.
    """
    product_name = session.guest_cmd_output('wmic os get caption')
    match = re.search(r"Windows((?: )Serverr?)? (\S+)(?: (R2))?",
                      product_name, re.I)
    if not match:
        return None
    server, name, suffix = match.groups()
    server = server if server else ""
    suffix = suffix if suffix else ""
    if not name:
        return None
    if server:
        if len(name) == 4:
            name = re.sub("0+", "k", name)
    else:
        if name[0].isdigit():
            name = "w" + name
    return name + suffix


def install_driver(session, serial, driver_name, device_hwid, timeout=360):
    """
    Install drivers for windows.

    :param session: The guest session object.
    :param serial: The guest serial object.
    :param driver_name: driver name.
    :param device_hwid: device hwid.
    :param timeout: Timeout in seconds.
    """
    session.test_print("Installing target driver")
    guest_arch = session.guest_cmd_output('wmic OS get OSArchitecture')
    if '64' in guest_arch:
        devcon_dirname = 'win7_amd64'
        arch = 'amd64'
    else:
        devcon_dirname = 'win7_x86'
        arch = 'x86'
    devcon_path = "WIN_UTILS:\\devcon\\%s\\devcon.exe" % devcon_dirname
    devcon_path = set_winutils_letter(session, devcon_path)
    guest_name = get_product_dirname_iso(session)
    viowin_ltr = get_win_disk_vol(session, condition="VolumeName like 'virtio-win%'")
    inf_middle_path = '%s\\%s' % (guest_name, arch)
    inf_find_cmd = 'dir /b /s %s:\\%s.inf | findstr "\\%s\\\\"'
    inf_find_cmd %= (viowin_ltr, driver_name, inf_middle_path)
    inf_path = session.cmd(inf_find_cmd, timeout=timeout).strip()
    session.test_print("Found inf file '%s'", inf_path)
    installed_any = False
    for hwid in device_hwid.split():
        output = session.cmd_output("%s find %s" % (devcon_path, hwid))
        if re.search("No matching devices found", output, re.I):
            continue
        inst_cmd = "%s updateni %s %s" % (devcon_path, inf_path, hwid)
        status = serial.serial_cmd_status(inst_cmd, timeout=timeout)
        # acceptable status: OK(0), REBOOT(1)
        if status > 1:
            session.test_fail("Failed to install driver '%s'" % driver_name)
        installed_any |= True

    if not installed_any:
        session.test_error("Failed to find target devices "
                           "by hwids: '%s'" % device_hwid)


def verify_target_driver(session, driver_name, device_name, timeout=360):
    """
    Verifying whether the driver installed is the target one.

    :param session: The guest session object.
    :param driver_name: driver name.
    :param device_name: device name.
    :param timeout: Timeout in seconds.
    """
    session.test_print("Verifying target driver")
    guest_arch = session.guest_cmd_output('wmic OS get OSArchitecture')
    arch = 'amd64' if '64' in guest_arch else 'x86'
    guest_name = get_product_dirname_iso(session)
    viowin_ltr = get_win_disk_vol(session, condition="VolumeName like 'virtio-win%'")
    inf_middle_path = '%s\\%s' % (guest_name, arch)
    inf_find_cmd = 'dir /b /s %s:\\%s.inf | findstr "\\%s\\\\"'
    inf_find_cmd %= (viowin_ltr, driver_name, inf_middle_path)
    inf_path = session.cmd(inf_find_cmd, timeout=timeout).strip()
    expected_ver = session.guest_cmd_output("type %s | findstr /i /r DriverVer.*=" %
                                            inf_path, timeout=timeout)
    expected_ver = expected_ver.strip().split(",", 1)[-1]
    expected_ver = str(expected_ver)
    if not expected_ver:
        session.test_error("Failed to find driver version from inf file")
    session.test_print("Target version is '%s'", expected_ver)
    cmd = "wmic path win32_pnpsigneddriver where (DeviceName like '%s') \
          get DriverVersion /format:list" % device_name
    ver_list = session.guest_cmd_output(cmd)
    ver_list = str(ver_list)
    if expected_ver not in ver_list:
        session.test_fail("The expected driver version is '%s', but "
                          "found '%s'" % (expected_ver, ver_list))


def create_win_file(monitor, filename, size):
    """
    Create file via fsutil cmd in windows guest.

    :param monitor: The guest serial object.
    :param filename: the new file name.
    :param size: the size of file.
    """
    monitor.serial_cmd_output(cmd="fsutil file createNew %s %s"
                                  % (filename, size), serial_debug=False)
    if not monitor.serial_cmd_status_output('dir |findstr /I %s' % filename)[1]:
        monitor.test_error('Fail to create %s in windows guest' % filename)
