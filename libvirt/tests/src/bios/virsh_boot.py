import logging
import os
import re
from aexpect.utils import astring
from aexpect.exceptions import ShellProcessTerminatedError

from avocado.utils import process

from virttest import remote
from virttest import virsh
from virttest import utils_package
from virttest import utils_misc
from virttest import data_dir
from virttest import ceph
from virttest import gluster

from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk

from virttest import libvirt_version

# Global test env cleanup variables
cleanup_iscsi = False
cleanup_gluster = False
cleanup_iso_file = False
cleanup_image_file = False
cleanup_released_image_file = False


def get_stripped_output(cont, custom_codes=None):
    """
    Return the STDOUT and STDERR output without the console
    codes escape and sequences of the process so far.

    :param cont: input string
    :param custom_codes: The special console codes escape
    """
    return astring.strip_console_codes(cont, custom_codes)


def get_console_output(session, cmd, custom_codes):
    """
    Send a command and return its output(serial console)

    :param session: The console session
    :param cmd: The send command
    :param custom_codes: The special console codes escape
    """
    output = session.cmd_output_safe(cmd)
    # Get rid of console codes escape
    stripped_output = get_stripped_output(output, custom_codes)
    logging.debug("'%s' running result is:\n%s", cmd, stripped_output)
    return stripped_output


def set_secure_key(console_session, custom_codes, test):
    """
    Set secure key in Uefi Shell

    :param console_session: Uefi console session
    :param custom_codes: Uefi special console escape
    :param test: Avocado test object
    """
    if console_session:
        # Uefi shell use "\r" as line separator
        console_session.set_linesep("\r")
        output = get_console_output(console_session, "EnrollDefaultKeys.efi", custom_codes)
        if re.search(r'info:\s*success', output):
            logging.debug("Set secure key successfully")
        else:
            test.fail("Failed to set secure key")
        try:
            status, output = console_session.cmd_status_output("reset -s", safe=True)
            logging.debug("'reset -s' running result is:\n%s", output)
            if status:
                test.fail("Failed to reset VM: %s", output)
        except ShellProcessTerminatedError:
            # VM will be shutdown by 'reset'
            logging.debug("VM reset successfully")


def add_cdrom_device(v_xml, iso_file, target_dev, device_bus):
    """
    Add cdrom disk in VM XML

    :param v_xml: The instance of VMXML class
    :param iso_file: The iso file path
    :param target_dev: The target dev in Disk XML
    :param device_bus: The target bus in Disk XML
    """
    disk_xml = Disk(type_name="file")
    disk_xml.device = "cdrom"
    disk_xml.target = {"dev": target_dev, "bus": device_bus}
    disk_xml.driver = {"name": "qemu", "type": "raw"}
    src_dict = {"file": iso_file}
    disk_xml.source = disk_xml.new_disk_source(
        **{"attrs": src_dict})
    disk_xml.readonly = False
    v_xml.add_device(disk_xml)
    return v_xml


def create_disk_xml(params):
    """
    Create a XML to be compatible with create_disk_xml function

    :param params: The instance of avocado params class
    """
    disk_type = params.get('disk_type')
    target_dev = params.get('target_dev', 'vda')
    target_bus = params.get('target_bus', 'virtio')
    driver_type = params.get('driver_type', 'qcow2')
    device_type = params.get('device_type', 'disk')
    disk_params = {'type_name': disk_type,
                   'device_type': device_type,
                   'driver_name': "qemu",
                   'driver_type': driver_type,
                   'target_dev': target_dev,
                   'target_bus': target_bus}
    if disk_type in ('file', 'block'):
        disk_params.update({'source_file': params.get("source_file")})
    elif disk_type == 'network':
        disk_params.update({'source_protocol': params.get('source_protocol'),
                            'source_name': params.get('source_name'),
                            'source_host_name': params.get('source_host_name'),
                            'source_host_port': params.get('source_host_port')})
    return utlv.create_disk_xml(disk_params)


