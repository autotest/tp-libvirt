import re
import logging
import commands
from autotest.client.shared import error
from virttest import utils_v2v
from virttest import utils_sasl


def run(test, params, env):
    """
    Check VM after conversion
    """
    target = params.get('target')
    vm_name = params.get('main_vm')
    os_type = params.get('os_type', 'linux')
    target = params.get('target', 'libvirt')

    def log_err(errs, msg):
        logging.error(msg)
        errs.append(msg)

    def check_linux_vm(check_obj):
        """
        Check linux guest after v2v convert.
        """
        # Create ssh session for linux guest
        check_obj.create_session()

        errs = []
        # 1. Check OS vender and version
        logging.info("Check guest os info")
        os_info = check_obj.get_vm_os_info()
        os_vendor = check_obj.get_vm_os_vendor()
        if os_vendor == 'Red Hat':
            os_version = os_info.split()[6]
        else:
            err_msg = "Only support RHEL for now"
            log_err(errs, err_msg)

        # 2. Check OS kernel
        logging.info("Check guest kernel")
        kernel_version = check_obj.get_vm_kernel()
        if re.search('xen', kernel_version, re.IGNORECASE):
            err_msg = "Still find xen kernel"
            log_err(errs, err_msg)

        # 3. Check disk partition
        logging.info("Check 'vdX' in disk partiton")
        parted_info = check_obj.get_vm_parted()
        if os_version != '3':
            if not re.findall('/dev/vd\S+', parted_info):
                err_msg = "Not find vdX"
                log_err(errs, err_msg)

        # 4. Check virtio_net in /etc/modprobe.conf
        # BTW, this file is removed on RHEL6 and later releases
        logging.info("Check virtio_net module in modprobe.conf")
        modprobe_conf = check_obj.get_vm_modprobe_conf()
        if not re.search('No such file', modprobe_conf, re.IGNORECASE):
            virtio_mod = re.findall(r'(?m)^alias.*virtio', modprobe_conf)
            net_blk_mod = re.findall(r'(?m)^alias\s+scsi|(?m)^alias\s+eth',
                                     modprobe_conf)
            if len(virtio_mod) != len(net_blk_mod):
                err_msg = "Unexpected content in modprobe.conf"
                log_err(errs, err_msg)

        # 5. Check kernel modules
        # BTW, RHEL3 not support virtio, so it may use 'e1000' and 'ide'
        logging.info("Check kernel modules")
        modules = check_obj.get_vm_modules()
        if os_version == '3':
            if not re.search("e1000|^ide", modules, re.IGNORECASE):
                err_msg = "Not find e1000|^ide module"
                log_err(errs, err_msg)
        elif not re.search("virtio", modules, re.IGNORECASE):
            err_msg = "Not find virtio module"
            log_err(errs, err_msg)

        # 6. Check virtio PCI devices
        if os_version != '3':
            logging.info("Check virtio PCI devices")
            pci = check_obj.get_vm_pci_list()
            if (re.search('Virtio network', pci, re.IGNORECASE) and
                    re.search('Virtio block', pci, re.IGNORECASE)):
                if (target != "ovirt" and not
                        re.search('Virtio memory', pci, re.IGNORECASE)):
                    err_msg = "Not find Virtio memory balloon"
                    log_err(errs, err_msg)
            else:
                err_msg = "Not find Virtio network and block devices"
                log_err(errs, err_msg)

        # 7. Check tty
        logging.info("Check tty")
        tty = check_obj.get_vm_tty()
        if re.search('[xh]vc0', tty, re.IGNORECASE):
            err_msg = "Unexpected [xh]vc0"
            log_err(errs, err_msg)

        # 8. Check video
        logging.info("Check video")
        video = check_obj.get_vm_video()
        if target == 'ovirt':
            if not re.search('qxl', video, re.IGNORECASE):
                err_msg = "Not find QXL driver after convert vm to oVirt"
                log_err(errs, err_msg)
        else:
            # dump VM XML
            cmd = "virsh dumpxml %s |grep -A 3 '<video>'" % vm_name
            status, output = commands.getstatusoutput(cmd)
            if status:
                raise error.TestError(vm_name, output)

            video_model = ""
            video_type = re.search("type='[a-z]*'", output, re.IGNORECASE)
            if video_type:
                video_model = eval(video_type.group(0).split('=')[1])

            if re.search('el7', kernel_version):
                if 'cirrus' in output:
                    if not re.search('kms', video, re.IGNORECASE):
                        err_msg = "Not find 'kms' for 'cirrus' video"
                        log_err(errs, err_msg)
                else:
                    if not re.search(video_model, video, re.IGNORECASE):
                        err_msg = "Not find '%s' video" % video_model
                        log_err(errs, err_msg)
            else:
                if not re.search(video_model, video, re.IGNORECASE):
                    err_msg = "Not find '%s' video" % video_model
                    log_err(errs, err_msg)

        # 9. Check device map
        logging.info("Check device map")
        dev_map = ""
        if re.search('el7', kernel_version):
            dev_map = '/boot/grub2/device.map'
        else:
            dev_map = '/boot/grub/device.map'
        if not check_obj.get_grub_device(dev_map):
            err_msg = "Not find vdX disk in device map"
            log_err(errs, err_msg)

        if errs:
            raise error.TestFail("Check failed: %s" % errs)
        else:
            logging.info("All check passed")

    def check_windows_vm(check_obj):
        """
        Check windows guest after v2v convert.
        """
        # Initialize windows boot up
        check_obj.init_windows()

        # Create nc/telnet session for windows guest
        check_obj.create_session()

        errs = []
        # 1. Check viostor file
        logging.info("Check windows viostor info")
        output = check_obj.get_viostor_info()
        if not output:
            err_msg = "Windows viostor info check failed"
            log_err(errs, err_msg)

        # 2. Check Red Hat drivers
        logging.info("Check Red Hat drivers")
        win_dirves = check_obj.get_driver_info()
        virtio_drivers = ["Red Hat VirtIO SCSI",
                          "Red Hat VirtIO Ethernet Adapte"]
        for driver in virtio_drivers:
            if driver in win_dirves:
                logging.info("Find driver: %s", driver)
            else:
                err_msg = "Not find driver: %s" % driver
                errs.append(err_msg)
                log_err(errs, err_msg)

        # TODO: This part should be update after bug fix
        # Now, virt-v2v has bugs about video driver, all guests will
        # vga video after convirt except windows2008r2.
        video_driver = "vga"
        if target == "ovirt":
            video_driver = "qxl"
        win_dirves = check_obj.get_driver_info(signed=False)
        if video_driver in win_dirves:
            logging.info("Find driver: %s", video_driver)
        else:
            err_msg = "Not find driver: %s" % video_driver
            logging.error(err_msg)
            #log_err(errs, err_msg)

        # 3. Renew network
        logging.info("Renew network for windows guest")
        if not check_obj.get_network_restart():
            err_msg = "Renew network failed"
            log_err(errs, err_msg)

        if errs:
            raise error.TestFail("Check failed: %s" % errs)
        else:
            logging.info("All check passed")

    check_obj = utils_v2v.VMCheck(test, params, env)
    virsh_session = None
    try:
        virsh_session_id = None
        if target == "ovirt":
            virsh_session = utils_sasl.VirshSessionSASL(params)
            virsh_session_id = virsh_session.get_id()
        check_obj.virsh_session_id = virsh_session_id
        if os_type == "linux":
            check_linux_vm(check_obj)
        else:
            check_windows_vm(check_obj)
    finally:
        if virsh_session:
            virsh_session.close()
        if check_obj:
            if check_obj.session:
                check_obj.session.close()
            check_obj.cleanup()
