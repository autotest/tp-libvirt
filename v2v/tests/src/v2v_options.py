"""
Test all options of command: virt-v2v
"""
import os
import re
import pwd
import logging
import shutil
import time

from autotest.client import utils
from autotest.client.shared import ssh_key
from autotest.client.shared import error
from xml.etree import ElementTree as ET

from virttest import virsh
from virttest import utils_v2v
from virttest import utils_misc
from virttest import utils_sasl
from virttest import libvirt_vm
from virttest import remote
from virttest import ppm_utils
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test various options of virt-v2v.
    """
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    vm_name = params.get("main_vm", "EXAMPLE")
    new_vm_name = params.get("new_vm_name")
    input_mode = params.get("input_mode")
    v2v_options = params.get("v2v_options", "")
    hypervisor = params.get("hypervisor", "kvm")
    remote_host = params.get("remote_host", "EXAMPLE")
    vpx_dc = params.get("vpx_dc", "EXAMPLE")
    esx_ip = params.get("esx_ip", "EXAMPLE")
    vpx_passwd = params.get("vpx_passwd", "EXAMPLE")
    ovirt_engine_url = params.get("ovirt_engine_url", "EXAMPLE")
    ovirt_engine_user = params.get("ovirt_engine_user", "EXAMPLE")
    ovirt_engine_passwd = params.get("ovirt_engine_password", "EXAMPLE")
    output_mode = params.get("output_mode")
    output_storage = params.get("output_storage", "default")
    export_name = params.get("export_name", "EXAMPLE")
    storage_name = params.get("storage_name", "EXAMPLE")
    disk_img = params.get("input_disk_image", "")
    nfs_storage = params.get("nfs_storage")
    mnt_point = params.get("mount_point")
    export_domain_uuid = params.get("export_domain_uuid", "")
    fake_domain_uuid = params.get("fake_domain_uuid")
    vdsm_image_uuid = params.get("vdsm_image_uuid")
    vdsm_vol_uuid = params.get("vdsm_vol_uuid")
    vdsm_vm_uuid = params.get("vdsm_vm_uuid")
    vdsm_ovf_output = params.get("vdsm_ovf_output")
    v2v_user = params.get("unprivileged_user", "")
    v2v_timeout = int(params.get("v2v_timeout", 1200))
    status_error = "yes" == params.get("status_error", "no")
    vm_user = params.get("vm_user")
    vm_pwd = params.get("vm_pwd")
    xen_host = params.get("xen_hostname")
    xen_host_user = params.get("xen_host_user")
    xen_host_passwd = params.get("xen_host_passwd")
    virtio = "yes" == params.get('virtio', 'no')
    virtio_on = "yes" == params.get('virtio_on', 'no')
    checkpoint = params.get("checkpoint")
    debug_kernel = 'debug_kernel' == params.get('checkpoint')
    os_type = params.get("os_type", "linux")
    for param in [vm_name, esx_ip, vpx_dc, ovirt_engine_url,
                  ovirt_engine_user, ovirt_engine_passwd, output_storage,
                  export_name, storage_name, disk_img, export_domain_uuid,
                  v2v_user]:
        if "EXAMPLE" in param:
            raise error.TestNAError("Please replace %s with real value" %
                                    param)

    su_cmd = "su - %s -c " % v2v_user
    output_uri = params.get("oc_uri", "")
    pool_name = params.get("pool_name", "v2v_test")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target_path", "v2v_pool")
    emulated_img = params.get("emulated_image_path", "v2v-emulated-img")
    pvt = utlv.PoolVolumeTest(test, params)
    new_v2v_user = False
    restore_image_owner = False
    address_cache = env.get('address_cache')
    params['vmcheck'] = None
    multi_kernel_lst = ["multi_kernel", "debug_kernel", "vmlinuz_init"]
    check_list_boot = ["noyumrepo-rhn", "kdump", "multiconsole", "xen_uuid",
                       "pool_uuid", "floppy", "corrupt_rpmdb"]
    revert_xml_lst = ["virtio", "fstab_virtio", "fstab_cdrom", "floppy",
                      "floppy_devmap"]

    def create_pool():
        """
        Create libvirt pool as the output storage
        """
        if output_uri == "qemu:///session":
            target_path = os.path.join("/home", v2v_user, pool_target)
            cmd = su_cmd + "'mkdir %s'" % target_path
            utils.system(cmd, verbose=True)
            cmd = su_cmd + "'virsh pool-create-as %s dir" % pool_name
            cmd += " --target %s'" % target_path
            utils.system(cmd, verbose=True)
        else:
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_img)

    def cleanup_pool():
        """
        Clean up libvirt pool
        """
        if output_uri == "qemu:///session":
            cmd = su_cmd + "'virsh pool-destroy %s'" % pool_name
            utils.system(cmd, verbose=True)
            target_path = os.path.join("/home", v2v_user, pool_target)
            cmd = su_cmd + "'rm -rf %s'" % target_path
            utils.system(cmd, verbose=True)
        else:
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)

    def get_all_uuids(output):
        """
        Get export domain uuid, image uuid and vol uuid from command output.
        """
        tmp_target = re.findall(r"qemu-img\sconvert\s.+\s'(\S+)'\n", output)
        if len(tmp_target) < 1:
            raise error.TestError("Fail to find tmp target file name when"
                                  " converting vm disk image")
        targets = tmp_target[0].split('/')
        return (targets[3], targets[5], targets[6])

    def get_ovf_content(output):
        """
        Find and read ovf file.
        """
        export_domain_uuid, _, vol_uuid = get_all_uuids(output)
        export_vm_dir = os.path.join(mnt_point, export_domain_uuid,
                                     'master/vms')
        ovf_content = ""
        if os.path.isdir(export_vm_dir):
            ovf_id = "ovf:id='%s'" % vol_uuid
            ret = utils.system_output("grep -R \"%s\" %s" % (ovf_id,
                                                             export_vm_dir))
            ovf_file = ret.split(":")[0]
            if os.path.isfile(ovf_file):
                ovf_f = open(ovf_file, "r")
                ovf_content = ovf_f.read()
                ovf_f.close()
        else:
            logging.error("Can't find ovf file to read")
        return ovf_content

    def get_img_path(output):
        """
        Get the full path of the converted image.
        """
        img_path = ""
        img_name = vm_name + "-sda"
        if output_mode == "libvirt":
            img_path = virsh.vol_path(img_name, output_storage).stdout.strip()
        elif output_mode == "local":
            img_path = os.path.join(output_storage, img_name)
        elif output_mode in ["rhev", "vdsm"]:
            export_domain_uuid, image_uuid, vol_uuid = get_all_uuids(output)
            img_path = os.path.join(mnt_point, export_domain_uuid, 'images',
                                    image_uuid, vol_uuid)
        if not img_path or not os.path.isfile(img_path):
            raise error.TestError("Get image path: '%s' is invalid", img_path)
        return img_path

    def check_vmtype(ovf, expected_vmtype):
        """
        Verify vmtype in ovf file.
        """
        if output_mode != "rhev":
            return
        if expected_vmtype == "server":
            vmtype_int = 1
        elif expected_vmtype == "desktop":
            vmtype_int = 0
        else:
            return
        if "<VmType>%s</VmType>" % vmtype_int in ovf:
            logging.info("Find VmType=%s in ovf file",
                         expected_vmtype)
        else:
            raise error.TestFail("VmType check failed")

    def check_image(img_path, check_point, expected_value):
        """
        Verify image file allocation mode and format
        """
        img_info = utils_misc.get_image_info(img_path)
        logging.debug("Image info: %s", img_info)
        if check_point == "allocation":
            if expected_value == "sparse":
                if img_info['vsize'] > img_info['dsize']:
                    logging.info("%s is a sparse image", img_path)
                else:
                    raise error.TestFail("%s is not a sparse image" % img_path)
            elif expected_value == "preallocated":
                if img_info['vsize'] <= img_info['dsize']:
                    logging.info("%s is a preallocated image", img_path)
                else:
                    raise error.TestFail("%s is not a preallocated image"
                                         % img_path)
        if check_point == "format":
            if expected_value == img_info['format']:
                logging.info("%s format is %s", img_path, expected_value)
            else:
                raise error.TestFail("%s format is not %s"
                                     % (img_path, expected_value))

    def check_new_name(output, expected_name):
        """
        Verify guest name changed to the new name.
        """
        found = False
        if output_mode == "libvirt":
            found = virsh.domain_exists(expected_name)
        if output_mode == "local":
            found = os.path.isfile(os.path.join(output_storage,
                                                expected_name + "-sda"))
        if output_mode in ["rhev", "vdsm"]:
            ovf = get_ovf_content(output)
            found = "<Name>%s</Name>" % expected_name in ovf
        else:
            return
        if found:
            logging.info("Guest name renamed when converting it")
        else:
            raise error.TestFail("Rename guest failed")

    def check_nocopy(output):
        """
        Verify no image created if convert command use --no-copy option
        """
        img_path = get_img_path(output)
        if not os.path.isfile(img_path):
            logging.info("No image created with --no-copy option")
        else:
            raise error.TestFail("Find %s" % img_path)

    def check_connection(output, expected_uri):
        """
        Check output connection uri used when converting guest
        """
        init_msg = "Initializing the target -o libvirt -oc %s" % expected_uri
        if init_msg in output:
            logging.info("Find message: %s", init_msg)
        else:
            raise error.TestFail("Not find message: %s" % init_msg)

    def check_disks(ori_disks):
        """
        Check disk counts inside the VM
        """
        vmcheck = params.get("vmcheck")
        if vmcheck is None:
            raise error.TestError("VM check object is None")
        # Initialize windows boot up
        os_type = params.get("os_type", "linux")
        if os_type == "windows":
            virsh_session = utils_sasl.VirshSessionSASL(params)
            virsh_session_id = virsh_session.get_id()
            vmcheck.virsh_session_id = virsh_session_id
            vmcheck.init_windows()
            virsh_session.close()
        # Creatge VM session
        vmcheck.create_session()
        expected_disks = int(params.get("added_disks_count", "1")) - ori_disks
        logging.debug("Expect %s disks im VM after convert", expected_disks)
        # Get disk counts
        disks = 0
        if os_type == "linux":
            cmd = "lsblk |grep disk |wc -l"
            disks = int(vmcheck.session.cmd(cmd).strip())
        else:
            cmd = r"echo list disk > C:\list_disk.txt"
            vmcheck.session.cmd(cmd)
            cmd = r"diskpart /s C:\list_disk.txt"
            output = vmcheck.session.cmd(cmd).strip()
            logging.debug("Disks in VM: %s", output)
            disks = len(output.splitlines()) - 6
        logging.debug("Find %s disks in VM after convert", disks)
        vmcheck.session.close()
        if disks == expected_disks:
            logging.info("Disk counts is expected")
        else:
            raise error.TestFail("Disk counts is wrong")

    def vm_shell(func):
        """
        Decorator of shell session
        """
        def wrapper(*args, **kwargs):
            vm = libvirt_vm.VM(vm_name, params, test.bindir,
                               env.get("address_cache"))
            session = vm_login(vm)
            kwargs["session"] = session
            func(*args, **kwargs)
            if session:
                session.close()
            vm.shutdown()
        return wrapper

    def virsh_edit(func):
        """
        Decorator of virsh edit
        """
        def wrapper(*args, **kwargs):
            tmp_xml = "/tmp/%s.xml" % str(time.time())
            virsh.dumpxml(vm_name, to_file=tmp_xml)
            params["bk_xml"] = "/tmp/%s.xml" % str(time.clock())
            cmd_bk = "cp %s %s" % (tmp_xml, params["bk_xml"])
            utils.run(cmd_bk, timeout=v2v_timeout, verbose=True)
            tree = ET.ElementTree(file=tmp_xml)
            kwargs["tree"] = tree
            func(*args, **kwargs)
            tree.write(tmp_xml)
            virsh.define(tmp_xml)
        return wrapper

    def vm_login(vm):
        """
        Create connection to vm 30 times
        """
        if vm.is_dead():
            logging.info('VM is down. Starting it now.')
            vm.start()
        retry = 30
        while retry >= 0:
            try:
                session = vm.login(username=vm_user, password=vm_pwd)
                return session
            except:
                logging.info('Retrying...')
                retry -= 1
                time.sleep(10)
        raise error.TestError('Connect to VM failed')

    def install_kernel(session, url=None, debug=False):
        """
        Install kernel to vm
        """
        if debug:
            if session.cmd_status('yum -y install kernel-debug', timeout=600):
                raise error.TestFail("Fail on installing debug kernel")
            else:
                logging.info('install debug kernel success')
            return 'kernel-debug'

        # Check if kernel already exists
        kernels = session.cmd('rpm -q kernel')
        logging.debug(kernels)
        cmd_get_kernel = "wget %s" % url
        kernel_fname = url.split('/')[-1]
        kernel_name = '.'.join(kernel_fname.split('.')[:-1])
        logging.debug('kernel to install is %s' % kernel_name)
        if kernel_name in kernels:
            raise error.TestError('Kernel %s already exists!' % kernel_name)
        cmd_install = "rpm -iv %s" % kernel_fname

        # rhel6 need to install kernel-firmware first
        if not session.cmd_status('cat /boot/grub/grub.conf'):
            kernel_fm_url = params.get('kernel_fm_url')
            fm_name = kernel_fm_url.split('/')[-1]
            cmd_get_kernel_fm = 'wget %s' % kernel_fm_url
            cmd_install_kernel_fm = 'rpm -Uv %s' % fm_name
            fm = '.'.join(fm_name.split('.')[:-1])
            exist_fm = session.cmd("rpm -q kernel-firmware",
                                   ignore_all_errors=True)
            if exist_fm is None or fm not in exist_fm:
                session.cmd(cmd_get_kernel_fm, timeout=v2v_timeout)
                session.cmd(cmd_install_kernel_fm, timeout=v2v_timeout)
                session.cmd("rm -f %s" % fm_name)

        session.cmd(cmd_get_kernel, timeout=v2v_timeout)
        session.cmd(cmd_install, timeout=v2v_timeout)
        session.cmd("rm -f %s" % kernel_fname)
        return kernel_name

    @vm_shell
    def cleanup_kernel(kernel, debug=False, **kwargs):
        """
        Clean up test kernel and revert grub entry
        """
        session = kwargs["session"]
        if debug:
            kernel_to_clean = 'kernel-debug'
        else:
            kernel_to_clean = kernel
        logging.info('Removing test kernel')
        session.cmd('rpm -e %s' % kernel_to_clean)
        # fm = params.get('fm_name')
        if session.cmd_status("ls /boot/grub/grub.conf"):
            session.cmd("grub2-set-default 0")
        else:
            session.cmd("sed -i 's/default=./default=0/' /boot/grub/grub.conf")

    def set_grub_seq(session, debug=False):
        """
        Set default kernel to the second one or to debug kernel
        """
        if debug:
            logging.info('installing kernel-debug')
            params['installed_kernel'] = install_kernel(session, debug=True)
        elif input_mode == 'libvirt' and hypervisor == 'kvm':
            logging.info('For kvm: Installing kernel...')
            kernel1_url = params.get('kernel1_url')
            params['installed_kernel'] = install_kernel(session, kernel1_url)
        else:
            raise error.TestNAError('Not for multi-kernel')

        if not session.cmd_status('cat /boot/grub/menu.lst'):
            grub = 1
            out = session.cmd(
                    "cat /boot/grub/menu.lst |grep 'initrd /initramfs-'"
            )
            kernel_lst = re.findall('initrd /initramfs-.*', out)
            logging.debug(kernel_lst)
        else:
            grub = 2
            cmd_list_all = 'cat /boot/grub2/grub.cfg |grep menuentry'
            out = session.cmd(cmd_list_all)
            kernel_lst = re.findall("menuentry '.*?'", out)
            logging.debug(kernel_lst)
        seq = 1

        if debug:
            logging.info('Setting debug kernel as default')
            for i in range(len(kernel_lst)):
                if 'debug' in kernel_lst[i]:
                    seq = i
                    break

        if grub == 1:
            cmd_set_grub = \
                "sed -i 's/default=./default=%d/' /boot/grub/grub.conf" % seq
            session.cmd(cmd_set_grub)
            kernel_to_set = kernel_lst[seq]
        elif grub == 2:
            kernel_to_set = kernel_lst[seq].split("'")[1].strip("'")
            cmd_set_grub = 'grub2-set-default "%s"' % kernel_to_set
            session.cmd(cmd_set_grub)
        else:
            raise error.TestFail("Grub version Unknown")

        return kernel_to_set

    @vm_shell
    def multi_kernel(*args, **kwargs):
        """
        Make multi-kernel test
        """
        session = kwargs["session"]
        params['installed_kernel'] = ''
        params['defaultkernel'] = set_grub_seq(session, debug_kernel)

    def check_vmlinuz_initramfs(v2v_result):
        """
        Check if vmlinuz matches initramfs on multi-kernel case
        """
        logging.info('Checking if vmlinuz matches initramfs')
        kernels = re.search(
                r'kernel packages in this guest:(.*?)grub kernels in this',
                v2v_result, flags=re.DOTALL
        )
        try:
            lines = kernels.group(1)
            kernel_lst = re.findall('\((.*?)\)', lines)
            for kernel in kernel_lst:
                vmlinuz = re.search(r'/boot/vmlinuz-(.*?),', kernel).group(1)
                initramfs = \
                    re.search(r'/boot/initramfs-(.*?)\.img', kernel).group(1)
                logging.debug('vmlinuz version is: %s' % vmlinuz)
                logging.debug('initramfs version is: %s' % initramfs)
                if vmlinuz != initramfs:
                    raise error.TestFail("vmlinz not match with initramfs")
        except Exception, e:
            raise error.TestError(
                    "Error on finding installed kernel info \n %s" % str(e)
            )

    def check_multi_kernel(default_kernel, debug=False):
        """
        Check if converted vm use the default kernel
        """
        logging.debug('debug kernel?: %s' % debug)
        vmcheck = params.get("vmcheck")
        if vmcheck:
            logging.info('Connecting to converted VM')
            vmcheck.create_session()
            current_kernel = vmcheck.session.cmd('uname -r').strip()
            if vmcheck.session:
                vmcheck.session.close()
            logging.debug('Current kernel: %s' % current_kernel)
            logging.debug('Default kernel: %s' % default_kernel)
        else:
            raise error.TestError('vmcheck is missing!')
        if debug:
            if 'debug' in current_kernel:
                raise error.TestFail("Chose debug kernel over non-debug")
        elif current_kernel not in default_kernel:
            raise error.TestFail("Chose 1st kernel over default kernel")

    def is_disk_virtio(vm):
        """
        Check if vm disk is virtio
        """
        xml = vm.get_xml()
        m = re.search(r"<disk.*?<target.*? bus='virtio'/>.*?</disk>", xml,
                      flags=re.DOTALL)
        if m:
            return True
        else:
            return False

    def check_floppy():
        """
        Check if floppy exists after convertion
        """
        vm = params.get("vmcheck")
        if vm:
            devices = virsh.domblklist(vm_name, session_id=vm.virsh_session_id) \
                .stdout
            logging.debug(devices)
            if not re.search(r"\bfda\b", devices):
                raise error.TestFail("Floppy not found")
        else:
            raise error.TestError("vmcheck is None")

    def virsh_revert():
        """
        Revert backup xml after test
        """
        bk_xml = params.get("bk_xml")
        if bk_xml:
            virsh.define(bk_xml)
        else:
            logging.warning("No backup xml found.")

    @virsh_edit
    def attach_floppy(img_path, floppy_name, **kwargs):
        """
        Attach floppy to vm if there isn't one
        """
        tree = kwargs["tree"]
        floppy_lst = tree.findall(".//devices/disk[@device='floppy']")
        if not floppy_lst:
            devices = tree.find(".//devices")
            node = ET.fromstring("""<disk type='file' device='floppy'>
                             <driver name='qemu' type='raw'/>
                             <source file='%s/%s.img'/>
                             <target dev='fda' bus='fdc'/>
                         </disk> """ % (img_path, floppy_name))
            devices.append(node)

    @virsh_edit
    def enable_disk_virtio(*args, **kwargs):
        """
        Change disk to virtio
        """
        logging.info("Enable disk virtio")
        disk_lst = kwargs["tree"].findall(".//devices/disk[@device='disk']")
        if disk_lst:
            logging.info(disk_lst)
            node = disk_lst[0]
            for child in node:
                if child.tag == "target":
                    child.set("bus", "virtio")
                    child.set("dev", "vda")
                if child.tag == "address":
                    node.remove(child)

    @virsh_edit
    def disable_disk_virtio(*args, **kwargs):
        """
        Change disk to ide
        """
        logging.info("Disable disk virtio")
        disk_lst = kwargs["tree"].findall(".//devices/disk[@device='disk']")
        if disk_lst:
            logging.info(disk_lst)
            node = disk_lst[0]
            for child in node:
                if child.tag == "target":
                    child.set("bus", "ide")
                    child.set("dev", "hda")
                if child.tag == "address":
                    node.remove(child)

    @virsh_edit
    def attach_cdrom(img_path, cdrom_name, **kwargs):
        """
        Attach cdrom to vm if there isn't one
        """
        logging.info("Attaching CDROM")
        tree = kwargs["tree"]
        cdrom_lst = tree.findall(".//devices/disk[@device='cdrom']")
        if not cdrom_lst:
            devices = tree.find(".//devices")
            node = ET.fromstring("""
                <disk type='file' device='cdrom'>
                  <driver name='qemu' type='raw'/>
                  <source file='%s/%s.iso'/>
                  <target dev='hdb' bus='ide'/>
                  <readonly/>
                </disk>""" % (img_path, cdrom_name))
            devices.append(node)

    @vm_shell
    def add_floppy_devmap(*args, **kwargs):
        """
        Add an entry of floppy to device.map
        """
        session = kwargs["session"]
        line = "(fd0)     /dev/fd0"
        devmap = "/boot/grub/device.map"
        if session.cmd_status("cat %s" % devmap):
            devmap = "/boot/grub2/device.map"
        cmd = [
            "grep '(fd0)' %s" % devmap,
            "cp -an %s %s.bk" % (devmap, devmap),
            "sed -i '2i%s' %s" % (line, devmap)
            # "echo '%s' >> %s" % (line, devmap)
        ]
        if session.cmd_status(cmd[0]):
            session.cmd(cmd[1])
            session.cmd(cmd[2])

    @vm_shell
    def revert_devmap(*args, **kwargs):
        """
        Revert device.map after test
        """
        session = kwargs["session"]
        devmap = "/boot/grub/device.map"
        if session.cmd_status("cat %s" % devmap):
            devmap = "/boot/grub2/device.map"
        cmd_lst = [
            "mv -f %s %s.old" % (devmap, devmap),
            "mv -f %s.bk %s" % (devmap, devmap),
            "rm -f %s.old" % devmap
        ]
        if not session.cmd_status("ls %s.bk" % devmap):
            for cmd in cmd_lst:
                session.cmd(cmd)

    @vm_shell
    def specify_cdrom_fstab(*args, **kwargs):
        """
        Add an entry of cdrom to fstab
        """
        session = kwargs["session"]
        line = "/dev/cdrom /media/CDROM auto exec 0 0"
        cmd = [
            "mkdir -p /media/CDROM",
            "mount /dev/cdrom /media/CDROM",
            "cp -an /etc/fstab /etc/fstab_bk",
            "echo '%s' >> /etc/fstab" % line,
            "grep '%s' /etc/fstab" % line
        ]
        for i in range(4):
            session.cmd(cmd[i])
        if session.cmd_status(cmd[4]):
            raise error.TestError("Failed to add cdrom to fstab")

    @vm_shell
    def revert_fstab(*args, **kwargs):
        """
        Revert fstab after test
        """
        session = kwargs["session"]
        cmd = [
            "mv -f /etc/fstab /etc/fstab_used",
            "mv -f /etc/fstab_bk /etc/fstab",
            "rm -f /etc/fstab_used"
        ]
        if not session.cmd_status("ls /etc/fstab_bk"):
            for i in range(len(cmd)):
                session.cmd(cmd[i])

    @vm_shell
    def specify_uuid_fstab(*args, **kwargs):
        """
        Make an entry in fstab specified by UUID
        """
        session = kwargs["session"]
        session.cmd("cp -an /etc/fstab /etc/fstab_bk")
        cmd_if_uuid = "grep UUID= /etc/fstab"
        if session.cmd_status(cmd_if_uuid):
            entry = session.cmd("blkid -s UUID|grep swap").strip().split()
            logging.info(entry)
            path = entry[0].strip(":")
            uuid = entry[1].replace('"', '')
            cmd_fstab = "sed -i 's|%s|%s|' /etc/fstab" % (path, uuid)
            session.cmd(cmd_fstab)

    @vm_shell
    def specify_label_fstab(*args, **kwargs):
        """
        Make an entry in fstab specified by label
        """
        session = kwargs["session"]
        session.cmd("cp -an /etc/fstab /etc/fstab_bk")
        cmd_map = {"root": "e2label %s ROOT",
                   "swap": "swaplabel -L SWAPPER %s"}
        if not session.cmd_status("swaplabel --help"):
            blk = "swap"
        elif not session.cmd_status("which e2label"):
            blk = "root"
        else:
            raise error.TestNAError("No tool to make label")
        entry = session.cmd("blkid|grep %s" % blk).strip()
        path = entry.split()[0].strip(":")
        cmd_label = cmd_map[blk] % path
        if "LABEL" not in entry:
            session.cmd(cmd_label)
        entry = session.cmd("blkid|grep %s" % blk).strip()
        label = entry.split()[1].strip().replace('"', '')
        cmd_fstab = "sed -i 's|%s|%s|' /etc/fstab" % (path, label)
        session.cmd(cmd_fstab)

    @vm_shell
    def specify_virtio_fstab(*args, **kwargs):
        """
        Make an entry in fstab specified by virtio device
        """
        session = kwargs["session"]
        session.cmd("cp -an /etc/fstab /etc/fstab_bk")
        entry = session.cmd("cat /etc/fstab|grep /boot").strip()
        logging.info(entry)
        if "/vd" not in entry:
            fstab_info = entry.split()[0]
            key = fstab_info.split("=")[1]
            blkinfo = session.cmd("blkid|grep %s" % key).strip()
            path = blkinfo.split()[0].strip(":")
            logging.info(path)
            cmd_fstab = "sed -i 's|%s|%s|' /etc/fstab" % (fstab_info, path)
            session.cmd(cmd_fstab)
        time.sleep(60)

    @vm_shell
    def create_large_file(*args, **kwargs):
        """
        Create a large file to make left space of root less than 20m
        """
        session = kwargs["session"]
        cmd_df = "df -m /|awk 'END{print $4}'"
        avail = int(session.cmd(cmd_df).strip())
        logging.info(avail)
        if avail > 19:
            params["large_file"] = "/file.large"
            cmd_create = "dd if=/dev/zero of=%s bs=1M count=%d" % \
                         (params["large_file"], avail - 19)
            session.cmd(cmd_create, timeout=v2v_timeout)
        logging.info(session.cmd(cmd_df).strip())

    @vm_shell
    def del_large_file(*args, **kwargs):
        """
        Delete the large file
        """
        session = kwargs["session"]
        if not session.cmd_status("ls %s" % params["large_file"]):
            session.cmd("rm -f %s" % params["large_file"])

    @vm_shell
    def corrupt_rpmdb(*args, **kwargs):
        """
        Corrupt rpm db
        """
        session = kwargs["session"]
        params["bk_rpm"] = "/root/backrpm_%s" % str(time.clock())
        session.cmd("mkdir %s" % params["bk_rpm"])
        if session.cmd_status("ls /var/lib/rpm/__db.001"):
            session.cmd("touch /var/lib/rpm/__db.001")
        else:
            session.cmd("rm -f /var/lib/rpm/__db.*")
            session.cmd("touch /var/lib/rpm/__db.001")
        if not session.cmd_status("yum update"):
            raise error.TestError("Corrupt rpmdb failed!")

    @vm_shell
    def rebuild_rpmdb(*args, **kwargs):
        """
        Rebuild rpm db
        """
        session = kwargs["session"]
        cmd_lst = [
            "rm -f /var/lib/rpm/__db*",
            "db_verify /var/lib/rpm/Packages",
            "rpm --rebuilddb",
            "yum clean all"
        ]
        for cmd in cmd_lst:
            session.cmd(cmd, timeout=v2v_timeout)
        session.cmd("rm -rf %s" % params["bk_rpm"])

    @vm_shell
    def bogus_kernel(*args, **kwargs):
        """
        Add a bogus kernel entry
        """
        session = kwargs["session"]
        cfg = {
            "file": ["/boot/grub/grub.conf", "/etc/grub.d/40_custom"],
            "cmd_bk": ["/boot/grub/grub.conf /boot/grub/grub.bk",
                       "/etc/grub.d/40_custom /etc/grub.d/40_custom.bk"],
            "grub_file": ["/grub.conf", "2/grub.cfg"],
            "search": ["title .*?.img", "menuentry '.*?}"],
            "sub_title": [["(title\s)", r"\1bogus "],
                          ["(menuentry\s'.*?)'", r"\1 bogus'"]],
            "sub_kernel": [["(kernel .*?)\s", r"\1.bogus "],
                           ["(/vmlinuz.*?)(\s)", r"\1.bogus\2"]],
            "make": ["pwd", "grub2-mkconfig -o /boot/grub2/grub.cfg"]
        }
        if session.cmd_status("ls /boot/grub"):
            logging.info("7")
            i = 1
        else:
            logging.info("6")
            i = 0
        cmd_bk = "cp -an %s" % cfg["cmd_bk"][i]
        session.cmd(cmd_bk)
        content = session.cmd("cat /boot/grub%s" % cfg["grub_file"][i]).strip()
        logging.info(content)
        search = re.search(cfg["search"][i], content, re.DOTALL)
        logging.info(search)
        if search:
            new_entry = search.group(0)
            logging.info(new_entry)
            new_entry = re.sub(cfg["sub_title"][i][0],
                               cfg["sub_title"][i][1], new_entry)
            new_entry = re.sub(cfg["sub_kernel"][i][0],
                               cfg["sub_kernel"][i][1], new_entry)
            logging.info(new_entry)
            session.cmd('echo "%s"|cat >> %s' % (new_entry, cfg["file"][i]))
            session.cmd(cfg["make"][i])
            logging.info(session.cmd("cat /boot/grub%s" % cfg["grub_file"][i]))
        else:
            raise error.TestError("No kernel found")

    @vm_shell
    def revert_menu(*args, **kwargs):
        """
        Revert grub list
        """
        session = kwargs["session"]
        if session.cmd_status("ls /boot/grub"):
            logging.info("r7")
            cmd_lst = [
                "mv -f /etc/grub.d/40_custom /etc/grub.d/40_custom.old",
                "mv -f /etc/grub.d/40_custom.bk /etc/grub.d/40_custom",
                "rm -f /etc/grub.d/40_custom.old",
                "grub2-mkconfig -o /boot/grub2/grub.cfg"
            ]
            state = session.cmd_status("ls /etc/grub.d/40_custom.bk")
        else:
            logging.info("r6")
            path = "/boot/grub"
            cmd_lst = [
                "mv -f %s/grub.conf %s/grub.old" % (path, path),
                "mv -f %s/grub.bk %s/grub.conf" % (path, path),
                "rm -f %s/grub.old" % path
            ]
            state = session.cmd_status("ls %s/grub.bk" % path)
        if not state:
            for cmd in cmd_lst:
                session.cmd(cmd)

    @vm_shell
    def grub_serial_terminal(*args, **kwargs):
        """
        Edit the serial and terminal lines of grub.conf
        """
        session = kwargs["session"]
        if session.cmd_status("ls /boot/grub"):
            logging.info("grub v2")
            session.cmd("cp -an /boot/grub2/grub.cfg /boot/grub2/grub_bk")
            cmd = "sed -i '1iserial -unit=0 -speed=115200\\n" \
                  "terminal -timeout=10 serial console' /boot/grub2/grub.cfg"
        else:
            logging.info("grub v1")
            session.cmd("cp -an /boot/grub/grub.conf /boot/grub/grub_bk")
            cmd = "sed -i '1iserial -unit=0 -speed=115200\\n" \
                  "terminal -timeout=10 serial console' /boot/grub/grub.conf"
        session.cmd(cmd)

    @vm_shell
    def revert_grub(*args, **kwargs):
        """
        Revert grub.conf/grub.cfg from backup file
        """
        session = kwargs["session"]
        # grub
        if session.cmd_status("ls /boot/grub"):
            logging.info("grub v2")
            cmd = [
                "mv /boot/grub2/grub.cfg /boot/grub2/grub_used",
                "mv /boot/grub2/grub_bk /boot/grub2/grub.cfg",
                "rm -f /boot/grub2/grub_used"
            ]
            status = "ls /boot/grub2/grub_bk"
        else:
            logging.info("grub v1")
            cmd = [
                "mv /boot/grub/grub.conf /boot/grub/grub_used",
                "mv /boot/grub/grub_bk /boot/grub/grub.conf",
                "rm -f /boot/grub/grub_used"
            ]
            status = "ls /boot/grub/grub_bk"
        if not session.cmd_status(status):
            for i in range(len(cmd)):
                session.cmd(cmd[i])

    def make_unclean_fs():
        """
        Use force off to make unclean file system of win8
        """
        virsh.start(vm_name)
        time.sleep(10)
        virsh.destroy(vm_name)

    def cleanup_fs():
        """
        Clean up file system by restart and shutdown normally
        """
        vm = libvirt_vm.VM(vm_name, params, test.bindir,
                           env.get("address_cache"))
        vm.start()
        time.sleep(60)
        vm.shutdown()

    def check_BSOD():
        """
        Check if boot up into BSOD
        """
        bar = 0.999
        blue = params.get("image_to_match")
        shot = "/tmp/v2v_win_screenshot.ppm"
        if blue is None:
            raise error.TestNAError("No BSOD example file!")
        cmd_man_page = 'man virt-v2v|grep -i "Boot failure: 0x0000007B"'
        if utils.run(cmd_man_page).exit_status != 0:
            raise error.TestFail("Man page doesn't contain boot failure msg")
        i = 0
        while i < 180:
            virsh.screenshot(vm_name, shot)
            logging.info(ppm_utils.img_ham_distance(shot, blue))
            similar = ppm_utils.image_histogram_compare(shot, blue)
            if similar > bar:
                logging.info("Meet BSOD with %s" % similar)
                return
            time.sleep(1)
        raise error.TestFail("No BSOD!")

    def check_rhev_file():
        """
        Check if rhev files exist
        """
        vmcheck = utils_v2v.VMCheck(test, params, env)
        vmcheck.init_windows()
        vmcheck.create_session()
        cmd_dir = {
            "rhev-apt.exe": "dir c:",
            "rhsrvany.exe": r'dir "c:\program files\redhat\rhev\apt"'}
        fail = False
        for key in cmd_dir:
            files = vmcheck.session.cmd(cmd_dir[key])
            logging.info(files)
            if re.search(key, files, re.IGNORECASE):
                logging.info("%s exists!" % key)
                fail = True
        if fail:
            raise error.TestFail("RHEV file exists while convert to kvm!")

    def virt_viewer():
        """
        Check if virt-viewer runs correctly
        """
        cmd = "virt-viewer %s &" % vm_name
        utils.run(cmd, timeout=v2v_timeout)
        pid = utils.run("pgrep virt-viewer").stdout.strip()
        logging.info(pid)
        time.sleep(20)  # how many seconds is the best
        es = utils.run("ps -p %s|grep virt-viewer" % pid).exit_status
        if es == 0:
            utils.run("kill -9 %s" % pid)
        else:
            raise error.TestFail("virt-viewer is not running")

    def check_grub_conf(check=None):
        """
        Check if xvc0 exists in grub.conf
        """
        logging.info("Checking grub.conf")
        vmcheck = params.get("vmcheck")
        vmcheck.create_session()
        if check == "console_xvc0":
            grub_file = "/grub/grub.conf"
            cmd = "cat /boot%s|grep 'console=xvc0'" % grub_file
            if not vmcheck.session.cmd_status(cmd):
                raise error.TestFail("'console=xvc0' still exists!")
        if vmcheck.session:
            vmcheck.session.close()

    def check_error_warning(output, check=None):
        """
        Check if error/warning meets expectation
        """
        err_map = {
            "fstab_cdrom": ["warning: /files/etc/fstab.*? references unknown"
                            " device \"cdrom\""],
            "fstab_label": ["unknown filesystem label.*"],
            "fstab_uuid": ["unknown filesystem UUID.*"],
            "fstab_virtio": ["unknown filesystem /dev/vd.*"],
            "kdump": [".*multiple files in /boot could be the initramfs.*"],
            "ctemp": [".*case_sensitive_path: v2v: no file or directory.*"],
            "floppy_devmap": ["unknown filesystem /dev/fd"],
            "corrupt_rpmdb": [".*error: rpmdb:.*"],
            "parent_ctrl": ["virt-v2v: warning: ova hard disk has no parent "
                            "controller.*"],
            "xvda_disk": [
                r"virt-v2v: WARNING: /boot/grub.*?/device.map references "
                r"unknown device /dev/vd.*?\n",
                r"virt-v2v: warning: /files/boot/grub/device.map/hd0 "
                r"references unknown.*?after conversion."
            ],
            "xvda_xen": [
                r"virt-v2v: WARNING: /boot/grub.*?/device.map references "
                r"unknown device /dev/vd.*?\n",
                r"virt-v2v: warning: /files/boot/grub/device.map/hd0 "
                r"references unknown.*?after conversion."
            ]
        }
        nega_map = {
            "not_shutdown": [
                ".*is running or paused.*",
                "virt-v2v: error: internal error: invalid argument:.*"
            ],
            "serial_terminal": ["virt-v2v: error: no kernels were found in "
                                "the grub configuration"],
            "no_space": ["virt-v2v: error: not enough free space for "
                         "conversion on filesystem '/'"],
            "unclean_fs": [".*Windows Hibernation or Fast Restart.*"]
        }
        if check is None or not (check in err_map or check in nega_map):
            logging.info("Skip checking Error")
        else:
            logging.info("Checking v2v output message")
            if status_error:
                if nega_map.has_key(check):
                    found = False
                    for err in nega_map[check]:
                        tmp = "\s*".join(err.split())
                        logging.debug("Searching for msg: %s" % err)
                        pattern = re.compile(tmp)
                        search = re.search(pattern, output)
                        if search:
                            found = True
                            logging.info("Found log: \n%s" % search.group(0))
                    if not found:
                        raise error.TestFail("Message not Found: %s" % "\n".
                                             join(nega_map[check]))
            else:
                if err_map.has_key(check):
                    for err in err_map[check]:
                        tmp = "\s*".join(err.split())
                        logging.debug("Searching for Error msg: %s" % err)
                        pattern = re.compile(tmp, re.IGNORECASE)
                        search = re.search(pattern, output)
                        if search:
                            raise error.TestFail("Error Msg of virt-v2v: %s" %
                                                 search.group(0))
                logging.debug("no Error/Warning found")

    def check_boot(local=False):
        """
        Check if guest can boot up
        """
        try:
            if not local and output_mode == "rhev":
                vmcheck = params.get("vmcheck")
                if os_type == "windows":
                    virsh_session = utils_sasl.VirshSessionSASL(params)
                    virsh_session_id = virsh_session.get_id()
                    vmcheck.virsh_session_id = virsh_session_id
                    vmcheck.init_windows()
                    virsh_session.close()
                vmcheck.create_session()
            else:
                vm = libvirt_vm.VM(vm_name, params, test.bindir,
                                   env.get("address_cache"))
                vm_login(vm)
            if local:
                # time.sleep(60)
                virsh.destroy(vm_name, "--graceful")
                logging.info("%s is destroied" % vm_name)
        except Exception, e:
            raise error.TestFail("Bootup guest and login failed: %s", str(e))

    def check_result(cmd, result, status_error):
        """
        Check virt-v2v command result
        """
        utlv.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if status_error:
            if checkpoint in ["running", "paused", "idle"]:
                check_error_warning(output, "not_shutdown")
            else:
                check_error_warning(output, checkpoint)
        else:
            if output_mode == "rhev":
                ovf = get_ovf_content(output)
                logging.debug("ovf content: %s", ovf)
                if '--vmtype' in cmd:
                    expected_vmtype = re.findall(r"--vmtype\s(\w+)", cmd)[0]
                    check_vmtype(ovf, expected_vmtype)
            if '-oa' in cmd and '--no-copy' not in cmd:
                expected_mode = re.findall(r"-oa\s(\w+)", cmd)[0]
                img_path = get_img_path(output)
                check_image(img_path, "allocation", expected_mode)
            if '-of' in cmd and '--no-copy' not in cmd:
                expected_format = re.findall(r"-of\s(\w+)", cmd)[0]
                img_path = get_img_path(output)
                check_image(img_path, "format", expected_format)
            if '-on' in cmd:
                expected_name = re.findall(r"-on\s(\w+)", cmd)[0]
                check_new_name(output, expected_name)
            if '--no-copy' in cmd:
                check_nocopy(output)
            if '-oc' in cmd:
                expected_uri = re.findall(r"-oc\s(\S+)", cmd)[0]
                check_connection(output, expected_uri)
            if output_mode == "rhev":
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    raise error.TestFail("Import VM failed")
                else:
                    params['vmcheck'] = utils_v2v.VMCheck(test, params, env)
                    if attach_disks:
                        check_disks(params.get("ori_disks"))
                    if checkpoint:
                        if checkpoint in ("multi_kernel", "debug_kernel"):
                            default_kernel = params.get('defaultkernel')
                            check_multi_kernel(default_kernel, debug_kernel)
                        elif checkpoint == "vmlinuz_init":
                            check_vmlinuz_initramfs(output)
                        elif checkpoint == "floppy":
                            virsh_session_id = None
                            virsh_session = utils_sasl.VirshSessionSASL(params)
                            virsh_session_id = virsh_session.get_id()
                            params['vmcheck'].virsh_session_id = virsh_session_id
                            check_floppy()
                        elif checkpoint == "console_xvc0":
                            check_grub_conf(checkpoint)

            if output_mode == "libvirt":
                if "qemu:///session" not in v2v_options:
                    try:
                        virsh.start(vm_name, debug=True, ignore_status=False)
                    except Exception, e:
                        raise error.TestFail("Start vm failed!")
                    if checkpoint:
                        if checkpoint == "ova_default":
                            virt_viewer()
                        elif checkpoint == "kvm_rhev_file":
                            check_rhev_file()
                        elif checkpoint == "win2008r2_ostk":
                            check_BSOD()
            if checkpoint:
                if checkpoint in check_list_boot:
                    check_boot()
                check_error_warning(output, checkpoint)

    backup_xml = None
    attach_disks = "yes" == params.get("attach_disk_config", "no")
    attach_disk_path = os.path.join(test.tmpdir, "attach_disks")
    vdsm_domain_dir, vdsm_image_dir, vdsm_vm_dir = ("", "", "")
    try:
        # Build input options
        input_option = ""
        exc = False
        if input_mode is None:
            # pass
            if checkpoint and checkpoint == "xvda_disk":
                logging.info("xen___" * 20)
                remote.scp_from_remote(xen_host, 22, xen_host_user,
                                       xen_host_passwd,
                                       params.get("remote_disk_image"),
                                       disk_img)
        elif input_mode == "libvirt":
            uri_obj = utils_v2v.Uri(hypervisor)
            ic_uri = uri_obj.get_uri(remote_host, vpx_dc, esx_ip)
            input_option = "-i %s -ic %s %s" % (input_mode, ic_uri, vm_name)
            # Build network&bridge option to avoid network error
            v2v_options += " -b %s -n %s" % (params.get("output_bridge"),
                                             params.get("output_network"))
            # Multiple disks testing
            if attach_disks:
                backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                # Get original vm disk counts
                params['ori_disks'] = backup_xml.get_disk_count(vm_name)
                utlv.attach_disks(env.get_vm(vm_name), attach_disk_path,
                                  None, params)
            # VirtIO test
            elif virtio:
                if os_type == "linux":
                    if virtio_on:
                        enable_disk_virtio()
                    else:
                        disable_disk_virtio()
            elif checkpoint:
                if checkpoint in multi_kernel_lst:
                    multi_kernel()
                # attached floppy
                elif checkpoint.startswith("floppy"):
                    img_path = params.get("img_path", "/tmp")
                    floppy_name = "flp"
                    cmd_floppy = "dd if=/dev/zero of=%s/%s.img bs=1024 " \
                                 "count=10240" % (img_path, floppy_name)
                    utils.run(cmd_floppy, timeout=v2v_timeout, verbose=True,
                              ignore_status=True)
                    attach_floppy(img_path, floppy_name)
                    if checkpoint == "floppy_devmap":
                        add_floppy_devmap()
                elif checkpoint == "fstab_cdrom":
                    img_path = params.get("img_path", "/tmp")
                    cdrom_name = "cdrom"
                    cmd_cdrom = "mkisofs -r -o %s/%s.iso /dev/zero" % \
                                (img_path, cdrom_name)
                    utils.run(cmd_cdrom, timeout=v2v_timeout, verbose=True,
                              ignore_status=True)
                    attach_cdrom(img_path, cdrom_name)
                    specify_cdrom_fstab()
                elif checkpoint == "fstab_uuid":
                    specify_uuid_fstab()
                elif checkpoint == "fstab_label":
                    specify_label_fstab()
                elif checkpoint == "fstab_virtio":
                    enable_disk_virtio()
                    specify_virtio_fstab()
                elif checkpoint == "running":
                    virsh.start(vm_name)
                    logging.info("Current state: %s" %
                                 virsh.domstate(vm_name).stdout.strip())
                elif checkpoint == "paused":
                    virsh.start(vm_name, "--paused")
                    logging.info("Current state: %s" %
                                 virsh.domstate(vm_name).stdout.strip())
                elif checkpoint == "serial_terminal":
                    grub_serial_terminal()
                    check_boot(local=True)
                elif checkpoint == "no_space":
                    create_large_file()
                elif checkpoint == "corrupt_rpmdb":
                    corrupt_rpmdb()
                elif checkpoint == "bogus_kernel":
                    bogus_kernel()
                    check_boot(local=True)
                elif checkpoint == "unclean_fs":
                    make_unclean_fs()

        elif input_mode == "disk":
            input_option += "-i %s %s" % (input_mode, disk_img)
        elif input_mode == "ova":
            ova_file = params.get("input_ova_file")
            if not ova_file:
                raise error.TestNAError("No input ova file!")
            input_option += "-i %s %s" % (input_mode, ova_file)
            if params.get("new_name"):
                input_option += " -on %s" % params["new_name"]
        elif input_mode in ['libvirtxml']:
            # elif input_mode in ['libvirtxml', 'ova']:
            raise error.TestNAError("Unsupported input mode: %s" % input_mode)
        else:
            raise error.TestError("Unknown input mode %s" % input_mode)
        input_format = params.get("input_format")
        input_allo_mode = params.get("input_allo_mode")
        if input_format:
            input_option += " -if %s" % input_format
            if not status_error:
                logging.info("Check image before convert")
                check_image(disk_img, "format", input_format)
                if input_allo_mode:
                    check_image(disk_img, "allocation", input_allo_mode)

        # Build output options
        output_option = ""
        if output_mode:
            output_option = "-o %s -os %s" % (output_mode, output_storage)
        output_format = params.get("output_format")
        if output_format:
            output_option += " -of %s" % output_format
        output_allo_mode = params.get("output_allo_mode")
        if output_allo_mode:
            output_option += " -oa %s" % output_allo_mode

        # Build vdsm related options
        if output_mode in ['vdsm', 'rhev']:
            if not os.path.isdir(mnt_point):
                os.mkdir(mnt_point)
            if not utils_misc.mount(nfs_storage, mnt_point, "nfs"):
                raise error.TestError("Mount NFS Failed")
            if output_mode == 'vdsm':
                v2v_options += " --vdsm-image-uuid %s" % vdsm_image_uuid
                v2v_options += " --vdsm-vol-uuid %s" % vdsm_vol_uuid
                v2v_options += " --vdsm-vm-uuid %s" % vdsm_vm_uuid
                v2v_options += " --vdsm-ovf-output %s" % vdsm_ovf_output
                vdsm_domain_dir = os.path.join(mnt_point, fake_domain_uuid)
                vdsm_image_dir = os.path.join(mnt_point, export_domain_uuid,
                                              "images", vdsm_image_uuid)
                vdsm_vm_dir = os.path.join(mnt_point, export_domain_uuid,
                                           "master/vms", vdsm_vm_uuid)
                # For vdsm_domain_dir, just create a dir to test BZ#1176591
                os.mkdir(vdsm_domain_dir)
                os.mkdir(vdsm_image_dir)
                os.mkdir(vdsm_vm_dir)

        # Output more messages
        v2v_options += " -v -x"

        # Prepare for libvirt unprivileged user session connection
        if "qemu:///session" in v2v_options:
            try:
                pwd.getpwnam(v2v_user)
            except KeyError:
                # create new user
                utils.system("useradd %s" % v2v_user, ignore_status=True)
                new_v2v_user = True
            user_info = pwd.getpwnam(v2v_user)
            logging.info("Convert to qemu:///session by user '%s'", v2v_user)
            if input_mode == "disk":
                # Change the image owner and group
                ori_owner = os.stat(disk_img).st_uid
                ori_group = os.stat(disk_img).st_uid
                os.chown(disk_img, user_info.pw_uid, user_info.pw_gid)
                restore_image_owner = True
            else:
                raise error.TestNAError("Only support convert local disk")

        # Setup ssh-agent access to xen hypervisor
        if hypervisor == 'xen':
            os.environ['LIBGUESTFS_BACKEND'] = 'direct'
            user = params.get("xen_host_user", "root")
            passwd = params.get("xen_host_passwd", "redhat")
            logging.info("set up ssh-agent access ")
            ssh_key.setup_ssh_key(remote_host, user=user,
                                  port=22, password=passwd)
            utils_misc.add_identities_into_ssh_agent()
            # If the input format is not define, we need to either define
            # the original format in the source metadata(xml) or use '-of'
            # to force the output format, see BZ#1141723 for detail.
            if '-of' not in v2v_options:
                v2v_options += ' -of %s' % params.get("default_output_format",
                                                      "qcow2")
            if checkpoint:
                if checkpoint == "xen_uuid":
                    cmd = "virsh -c xen+ssh://%s dominfo %s|grep UUID" % \
                          (xen_host, vm_name)
                    info = utils.run(cmd, timeout=v2v_timeout).stdout
                    uuid = info.strip().split()[1]
                    input_option = input_option.replace(vm_name, uuid)

        # Create password file for access to ESX hypervisor
        if hypervisor == 'esx':
            vpx_passwd = params.get("vpx_passwd")
            vpx_passwd_file = os.path.join(test.tmpdir, "vpx_passwd")
            logging.info("Building ESX no password interactive verification.")
            pwd_f = open(vpx_passwd_file, 'w')
            pwd_f.write(vpx_passwd)
            pwd_f.close()
            output_option += " --password-file %s" % vpx_passwd_file

        # Create libvirt dir pool
        if output_mode == "libvirt":
            create_pool()
            output_option += " -b %s -n %s" % (params.get("output_bridge"),
                                               params.get("output_network"))

            if checkpoint and checkpoint == "pool_uuid":
                virsh.pool_start(pool_name)
                pooluuid = virsh.pool_uuid(pool_name).stdout.strip()
                output_option = output_option.replace(pool_name, pooluuid)

        if input_mode == "ova":
            os.environ['LIBGUESTFS_BACKEND'] = 'direct'
        # Running virt-v2v command
        cmd = "%s %s %s %s" % (utils_v2v.V2V_EXEC, input_option,
                               output_option, v2v_options)
        if v2v_user:
            cmd = su_cmd + "'%s'" % cmd
        cmd_result = utils.run(cmd, timeout=v2v_timeout, verbose=True,
                               ignore_status=True)
        if new_vm_name:
            vm_name = new_vm_name
            params['main_vm'] = new_vm_name
        check_result(cmd, cmd_result, status_error)
    except Exception, e:
        exc = True
        raise e
    finally:
        case_type = params.get('type')
        if hypervisor == "xen":
            utils.run("ssh-agent -k")
        if hypervisor == "esx":
            utils.run("rm -rf %s" % vpx_passwd_file)
        for vdsm_dir in [vdsm_domain_dir, vdsm_image_dir, vdsm_vm_dir]:
            if os.path.exists(vdsm_dir):
                shutil.rmtree(vdsm_dir)
        if os.path.exists(mnt_point):
            utils_misc.umount(nfs_storage, mnt_point, "nfs")
            os.rmdir(mnt_point)
        if output_mode == "local":
            image_name = vm_name + "-sda"
            img_file = os.path.join(output_storage, image_name)
            xml_file = img_file + ".xml"
            for local_file in [img_file, xml_file]:
                if os.path.exists(local_file):
                    os.remove(local_file)
        if checkpoint:
            if checkpoint == "floppy_devmap":
                revert_devmap()
            if checkpoint.startswith("fstab"):
                revert_fstab()
            if checkpoint == "serial_terminal":
                revert_grub()
            if checkpoint == "no_space":
                del_large_file()
            if checkpoint == "corrupt_rpmdb":
                rebuild_rpmdb()
            if checkpoint == "bogus_kernel":
                revert_menu()
            if checkpoint == "unclean_fs":
                cleanup_fs()
            if checkpoint in multi_kernel_lst and params['installed_kernel']:
                cleanup_kernel(params['installed_kernel'])

            if checkpoint in revert_xml_lst:
                virsh_revert()
            if checkpoint in ["running", "paused", "idle"]:
                virsh.destroy(vm_name, gracefully=True)
        if output_mode == "libvirt" and not \
                (case_type.endswith('vm_check') and not exc):
            if "qemu:///session" in v2v_options:
                cmd = su_cmd + "'virsh undefine %s'" % vm_name
                utils.system(cmd)
            else:
                virsh.remove_domain(vm_name)
            cleanup_pool()
        vmcheck = params.get("vmcheck")
        if vmcheck and not (case_type.endswith('vm_check') and not exc):
            vmcheck.cleanup()
        if new_v2v_user:
            utils.system("userdel -f %s" % v2v_user)
        if restore_image_owner:
            os.chown(disk_img, ori_owner, ori_group)
        if backup_xml:
            backup_xml.sync()
        if os.path.exists(attach_disk_path):
            shutil.rmtree(attach_disk_path)