def read_until_any_line_matches(session, patterns, timeout=60.0,
                                internal_timeout=None, print_func=None,
                                custom_codes=None):
    """
    To be compatible with the read_until_any_line_matches function
    in client.py of aexpect. The old function doesn't handle the console
    escape codes, and may impact match serial console pattern

   :param patterns: A list of strings (regular expression patterns)
                    Consider using '^' in the beginning.
   :param timeout: The duration (in seconds) to wait until a match is
                found
   :param internal_timeout: The timeout to pass to read_nonblocking
   :param print_func: A function to be used to print the data being read
                (should take a string parameter)
   :return: A tuple containing the match index and the data read so far
   :raise ExpectTimeoutError: Raised if timeout expires
   :raise ExpectProcessTerminatedError: Raised if the child process
                terminates while waiting for output
   :raise ExpectError: Raised if an unknown error occurs
    """
    def match_patterns_multiline(cont, patterns):
        """
        Match list of lines against a list of patterns.
        Return the index of the first pattern that matches a substring of cont.
        None and empty strings in patterns are ignored.
        If no match is found, return None.

        :param cont: List of strings (input strings)
        :param patterns: List of strings (regular expression patterns). The
                         pattern priority is from the last to first.
        """
        for i in range(-len(patterns), 0):
            if not patterns[i]:
                continue
            for line in cont:
                line = get_stripped_output(line, custom_codes)
                if re.search(patterns[i], line):
                    return i

    return session.read_until_output_matches(patterns,
                                             lambda x: x.splitlines(), timeout,
                                             internal_timeout, print_func,
                                             match_patterns_multiline)


def download_file(url, dest_file, test):
    """
    Perform file download via wget

    :param url: The source url
    :param dest_file: The dest file path
    :param test: Avocado test object
    :return: True or raise exception
    """
    if utils_package.package_install("wget"):
        if url.count("EXAMPLE"):
            test.cancel("Please provide the URL %s" % url)
        download_cmd = ("wget %s -O %s" % (url, dest_file))
        if not os.path.exists(dest_file):
            if process.system(download_cmd, verbose=False, shell=True):
                test.error("Failed to download boot iso file")
        return True
    else:
        test.error("wget install failed")


def setup_test_env(params, test):
    """
     Prepare test env for OVMF, Seabios, Gluster, Ceph and download
     the testing image

     :param params: Avocado params object
     :param test: Avocado test object
    """
    boot_type = params.get("boot_type", "seabios")
    source_protocol = params.get("source_protocol", "")
    boot_dev = params.get("boot_dev", "hd")
    boot_iso_url = params.get("boot_iso_url", "EXAMPLE_BOOT_ISO_URL")
    boot_iso_file = os.path.join(data_dir.get_tmp_dir(), "boot.iso")
    non_release_os_url = params.get("non_release_os_url", "")
    download_file_path = os.path.join(data_dir.get_tmp_dir(), "non_released_os.qcow2")
    release_os_url = params.get("release_os_url", "")
    download_released_file_path = os.path.join(data_dir.get_tmp_dir(), "released_os.qcow2")
    mon_host = params.get("mon_host")
    disk_src_name = params.get("disk_source_name")
    disk_src_host = params.get("disk_source_host")
    disk_src_port = params.get("disk_source_port")
    client_name = params.get("client_name")
    client_key = params.get("client_key")
    key_file = os.path.join(data_dir.get_tmp_dir(), "ceph.key")

    global cleanup_iso_file
    global cleanup_image_file
    global cleanup_released_image_file

    os_version = params.get("os_version")
    if not os_version.count("EXAMPLE"):
        os_version = os_version.split(".")[0]

    if boot_type == "ovmf":
        if not libvirt_version.version_compare(2, 0, 0):
            test.error("OVMF doesn't support in current"
                       " libvirt version.")

        if not utils_package.package_install('OVMF'):
            test.error("OVMF package install failed")

        if os_version == "RHEL-7" and not \
                utils_package.package_install('qemu-kvm-rhev'):
            test.error("qemu-kvm-rhev package install failed")
        elif not utils_package.package_install('qemu-kvm'):
            test.error("qemu-kvm package install failed")

    if boot_type == "seabios" and \
            not utils_package.package_install('seabios-bin'):
        test.error("seabios package install failed")

    if (source_protocol == "gluster"
            and not params.get("gluster_server_ip")
            and not utils_package.package_install('glusterfs-server')):
        test.error("glusterfs-server install failed")

    if source_protocol == "rbd":
        if utils_package.package_install("ceph-common"):
            if disk_src_host.count("EXAMPLE") or \
                    disk_src_port.count("EXAMPLE") or \
                    disk_src_name.count("EXAMPLE") or \
                    mon_host.count("EXAMPLE") or \
                    client_name.count("EXAMPLE") or \
                    client_key.count("EXAMPLE"):
                test.cancel("Please provide access info of the ceph")

            with open(key_file, 'w') as f:
                f.write("[%s]\n\tkey = %s\n" %
                        (client_name, client_key))
            key_opt = "--keyring %s" % key_file

            # Delete the disk if it exists
            cmd = ("rbd -m {0} {1} info {2} && rbd -m {0} {1} rm "
                   "{2}".format(mon_host, key_opt, disk_src_name))
            process.run(cmd, ignore_status=True, shell=True)
        else:
            test.error("ceph-common install failed")

    if boot_dev == "cdrom":
        if download_file(boot_iso_url, boot_iso_file, test):
            cleanup_iso_file = True

    if non_release_os_url:
        if download_file(non_release_os_url, download_file_path, test):
            cleanup_image_file = True

    if release_os_url:
        if download_file(release_os_url, download_released_file_path, test):
            cleanup_released_image_file = True


