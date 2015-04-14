import os
import time
import stat
import signal
import logging
import threading
from autotest.client.shared import error
from autotest.client.shared import utils
from virttest import libvirt_storage
from virttest import utils_selinux
from virttest import qemu_storage
from virttest import libvirt_vm
from virttest import utils_misc
from virttest.staging import service
from virttest import virsh
from virttest import remote
from virttest import data_dir
from virttest.libvirt_xml import vol_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging import utils_cgroup
from virttest.staging import service
from virttest.tests import unattended_install


class Vol_clone(object):

    """
    Test volume clone with abnormal resource
    """

    def __init__(self, test, params):
        self.pvtest = None
        self.pool = None
        self.test = test
        self.params = params
        self.vol_name = params.get("volume_name")
        self.vol_new_name = params.get("volume_new_name")
        self.pool_name = params.get("pool_name")
        self.volume_size = params.get("volume_size", "1G")
        self.pool_type = params.get("pool_type")
        self.pool_target = params.get("pool_target")
        self.emulated_img = params.get("emulated_img", "emulated_img")

    def run_test(self):
        """
        Start test, Creat a volume.
        """
        emulated_size = "%sG" % (int(self.volume_size[:-1]) + 1)
        if int(self.volume_size[:-1]) <= 1:
            raise error.TestNAError("Volume size must large than 1G")
        self.pvtest = libvirt.PoolVolumeTest(self.test, self.params)
        self.pvtest.pre_pool(self.pool_name, self.pool_type, self.pool_target,
                             self.emulated_img, emulated_size,
                             pre_disk_vol=[self.volume_size])
        self.pool = libvirt_storage.PoolVolume(self.pool_name)
        self.pool.create_volume(self.vol_name, self.volume_size)

    def result_confirm(self, params):
        """
        Confirm if volume clone executed succeed
        """
        if self.pool:
            if not self.pool.clone_volume(self.vol_name, self.vol_new_name):
                raise error.TestFail("Clone volume failed!")

    def recover(self, params=None):
        """
        Recover test environment
        """
        if self.pvtest:
            self.pvtest.cleanup_pool(self.pool_name, self.pool_type,
                                     self.pool_target, self.emulated_img)


class Vol_create(object):

    """
    Test volume create with abnormal resource
    """

    def __init__(self, test, params):
        self.pvtest = None
        self.pool = None
        self.test = test
        self.params = params
        self.vol_name = params.get("volume_name")
        self.vol_new_name = params.get("volume_new_name")
        self.pool_name = params.get("pool_name")
        self.volume_size = params.get("volume_size", "1G")
        self.pool_type = params.get("pool_type")
        self.pool_target = params.get("pool_target")
        self.emulated_img = params.get("emulated_img", "emulated_img")

    def run_test(self):
        """
        Start test, Creat a volume.
        """
        emulated_size = "%sG" % (int(self.volume_size[:-1]) + 1)
        if int(self.volume_size[:-1]) <= 1:
            raise error.TestNAError("Volume size must large than 1G")
        self.pvtest = libvirt.PoolVolumeTest(self.test, self.params)
        self.pvtest.pre_pool(self.pool_name, self.pool_type, self.pool_target,
                             self.emulated_img, emulated_size,
                             pre_disk_vol=[self.volume_size])
        self.pool = libvirt_storage.PoolVolume(self.pool_name)
        self.pool.create_volume(self.vol_name, self.volume_size)

    def result_confirm(self, params):
        """
        Confirm if volume create executed succeed.
        """
        if self.pool:
            volxml = vol_xml.VolXML.new_from_vol_dumpxml(self.vol_name,
                                                         self.pool_name)
            volxml.name = self.vol_new_name
            if volxml.create(self.pool_name):
                raise error.TestFail("Volume '%s' created succeed but"
                                     " expect failed!" % self.vol_new_name)
            volxml.capacity = 1024 * 1024 * 1024 / 2
            volxml.allocation = 1024 * 1024 * 1024 / 2
            if not volxml.create(self.pool_name):
                raise error.TestFail("Volume '%s' created failed!"
                                     % self.vol_new_name)

    def recover(self, params=None):
        """
        Recover test environment
        """
        if self.pvtest:
            self.pvtest.cleanup_pool(self.pool_name, self.pool_type,
                                     self.pool_target, self.emulated_img)


