import os
import time
import threading
from autotest.client.shared import error
from virttest import libvirt_storage
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vol_xml
from virttest.utils_test import libvirt
from virttest.staging import utils_cgroup


class Vol_clone(object):

    """
    Test volume clone with lack disk resource
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

    def result_confirm(self):
        """
        Confirm if volume clone executed succeed
        """
        if self.pool:
            if not self.pool.clone_volume(self.vol_name, self.vol_new_name):
                raise error.TestFail("Clone volume failed!")

    def recover(self):
        """
        Recover test environment
        """
        if self.pvtest:
            self.pvtest.cleanup_pool(self.pool_name, self.pool_type,
                                     self.pool_target, self.emulated_img)


class Vol_create(object):

    """
    Test volume create with lack disk resource
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

    def result_confirm(self):
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

    def recover(self):
        """
        Recover test environment
        """
        if self.pvtest:
            self.pvtest.cleanup_pool(self.pool_name, self.pool_type,
                                     self.pool_target, self.emulated_img)


class Virt_clone(object):

    """
    Test virt-clone with disable cpu
    """

    def __init__(self, test, params):
        self.td = None
        self.cpu_num = int(params.get("cpu_num"))
        self.vm_name = params.get("main_vm")
        self.vm_new_name = params.get("vm_new_name")
        self.cgroup_name = params.get("cgroup_name")
        self.cgroup_dir = params.get("cgroup_dir")
        self.new_image_file = params.get("new_image_file")
        self.time_out = int(params.get("time_out", "600"))
        self.cpu_status = utils_misc.get_cpu_status(self.cpu_num)

    def run_test(self):
        """
        Start test, clone a guest in a cgroup
        """
        if virsh.domain_exists(self.vm_new_name):
            raise error.TestNAError("'%s' already exists! Please"
                                    " select another domain name!")
        if os.path.exists(self.new_image_file):
            os.remove(self.new_image_file)
        modules = utils_cgroup.CgroupModules(self.cgroup_dir)
        modules.init(['cpuset'])
        cgroup = utils_cgroup.Cgroup('cpuset', None)
        cgroup.initialize(modules)
        cgroup_index = cgroup.mk_cgroup(cgroup=self.cgroup_name)
        # Before use the cpu, set it to be enable
        if self.cpu_status < 1:
            utils_misc.set_cpu_status(self.cpu_num, True)
        cgroup.set_property("cpuset.cpus", self.cpu_num, cgroup_index,
                            check=False)
        cgroup.set_property("cpuset.mems", 0, cgroup_index,
                            check=False)
        self.td = threading.Thread(target=cgroup.cgexec,
                                   args=(self.cgroup_name, "virt-clone",
                                         "-o %s -n %s --force --file %s"
                                         % (self.vm_name, self.vm_new_name,
                                            self.new_image_file)))
        self.td.start()
        # Wait for virt-clone has been started
        time.sleep(3)

    def result_confirm(self):
        """
        Confirm if virt-clone executed succeed
        """
        self.td.join(self.time_out)
        if not virsh.domain_exists(self.vm_new_name):
            raise error.TestFail("Clone '%s' failed" % self.vm_new_name)
        else:
            result = virsh.start(self.vm_new_name, ignore_status=True)
            if result.exit_status:
                raise error.TestFail("Cloned domain cannot be started!")

    def recover(self):
        """
        Recover test environment
        """
        cpu_enable = True if self.cpu_status else False
        utils_misc.set_cpu_status(self.cpu_num, cpu_enable)
        if virsh.domain_exists(self.vm_new_name):
            virsh.remove_domain(self.vm_new_name)
        if os.path.exists(self.new_image_file):
            os.remove(self.new_image_file)


def cpu_lack(params):
    """
    Disable assigned cpu.
    """
    cpu_num = int(params.get("cpu_num", "0"))
    if not utils_misc.set_cpu_status(cpu_num, False):
        raise error.TestError("Set cpu '%s' failed!" % cpu_num)


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

        # Start test before resource becomes to abnormal
        test_case = globals()[test_type](test, params)
        test_case.run_test()

        # Make resource abnormal
        if abnormal_type:
            globals()[abnormal_type](params)

        # Confirm test result
        test_case.result_confirm()
    finally:
        test_case.recover()