def apply_boot_options(vmxml, params, test):
    """
    Apply Uefi/Seabios Boot options in VMXML

    :param test: Avocado test object
    :param vmxml: The instance of VMXML class
    :param params: Avocado params object
    """
    # Boot options
    loader = params.get("loader", "")
    nvram = params.get("nvram", "")
    readonly = params.get("readonly", "")
    template = params.get("template", "")
    boot_dev = params.get("boot_dev", "hd")
    loader_type = params.get("loader_type", "")
    boot_type = params.get("boot_type", "seabios")
    os_firmware = params.get("os_firmware", "")
    with_secure = (params.get("with_secure", "no") == "yes")
    with_nvram = (params.get("with_nvram", "no") == "yes")
    with_loader = (params.get("with_loader", "yes") == "yes")
    with_readonly = (params.get("with_readonly", "yes") == "yes")
    with_loader_type = (params.get("with_loader_type", "yes") == "yes")
    with_nvram_template = (params.get("with_nvram_template", "yes") == "yes")
    vm_name = params.get("main_vm", "")

    dict_os_attrs = {}
    # Set attributes of loader of VMOSXML
    if with_loader:
        logging.debug("Set os loader to test non-released os version without secure boot enabling")
        dict_os_attrs.update({"loader": loader})
        if with_readonly:
            dict_os_attrs.update({"loader_readonly": readonly})
        if with_loader_type:
            dict_os_attrs.update({"loader_type": loader_type})
    else:
        if not libvirt_version.version_compare(5, 3, 0):
            test.cancel("Firmware attribute is not supported in"
                        " current libvirt version")
        else:
            logging.debug("Set os firmware to test released os version with secure boot enabling")
            dict_os_attrs.update({"os_firmware": os_firmware})
            # Include secure='yes' in loader and support no smm element in guest xml
            if with_secure:
                dict_os_attrs.update({"secure": "yes"})

    # To use BIOS Serial Console, need set userserial=yes in VMOSXML
    if boot_type == "seabios" and boot_dev == "cdrom":
        logging.debug("Enable bios serial console in OS XML")
        dict_os_attrs.update({"bios_useserial": "yes"})
        dict_os_attrs.update({"bios_reboot_timeout": "0"})
        dict_os_attrs.update({"bootmenu_enable": "yes"})
        dict_os_attrs.update({"bootmenu_timeout": "3000"})

    # Set attributes of nvram of VMOSXML
    if with_nvram:
        logging.debug("Set os nvram")
        nvram = nvram.replace("<VM_NAME>", vm_name)
        dict_os_attrs.update({"nvram": nvram})
        if with_nvram_template:
            dict_os_attrs.update({"nvram_template": template})

    vmxml.set_os_attrs(**dict_os_attrs)