class Virt_clone(object):

    """
    Test virt-clone with abnormal resource
    """

    def __init__(self, test, params):
        self.td = None
        self.cpu_num = int(params.get("cpu_num", "1"))
        self.vm_name = params.get("main_vm")
        self.vm_new_name = params.get("vm_new_name")
        self.cgroup_name = params.get("cgroup_name")
        self.cgroup_dir = params.get("cgroup_dir")
        self.new_image_file = params.get("new_image_file")
        if self.new_image_file:
            self.new_image_file = os.path.join(test.virtdir,
                                               self.new_image_file)
        self.time_out = int(params.get("time_out", "600"))
        self.cpu_status = utils_misc.get_cpu_status(self.cpu_num)
        self.twice_execute = "yes" == params.get("twice_execute", "no")
        self.kill_first = "yes" == params.get("kill_first", "no")
        if params.get("abnormal_type") in ["disk_lack", ""]:
            self.selinux_enforcing = utils_selinux.is_enforcing()
            if self.selinux_enforcing:
                utils_selinux.set_status("permissive")
            self.fs_type = params.get("fs_type", "ext4")
            xml_file = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm_name)
            disk_node = xml_file.get_disk_all()['vda']
            source_file = disk_node.find('source').get('file')
            self.image_size = utils_misc.get_image_info(source_file)['dsize']
            # Set the size to be image_size
            iscsi_size = "%sM" % (self.image_size / 1024 / 1024)
            params['image_size'] = iscsi_size
            self.iscsi_dev = qemu_storage.Iscsidev(params, test.virtdir,
                                                   "iscsi")
            try:
                device_source = self.iscsi_dev.setup()
            except (error.TestError, ValueError), detail:
                self.iscsi_dev.cleanup()
                raise error.TestNAError("Cannot get iscsi device on this"
                                        " host:%s\n" % detail)
            libvirt.mk_part(device_source, iscsi_size)
            self.mount_dir = os.path.join(test.virtdir,
                                          params.get('mount_dir'))
            if not os.path.exists(self.mount_dir):
                os.mkdir(self.mount_dir)
            params['mount_dir'] = self.mount_dir
            self.partition = device_source + "1"
            libvirt.mkfs(self.partition, self.fs_type)
            utils_misc.mount(self.partition, self.mount_dir, self.fs_type)
            self.new_image_file = os.path.join(self.mount_dir, "new_file")

    def run_test(self):
        """
        Start test, clone a guest in a cgroup
        """
        if virsh.domain_exists(self.vm_new_name):
            raise error.TestNAError("'%s' already exists! Please"
                                    " select another domain name!"
                                    % self.vm_new_name)
        if os.path.exists(self.new_image_file):
            os.remove(self.new_image_file)
        modules = utils_cgroup.CgroupModules(self.cgroup_dir)
        modules.init(['cpuset'])
        self.cgroup = utils_cgroup.Cgroup('cpuset', None)
        self.cgroup.initialize(modules)
        self.cgroup_index = self.cgroup.mk_cgroup(cgroup=self.cgroup_name)
        # Before use the cpu, set it to be enable
        if self.cpu_status < 1:
            utils_misc.set_cpu_status(self.cpu_num, True)
        self.cgroup.set_property("cpuset.cpus", self.cpu_num,
                                 self.cgroup_index, check=False)
        self.cgroup.set_property("cpuset.mems", 0, self.cgroup_index,
                                 check=False)
        self.td0 = threading.Thread(target=self.cgroup.cgexec,
                                    args=(self.cgroup_name, "virt-clone",
                                          "-o %s -n %s --force --file %s"
                                          % (self.vm_name, self.vm_new_name,
                                             self.new_image_file)))
        self.td1 = None
        if self.twice_execute:
            self.vm_new_name1 = self.vm_new_name + "1"
            self.new_image_file1 = self.new_image_file + "1"
            self.td1 = threading.Thread(target=self.cgroup.cgexec,
                                        args=(self.cgroup_name, "virt-clone",
                                              "-o %s -n %s --force --file %s"
                                              % (self.vm_name,
                                                 self.vm_new_name1,
                                                 self.new_image_file1)))
            self.td1.start()
        self.td0.start()
        # Wait for virt-clone has been started
        time.sleep(1)

    def result_confirm(self, params):
        """
        Confirm if virt-clone executed succeed
        """
        if self.kill_first:
            # Stop this threading
            first_pid = self.cgroup.get_pids(self.cgroup_index)[-1]
            utils_misc.safe_kill(int(first_pid), signal.SIGKILL)
        else:
            self.td0.join(self.time_out)
        if self.td1:
            self.td1.join(self.time_out)
        abnormal_type = params.get("abnormal_type")
        if abnormal_type == "cpu_lack":
            if not virsh.domain_exists(self.vm_new_name):
                raise error.TestFail("Clone '%s' failed" % self.vm_new_name)
            else:
                result = virsh.start(self.vm_new_name, ignore_status=True)
                if result.exit_status:
                    raise error.TestFail("Cloned domain cannot be started!")
        elif abnormal_type == "disk_lack":
            if virsh.domain_exists(self.vm_new_name):
                raise error.TestFail("Clone '%s' succeed but expect failed!"
                                     % self.vm_new_name)
        else:
            if self.twice_execute and not self.kill_first:
                if virsh.domain_exists(self.vm_new_name):
                    raise error.TestFail("Clone '%s' succeed but expect"
                                         " failed!" % self.vm_new_name)
                if virsh.domain_exists(self.vm_new_name1):
                    raise error.TestFail("Clone '%s' succeed but expect"
                                         " failed!" % self.vm_new_name1)

            elif self.twice_execute and self.kill_first:
                if not virsh.domain_exists(self.vm_new_name):
                    raise error.TestFail("Clone '%s' failed!"
                                         % self.vm_new_name)

    def recover(self, params):
        """
        Recover test environment
        """
        abnormal_type = params.get("abnormal_type")
        cpu_enable = True if self.cpu_status else False
        utils_misc.set_cpu_status(self.cpu_num, cpu_enable)
        if virsh.domain_exists(self.vm_new_name):
            virsh.remove_domain(self.vm_new_name)
        if os.path.exists(self.new_image_file):
            os.remove(self.new_image_file)
        if self.twice_execute:
            if virsh.domain_exists(self.vm_new_name1):
                virsh.remove_domain(self.vm_new_name1)
            if os.path.exists(self.new_image_file1):
                os.remove(self.new_image_file1)
        if abnormal_type == "memory_lack":
            if params.has_key('memory_pid'):
                pid = params.get('memory_pid')
                utils_misc.safe_kill(pid, signal.SIGKILL)
                utils.run("swapon -a")
            tmp_c_file = params.get("tmp_c_file", "/tmp/test.c")
            tmp_exe_file = params.get("tmp_exe_file", "/tmp/test")
            if os.path.exists(tmp_c_file):
                os.remove(tmp_c_file)
            if os.path.exists(tmp_exe_file):
                os.remove(tmp_exe_file)
        elif abnormal_type in ["disk_lack", ""]:
            if self.selinux_enforcing:
                utils_selinux.set_status("enforcing")
            tmp_file = os.path.join(self.mount_dir, "tmp")
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
            # Sometimes one umount action is not enough
            utils_misc.wait_for(lambda: utils_misc.umount(self.partition,
                                                          self.mount_dir,
                                                          self.fs_type), 120)
            if self.iscsi_dev:
                self.iscsi_dev.cleanup()
            os.rmdir(self.mount_dir)
        remove_machine_cgroup()


