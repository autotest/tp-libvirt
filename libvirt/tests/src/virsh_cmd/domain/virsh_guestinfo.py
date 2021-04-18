import logging
import re
import os
import json
import datetime

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_misc
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test guestinfo command, make sure that all supported options work well
    """
    def hotplug_disk(disk_name):
        """
        hotplug a disk to guest

        :param disk_name: the name of the disk be hotplugged
        """
        device_source = os.path.join(data_dir.get_tmp_dir(), disk_name)
        libvirt.create_local_disk("file", device_source, size='1')
        try:
            res = virsh.attach_disk(vm_name, device_source,
                                    disk_target_name, debug=True)
            utils_misc.wait_for(lambda: (res.stdout ==
                                "Disk attached successfully"), 10)
        except Exception:
            test.error("Can not attach %s to the guest" % disk_target_name)

    def check_attached_disk_info(disk_info, target_name):
        """
        Check the info of the attached disk

        :param disk_info: the disk info returned from virsh guestinfo --disk
        :param target_name: the target name for the attached disk
        :return: the attached disk info returned from virsh guestinfo --disk
        """
        attached_disk_info_reported = False
        disk_logical_name = '/dev/%s' % target_name
        for i in range(int(disk_info['disk.count'])):
            prefix = 'disk.' + str(i) + '.'
            try:
                if disk_info[prefix + 'alias'] == target_name:
                    if (disk_info[prefix + 'name'] == disk_logical_name and
                            disk_info[prefix + 'partition'] == 'no'):
                        attached_disk_info_reported = True
                        break
            except KeyError:
                logging.error("Num %i is not the attached disk", i)
        return attached_disk_info_reported

    def check_guest_os_info():
        """
        Check the info of guest os from guest side

        :return: the guest os info from guest side
        """
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
        """
        Parse the info returned from timedatectl cmd

        :return: the guest timezone name and offset to UTC time
        """
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
        """
        Check the info of guest timezone from guest side

        :return: the guest timezone info from guest side
        """
        timezone_info = {}
        timezone_name, hour_offset = parse_timezone_info()
        timezone_info["timezone.name"] = timezone_name
        sign = 1 if int(hour_offset) > 0 else -1
        second_offset = int(hour_offset[-4:-2])*3600 + int(hour_offset[-2:]*60)
        timezone_info["timezone.offset"] = str(sign * second_offset)
        return timezone_info

    def check_guest_hostname_info():
        """
        Check the info of guest hostname from guest side

        :return: the guest hostname info from guest side
        """
        hostname_info = {}
        session = vm.wait_for_login()
        try:
            output = session.cmd_output('hostnamectl --static').strip()
            if not output:
                output = session.cmd_output('hostnamectl --transient').strip()
        finally:
            session.close()
        hostname_info['hostname'] = output
        return hostname_info

    def add_user(name, passwd):
        """
        Added a user account

        :param name: user name
        :param passwd: password of user account
        """
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
        """
        check the info of guest user from guest side

        :return: the guest user info from guest side
        """
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
            user_info["user.count"] = str(len(users_list))
        finally:
            session.close()
        return user_info

    def check_disk_size(ses, disk):
        """
        check the disk size from guest side

        :return: total size and used size of the disk
        """
        disk_size = ses.cmd_output('df %s' % disk).strip().splitlines()[-1]
        total_size = disk_size.split()[1]
        used_size = disk_size.split()[2]
        return total_size, used_size

    def check_guest_filesystem_info():
        """
        check the info of filesystem from guest side

        :return: the filesystem info from guest side
        """
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
    option = params.get("option", " ")
    added_user_name = params.get("added_user_name")
    added_user_passwd = params.get("added_user_passwd")
    status_error = ("yes" == params.get("status_error", "no"))
    start_ga = ("yes" == params.get("start_ga", "yes"))
    prepare_channel = ("yes" == params.get("prepare_channel", "yes"))
    disk_target_name = params.get("disk_target_name")
    disk_name = params.get("disk_name")
    readonly_mode = ("yes" == params.get("readonly_mode"))

    if not libvirt_version.version_compare(6, 0, 0):
        test.cancel("Guestinfo command is not supported before version libvirt-6.0.0 ")
    import dateutil.parser

    added_user_session = None
    root_session = None

    try:
        vm = env.get_vm(vm_name)
        virsh_dargs = {}
        if readonly_mode:
            virsh_dargs["readonly"] = True

        if start_ga and prepare_channel:
            vm.prepare_guest_agent(start=True, channel=True)

        if "user" in option:
            add_user(added_user_name, added_user_passwd)
            added_user_session = vm.wait_for_login(username=added_user_name,
                                                   password=added_user_passwd)
            root_session = vm.wait_for_login()

        if "disk" in option:
            hotplug_disk(disk_name)

        result = virsh.guestinfo(vm_name, option, **virsh_dargs,
                                 ignore_status=True, debug=True)
        error_msg = []
        if not prepare_channel:
            error_msg.append("QEMU guest agent is not configured")
        if readonly_mode:
            error_msg.append("read only access prevents virDomainGetGuestInfo")
        libvirt.check_result(result, expected_fails=error_msg,
                             any_error=status_error)

        if status_error:
            return

        out = result.stdout.strip().splitlines()
        out_dict = dict(item.split(": ") for item in out)
        info_from_agent_cmd = dict((x.strip(), y.strip()) for x, y in out_dict.items())
        logging.debug("info from the guest is %s", info_from_agent_cmd)

        if "disk" in option:
            if not check_attached_disk_info(info_from_agent_cmd, disk_target_name):
                test.fail("The disk info reported by agent cmd is not correct. "
                          "result: %s" % info_from_agent_cmd)
            return
        else:
            func_name = "check_guest_%s_info" % option[2:]
            info_from_guest = locals()[func_name]()
            logging.debug('%s_info_from_guest is %s', option[2:], info_from_guest)

        if ("user" not in option) and ("filesystem" not in option):
            if info_from_guest != info_from_agent_cmd:
                test.fail("The %s info get from guestinfo cmd is not correct." % option[2:])
        else:
            for key, value in info_from_guest.items():
                if "used-bytes" in key:
                    # The guest block size may change by about 16000Kib after
                    # getting info via guestinfo cmd. We just need make sure
                    # the size difference will not exceed then 17000KiB.
                    if abs(int(value) - int(info_from_agent_cmd[key])) > 17408000:
                        test.fail("The block size returned from guest agent "
                                  "is not correct.")
                elif "login-time" in key:
                    # login time returned from guestinfo cmd is with milliseconds,
                    # so it may cause at most 1 second deviation
                    if abs(float(value) - int(info_from_agent_cmd[key])/1000) > 1.0:
                        test.fail("The login time of active users get from guestinfo "
                                  "is not correct.")
                else:
                    if value != info_from_agent_cmd[key]:
                        test.fail("The %s info get from guestinfo cmd"
                                  "is not correct." % option[2:])
    finally:
        if "user" in option:
            if added_user_session:
                added_user_session.close()
            if root_session:
                root_session.cmd('userdel -f %s' % added_user_name)
                root_session.close()
        if "disk" in option:
            virsh.detach_disk(vm_name, disk_target_name, ignore_status=False,
                              debug=True)
        vm.destroy()