def enable_secure_boot(vm, vmxml, test, **kwargs):
    """
    Enroll Uefi secure key and set to boot from hd

    :param vm: The instance of VM Guest
    :param vmxml: The instance of VMXML class
    :param test: Avocado test object
    :param kwargs: Key words to setup boot from Uefi.iso
    """
    uefi_iso = kwargs.get("uefi_iso", "")
    uefi_target_dev = kwargs.get("uefi_target_dev", "")
    uefi_device_bus = kwargs.get("uefi_device_bus", "")
    custom_codes = kwargs.get("uefi_custom_codes", "")
    dict_os_attrs = {}

    # Enable smm=on for secure boot
    logging.debug("Set smm=on in VMFeaturesXML")
    features_xml = vmxml.features
    features_xml.smm = "on"
    vmxml.features = features_xml
    # Add cdrom device with Uefi.iso
    add_cdrom_device(vmxml, uefi_iso, uefi_target_dev, uefi_device_bus)
    vmxml.remove_all_boots()
    dict_os_attrs.update({"boots": ["cdrom"]})
    dict_os_attrs.update({"secure": "yes"})
    vmxml.set_os_attrs(**dict_os_attrs)
    logging.debug("Enable secure boot mode:\n%s", open(vmxml.xml).read())
    # Enroll key in Uefi shell
    vmxml.undefine()
    if vmxml.define():
        if vm.is_dead():
            vm.start()
        console_session = vm.wait_for_serial_login(timeout=240)
        set_secure_key(console_session, custom_codes, test)
        console_session.close()
    else:
        test.fail("Failed to define %s from %s" % (vm.name, vmxml.xml))
    # Change OS boot to hd device
    edit_cmd = []
    edit_cmd.append(":%s/boot dev=\'cdrom/boot dev=\'hd")
    utlv.exec_virsh_edit(vm.name, edit_cmd)


def enable_normal_boot(vmxml, check_points, define_error, test):
    """
    Undefine/Define VM and check the result

    :param vmxml: The instance of VMXML class
    :param  check_points: The list of check points of result
    :param define_error: The define error status
    :param test: Avocado test object
    """
    logging.debug("Boot guest in normal mode:\n%s",
                  open(vmxml.xml).read())
    vmxml.undefine(options="--nvram")
    ret = virsh.define(vmxml.xml)
    if ret.exit_status:
        if define_error:
            utlv.check_result(ret, expected_fails=check_points)
        else:
            test.fail("Failed to define VM from %s" % vmxml.xml)


def prepare_iscsi_disk(blk_source, **kwargs):
    """
    Set up iscsi disk device and replace the domain disk image

    :param blk_source: The domain disk image path
    :param **kwargs: Key words for iscsi device setup
    :return: iscsi disk path
    """
    device_name = utlv.setup_or_cleanup_iscsi(True, image_size='3G')
    disk_format = kwargs.get("disk_format")
    image_size = kwargs.get("image_size")
    if device_name:
        # If disk format is qcow2, format the iscsi disk first
        if disk_format == "qcow2":
            cmd = ("qemu-img create -f %s %s %s" %
                   (disk_format, device_name, image_size))
            process.run(cmd, shell=True)
        # Copy the domain disk image to the iscsi disk path
        cmd = ("cp -f %s %s" % (blk_source, device_name))
        process.run(cmd, shell=True)
        return device_name


def prepare_gluster_disk(blk_source, test, **kwargs):
    """
    Set up gluster disk device and replace the domain disk image

    :param blk_source: The domain disk image path
    :param test: Avocado test object
    :param kwargs: Key words for gluster device setup
    :return: host_ip
    """
    vol_name = kwargs.get("vol_name")
    brick_path = kwargs.get("brick_path")
    disk_img = kwargs.get("disk_img")
    disk_format = kwargs.get("disk_format")
    host_ip = gluster.setup_or_cleanup_gluster(True, **kwargs)
    logging.debug("host ip: %s ", host_ip)
    # Copy the domain disk image to gluster disk path
    image_info = utils_misc.get_image_info(blk_source)
    dest_image = "/mnt/%s" % disk_img
    if image_info["format"] == disk_format:
        disk_cmd = ("cp -f %s %s" % (blk_source, dest_image))
    else:
        disk_cmd = ("qemu-img convert -f %s -O %s %s %s" %
                    (image_info["format"], disk_format,
                     blk_source, dest_image))
    # Mount the gluster disk and create the image
    src_mnt = "%s:%s" % (host_ip, vol_name)
    if not utils_misc.mount(src_mnt, "/mnt", "glusterfs"):
        test.error("glusterfs mount failed")
    process.run("%s && chmod a+rw /mnt/%s && umount /mnt" %
                (disk_cmd, disk_img), shell=True)
    return host_ip