class Snapshot_create(object):

    """
    Test snapshot create
    """

    def __init__(self, test, params):
        self.cpu_num = int(params.get("cpu_num", "1"))
        self.cgroup_name = params.get("cgroup_name")
        self.cgroup_dir = params.get("cgroup_dir")
        self.time_out = int(params.get("time_out", "600"))
        self.vm_name = params.get("main_vm")
        self.time_out = int(params.get("time_out", "600"))
        self.twice_execute = "yes" == params.get("twice_execute", "no")
        self.kill_first = "yes" == params.get("kill_first", "no")
        xml_file = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm_name)
        disk_node = xml_file.get_disk_all()['vda']
        source_file = disk_node.find('source').get('file')
        image_type = utils_misc.get_image_info(source_file)['format']
        if image_type != "qcow2":
            raise error.TestNAError("Disk image format is not qcow2, "
                                    "ignore snapshot test!")
        self.cpu_status = utils_misc.get_cpu_status(self.cpu_num)
        self.current_snp_list = []
        self.snp_list = virsh.snapshot_list(self.vm_name)
        env = params.get("env")
        vm = env.get_vm(self.vm_name)
        # This can add snapshot create time
        vm.wait_for_login()

    def run_test(self):
        """
        Start test, Creat a cgroup to create snapshot.
        """
        modules = utils_cgroup.CgroupModules(self.cgroup_dir)
        modules.init(['cpuset'])
        self.cgroup = utils_cgroup.Cgroup('cpuset', None)
        self.cgroup.initialize(modules)
        self.cgroup_index = self.cgroup.mk_cgroup(cgroup=self.cgroup_name)
        # Before use the cpu, set it to be enable
        if self.cpu_status < 1:
            utils_misc.set_cpu_status(self.cpu_num, True)
        self.cgroup.set_property("cpuset.cpus", self.cpu_num,
                                 self.cgroup_index, check=False)
        self.cgroup.set_property("cpuset.mems", 0, self.cgroup_index,
                                 check=False)
        self.td0 = threading.Thread(target=self.cgroup.cgexec,
                                    args=(self.cgroup_name, "virsh",
                                          "snapshot-create %s" % self.vm_name))
        self.td1 = None
        if self.twice_execute:
            self.td1 = threading.Thread(target=self.cgroup.cgexec,
                                        args=(self.cgroup_name, "virsh",
                                              "snapshot-create %s"
                                              % self.vm_name))
            self.td1.start()
        self.td0.start()

    def result_confirm(self, params):
        """
        Confirm if snapshot has been created.
        """
        if params.has_key('cpu_pid'):
            cpu_id = params.get('cpu_pid')
            self.cgroup.cgclassify_cgroup(int(cpu_id), self.cgroup_name)
        if self.kill_first:
            # Stop this threading
            try:
                first_pid = self.cgroup.get_pids(self.cgroup_index)[1]
                utils_misc.safe_kill(int(first_pid), signal.SIGKILL)
            except IndexError:
                logging.info("Snapshot create process in cgroup"
                             " has been over")
        else:
            if self.td1:
                self.td1.join(self.time_out)
        self.td0.join(self.time_out)
        self.current_snp_list = virsh.snapshot_list(self.vm_name)
        if len(self.snp_list) >= len(self.current_snp_list):
            raise error.TestFail("Create snapshot failed for low memory!")

    def recover(self, params=None):
        """
        Recover test environment
        """
        cpu_enable = True if self.cpu_status else False
        utils_misc.set_cpu_status(self.cpu_num, cpu_enable)
        tmp_c_file = params.get("tmp_c_file", "/tmp/test.c")
        tmp_exe_file = params.get("tmp_exe_file", "/tmp/test")
        if os.path.exists(tmp_c_file):
            os.remove(tmp_c_file)
        if os.path.exists(tmp_exe_file):
            os.remove(tmp_exe_file)
        if params.has_key('memory_pid'):
            pid = int(params.get('memory_pid'))
            utils_misc.safe_kill(pid, signal.SIGKILL)
            utils.run("swapon -a")
        if params.has_key('cpu_pid'):
            pid = int(params.get('cpu_pid'))
            utils_misc.safe_kill(pid, signal.SIGKILL)
            tmp_sh_file = params.get("tmp_sh_file")
            if os.path.exists(tmp_sh_file):
                os.remove(tmp_sh_file)
        virsh.destroy(self.vm_name)
        if len(self.snp_list) < len(self.current_snp_list):
            self.diff_snp_list = list(set(self.current_snp_list) -
                                      set(self.snp_list))
            for item in self.diff_snp_list:
                virsh.snapshot_delete(self.vm_name, item)
        remove_machine_cgroup()


