import re
import logging
import time
from distutils.version import LooseVersion  # pylint: disable=E0611

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_v2v
from virttest import utils_sasl
from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.compat_52lts import results_stdout_52lts

V2V_7_3_VERSION = 'virt-v2v-1.32.1-1.el7'
RETRY_TIMES = 10
# Temporary workaround <Fix in future with a better solution>
FEATURE_SUPPORT = {
    'genid': 'virt-v2v-1.40.1-1.el7',
    'libosinfo': 'virt-v2v-1.40.2-2.el7'}


class VMChecker(object):

    """
    Check VM after virt-v2v converted
    """

    def __init__(self, test, params, env):
        self.errors = []
        self.params = params
        self.vm_name = params.get('main_vm')
        self.original_vm_name = params.get('original_vm_name')
        self.hypervisor = params.get("hypervisor")
        self.target = params.get('target')
        self.os_type = params.get('os_type')
        self.os_version = params.get('os_version', 'OS_VERSION_V2V_EXAMPLE')
        self.original_vmxml = params.get('original_vmxml')
        self.vmx_nfs_src = params.get('vmx_nfs_src')
        self.virsh_session = None
        self.virsh_session_id = None
        self.setup_session()
        self.checker = utils_v2v.VMCheck(test, params, env)
        self.checker.virsh_session_id = self.virsh_session_id
        self.virsh_instance = virsh.VirshPersistent(
            session_id=self.virsh_session_id)
        self.vmxml = virsh.dumpxml(
            self.vm_name,
            session_id=self.virsh_session_id).stdout.strip()
        # Save NFS mount records like {0:(src, dst, fstype)}
        self.mount_records = {}

    def cleanup(self):
        self.close_virsh_session()
        try:
            if self.checker.session:
                self.checker.session.close()
            self.checker.cleanup()
        except Exception:
            pass

        if len(self.mount_records) != 0:
            for src, dst, fstype in self.mount_records.values():
                utils_misc.umount(src, dst, fstype)

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
                    self.virsh_session = utils_sasl.VirshSessionSASL(
                        self.params)
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
        self.check_metadata_libosinfo()
        self.check_genid()
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
        v2v_version = LooseVersion(process.run(
            cmd, shell=True).stdout_text.strip())
        compare_version = LooseVersion(compare_version)
        if v2v_version >= compare_version:
            return True
        return False

    def get_expect_graphic_type(self):
        """
        The graphic type in VM XML is different for different target.
        """
        # 'ori_graphic' only can be set when hypervior is KVM. For Xen and
        # Esx, it will always be 'None' and 'vnc' will be set by default.
        graphic_type = self.params.get('ori_graphic', 'vnc')
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
            # video mode of windows guest will be cirrus if there is no virtio-win
            # driver installed on host
            if process.run('rpm -q virtio-win', ignore_status=True).exit_status != 0:
                video_model = 'cirrus'
        return video_model

    def check_metadata_libosinfo(self):
        """
        Check if metadata libosinfo attributes value in vm xml match with given param.

        Note: This is not a mandatory checking, if you need to check it, you have to
        set related parameters correctly.
        """
        logging.info("Checking metadata libosinfo")
        # 'os_short_id' must be set for libosinfo checking, you can query it by
        # 'osinfo-query os'
        short_id = self.params.get('os_short_id')
        if not short_id:
            reason = 'short_id is not set'
            logging.info(
                'Skip Checking metadata libosinfo parameters: %s' %
                reason)
            return

        # Checking if the feature is supported
        if not self.compare_version(FEATURE_SUPPORT['libosinfo']):
            reason = "Unsupported if v2v < %s" % FEATURE_SUPPORT['libosinfo']
            logging.info(
                'Skip Checking metadata libosinfo parameters: %s' %
                reason)
            return

        # Need target or output_mode be set explicitly
        if not self.params.get(
                'target') and not self.params.get('output_mode'):
            reason = 'Both target and output_mode are not set'
            logging.info(
                'Skip Checking metadata libosinfo parameters: %s' %
                reason)
            return

        supported_output = ['libvirt', 'local']
        # Skip checking if any of them is not in supported list
        if self.params.get('target') not in supported_output or self.params.get(
                'output_mode') not in supported_output:
            reason = 'target or output_mode is not in %s' % supported_output
            logging.info(
                'Skip Checking metadata libosinfo parameters: %s' %
                reason)
            return

        cmd = 'osinfo-query os --fields=short-id | tail -n +3'
        # Too much debug output if verbose is True
        output = process.run(cmd, timeout=20, shell=True, ignore_status=True)
        short_id_all = results_stdout_52lts(output).splitlines()
        if short_id not in [os_id.strip() for os_id in short_id_all]:
            raise exceptions.TestError('Invalid short_id: %s' % short_id)

        cmd = "osinfo-query os --fields=id short-id='%s'| tail -n +3" % short_id
        output = process.run(
            cmd,
            timeout=20,
            verbose=True,
            shell=True,
            ignore_status=True)
        long_id = results_stdout_52lts(output).strip()
        # '<libosinfo:os id' was changed to '<ns0:os id' after calling
        # vm_xml.VMXML.new_from_inactive_dumpxml.
        # It's problably a problem in vm_xml.
        # <TODO>  Fix it
        #libosinfo_pattern = r'<libosinfo:os id="%s"/>' % long_id
        # A temp workaround for above problem
        libosinfo_pattern = r'<.*?:os id="%s"/>' % long_id
        logging.info('libosinfo pattern: %s' % libosinfo_pattern)

        if not re.search(libosinfo_pattern, self.vmxml):
            self.log_err('Not find metadata libosinfo')

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
        except Exception as e:
            err_msg = "Fail to get OS distribution: %s" % e
            self.log_err(err_msg)
        if dist_major < 4:
            logging.warning("Skip unsupported distribution check")
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
        if self.checker.is_uefi_guest():
            logging.info("The guest is uefi mode,skip the checkpoint")
        elif not self.checker.get_grub_device():
            err_msg = "Not find vd? in disk partition"
            if self.hypervisor != 'kvm':
                self.log_err(err_msg)
            else:
                # Just warning the err if converting from KVM. It may
                # happen that disk's bus type in xml is not the real bus
                # type be used when preparing the image. Ex, if the image
                # is installed with IDE, then you import the image with
                # bus virtio, the device.map file will be inconsistent with
                # the xml.
                # V2V doesn't modify device.map file for this scenario.
                logging.warning(err_msg)
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
                err_msg = "Not find %s in Xorg log" % expect_video
                logging.info(err_msg)
                # RHEL8 desn't include any qxl string in xorg log.
                # If expect_video is in lspci output, we think it passed.
                if re.search(expect_video, pci_devs, re.IGNORECASE) is None:
                    err_msg += " And Not find %s device by lspci" % expect_video
                    self.log_err(err_msg)
                    return
            logging.info("PASS")
        else:
            logging.warning("Xorg log file not exist, skip checkpoint")

    def check_windows_vm(self):
        """
        Check windows guest after v2v convert.
        """
        try:
            # Sometimes windows guests needs >10mins to finish drivers
            # installation
            self.checker.create_session(timeout=900)
        except Exception as detail:
            raise exceptions.TestError(
                'Failed to connect to windows guest: %s' %
                detail)
        logging.info("Wait 60 seconds for installing drivers")
        time.sleep(60)
        # Close and re-create session in case connection reset by peer during
        # sleeping time. Keep trying until the test command runs successfully.
        for retry in range(RETRY_TIMES):
            try:
                self.checker.run_cmd('dir')
            except BaseException:
                self.checker.session.close()
                self.checker.create_session()
            else:
                break
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
        expect_drivers = ["Red Hat VirtIO SCSI",
                          "Red Hat VirtIO Ethernet Adapte"]
        # Windows display adapter is different for each release
        if self.os_version in ['win7', 'win2008r2']:
            expect_adapter = 'QXL'
        if self.os_version in ['win2003', 'win2008']:
            expect_adapter = 'Standard VGA Graphics Adapter'
        bdd_list = [
            'win8',
            'win8.1',
            'win10',
            'win2012',
            'win2012r2',
            'win2016',
            'win2019']
        if self.os_version in bdd_list:
            expect_adapter = 'Basic Display Driver'
        expect_drivers.append(expect_adapter)
        check_drivers = expect_drivers[:]
        for check_times in range(5):
            logging.info('Check drivers for the %dth time', check_times + 1)
            win_dirvers = self.checker.get_driver_info()
            for driver in expect_drivers:
                if driver in win_dirvers:
                    logging.info("Driver %s found", driver)
                    check_drivers.remove(driver)
                else:
                    err_msg = "Driver %s not found" % driver
                    logging.error(err_msg)
            expect_drivers = check_drivers[:]
            if not expect_drivers:
                break
            else:
                wait = 60
                logging.info('Wait another %d seconds...', wait)
                time.sleep(wait)
        if expect_drivers:
            for driver in expect_drivers:
                self.log_err("Not find driver: %s" % driver)

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

    def check_genid(self):
        """
        Check genid value in vm xml match with given param.
        """
        def _compose_genid(vm_genid, vm_genidX):
            for index, val in enumerate(
                    map(lambda x: hex(int(x) & ((1 << 64) - 1)), [vm_genid, vm_genidX])):
                if index == 0:
                    gen_id = '-'.join([val[n:] if n == -8 else val[n:n + 4]
                                       for n in range(-8, -17, -4)])
                elif index == 1:
                    temp_str = ''.join([val[i:i + 2]
                                        for i in range(0, len(val), 2)][:0:-1])
                    gen_idX = temp_str[:4] + '-' + temp_str[4:]
            return gen_id + '-' + gen_idX

        has_genid = self.params.get('has_genid')
        if not has_genid:
            return

        # Checking if the feature is supported
        if not self.compare_version(FEATURE_SUPPORT['genid']):
            reason = "Unsupported if v2v < %s" % FEATURE_SUPPORT['genid']
            logging.info('Skip Checking genid: %s' % reason)
            return

        supported_output = ['libvirt', 'local', 'qemu']
        # Skip checking if any of them is not in supported list
        if self.params.get('output_mode') not in supported_output:
            reason = 'output_mode is not in %s' % supported_output
            logging.info('Skip Checking genid: %s' % reason)
            return

        logging.info('Checking genid info in xml')
        logging.debug('vmxml is:\n%s' % self.vmxml)
        if has_genid == 'yes':
            mount_point = utils_v2v.v2v_mount(self.vmx_nfs_src, 'vmx_nfs_src')
            # For clean up
            self.mount_records[len(self.mount_records)] = (
                self.vmx_nfs_src, mount_point, None)

            cmd = "cat {}/{name}/{name}.vmx".format(
                mount_point, name=self.original_vm_name)
            cmd_result = process.run(cmd, timeout=20, ignore_status=True)
            cmd_result.stdout = results_stdout_52lts(cmd_result)
            genid_pattern = r'vm.genid = "(-?\d+)"'
            genidX_pattern = r'vm.genidX = "(-?\d+)"'

            genid_list = [
                re.search(
                    i, cmd_result.stdout).group(1) if re.search(
                    i, cmd_result.stdout) else None for i in [
                    genid_pattern, genidX_pattern]]
            if not all(genid_list):
                logging.info(
                    'vm.genid or vm.genidX is missing:%s' %
                    genid_list)
                # genid will not be in vmxml
                if re.search(r'genid', self.vmxml):
                    self.log_err('Unexpected genid in xml')
                return

            genid_str = _compose_genid(*genid_list)
            logging.debug('genid string is %s' % genid_str)

            if not re.search(genid_str, self.vmxml):
                self.log_err('Not find genid or genid is incorrect')
        elif has_genid == 'no':
            if re.search(r'genid', self.vmxml):
                self.log_err('Unexpected genid in xml')
