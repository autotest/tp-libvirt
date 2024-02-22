import os
import logging as log
import locale
import base64

from avocado.utils import process

from virttest import data_dir
from virttest import virt_vm
from virttest import virsh

from virttest.utils_test import libvirt

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_ceph_utils
from virttest.utils_libvirt import libvirt_secret

from virttest.utils_nbd import NbdExport

from virttest.libvirt_xml import vm_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test virsh blockpull with various option on VM.

    1.Prepare backend storage (iscsi,nbd,file,block)
    2.Start VM
    3.Execute virsh blockpull target command
    4.Check status after operation accomplished
    5.Clean up test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def setup_iscsi_block_env(params):
        """
        Setup iscsi as block test environment

        :param params: one dict to wrap up parameters
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        emulated_size = params.get("emulated_size", "10G")
        chap_user = params.get("iscsi_user")
        chap_passwd = params.get("iscsi_password")
        auth_sec_usage_type = params.get("secret_usage_type")
        encoding = locale.getpreferredencoding()
        secret_string = base64.b64encode(chap_passwd.encode(encoding)).decode(encoding)

        device_source = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                       is_login=True,
                                                       image_size=emulated_size,
                                                       chap_user=chap_user,
                                                       chap_passwd=chap_passwd,
                                                       portal_ip="127.0.0.1")

        auth_sec_uuid = libvirt_ceph_utils._create_secret(auth_sec_usage_type, secret_string)
        disk_auth_dict = {"auth_user": chap_user,
                          "secret_type": auth_sec_usage_type,
                          "secret_uuid": auth_sec_uuid}

        disk_src_dict = {'attrs': {'dev': device_source}}
        iscsi_disk = libvirt_disk.create_primitive_disk_xml(
            type_name, disk_device,
            device_target, device_bus,
            device_format, disk_src_dict, disk_auth_dict)
        # Add disk xml.
        logging.debug("disk xml is:\n%s" % iscsi_disk)
        # Sync VM xml.
        vmxml.add_device(iscsi_disk)
        vmxml.sync()

    def setup_file_env(params):
        """
        Setup file test environment

        :param params: one dict to wrap up parameters
        """
        # If additional_disk is False, it means that there is no need to create additional disk
        if additional_disk is False:
            return
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        backstore_image_target_path = params.get("backstore_image_name")
        tmp_blkpull_path.append(backstore_image_target_path)
        libvirt.create_local_disk("file", backstore_image_target_path, "1", "qcow2")
        backing_chain_list.append(backstore_image_target_path)

        disk_src_dict = {"attrs": {"file": backstore_image_target_path}}

        file_disk = libvirt_disk.create_primitive_disk_xml(
            type_name, disk_device,
            device_target, device_bus,
            device_format, disk_src_dict, None)
        logging.debug("disk xml is:\n%s" % file_disk)
        # Sync VM xml.
        vmxml.add_device(file_disk)
        vmxml.sync()
        _generate_backstore_attribute(params)

    def _generate_backstore_attribute(params):
        """
        Create one disk with backingStore attribute by creating snapshot

        :param params: one dict to wrap up parameters
        """
        device_target = params.get("virt_disk_device_target")
        top_file_image_name = params.get("top_file_image_name")
        second_file_image_name = params.get("second_file_image_name")
        tmp_blkpull_path.append(top_file_image_name)
        tmp_blkpull_path.append(second_file_image_name)
        backing_chain_list.append(top_file_image_name)
        if vm.is_dead():
            vm.start()
        snapshot_tmp_name = "blockpull_tmp_snap"
        options = " %s --disk-only --diskspec %s,file=%s" % (snapshot_tmp_name, 'vda', second_file_image_name)
        options += " --diskspec %s,file=%s" % (device_target, top_file_image_name)
        virsh.snapshot_create_as(vm_name, options,
                                 ignore_status=False,
                                 debug=True)
        vm.destroy()
        virsh.snapshot_delete(vm_name, snapshot_tmp_name, "--metadata", ignore_status=False, debug=True)
        vmxml_dir = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("backstore prepare readiness :\n%s", vmxml_dir)

    def setup_block_env(params):
        """
        Setup block test environment

        :param params: one dict to wrap up parameters
        """
        if additional_disk is False:
            return
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        device_source = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        disk_src_dict = {'attrs': {'dev': device_source}}
        backing_chain_list.append(device_source)

        file_disk = libvirt_disk.create_primitive_disk_xml(
            type_name, disk_device,
            device_target, device_bus,
            device_format, disk_src_dict, None)
        logging.debug("disk xml is:\n%s" % file_disk)
        # Sync VM xml.
        vmxml.add_device(file_disk)
        vmxml.sync()
        _generate_backstore_attribute(params)

    def setup_nbd_env(params):
        """
        Setup nbd test environment

        :param params: one dict to wrap up parameters
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Get server hostname.
        hostname = process.run('hostname', ignore_status=False, shell=True, verbose=True).stdout_text.strip()
        # Setup backend storage
        nbd_server_host = hostname
        nbd_server_port = params.get("nbd_server_port", "10001")
        image_path = params.get("emulated_image", "/var/lib/libvirt/images/nbdtest.img")
        enable_ga_agent = "yes" == params.get("enable_ga_agent", "no")

        # Create NbdExport object
        nbd = NbdExport(image_path, image_format=device_format,
                        port=nbd_server_port)
        nbd.start_nbd_server()

        # Prepare disk source xml
        source_attrs_dict = {"protocol": "nbd", "tls": "%s" % "no"}

        disk_src_dict = {}
        disk_src_dict.update({"attrs": source_attrs_dict})
        disk_src_dict.update({"hosts": [{"name": nbd_server_host, "port": nbd_server_port}]})

        network_disk = libvirt_disk.create_primitive_disk_xml(
            type_name, disk_device,
            device_target, device_bus,
            device_format, disk_src_dict, None)

        logging.debug("disk xml is:\n%s" % network_disk)
        # Sync VM xml.
        vmxml.add_device(network_disk)
        vmxml.sync()
        if enable_ga_agent:
            vm.prepare_guest_agent()
            vm.destroy(gracefully=False)

    def check_chain_backing_files(chain_list, disk_target):
        """
        Check chain backing files

        :param chain_list: list, expected backing chain list
        :param disk_target: disk target
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.devices.by_device_tag('disk')
        disk_xml = None
        for disk in disks:
            if disk.target['dev'] == disk_target:
                disk_xml = disk
                break
        logging.debug("disk xml in check_blockcommit_with_bandwidth: %s\n", disk_xml.xmltreefile)
        backingstore_list = disk_xml.get_backingstore_list()
        backingstore_list.pop()
        parse_source_file_list = [elem.find('source').get('file') or elem.find('source').get('name')
                                  or elem.find('source').get('dev')
                                  for elem in backingstore_list]
        logging.debug("before expected backing chain list is %s", chain_list)
        chain_list = chain_list[0:3]
        if backend_storage_type == "nbd":
            chain_list = chain_list[0:1]
        chain_list = chain_list[::-1]
        logging.debug("expected backing chain list is %s", chain_list)
        logging.debug("parse source list is %s", parse_source_file_list)
        # Check whether two are equals
        if blockpull_option in ['keep_relative']:
            if parse_source_file_list[-1] != chain_list[-1]:
                test.fail("checked backchain list in last element is not equals to expected one")
        elif parse_source_file_list != chain_list:
            test.fail("checked backchain list is not equals to expected one")

    def _extend_blkpull_execution(base=None, status_error=False, err_msg=None, expected_msg=None):
        """
        Wrap up blockpull execution combining with various options

        :params base: specific base
        :params status_error: expected error or not
        :params err_msg: error message if blockpull command fail
        :params expected_msg: jobinfo expected message if checked
        """
        blockpull_options = params.get("options")
        if '--base' in blockpull_options:
            if base:
                blockpull_options = params.get("options") % base
            else:
                blockpull_options = params.get("options") % external_snapshot_disks[0]
        result = virsh.blockpull(vm_name, device_target,
                                 blockpull_options, ignore_status=True, debug=True)
        libvirt.check_exit_status(result, expect_error=status_error)
        if status_error:
            if err_msg not in result.stdout_text and err_msg not in result.stderr_text:
                test.fail("Can not find failed message in standard output: %s or : %s"
                          % (result.stdout_text, result.stderr_text))
        res = virsh.blockjob(vm_name, device_target, "--info").stdout.strip()
        logging.debug("virsh jobinfo is :%s\n", res)
        if expected_msg:
            job_msg = expected_msg
        else:
            job_msg = "No current block job for %s" % device_target
        if res and job_msg not in res:
            test.fail("Find unexpected block job information in %s" % res)

    def start_async_blkpull_on_vm():
        """Start blockpull with async"""
        context_msg = "Pull aborted"
        _extend_blkpull_execution(None, True, context_msg)

    def start_bandwidth_blkpull_on_vm():
        """Start blockpull with bandwidth option """
        _extend_blkpull_execution()

    def start_timeout_blkpull_on_vm():
        """Start blockpull with timeout option """
        _extend_blkpull_execution()

    def start_middle_to_top_to_base_on_vm():
        """Start blockpull from middle to top """
        _extend_blkpull_execution()
        virsh.blockjob(vm_name, device_target, '--abort', ignore_status=True)
        params.update({"options": params.get("top_options")})
        _extend_blkpull_execution()

    def start_reuse_external_blkpull_on_vm():
        """Start blockpull with reuse_external """
        _extend_blkpull_execution(base=backing_chain_list[1])
        check_chain_backing_files(backing_chain_list, params.get("virt_disk_device_target"))
        virsh.blockjob(vm_name, device_target, '--abort', ignore_status=True)
        params.update({"options": params.get("top_options")})
        _extend_blkpull_execution()

    def start_top_as_base_blkpull_on_vm():
        """Start blockpull with top as base """
        error_msg = "error: invalid argument"
        _extend_blkpull_execution(base=backing_chain_list[-1], status_error=True, err_msg=error_msg)

    def start_base_to_top_blkpull_on_vm():
        """Start blockpull with base as top """
        _extend_blkpull_execution()

    def start_middletotop_blkpull_on_vm():
        """start middletotop blockpull on vm """
        _extend_blkpull_execution()
        check_chain_backing_files(backing_chain_list, params.get("virt_disk_device_target"))

    # Disk specific attributes.
    type_name = params.get("virt_disk_device_type")
    disk_device = params.get("virt_disk_device")
    device_target = params.get("virt_disk_device_target")
    device_bus = params.get("virt_disk_device_bus")
    device_format = params.get("virt_disk_device_format")

    blockpull_option = params.get("blockpull_option")
    options_value = params.get("options_value")
    backend_storage_type = params.get("backend_storage_type")
    backend_path = params.get("backend_path")
    additional_disk = "yes" == params.get("additional_disk", "yes")

    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")
    snapshot_take = int(params.get("snapshot_take", "4"))
    fill_in_vm = "yes" == params.get("fill_in_vm", "no")

    first_src_file = libvirt_disk.get_first_disk_source(vm)
    pre_set_root_dir = os.path.dirname(first_src_file)
    replace_disk_image = None

    # Additional disk images.
    tmp_dir = data_dir.get_data_dir()
    tmp_blkpull_path = []
    disks_img = []
    external_snapshot_disks = []
    attach_disk_xml = None
    backing_chain_list = []

    # Initialize one NbdExport object
    nbd = None

    # Start VM
    if vm.is_dead():
        vm.start()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        libvirt_secret.clean_up_secrets()

        # Setup backend storage
        if backend_storage_type == "iscsi":
            setup_iscsi_block_env(params)
        elif backend_storage_type == "file":
            setup_file_env(params)
        elif backend_storage_type == "block":
            setup_block_env(params)
        elif backend_storage_type == "nbd":
            setup_nbd_env(params)
        try:
            vm.start()
            session = vm.wait_for_login()
            disk_name = device_target
            if additional_disk:
                disk_name, _ = libvirt_disk.get_non_root_disk_name(session)
            session.close()
        except virt_vm.VMStartError as details:
            # VM cannot be started
            if status_error:
                logging.info("VM failed to start as expected: %s", str(details))
            else:
                test.fail("VM should start but failed: %s" % str(details))

        if fill_in_vm:
            libvirt_disk.fill_null_in_vm(vm, disk_name)

        if backend_path in ['native_path']:
            external_snapshot_disks = libvirt_disk.make_external_disk_snapshots(vm, device_target, "blockpull_snapshot", snapshot_take)
            backing_chain_list.extend(external_snapshot_disks)
        elif backend_path in ['reuse_external']:
            replace_disk_image, backing_chain_list = libvirt_disk.make_relative_path_backing_files(
                vm, pre_set_root_dir, first_src_file, device_format)
            params.update({'disk_source_name': replace_disk_image,
                           'disk_type': 'file',
                           'disk_format': 'qcow2',
                           'disk_source_protocol': 'file'})
            libvirt.set_vm_disk(vm, params, tmp_dir)

        if blockpull_option in ['middle_to_top']:
            start_middletotop_blkpull_on_vm()

        if blockpull_option in ['async']:
            start_async_blkpull_on_vm()

        if blockpull_option in ['bandwidth']:
            start_bandwidth_blkpull_on_vm()

        if blockpull_option in ['timeout']:
            start_timeout_blkpull_on_vm()

        if blockpull_option in ['middle_to_top_to_base']:
            start_middle_to_top_to_base_on_vm()

        if blockpull_option in ['keep_relative']:
            start_reuse_external_blkpull_on_vm()

        if blockpull_option in ['top_as_base']:
            start_top_as_base_blkpull_on_vm()

        if blockpull_option in ['base_to_top']:
            start_base_to_top_blkpull_on_vm()
    finally:
        # Recover VM.
        libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
        libvirt_secret.clean_up_secrets()
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # Delete reuse external disk if exists
        for disk in external_snapshot_disks:
            if os.path.exists(disk):
                os.remove(disk)
        # Clean up backend storage
        for tmp_path in tmp_blkpull_path:
            if os.path.exists(tmp_path):
                libvirt.delete_local_disk('file', tmp_path)
        if backend_storage_type == "iscsi":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        if nbd:
            nbd.cleanup()
        # Clean up created folders
        for folder in [chr(letter) for letter in range(ord('a'), ord('a') + 4)]:
            rm_cmd = "rm -rf %s" % os.path.join(pre_set_root_dir, folder)
            process.run(rm_cmd, shell=True)