def set_domain_disk(vmxml, blk_source, params, test):
    """
    Replace the domain disk with new setup device or download image

    :param vmxml: The instance of VMXML class
    :param params: Avocado params object
    :param test: Avocado test object
    :param blk_source: The domain disk image path
    """
    disk_type = params.get("disk_type", "file")
    boot_dev = params.get("boot_dev", "hd")
    target_dev = params.get("target_dev", "vdb")
    device_bus = params.get("device_bus", "virtio")
    disk_img = params.get("disk_img")
    image_size = params.get("image_size", "3G")
    vol_name = params.get("vol_name")
    disk_format = params.get("disk_format", "qcow2")
    driver_type = params.get("driver_type", "qcow2")
    mon_host = params.get("mon_host")
    disk_src_name = params.get("disk_source_name")
    disk_src_host = params.get("disk_source_host")
    disk_src_port = params.get("disk_source_port")
    source_protocol = params.get("source_protocol", "")
    boot_iso_file = os.path.join(data_dir.get_tmp_dir(), "boot.iso")
    non_release_os_url = params.get("non_release_os_url", "")
    download_file_path = os.path.join(data_dir.get_tmp_dir(), "non_released_os.qcow2")
    release_os_url = params.get("release_os_url", "")
    download_released_file_path = os.path.join(data_dir.get_tmp_dir(), "released_os.qcow2")
    brick_path = os.path.join(test.virtdir, "gluster-pool")
    usb_index = params.get("usb_index", "0")
    bus_controller = params.get("bus_controller", "")
    usb_controller = params.get("usb_controller", "")
    usb_model = params.get("usb_model", "")

    global cleanup_iscsi
    global cleanup_gluster
    disk_params = {'disk_type': disk_type,
                   'target_dev': target_dev,
                   'target_bus': device_bus,
                   'driver_type': driver_type}
    if source_protocol == 'iscsi':
        if disk_type == 'block':
            if release_os_url:
                blk_source = download_released_file_path
            kwargs = {'image_size': image_size, 'disk_format': disk_format}
            iscsi_target = prepare_iscsi_disk(blk_source, **kwargs)
            if iscsi_target is None:
                test.error("Failed to create iscsi disk")
            else:
                cleanup_iscsi = True
                disk_params.update({'source_file': iscsi_target})
    elif source_protocol == 'usb':

        # assemble the xml of usb controller
        controllers = vmxml.get_devices(device_type="controller")
        for dev in controllers:
            if dev.type == "usb":
                vmxml.del_device(dev)

        for model in usb_model.split(','):
            controller = Controller("controller")
            controller.type = "usb"
            controller.index = usb_index
            controller.model = model
            vmxml.add_device(controller)

        # prepare virtual disk device
        dir_name = os.path.dirname(blk_source)
        device_name = os.path.join(dir_name, "usb_virtual_disk.qcow2")
        cmd = ("qemu-img convert -O {} {} {}".format(disk_format, blk_source, device_name))
        process.run(cmd, shell=True)
        disk_params.update({'source_file': device_name})

    elif source_protocol == 'gluster':
        if disk_type == 'network':
            if release_os_url:
                blk_source = download_released_file_path
            host_ip = prepare_gluster_disk(blk_source, test, brick_path=brick_path, **params)
            if host_ip is None:
                test.error("Failed to create glusterfs disk")
            else:
                cleanup_gluster = True
            source_name = "%s/%s" % (vol_name, disk_img)
            disk_params.update({'source_name': source_name,
                                'source_host_name': host_ip,
                                'source_host_port': '24007',
                                'source_protocol': source_protocol})
    elif source_protocol == 'rbd':
        if disk_type == 'network':
            if release_os_url:
                blk_source = download_released_file_path
            disk_path = ("rbd:%s:mon_host=%s" %
                         (disk_src_name, mon_host))
            disk_cmd = ("qemu-img convert -O %s %s %s"
                        % (disk_format, blk_source, disk_path))
            process.run(disk_cmd, ignore_status=False)
            disk_params.update({'source_name': disk_src_name,
                                'source_host_name': disk_src_host,
                                'source_host_port': disk_src_port,
                                'source_protocol': source_protocol})
    elif non_release_os_url:
        disk_params.update({'source_file': download_file_path})
    elif boot_dev == "cdrom":
        disk_params.update({'device_type': 'cdrom',
                            'source_file': boot_iso_file})
    elif release_os_url:
        disk_params.update({'source_file': download_released_file_path})
    else:
        disk_params.update({'source_file': blk_source})

    new_disk = Disk(type_name=disk_type)
    new_disk.xml = open(create_disk_xml(disk_params)).read()
    vmxml.remove_all_disk()
    vmxml.add_device(new_disk)


