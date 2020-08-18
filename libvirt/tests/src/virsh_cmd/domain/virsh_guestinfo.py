import logging
import re
import os
import json
import datetime

from virttest import virsh
from virttest import libvirt_version
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test guestinfo command, make sure that all supported options work well
    """
    def check_guest_os_info():
        os_info = {}
        session = vm.wait_for_login()
        try:
            output = session.cmd_output('cat /etc/os-release').strip().splitlines()
            os_info_dict = dict(item.split("=") for item in output if item)
            os_info["os.id"] = os_info_dict["ID"].strip('"')
            os_info["os.name"] = os_info_dict["NAME"].strip('"')
            os_info["os.pretty-name"] = os_info_dict["PRETTY_NAME"].strip('"')
            os_info["os.version"] = os_info_dict["VERSION"].strip('"')
            os_info["os.version-id"] = os_info_dict["VERSION_ID"].strip('"')
            os_info["os.machine"] = session.cmd_output('uname -m').strip()
            os_info["os.kernel-release"] = session.cmd_output('uname -r').strip()
            os_info["os.kernel-version"] = session.cmd_output('uname -v').strip()
        finally:
            session.close()
        return os_info

    def parse_timezone_info():
        session = vm.wait_for_login()
        try:
            output = session.cmd_output('timedatectl').strip().splitlines()
            out_dict = dict(item.split(": ") for item in output if item)
            tz_dict = dict((x.strip(), y.strip()) for x, y in out_dict.items())
            tz_info = re.search(r"\((.+)\)", tz_dict["Time zone"]).group(1)
            name, offset = tz_info.split(', ')
        finally:
            session.close()
        return name, offset

    def check_guest_timezone_info():
        timezone_info = {}
        timezone_name, hour_offset = parse_timezone_info()
        timezone_info["timezone.name"] = timezone_name
        sign = 1 if int(hour_offset) > 0 else -1
        second_offset = int(hour_offset[-4:-2])*3600 + int(hour_offset[-2:]*60)
        timezone_info["timezone.offset"] = str(sign * second_offset)
        return timezone_info

    def check_guest_hostname_info():
        hostname_info = {}
        session = vm.wait_for_login()
        try:
            hostname_info['hostname'] = session.cmd_output('hostname').strip()
        finally:
            session.close()
        return hostname_info

    def add_user(name, passwd):
        session = vm.wait_for_login()
        try:
            session.cmd_output('useradd %s' % name)
            logging.debug('now system users are %s', session.cmd_output('users'))
        finally:
            session.close()
        virsh.set_user_password(vm_name, name, passwd, debug=True)

    def convert_to_timestamp(t_str):
        dt = dateutil.parser.parse(t_str)
        timestamp = datetime.datetime.timestamp(dt)
        return timestamp

    def check_guest_user_info():
        user_info = {}
        session = vm.wait_for_login()
        try:
            output = session.cmd_output('last --time-format iso').strip().splitlines()
            users_login = [item for item in output if re.search(r'still logged in', item)]
            users_login_list = [re.split(r"\s{2,}", item) for item in users_login]
            users_login_info = [[item[0], convert_to_timestamp(item[-2])] for item in users_login_list]
            sorted_user_info = sorted(users_login_info, key=lambda item: item[1])
            count = -1
            users_list = []
            for user, login_time in sorted_user_info:
                if user not in users_list:
                    users_list.append(user)
                    count += 1
                    user_key = "user." + str(count) + ".name"
                    login_time_key = "user." + str(count) + ".login-time"
                    user_info[user_key] = user
                    user_info[login_time_key] = login_time
        finally:
            session.close()
        return len(users_list), user_info

    def check_disk_size(ses, disk):
        disk_size = ses.cmd_output('df %s' % disk).strip().splitlines()[-1]
        total_size = disk_size.split()[1]
        used_size = disk_size.split()[2]
        return total_size, used_size

    def check_guest_filesystem_info():
        fs_info = {}
        count = -1
        session = vm.wait_for_login()
        try:
            lsblk_cmd = 'lsblk -Jp -o KNAME,FSTYPE,TYPE,MOUNTPOINT,PKNAME,SERIAL'
            output = json.loads(session.cmd_output(lsblk_cmd).strip())

            fs_unsorted = [item for item in dict(output)['blockdevices']
                           if item['mountpoint'] not in [None, '[SWAP]']]
            fs = sorted(fs_unsorted, key=lambda item: item['kname'])

            fs_info['fs.count'] = str(len(fs))
            for item in fs:
                total_size, used_size = check_disk_size(session, item['kname'])
                count += 1
                key_prefix = 'fs.' + str(count) + '.'
                fs_info[key_prefix + 'name'] = os.path.basename(item['kname'])
                fs_info[key_prefix + 'mountpoint'] = item['mountpoint']
                fs_info[key_prefix + 'fstype'] = item['fstype']
                fs_info[key_prefix + 'total-bytes'] = str(int(total_size)*1024)
                fs_info[key_prefix + 'used-bytes'] = str(int(used_size)*1024)
                disks_count = item['pkname'].count('/dev')
                fs_info[key_prefix + 'disk.count'] = str(disks_count)
                for i in range(disks_count):
                    fs_info[key_prefix + "disk." + str(i) + ".alias"] = re.search(
                        r"(\D+)", os.path.basename(item['pkname'])).group(0)
                    if item['serial']:
                        fs_info[key_prefix + "disk." + str(i) + ".serial"] = item['serial']
                if item['type'] == "lvm":
                    fs_info[key_prefix + "disk." + str(i) + ".device"] = item['pkname']
                else:
                    fs_info[key_prefix + "disk." + str(i) + ".device"] = item['kname']
        finally:
            session.close()
        return fs_info

    vm_name = params.get("main_vm")
    option = params.get("option")
    added_user_name = params.get("added_user_name")
    added_user_passwd = params.get("added_user_passwd")
    status_error = ("yes" == params.get("status_error", "no"))
    start_ga = ("yes" == params.get("start_ga", "yes"))
    prepare_channel = ("yes" == params.get("prepare_channel", "yes"))

    if not libvirt_version.version_compare(6, 0, 0):
        test.cancel("Guestinfo command is not supported before version libvirt-6.0.0 ")
    import dateutil.parser

    try:
        vm = env.get_vm(vm_name)
        if start_ga and prepare_channel:
            vm.prepare_guest_agent(start=True, channel=True)

        if "user" in option:
            add_user(added_user_name, added_user_passwd)
            added_user_session = vm.wait_for_login(username=added_user_name,
                                                   password=added_user_passwd)
            root_session = vm.wait_for_login()

        result = virsh.guestinfo(vm_name, option, ignore_status=True, debug=True)
        libvirt.check_exit_status(result, status_error)

        out = result.stdout.strip().splitlines()
        out_dict = dict(item.split(" : ") for item in out)
        info_from_agent_cmd = dict((x.strip(), y.strip()) for x, y in out_dict.items())
        logging.debug("info from the guest is %s", info_from_agent_cmd)

        func_name = "check_guest_%s_info" % option[2:]
        if "user" not in option:
            info_from_guest = locals()[func_name]()
            logging.debug('%s_info_from_guest is %s', option[2:], info_from_guest)
            if info_from_guest != info_from_agent_cmd:
                test.fail("The %s info get from guestinfo cmd is not correct." % option[2:])
        else:
            user_count, user_info_from_guest = check_guest_user_info()
            if user_count != int(info_from_agent_cmd["user.count"]):
                test.fail("The num of active users returned from guestinfo "
                          "is not correct.")
            for key, value in user_info_from_guest.items():
                # login time returned from guestinfo cmd is with milliseconds,
                # so it may cause at most 1 second deviation
                if "name" in key:
                    if value != info_from_agent_cmd[key]:
                        test.fail("The active users get from guestinfo "
                                  "are not correct.")
                if "login-time" in key:
                    if abs(float(value) - int(info_from_agent_cmd[key])/1000) > 1.0:
                        test.fail("The login time of active users get from guestinfo "
                                  "is not correct.")
    finally:
        if "user" in option:
            added_user_session.close()
            root_session.cmd('userdel -f %s' % added_user_name)
            root_session.close()
        vm.destroy()