class Virsh_dump(object):

    """
    Test virsh dump with abnormal resource
    """

    def __init__(self, test, params):
        self.cpu_num = int(params.get("cpu_num", "1"))
        self.cgroup_name = params.get("cgroup_name")
        self.cgroup_dir = params.get("cgroup_dir")
        self.time_out = int(params.get("time_out", "600"))
        self.vm_name = params.get("main_vm")
        self.time_out = int(params.get("time_out", "600"))
        self.twice_execute = "yes" == params.get("twice_execute", "no")
        self.kill_first = "yes" == params.get("kill_first", "no")
        self.cpu_status = utils_misc.get_cpu_status(self.cpu_num)
        self.dump_file = os.path.join(test.virtdir,
                                      params.get("dump_file", "dump.info"))
        self.dump_file1 = self.dump_file + "1"
        env = params.get("env")
        vm = env.get_vm(self.vm_name)
        vm.wait_for_login()

    def run_test(self):
        """
        Start test, Creat a cgroup to create snapshot.
        """
        modules = utils_cgroup.CgroupModules(self.cgroup_dir)
        modules.init(['cpuset'])
        self.cgroup = utils_cgroup.Cgroup('cpuset', None)
        self.cgroup.initialize(modules)
        self.cgroup_index = self.cgroup.mk_cgroup(cgroup=self.cgroup_name)
        # Before use the cpu, set it to be enable
        if self.cpu_status < 1:
            utils_misc.set_cpu_status(self.cpu_num, True)
        self.cgroup.set_property("cpuset.cpus", self.cpu_num,
                                 self.cgroup_index, check=False)
        self.cgroup.set_property("cpuset.mems", 0, self.cgroup_index,
                                 check=False)
        self.td0 = threading.Thread(target=self.cgroup.cgexec,
                                    args=(self.cgroup_name, "virsh",
                                          "dump %s %s"
                                          % (self.vm_name, self.dump_file)))
        self.td1 = None
        if self.twice_execute:
            self.td1 = threading.Thread(target=self.cgroup.cgexec,
                                        args=(self.cgroup_name, "virsh",
                                              "dump %s %s"
                                              % (self.vm_name,
                                                 self.dump_file1)))
            self.td1.start()
        self.td0.start()

    def result_confirm(self, params):
        """
        Confirm if dump file has been created.
        """
        if params.has_key('cpu_pid'):
            cpu_id = params.get('cpu_pid')
            self.cgroup.cgclassify_cgroup(int(cpu_id), self.cgroup_name)
        if self.kill_first:
            # Stop this threading
            try:
                first_pid = self.cgroup.get_pids(self.cgroup_index)[1]
                utils_misc.safe_kill(int(first_pid), signal.SIGKILL)
            except IndexError:
                logging.info("Dump process in cgroup has been over")
        else:
            if self.td1:
                self.td1.join(self.time_out)
        self.td0.join(self.time_out)
        if not os.path.join(self.dump_file1):
            raise error.TestFail("Dump file %s doesn't exist!"
                                 % self.dump_file)
        if self.twice_execute and not os.path.join(self.dump_file1):
            raise error.TestFail("Dump file %s doesn't exist!"
                                 % self.dump_file1)

    def recover(self, params=None):
        """
        Recover test environment
        """
        cpu_enable = True if self.cpu_status else False
        utils_misc.set_cpu_status(self.cpu_num, cpu_enable)
        virsh.destroy(self.vm_name)
        if params.has_key('cpu_pid'):
            pid = int(params.get('cpu_pid'))
            utils_misc.safe_kill(pid, signal.SIGKILL)
            tmp_sh_file = params.get("tmp_sh_file")
            if os.path.exists(tmp_sh_file):
                os.remove(tmp_sh_file)
        if os.path.exists(self.dump_file):
            os.remove(self.dump_file)
        if os.path.exists(self.dump_file1):
            os.remove(self.dump_file1)
        remove_machine_cgroup()


