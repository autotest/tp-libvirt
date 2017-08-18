import re
import logging
import time
from distutils.version import LooseVersion  # pylint: disable=E0611

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_v2v
from virttest import utils_sasl
from virttest import virsh
from virttest.libvirt_xml import vm_xml

V2V_7_3_VERSION = 'virt-v2v-1.32.1-1.el7'
RETRY_TIMES = 10


class VMChecker(object):

    """
    Check VM after virt-v2v converted
    """

    def __init__(self, test, params, env):
        self.errors = []
        self.params = params
        self.vm_name = params.get('main_vm')
        self.hypervisor = params.get("hypervisor")
        self.target = params.get('target')
        self.os_type = params.get('os_type')
        self.os_version = params.get('os_version', 'OS_VERSION_V2V_EXAMPLE')
        self.original_vmxml = params.get('original_vmxml')
        self.virsh_session = None
        self.virsh_session_id = None
        self.setup_session()
        self.checker = utils_v2v.VMCheck(test, params, env)
        self.checker.virsh_session_id = self.virsh_session_id
        self.virsh_instance = virsh.VirshPersistent(session_id=self.virsh_session_id)
        self.vmxml = virsh.dumpxml(self.vm_name,
                                   session_id=self.virsh_session_id).stdout.strip()

    def cleanup(self):
        self.close_virsh_session()
        try:
            if self.checker.session:
                self.checker.session.close()
            self.checker.cleanup()
        except Exception:
            pass

    def close_virsh_session(self):
        if not self.virsh_session:
            return
        if self.target == "ovirt":
            self.virsh_session.close()
        else:
            self.virsh_session.close_session()

    def setup_session(self):
        for index in range(RETRY_TIMES):
            logging.info('Trying %d times', index + 1)
            try:
                if self.target == "ovirt":
                    self.virsh_session = utils_sasl.VirshSessionSASL(self.params)
                    self.virsh_session_id = self.virsh_session.get_id()
                else:
                    self.virsh_session = virsh.VirshPersistent()
                    self.virsh_session_id = self.virsh_session.session_id
            except Exception as detail:
                logging.error(detail)
            else:
                break
        if not self.virsh_session_id:
            raise exceptions.TestError('Fail to create SASL virsh session')

    def log_err(self, msg):
        logging.error(msg)
        self.errors.append(msg)

    def run(self):
        if self.os_type == 'linux':
            self.check_linux_vm()
        elif self.os_type == 'windows':
            self.check_windows_vm()
        else:
            logging.warn("Unspported os type: %s", self.os_type)
        return self.errors

    def compare_version(self, compare_version):
        """
        Compare virt-v2v version against given version.
        """
        cmd = 'rpm -q virt-v2v|grep virt-v2v'
        v2v_version = LooseVersion(process.run(cmd, shell=True).stdout.strip())
        compare_version = LooseVersion(compare_version)
        if v2v_version > compare_version:
            return True
        return False

    def get_expect_graphic_type(self):
        """
        The graphic type in VM XML is different for different target.
        """
        graphic_type = 'vnc'
        # Video modle will change to QXL if convert target is ovirt/RHEVM
        if self.target == 'ovirt':
            graphic_type = 'spice'
        return graphic_type

    def get_expect_video_model(self):
        """
        The video model in VM XML is different in different situation.
        """
        video_model = 'cirrus'
        # Video modle will change to QXL if convert target is ovirt/RHEVM
        if self.target == 'ovirt':
            video_model = 'qxl'
        # Since RHEL7.3(virt-v2v-1.32.1-1.el7), video model will change to
        # QXL for linux VMs
        if self.os_type == 'linux':
            if self.compare_version(V2V_7_3_VERSION):
                video_model = 'qxl'
        # Video model will change to QXL for Windows2008r2 and windows7
        if self.os_version in ['win7', 'win2008r2']:
            video_model = 'qxl'
        return video_model

    def check_vm_xml(self):
        """
        Check graphic/video XML of the VM.
        """
        logging.info("Checking graphic type in VM XML")
        expect_graphic = self.get_expect_graphic_type()
        logging.info("Expect type: %s", expect_graphic)
        pattern = r"<graphics type='(\w+)'"
        vmxml_graphic_type = re.search(pattern, self.vmxml).group(1)
        if vmxml_graphic_type != expect_graphic:
            err_msg = "Not find %s type graphic in VM XML" % expect_graphic
            self.log_err(err_msg)
        else:
            logging.info("PASS")

        logging.info("Checking video model type in VM XML")
        expect_video = self.get_expect_video_model()
        logging.info("Expect driver: %s", expect_video)
        pattern = r"<video>\s+<model type='(\w+)'"
        vmxml_video_type = re.search(pattern, self.vmxml).group(1)
        if vmxml_video_type != expect_video:
            err_msg = "Not find %s type video in VM XML" % expect_video
            self.log_err(err_msg)
        else:
            logging.info("PASS")

    def check_linux_vm(self):
        """
        Check linux VM after v2v convert.
        Only for RHEL VMs(RHEL4 or later)
        """
        self.checker.create_session()
        self.errors = []
        # 1. Check OS vender and distribution
        logging.info("Checking VM os info")
        os_info = self.checker.get_vm_os_info()
        os_vendor = self.checker.get_vm_os_vendor()
        logging.info("OS: %s", (os_info))
        if os_vendor != 'Red Hat':
            logging.warn("Skip non-RHEL VM check")
            return
        try:
            os_version = re.search(r'(\d\.?\d?)', os_info).group(1).split('.')
            dist_major = int(os_version[0])
        except Exception, e:
            err_msg = "Fail to get OS distribution: %s" % e
            self.log_err(err_msg)
        if dist_major < 4:
            logging.warn("Skip unspported distribution check")
            return
        else:
            logging.info("PASS")

        # 2. Check OS kernel
        logging.info("Checking VM kernel")
        kernel_version = self.checker.get_vm_kernel()
        logging.info("Kernel: %s", kernel_version)
        if re.search('xen', kernel_version, re.IGNORECASE):
            err_msg = "xen kernel still exist after convert"
            self.log_err(err_msg)
        else:
            logging.info("PASS")

        # 3. Check virtio module
        logging.info("Checking virtio kernel modules")
        modules = self.checker.get_vm_modules()
        if not re.search("virtio", modules):
            err_msg = "Not find virtio module"
            self.log_err(err_msg)
        else:
            logging.info("PASS")

        # 4. Check virtio PCI devices
        logging.info("Checking virtio PCI devices")
        pci_devs = self.checker.get_vm_pci_list()
        virtio_devs = ["Virtio network device", "Virtio block device"]
        if self.target != "ovirt":
            virtio_devs.append("Virtio memory balloon")
        for dev in virtio_devs:
            if not re.search(dev, pci_devs, re.IGNORECASE):
                err_msg = "Not find %s" % dev
                self.log_err(err_msg)
        else:
            logging.info("PASS")

        # 5. Check virtio disk partition
        logging.info("Checking virtio disk partition in device map")
        if not self.checker.get_grub_device():
            err_msg = "Not find vd? in disk partition"
            self.log_err(err_msg)
        else:
            logging.info("PASS")

        # 6. Check graphic/video
        self.check_vm_xml()
        logging.info("Checking video device inside the VM")
        expect_video = self.get_expect_video_model()
        # Since RHEL7, it use 'kms' instead of 'cirrus'
        if 'rhel7' in self.os_version and expect_video == 'cirrus':
            expect_video = 'kms'
        # As VM may not install X server, or X server not start completely,
        # checking xorg log will fail
        vm_xorg_log = self.checker.get_vm_xorg()
        if vm_xorg_log:
            if expect_video not in vm_xorg_log:
                err_msg = "Not find %s in Xorg log", expect_video
                self.log_err(err_msg)
            else:
                logging.info("PASS")
        else:
            logging.warning("Xorg log file not exist, skip checkpoint")

    def check_windows_vm(self):
        """
        Check windows guest after v2v convert.
        """
        # Make sure windows boot up successfully first
        self.checker.boot_windows()
        try:
            self.checker.create_session()
        except Exception as detail:
            raise exceptions.TestError('Failed to connect to windows guest: %s' %
                                       detail)
        logging.info("Wait 60 seconds for installing drivers")
        time.sleep(60)
        # Close and re-create session in case connection reset by peer during
        # sleeping time. Keep trying until the test command runs successfully.
        for retry in range(RETRY_TIMES):
            try:
                self.checker.run_cmd('dir')
            except:
                self.checker.session.close()
                self.checker.create_session()
            else:
                break
        self.errors = []
        # 1. Check viostor file
        logging.info("Checking windows viostor info")
        output = self.checker.get_viostor_info()
        if not output:
            err_msg = "Not find viostor info"
            self.log_err(err_msg)
        else:
            logging.info("PASS")

        # 2. Check Red Hat VirtIO drivers and display adapter
        logging.info("Checking VirtIO drivers and display adapter")
        win_dirves = self.checker.get_driver_info()
        expect_drivers = ["Red Hat VirtIO SCSI",
                          "Red Hat VirtIO Ethernet Adapte"]
        # Windows display adapter is different for each release
        if self.os_version in ['win7', 'win2008r2']:
            expect_adapter = 'QXL'
        if self.os_version in ['win2003', 'win2008']:
            expect_adapter = 'Standard VGA Graphics Adapter'
        if self.os_version in ['win8', 'win8.1', 'win10', 'win2012', 'win2012r2', 'win2016']:
            expect_adapter = 'Basic Display Driver'
        expect_drivers.append(expect_adapter)
        for driver in expect_drivers:
            if driver in win_dirves:
                logging.info("Find driver: %s", driver)
                logging.info("PASS")
            else:
                err_msg = "Not find driver: %s" % driver
                self.log_err(err_msg)

        # 3. Check graphic and video type in VM XML
        if self.compare_version(V2V_7_3_VERSION):
            self.check_vm_xml()

        # 4. Renew network
        logging.info("Renew network for windows guest")
        if not self.checker.get_network_restart():
            err_msg = "Renew network failed"
            self.log_err(err_msg)
        else:
            logging.info("PASS")

    def check_graphics(self, param):
        """
        Check if graphics attributes value in vm xml match with given param.
        """
        logging.info('Check graphics parameters')
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
                self.vm_name, options='--security-info',
                virsh_instance=self.virsh_instance)
        graphic = vmxml.xmltreefile.find('devices').find('graphics')
        status = True
        for key in param:
            logging.debug('%s = %s' % (key, graphic.get(key)))
            if graphic.get(key) != param[key]:
                logging.error('Attribute "%s" match failed' % key)
                status = False
        if not status:
            self.log_err('Graphic parameter check failed')