def set_boot_dev_or_boot_order(vmxml, **kwargs):
    """
    Set up VM start sequence by given boot dev or boot order
    :param vmxml: The instance of VMXML class
    :param kwargs: Key words to specify boot dev or boot order
    :return:
    """
    vmxml.remove_all_boots()
    boot_ref = kwargs.get("boot_ref", "dev")
    boot_dev = kwargs.get("boot_dev", "hd")
    boot_order = kwargs.get("boot_order", "1")
    target_dev = kwargs.get("target_dev", "vdb")
    two_same_boot_dev = kwargs.get("two_same_boot_dev", False)
    boot_loadparm = kwargs.get("loadparm", None)
    if boot_ref == "dev":
        boot_list = []
        boot_list.append(boot_dev)
        # If more than one boot dev
        if two_same_boot_dev:
            boot_list.append(boot_dev)
        vmxml.set_os_attrs(**{"boots": boot_list})
    elif boot_ref == "order":
        if boot_loadparm:
            vmxml.set_boot_attrs_by_target_dev(target_dev, order=boot_order,
                                               loadparm=boot_loadparm)
        else:
            vmxml.set_boot_order_by_target_dev(target_dev, order=boot_order)


def run(test, params, env):
    """
    Test Boot OVMF Guest and Seabios Guest with options

    Steps:
    1) Edit VM xml with specified options
    2) For secure boot mode, boot OVMF Guest from
       cdrom first, enroll the key, then switch
       boot from hd
    3) For normal boot mode, directly boot Guest from given device
    4) Verify if Guest can boot as expected
    """
    vm_name = params.get("main_vm", "")
    vm = env.get_vm(vm_name)
    username = params.get("username", "root")
    password = params.get("password", "redhat")
    test_cmd = params.get("test_cmd", "")
    expected_output = params.get("expected_output", "")
    check_point = params.get("checkpoint", "")
    status_error = "yes" == params.get("status_error", "no")
    boot_iso_file = os.path.join(data_dir.get_tmp_dir(), "boot.iso")
    non_release_os_url = params.get("non_release_os_url", "")
    download_file_path = os.path.join(data_dir.get_tmp_dir(), "non_released_os.qcow2")
    release_os_url = params.get("release_os_url", "")
    download_released_file_path = os.path.join(data_dir.get_tmp_dir(), "released_os.qcow2")
    uefi_iso = params.get("uefi_iso", "")
    custom_codes = params.get("uefi_custom_codes", "")
    uefi_target_dev = params.get("uefi_target_dev", "")
    uefi_device_bus = params.get("uefi_device_bus", "")
    with_boot = (params.get("with_boot", "no") == "yes")
    boot_ref = params.get("boot_ref", "dev")
    boot_order = params.get("boot_order", "1")
    boot_dev = params.get("boot_dev", "hd")
    target_dev = params.get("target_dev", "vdb")
    vol_name = params.get("vol_name")
    brick_path = os.path.join(test.virtdir, "gluster-pool")
    boot_type = params.get("boot_type", "seabios")
    boot_loadparm = params.get("boot_loadparm", None)

    # Prepare result checkpoint list
    check_points = []
    if check_point:
        check_points.append(check_point)

    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Prepare a blank params to confirm if delete the configure at the end of the test
    ceph_cfg = ''
    try:
        # Create config file if it doesn't exist
        ceph_cfg = ceph.create_config_file(params.get("mon_host"))
        setup_test_env(params, test)
        apply_boot_options(vmxml, params, test)
        blk_source = vm.get_first_disk_devices()['source']
        set_domain_disk(vmxml, blk_source, params, test)
        vmxml.remove_all_boots()
        if with_boot:
            boot_kwargs = {"boot_ref": boot_ref,
                           "boot_dev": boot_dev,
                           "boot_order": boot_order,
                           "target_dev": target_dev,
                           "loadparm": boot_loadparm}
            if "yes" == params.get("two_same_boot_dev", "no"):
                boot_kwargs.update({"two_same_boot_dev": True})
            set_boot_dev_or_boot_order(vmxml, **boot_kwargs)
        define_error = ("yes" == params.get("define_error", "no"))
        enable_normal_boot(vmxml, check_points, define_error, test)
        # Some negative cases failed at virsh.define
        if define_error:
            return

        # Start VM and check result
        # For boot from cdrom or non_released_os, just verify key words from serial console output
        # For boot from disk image, run 'test cmd' to verify if OS boot well
        if boot_dev == "cdrom" or non_release_os_url:
            if not vm.is_alive():
                vm.start()
                check_prompt = params.get("check_prompt", "")
                while True:
                    if boot_type == "ovmf":
                        match, text = vm.serial_console.read_until_any_line_matches(
                                [check_prompt],
                                timeout=30.0, internal_timeout=0.5)
                    else:
                        match, text = read_until_any_line_matches(
                                vm.serial_console,
                                [check_prompt],
                                timeout=30.0, internal_timeout=0.5)
                    logging.debug("matches %s", check_prompt)
                    if match == -1:
                        logging.debug("Got check point as expected")
                        break
        elif boot_dev == "hd":
            ret = virsh.start(vm_name, timeout=60)
            utlv.check_result(ret, expected_fails=check_points)
            # For no boot options, further check if boot dev can be automatically added
            if not with_boot:
                if re.search(r"<boot dev='hd'/>", virsh.dumpxml(vm_name).stdout.strip()):
                    logging.debug("OS boot dev added automatically")
                else:
                    test.fail("OS boot dev not added as expected")
            if not status_error:
                vm_ip = vm.wait_for_get_address(0, timeout=240)
                remote_session = remote.wait_for_login("ssh", vm_ip, "22", username, password,
                                                       r"[\#\$]\s*$")
                if test_cmd:
                    status, output = remote_session.cmd_status_output(test_cmd)
                    logging.debug("CMD '%s' running result is:\n%s", test_cmd, output)
                    if expected_output:
                        if not re.search(expected_output, output):
                            test.fail("Expected '%s' to match '%s'"
                                      " but failed." % (output,
                                                        expected_output))
                    if status:
                        test.fail("Failed to boot %s from %s" % (vm_name, vmxml.xml))
                remote_session.close()
        logging.debug("Succeed to boot %s" % vm_name)
    finally:
        # Remove ceph configure file if created.
        if ceph_cfg:
            os.remove(ceph_cfg)
        logging.debug("Start to cleanup")
        if vm.is_alive:
            vm.destroy()
        logging.debug("Restore the VM XML")
        vmxml_backup.sync(options="--nvram")
        if cleanup_gluster:
            process.run("umount /mnt", ignore_status=True, shell=True)
            gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **params)
        if cleanup_iscsi:
            utlv.setup_or_cleanup_iscsi(False)
        if cleanup_iso_file:
            process.run("rm -rf %s" % boot_iso_file, shell=True, ignore_status=True)
        if cleanup_image_file:
            process.run("rm -rf %s" % download_file_path, shell=True, ignore_status=True)
        if cleanup_released_image_file:
            process.run("rm -rf %s" % download_released_file_path, shell=True, ignore_status=True)
