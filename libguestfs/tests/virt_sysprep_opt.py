import os
import stat

from virttest import data_dir
from virttest import remote
import virttest.utils_libguestfs as lgf


def run(test, params, env):
    """
    Test the command virt-sysprep options:--delete, --first-boot,
    --script, --enable, --password
    """

    def prepare_action(session):
        """
        Do some prepare action before testing
        """
        if sysprep_opt.count("delete"):
            session.cmd('rm -f /test')
            if dir_mode == 'file':
                session.cmd('mkdir /test')
                session.cmd("touch /test/tmp.file")
            elif dir_mode == 'subdir':
                session.cmd('mkdir -p /test/test')
                session.cmd("touch /test/test/tmp.file")
        elif sysprep_opt.count('first-boot'):
            session.cmd('rm -f /tmp/test*')
        elif sysprep_opt.count('script'):
            session.cmd('rm -f /tmp/test1.img')
        elif sysprep_opt.count('enable'):
            if action == "logfiles":
                session.cmd('touch /var/log/audit/tmp.log')
                session.cmd('touch /var/log/sa/tmp.log')
                session.cmd('touch /var/log/gdm/tmp.log')
                session.cmd('touch /var/log/ntpstats/tmp.log')
            elif action == "tmp-files":
                session.cmd('touch /tmp/tmp')
                session.cmd('touch /var/tmp/tmp')
            elif action == "firewall-rules":
                session.cmd_status('touch /etc/sysconfig/iptables')
            elif action == "user-account":
                session.cmd_status('userdel test')
                session.cmd_status('useradd test')
        elif sysprep_opt.count('password'):
            if user == "test":
                session.cmd_status('userdel test')
                session.cmd_status('useradd test')

    def domain_recover(session):
        """
        Recover test env after testing
        """
        if sysprep_opt.count('first-boot'):
            session.cmd_status('rm -f /tmp/test*')
        elif sysprep_opt.count('script'):
            session.cmd_status('rm -f /tmp/test1.img')
        elif sysprep_opt.count('password'):
            if user == "test":
                session.cmd_status('userdel test')

    def result_confirm_vm(session):
        """
        Confirm file has been created on guest.
        """
        if sysprep_opt.count("delete"):
            if dir_mode in ['file', 'subdir']:
                status = 1 if session.cmd_status('ls /test') else 0
                return status
        elif sysprep_opt.count('first-boot'):
            return session.cmd_status('ls /tmp/test*')
        elif sysprep_opt.count('script'):
            return session.cmd_status('ls /tmp/test1.img')

    def result_confirm_host():
        """
        Confirm file has been cleaned up.
        """
        if sysprep_opt.count("enable"):
            if action == "logfiles":
                au_o = lgf.virt_ls_cmd(vm_name,
                                       "/var/log/audit").stdout.strip()
                sa_o = lgf.virt_ls_cmd(vm_name, "/var/log/sa").stdout.strip()
                gdm_o = lgf.virt_ls_cmd(vm_name, "/var/log/gdm").stdout.strip()
                ntp_o = lgf.virt_ls_cmd(vm_name,
                                        "/var/log/ntpstats").stdout.strip()
                status = 1 if au_o or sa_o or gdm_o or ntp_o else 0
                return status
            elif action == "tmp-files":
                tmp_o = lgf.virt_ls_cmd(vm_name, "/tmp").stdout.strip()
                vartmp_o = lgf.virt_ls_cmd(vm_name, "/var/tmp").stdout.strip()
                status = 1 if tmp_o or vartmp_o else 0
                return status
            elif action == "firewall-rules":
                fw_r = lgf.virt_cat_cmd(vm_name,
                                        "/etc/sysconfig/iptables")
                status = 0 if fw_r.stdout.strip() else 1
                return status
            elif action == "user-account":
                status = 0
                pwd_o = lgf.virt_cat_cmd(vm_name, "/etc/passwd").stdout.strip()
                grp_o = lgf.virt_cat_cmd(vm_name, "/etc/group").stdout.strip()
                if pwd_o.count("test") or grp_o.count("test"):
                    status = 1
                vm.start()
                try:
                    vm.wait_for_login()
                except remote.LoginProcessTerminatedError:
                    status = 1
                return status

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sysprep_opt = params.get("sysprep_opt", "")
    dir_mode = params.get("sysprep_opt", "file")
    action = params.get("sysprep_action", "logfiles")
    sh_file1 = params.get("shell_file1", "")
    sh_file2 = params.get("shell_file2", "")
    sysprep_path = "yes" == params.get("sysprep_path", "yes")
    twice = "yes" == params.get("sysprep_twice", "no")
    user = params.get("sysprep_user", "root")
    org_password = params.get("password", "123456")
    test_password = params.get("test_password", "test")
    sysprep_target = params.get("sysprep_target", "guest")
    status_error = "yes" == params.get("status_error", "no")
    if not lgf.virt_cmd_contain_opt("virt-sysprep", sysprep_opt):
        test.cancel("The '%s' isn't supported in this version"
                    % sysprep_opt)
    if sysprep_opt.count('enable'):
        if action not in lgf.virt_sysprep_operations():
            test.cancel("The operation '%s' isn't support in"
                        " this version" % action)
    disks = vm.get_disk_devices()
    if len(disks):
        disk = list(disks.values())[0]
        image_name = disk['source']
    else:
        test.error("Can not get disk of %s" % vm_name)
    if sysprep_target == "guest":
        disk_or_domain = vm_name
    else:
        disk_or_domain = image_name
    if sysprep_path:
        sh_file1 = os.path.join(data_dir.get_tmp_dir(), sh_file1)
        tmp_file1 = "tmp/test1.img"
        if sysprep_opt.count("first-boot"):
            tmp_file1 = "/tmp/test1.img"
        with open(sh_file1, 'w') as f1:
            f1.write("dd if=/dev/zero of=%s bs=1M count=10" % tmp_file1)
        os.chmod(sh_file1, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        if sysprep_opt.count("first-boot"):
            sh_file2 = os.path.join(data_dir.get_tmp_dir(), sh_file2)
            with open(sh_file2, 'w') as f2:
                f2.write("dd if=/dev/zero of=/tmp/test2.img bs=1M count=10")
            os.chmod(sh_file2, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    try:
        try:
            prepare_action(session)
        except Exception as detail:
            test.cancel("Enviroment doesn't support this test:%s"
                        % str(detail))
        session.close()
        vm.destroy(gracefully=False)

        if sysprep_opt == '--delete':
            options = sysprep_opt + " /test"
        elif sysprep_opt in ['--first-boot', '--script']:
            options = sysprep_opt + " " + sh_file1
            if sh_file2 and not twice:
                options += " " + sysprep_opt + " " + sh_file2
        elif sysprep_opt == "--enable":
            options = sysprep_opt + " " + action
            if action == "user-account":
                options += " --selinux-relabel"
        elif sysprep_opt == "--password":
            options = "--enable password %s %s:password:%s"\
                      % (sysprep_opt, user, test_password)
        try:
            lgf.virt_sysprep_cmd(disk_or_domain, options, ignore_status=False,
                                 debug=True)
            if twice and sysprep_opt == '--first-boot':
                options = sysprep_opt + " " + sh_file2
                lgf.virt_sysprep_cmd(disk_or_domain, options,
                                     ignore_status=False, debug=True)
        except Exception as detail:
            if status_error:
                pass
            else:
                test.fail(detail)
        if not status_error:
            if not sysprep_opt.count("enable"):
                vm.start()
                if sysprep_opt == "--password":
                    session = vm.wait_for_login(username=user,
                                                password=test_password)
                else:
                    session = vm.wait_for_login()
                if result_confirm_vm(session):
                    test.fail("'%s' check falied in guest!"
                              % sysprep_opt)
            else:
                if result_confirm_host():
                    test.fail("'%s' check falied in host!"
                              % sysprep_opt)
        else:
            try:
                if sysprep_opt == "--password":
                    vm.start()
                    vm.wait_for_login(username=user, password=test_password,
                                      timeout=30)
                    test.fail("Should not login in guest via %s"
                              % user)
            except Exception:
                pass
    finally:
        if os.path.exists(sh_file1):
            os.remove(sh_file1)
        if os.path.exists(sh_file2):
            os.remove(sh_file2)
        if sysprep_opt.count("password"):
            if vm.is_alive():
                vm.destroy(gracefully=False)
            options = "--enable password %s %s:password:%s"\
                      % (sysprep_opt, user, org_password)
            lgf.virt_sysprep_cmd(disk_or_domain, options, ignore_status=True)
        if not sysprep_opt.count("enable") and not status_error:
            if vm.is_dead():
                vm.start()
            session = vm.wait_for_login()
            domain_recover(session)
            session.close()