class Virt_install(object):

    """
    Test virt-install with abnormal resource
    """

    def __init__(self, test, params):
        self.vm_name = params.get("vm_name", "test-vm1")
        while virsh.domain_exists(self.vm_name):
            self.vm_name += ".test"
        params["main_vm"] = self.vm_name
        ios_file = os.path.join(data_dir.get_data_dir(),
                                params.get('cdrom_cd1'))
        if not os.path.exists(ios_file):
            raise error.TestNAError("Please prepare ios file:%s" % ios_file)
        self.env = params.get('env')
        self.vm = self.env.create_vm("libvirt", None, self.vm_name, params,
                                     test.bindir)
        self.env.register_vm(self.vm_name, self.vm)
        self.twice_execute = "yes" == params.get("twice_execute", "no")
        self.kill_first = "yes" == params.get("kill_first", "no")
        self.read_only = "yes" == params.get("read_only", "no")
        self.selinux_enforcing = utils_selinux.is_enforcing()
        if self.selinux_enforcing:
            utils_selinux.set_status("permissive")
        self.image_path = os.path.join(test.virtdir, "test_image")
        if not os.path.exists(self.image_path):
            os.mkdir(self.image_path)
        if self.read_only:
            os.chmod(self.image_path,
                     stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        params["image_name"] = os.path.join(self.image_path, self.vm_name)
        params["image_format"] = "raw"
        params['force_create_image'] = "yes"
        params['remove_image'] = "yes"
        params['shutdown_cleanly'] = "yes"
        params['shutdown_cleanly_timeout'] = 120
        params['guest_port_unattended_install'] = 12323
        params['inactivity_watcher'] = "error"
        params['inactivity_treshold'] = 1800
        params['image_verify_bootable'] = "no"
        params['unattended_delivery_method'] = "cdrom"
        params['drive_index_unattended'] = 1
        params['drive_index_cd1'] = 2
        params['boot_once'] = "d"
        params['medium'] = "cdrom"
        params['wait_no_ack'] = "yes"
        params['image_raw_device'] = "yes"
        params['backup_image_before_testing'] = "no"
        params['kernel_params'] = ("ks=cdrom nicdelay=60 "
                                   "console=ttyS0,115200 console=tty0")
        params['cdroms'] += " unattended"
        params['redirs'] += " unattended_install"

        self.params = params
        self.test = test

    def run_test(self):
        """
        Start test, Creat a threading to install VM.
        """
        self.td = threading.Thread(target=unattended_install.run,
                                   args=(self.test, self.params, self.env))
        self.td.start()
        # Wait for install start
        time.sleep(10)

    def result_confirm(self, params):
        """
        Confirm if VM installation is succeed
        """
        if self.twice_execute and self.kill_first:
            get_pid_cmd = "ps -ef | grep '%s' | grep qemu-kvm | grep -v grep"\
                          % self.vm_name
            result = utils.run(get_pid_cmd, ignore_status=True)
            if result.exit_status:
                raise error.TestFail("First install failed!")
            install_pid = result.stdout.strip().split()[1]
            utils_misc.safe_kill(int(install_pid), signal.SIGKILL)
        self.td.join()
        if self.read_only:
            if virsh.domain_exists(self.vm_name):
                raise error.TestFail("Domain '%s' should not exist"
                                     % self.vm_name)
            os.chmod(self.image_path,
                     stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        else:
            if not virsh.domain_exists(self.vm_name):
                raise error.TestFail("Domain '%s' should exists, no matter its"
                                     " installation is succeed or failed!"
                                     % self.vm_name)
            else:
                if not self.kill_first:
                    if self.vm.is_dead():
                        self.vm.start()
                    try:
                        self.vm.wait_for_login()
                    except remote.LoginTimeoutError, detail:
                        raise error.TestFail(str(detail))
                else:
                    virsh.remove_domain(self.vm_name)
        if self.twice_execute or self.read_only:
            self.td1 = threading.Thread(target=unattended_install.run,
                                        args=(self.test, params, self.env))
            self.td1.start()
            self.td1.join()
            if not virsh.domain_exists(self.vm_name):
                raise error.TestFail("Domain '%s' installation failed!"
                                     % self.vm_name)

    def recover(self, params=None):
        """
        Recover test environment
        """
        if self.selinux_enforcing:
            utils_selinux.set_status("enforcing")
        if virsh.domain_exists(self.vm_name):
            virsh.remove_domain(self.vm_name)
        image_file = params.get("image_name")
        if os.path.exists(image_file):
            os.remove(image_file)
        if os.path.isdir(self.image_path):
            os.rmdir(self.image_path)
        self.env.unregister_vm(self.vm_name)


class Migration(object):

    """
    Test virsh migrate --live with abnormal resource
    """

    def __init__(self, test, params):
        self.vm_name = params.get("main_vm", "test-vm1")
        self.env = params.get('env')
        self.time_out = int(params.get('time_out'))
        self.time_out_test = "yes" == params.get('time_out_test')
        self.remote_ip = params.get('remote_ip')
        self.remote_user = params.get('remote_user')
        self.local_ip = params.get('local_ip')
        if self.remote_ip.count("ENTER") or self.local_ip.count("ENTER"):
            raise error.TestNAError("Please set remote/local ip in base.cfg")
        self.remote_pwd = params.get('remote_pwd')
        self.local_mnt = params.get('local_mnt')
        self.remote_mnt = params.get('remote_mnt')
        self.session = remote.remote_login("ssh", self.remote_ip, "22",
                                           self.remote_user,
                                           self.remote_pwd, "#")
        self.session.cmd("setsebool virt_use_nfs on")
        local_hostname = utils.run("hostname").stdout.strip()
        remote_hostname = self.session.cmd_output("hostname")

        def file_add(a_str, a_file, session=None):
            """
            Add detail to a file
            """
            write_cmd = "echo '%s' >> %s" % (a_str, a_file)
            if session:
                session.cmd(write_cmd)
            else:
                utils.run(write_cmd)

        # Edit /etc/hosts file on local and remote host
        backup_hosts_cmd = "cat /etc/hosts > /etc/hosts.bak"
        utils.run(backup_hosts_cmd)
        self.session.cmd(backup_hosts_cmd)
        hosts_local_str = "%s %s" % (self.local_ip, local_hostname)
        hosts_remote_str = "%s %s" % (self.remote_ip, remote_hostname)
        file_add(hosts_local_str, "/etc/hosts")
        file_add(hosts_remote_str, "/etc/hosts")
        file_add(hosts_local_str, "/etc/hosts", self.session)
        file_add(hosts_remote_str, "/etc/hosts", self.session)

        # Edit /etc/exports file on local host
        utils.run("cat /etc/exports > /etc/exports.bak")
        exports_str = "%s *(insecure,rw,sync,no_root_squash)" % self.local_mnt
        file_add(exports_str, "/etc/exports")
        nfs_mount_cmd = "mount -t nfs %s:%s %s"\
                        % (self.local_ip, self.local_mnt, self.remote_mnt)
        self.session.cmd(nfs_mount_cmd)
        vm = self.env.get_vm(self.vm_name)
        vm.wait_for_login()

    def run_test(self):
        """
        Start test, Creat a threading to migrate VM.
        """
        remote_uri = libvirt_vm.get_uri_with_transport(transport="ssh",
                                                       dest_ip=self.remote_ip)
        option = "--live"
        if self.time_out_test:
            option += " --timeout %s" % self.time_out
        self.td = threading.Thread(target=virsh.migrate,
                                   args=(self.vm_name, remote_uri, option))
        self.td.start()

    def result_confirm(self, params):
        """
        Confirm if migratiton is succeed.
        """
        if self.time_out_test:
            time.sleep(self.time_out)
            domain_state = self.session.cmd_output("virsh domstate %s"
                                                   % self.vm_name)
            if not domain_state.count("paused"):
                raise error.TestFail("Guest should suspend with time out!")
        self.td.join(self.time_out)
        domain_info = self.session.cmd_output("virsh list")
        abnormal_type = params.get("abnormal_type")
        if not abnormal_type:
            if not domain_info.count(self.vm_name):
                raise error.TestFail("Guest migration failed!")
        else:
            if domain_info.count(self.vm_name):
                raise error.TestFail("Guest migration succeed but expect"
                                     " with %s!" % abnormal_type)

    def recover(self, params=None):
        """
        Recover test environment
        """
        if self.session.cmd_output("virsh list").count(self.vm_name):
            self.session.cmd("virsh destroy %s" % self.vm_name)
        abnormal_type = params.get("abnormal_type")
        if not abnormal_type:
            self.session.cmd("umount %s -l" % self.remote_mnt)
        recover_hosts_cmd = "mv -f /etc/hosts.bak /etc/hosts"
        utils.run(recover_hosts_cmd)
        self.session.cmd_status(recover_hosts_cmd)
        utils.run("mv -f /etc/exports.bak /etc/exports")
        self.session.close()


def cpu_lack(params):
    """
    Disable assigned cpu.
    """
    cpu_num = int(params.get("cpu_num", "0"))
    if not utils_misc.set_cpu_status(cpu_num, False):
        raise error.TestError("Set cpu '%s' failed!" % cpu_num)


def memory_lack(params):
    """
    Lower the available memory of host
    """
    tmp_c_file = params.get("tmp_c_file", "/tmp/test.c")
    tmp_exe_file = params.get("tmp_exe_file", "/tmp/test")
    c_str = """
#include <stdio.h>
#include <malloc.h>
#define MAX 1024*4
int main(void){
    char *a;
    while(1) {
        a = malloc(MAX);
        if (a == NULL) {
            break;
        }
    }
    while (1){
    }
    return 0;
}"""
    c_file = open(tmp_c_file, 'w')
    c_file.write(c_str)
    c_file.close()
    try:
        utils_misc.find_command('gcc')
    except ValueError:
        raise error.TestNAError('gcc command is needed!')
    result = utils.run("gcc %s -o %s" % (tmp_c_file, tmp_exe_file))
    if result.exit_status:
        raise error.TestError("Compile C file failed: %s"
                              % result.stderr.strip())
    # Set swap off before fill memory
    utils.run("swapoff -a")
    utils.run("%s &" % tmp_exe_file)
    result = utils.run("ps -ef | grep %s | grep -v grep" % tmp_exe_file)
    pid = result.stdout.strip().split()[1]
    params['memory_pid'] = pid


def disk_lack(params):
    """
    Lower the available disk space
    """
    disk_size = params.get('image_size')
    mount_dir = params.get('mount_dir')
    # Will use 2/3 space of disk
    use_size = int(disk_size[0:-1]) * 2 / 3
    tmp_file = os.path.join(mount_dir, "tmp")
    utils.run('dd if=/dev/zero of=%s bs=1G count=%s &' % (tmp_file, use_size))


def cpu_busy(params):
    """
    Make the cpu busy, almost 100%
    """
    tmp_sh_file = params.get("tmp_sh_file", "/tmp/test.sh")
    shell_str = """
while true
do
    j==${j:+1}
    j==${j:-1}
done"""
    sh_file = open(tmp_sh_file, 'w')
    sh_file.write(shell_str)
    sh_file.close()
    os.chmod(tmp_sh_file, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
    result = utils.run("%s &" % tmp_sh_file)
    result = utils.run("ps -ef | grep %s | grep -v grep" % tmp_sh_file)
    pid = result.stdout.strip().split()[1]
    params['cpu_pid'] = pid


def network_restart(params):
    """
    Restart remote network
    """
    time_out = int(params.get('time_out'))
    remote_ip = params.get('remote_ip')
    remote_user = params.get('remote_user')
    remote_pwd = params.get('remote_pwd')
    session = remote.remote_login("ssh", remote_ip, "22", remote_user,
                                  remote_pwd, "#")
    runner = remote.RemoteRunner(session=session)
    net_service = service.Factory.create_service("network", runner.run)
    net_service.restart()
    session.close()
    try:
        remote.wait_for_login("ssh", remote_ip, "22", remote_user,
                              remote_pwd, "#", timeout=time_out)
    except remote.LoginTimeoutError, detail:
        raise error.TestError(str(detail))


def remove_machine_cgroup():
    """
    Remove machine/machine.slice cgroup by restart cgconfig and libvirtd
    """
    cg_ser = utils_cgroup.CgconfigService()
    cg_ser.cgconfig_restart()
    libvirt_ser = service.Factory.create_specific_service("libvirtd")
    libvirt_ser.restart()


def run(test, params, env):
    """
    Test some commands' execution with abnormal resource.
    1. Do some test preparation before test
    2. Start test
    3. Make resource abnormal
    4. Confirm test result
    5. Recover test environment
    """
    # Test start
    try:
        test_type = params.get("test_type")
        abnormal_type = params.get("abnormal_type")
        params['env'] = env
        # Start test before resource becomes to abnormal
        test_case = globals()[test_type](test, params)
        test_case.run_test()

        # Make resource abnormal
        if abnormal_type:
            globals()[abnormal_type](params)

        # Confirm test result
        test_case.result_confirm(params)
    finally:
        if 'test_case' in dir():
            test_case.recover(params)
