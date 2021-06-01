import os
import logging

from avocado.utils import process

from virttest import data_dir
from virttest import virt_vm
from virttest import virsh

from virttest import utils_disk
from virttest import utils_misc

from virttest.utils_test import libvirt

from virttest.utils_libvirt import libvirt_disk

from virttest.libvirt_xml import vm_xml, xcepts


def run(test, params, env):
    """
    Test virsh blockcopy with various option based on transient_guest.

    1.Prepare backend storage (iscsi)
    2.Start VM
    3.Execute virsh blockcopy target command
    4.Check status after operation accomplished
    5.Clean up test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def setup_file_backend_env(params):
        """
        Setup iscsi test environment

        :param params: one dict to wrap up parameters
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        type_name = params.get("virt_disk_device_type")
        disk_device = params.get("virt_disk_device")
        device_target = params.get("virt_disk_device_target")
        device_bus = params.get("virt_disk_device_bus")
        device_format = params.get("virt_disk_device_format")
        blockcopy_image_name = params.get("blockcopy_image_name")
        emulated_size = int(params.get("emulated_size", "2"))

        libvirt.create_local_disk("file", blockcopy_image_name, emulated_size, "qcow2")

        disk_src_dict = {"attrs": {"file": blockcopy_image_name}}

        file_disk = libvirt_disk.create_primitive_disk_xml(
            type_name, disk_device,
            device_target, device_bus,
            device_format, disk_src_dict, None)
        logging.debug("guest xml after undefined and recreated:\n%s", file_disk)
        return file_disk

    def start_pivot_blkcpy_on_transient_vm():
        """
        Start blockcopy with pivot option
        """
        external_snapshot_disks = libvirt_disk.make_external_disk_snapshots(vm, device_target, "trans_snapshot", snapshot_take)
        logging.debug("external snapshots:%s\n", external_snapshot_disks)
        external_snapshot_disks.pop()
        for sub_option in ["--shallow --pivot", "--pivot"]:
            tmp_copy_path = os.path.join(data_dir.get_data_dir(), "%s_%s.img" % (vm_name, sub_option[2:5]))
            tmp_blkcopy_path.append(tmp_copy_path)
            if os.path.exists(tmp_copy_path):
                libvirt.delete_local_disk('file', tmp_copy_path)
            virsh.blockcopy(vm_name, device_target, tmp_copy_path,
                            options=sub_option, ignore_status=False,
                            debug=True)
            back_chain_files = libvirt_disk.get_chain_backing_files(tmp_copy_path)
            back_chain_files = back_chain_files[1:len(back_chain_files)]
            logging.debug("debug blockcopy xml restore:%s and %s\n", external_snapshot_disks, back_chain_files)
            if back_chain_files != external_snapshot_disks:
                test.fail("can not get identical backing chain")
            utils_misc.wait_for(lambda: libvirt.check_blockjob(vm_name, device_target), 5)
            #After pivot, no backing chain exists
            external_snapshot_disks = []

    def check_info_in_libvird_log_file(matchedMsg=None):
        """
        Check if information can be found in libvirtd log.

        :params matchedMsg: expected matched messages
        """
        # Check libvirtd log file.
        libvirtd_log_file = log_config_path
        if not os.path.exists(libvirtd_log_file):
            test.fail("Expected VM log file: %s not exists" % libvirtd_log_file)
        cmd = ("grep -nr '%s' %s" % (matchedMsg, libvirtd_log_file))
        return process.run(cmd, ignore_status=True, shell=True).exit_status == 0

    def check_bandwidth_progress(bandwidth_value):
        """
        Check bandwidth

        :param bandwidth_value: expected bandwidth value
        """
        ret = utils_misc.wait_for(lambda: libvirt.check_blockjob(vm_name, device_target, "bandwidth", bandwidth_value), 30)
        if not ret:
            test.fail("Failed to get bandwidth limit output")

    def _extend_blkcpy_execution(sub_option, sub_status_error, pre_created=False):
        """
        Wrap up blockcopy execution combining with various options

        :params sub_option: option
        :params sub_status_error: expected error or not
        :params pre_created: whether pre-created
        """
        tmp_copy_path = os.path.join(data_dir.get_data_dir(), "%s_%s.img" % (vm_name, sub_option))
        if os.path.exists(tmp_copy_path):
            libvirt.delete_local_disk('file', tmp_copy_path)
        if pre_created:
            libvirt.create_local_disk('file', tmp_copy_path, '10M', 'qcow2')
        tmp_option = params.get("options") % sub_option
        if "default" in tmp_option:
            tmp_option = " --wait --verbose"
        result = virsh.blockcopy(vm_name, device_target, tmp_copy_path,
                                 options=tmp_option, ignore_status=True,
                                 debug=True)
        logging.debug(sub_status_error)
        libvirt.check_exit_status(result, expect_error=sub_status_error)

    def start_granularity_blkcpy_on_transient_vm():
        """Start blockcopy with granularity operations """
        granularity_value = params.get('granularity_value').split()
        option_status_error = [value == 'yes' for value in params.get('option_status_error').split()]
        for sub_option, sub_status_error in zip(granularity_value, option_status_error):
            _extend_blkcpy_execution(sub_option, sub_status_error)
            if not option_status_error:
                #Check log whether granularity keyword is there
                result = utils_misc.wait_for(lambda: check_info_in_libvird_log_file('"granularity":%s' % sub_option), timeout=20)
                if not result:
                    test.fail("Failed to get expected messages from log file: %s."
                              % log_config_path)

            virsh.blockjob(vm_name, device_target, '--abort', ignore_status=True)

    def start_bandwidth_blkcpy_on_transient_vm():
        """Start blockcopy with bandwidth operations """
        bandwidth_value = params.get('bandwidth_value').split()
        option_status_error = [value == 'yes' for value in params.get('option_status_error').split()]
        for sub_option, sub_status_error in zip(bandwidth_value, option_status_error):
            _extend_blkcpy_execution(sub_option, sub_status_error)
            if not sub_status_error and 'default' not in sub_option:
                check_bandwidth_progress(sub_option)
            virsh.blockjob(vm_name, device_target, '--abort', ignore_status=True)

    def start_timeout_blkcpy_on_transient_vm():
        """Start blockcopy with timeout operations """
        timeout_value = params.get('timeout_value').split()
        option_status_error = [value == 'yes' for value in params.get('option_status_error').split()]
        for sub_option, sub_status_error in zip(timeout_value, option_status_error):
            _extend_blkcpy_execution(sub_option, sub_status_error)
            virsh.blockjob(vm_name, device_target, '--abort', ignore_status=True)

    def start_bufsize_blkcpy_on_transient_vm():
        """Start blockcopy with buffer size operations """
        bufsize_value = params.get('bufsize_value').split()
        option_status_error = [value == 'yes' for value in params.get('option_status_error').split()]
        for sub_option, sub_status_error in zip(bufsize_value, option_status_error):
            _extend_blkcpy_execution(sub_option, sub_status_error)
            if not option_status_error:
                #Check log whether  buf-size keyword is there
                result = utils_misc.wait_for(lambda: check_info_in_libvird_log_file('buf-size'), timeout=20)
                if not result:
                    test.fail("Failed to get expected messages from log file: %s."
                              % log_config_path)
            virsh.blockjob(vm_name, device_target, '--abort', ignore_status=True)

    def start_reuse_external_blkcpy_on_transient_vm():
        """Start reuse external blockcopy operations """
        reuse_external_value = params.get('reuse-external_value').split()
        option_status_error = [value == 'yes' for value in params.get('option_status_error').split()]
        for sub_option, sub_status_error in zip(reuse_external_value, option_status_error):
            _extend_blkcpy_execution(sub_option, sub_status_error, pre_created=True)
            if option_status_error:
                #Check blockcommit job information
                job_result = virsh.blockjob(vm_name, device_target, '--info', ignore_status=True).stdout_text.strip()
                if 'No current block job for' not in job_result:
                    test.fail("Failed to get unexpected active blockcommit job")
            virsh.blockjob(vm_name, device_target, '--abort', ignore_status=True)

    # Disk specific attributes.
    device_target = params.get("virt_disk_device_target", "vdd")
    blockcopy_option = params.get("blockcopy_option")
    backend_storage_type = params.get("backend_storage_type")
    device_type = params.get("virt_disk_device_type")

    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")
    snapshot_take = int(params.get("snapshot_take", "4"))

    # Configure libvirtd log path
    log_config_path = params.get("libvirtd_debug_file", "/var/log/libvirt/libvird.log")

    # Additional disk images.
    tmp_blkcopy_path = []
    external_snapshot_disks = []
    attach_disk_xml = None

    # Start VM and get all partitions in VM.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        # Setup backend storage
        if backend_storage_type == "file":
            attach_disk_xml = setup_file_backend_env(params)

        # Add disk xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("disk xml is:\n%s" % attach_disk_xml)
        # Sync VM xml.
        if attach_disk_xml is None:
            test.fail("Fail to create attached disk xml")
        else:
            vmxml.add_device(attach_disk_xml)
            vmxml.sync()
        try:
            # Create a transient VM
            transient_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            virsh.undefine(vm_name, debug=True, ignore_status=False)
            virsh.create(transient_vmxml.xml, ignore_status=False, debug=True)
            vm.wait_for_login().close()
            debug_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            logging.debug("guest xml after undefined and recreated:%s\n", debug_xml)
        except xcepts.LibvirtXMLError as xml_error:
            if not define_error:
                test.fail("Failed to define VM:\n%s" % str(xml_error))
        except virt_vm.VMStartError as details:
            # VM cannot be started
            if status_error:
                logging.info("VM failed to start as expected: %s", str(details))
            else:
                test.fail("VM should start but failed: %s" % str(details))

        if blockcopy_option in ['pivot_shadow']:
            start_pivot_blkcpy_on_transient_vm()

        if blockcopy_option in ['granularity']:
            start_granularity_blkcpy_on_transient_vm()

        if blockcopy_option in ['bandwidth']:
            start_bandwidth_blkcpy_on_transient_vm()

        if blockcopy_option in ['timeout']:
            start_timeout_blkcpy_on_transient_vm()

        if blockcopy_option in ['buf_size']:
            start_bufsize_blkcpy_on_transient_vm()

        if blockcopy_option in ['reuse_external']:
            start_reuse_external_blkcpy_on_transient_vm()
    finally:
        if virsh.domain_exists(vm_name):
            #To clean up snapshots and restore VM
            try:
                libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
            finally:
                if vm.is_alive():
                    vm.destroy(gracefully=False)
                virsh.define(vmxml_backup.xml, debug=True)
        vmxml_backup.sync()
        # Clean up backend storage
        for tmp_path in tmp_blkcopy_path:
            if os.path.exists(tmp_path):
                libvirt.delete_local_disk('file', tmp_path)
        if backend_storage_type == "iscsi":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
