import os
import re
import logging
import aexpect

from virttest import virsh
from virttest import data_dir
from virttest import utils_disk
from virttest import utils_backup
from virttest import utils_secret
from virttest import utils_misc
from virttest import xml_utils
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test the pull-mode backup function

    Steps:
    1. craete a vm with extra disk vdb
    2. create some data on vdb
    3. start a pull mode full backup on vdb
    4. monitor block-threshold event on scratch file/dev
    5. create some data on vdb's same postion as step 2 to trigger event
    6. check the block-threshold event captured
    """

    # Basic case config
    hotplug_disk = "yes" == params.get("hotplug_disk", "no")
    original_disk_size = params.get("original_disk_size", "100M")
    original_disk_type = params.get("original_disk_type", "local")
    original_disk_target = params.get("original_disk_target", "vdb")
    event_type = params.get("event_type")
    usage_threshold = params.get("usage_threshold", "100")
    tmp_dir = data_dir.get_tmp_dir()
    local_hostname = params.get("loal_hostname", "localhost")
    # Backup config
    scratch_type = params.get("scratch_type", "file")
    reuse_scratch_file = "yes" == params.get("reuse_scratch_file")
    scratch_blkdev_size = params.get("scratch_blkdev_size", original_disk_size)
    # NBD service config
    nbd_protocol = params.get("nbd_protocol", "unix")
    nbd_socket = params.get("nbd_socket", "/tmp/pull_backup.socket")
    nbd_tcp_port = params.get("nbd_tcp_port", "10809")
    nbd_hostname = local_hostname
    # LUKS config
    scratch_luks_encrypted = "yes" == params.get("scratch_luks_encrypted")
    luks_passphrase = params.get("luks_passphrase", "password")
    # Open a new virsh session for event monitor
    virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC, auto_close=True)
    # Cancel the test if libvirt support related functions
    if not libvirt_version.version_compare(7, 0, 0):
        test.cancel("Current libvirt version doesn't support "
                    "event monitor for incremental backup.")

    def get_backup_disk_index(vm_name, disk_name):
        """
        Get the index of the backup disk to be monitored by the virsh event

        :param vm_name: vm name
        :param disk_name: virtual disk name, such as 'vdb'
        :return: the index of the virtual disk in backup xml
        """
        backup_xml = virsh.backup_dumpxml(vm_name).stdout.strip()
        logging.debug("%s's current backup xml is: %s" % (vm_name, backup_xml))
        backup_xml_dom = xml_utils.XMLTreeFile(backup_xml)
        index_xpath = "/disks/disk"
        for disk_element in backup_xml_dom.findall(index_xpath):
            if disk_element.get("name") == disk_name:
                return disk_element.get("index")

    def is_event_captured(virsh_session, re_pattern):
        """
        Check if event captured

        :param virsh_session: the virsh session of the event monitor
        :param re_pattern: the re pattern used to represent the event
        :return: True means event captured, False means not
        """
        ret_output = virsh_session.get_stripped_output()
        if (not re.search(re_pattern, ret_output, re.IGNORECASE)):
            return False
        logging.debug("event monitor output: %s", ret_output)
        return True

    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)

        # Make sure thedisk_element.getre is no checkpoint metadata before test
        utils_backup.clean_checkpoints(vm_name)

        # Backup vm xml
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()
        utils_backup.enable_inc_backup_for_vm(vm)

        # Prepare libvirt secret
        if scratch_luks_encrypted:
            utils_secret.clean_up_secrets()
            luks_secret_uuid = libvirt.create_secret(params)
            virsh.secret_set_value(luks_secret_uuid, luks_passphrase,
                                   encode=True, debug=True)

        # Prepare the disk to be backuped.
        disk_params = {}
        disk_path = ""
        image_name = "{}_image.qcow2".format(original_disk_target)
        disk_path = os.path.join(tmp_dir, image_name)
        libvirt.create_local_disk("file", disk_path, original_disk_size,
                                  "qcow2")
        disk_params = {"device_type": "disk",
                       "type_name": "file",
                       "driver_type": "qcow2",
                       "target_dev": original_disk_target,
                       "source_file": disk_path}
        disk_params["target_dev"] = original_disk_target
        disk_xml = libvirt.create_disk_xml(disk_params)
        virsh.attach_device(vm.name, disk_xml,
                            flagstr="--config", debug=True)
        vm.start()
        session = vm.wait_for_login()
        new_disks_in_vm = list(utils_disk.get_linux_disks(session).keys())
        session.close()
        if len(new_disks_in_vm) != 1:
            test.fail("Test disk not prepared in vm")

        # Use the newly added disk as the test disk
        test_disk_in_vm = "/dev/" + new_disks_in_vm[0]

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_disks = list(vmxml.get_disk_all().keys())

        checkpoint_list = []
        backup_file_list = []

        # Prepare backup xml
        backup_params = {"backup_mode": "pull"}
        # Set libvirt default nbd export name and bitmap name
        nbd_export_name = original_disk_target
        nbd_bitmap_name = "backup-" + original_disk_target

        backup_server_dict = {}
        if nbd_protocol == "unix":
            backup_server_dict["transport"] = "unix"
            backup_server_dict["socket"] = nbd_socket
        else:
            backup_server_dict["name"] = nbd_hostname
            backup_server_dict["port"] = nbd_tcp_port
        backup_params["backup_server"] = backup_server_dict
        backup_disk_xmls = []
        for vm_disk in vm_disks:
            backup_disk_params = {"disk_name": vm_disk}
            if vm_disk != original_disk_target:
                backup_disk_params["enable_backup"] = "no"
            else:
                backup_disk_params["enable_backup"] = "yes"
                backup_disk_params["disk_type"] = scratch_type
                # Prepare nbd scratch file/dev params
                scratch_params = {"attrs": {}}
                scratch_path = None
                if scratch_type == "file":
                    scratch_file_name = "scratch_file"
                    scratch_path = os.path.join(tmp_dir, scratch_file_name)
                    if reuse_scratch_file:
                        libvirt.create_local_disk("file", scratch_path,
                                                  original_disk_size, "qcow2")
                    scratch_params["attrs"]["file"] = scratch_path
                elif scratch_type == "block":
                    scratch_path = libvirt.setup_or_cleanup_iscsi(
                            is_setup=True, image_size=scratch_blkdev_size)
                    scratch_params["attrs"]["dev"] = scratch_path
                else:
                    test.fail("We do not support backup scratch type: '%s'"
                              % scratch_type)
                if scratch_luks_encrypted:
                    encryption_dict = {"encryption": "luks",
                                       "secret": {"type": "passphrase",
                                                  "uuid": luks_secret_uuid}}
                    scratch_params["encryption"] = encryption_dict
                logging.debug("scratch params: %s", scratch_params)
                backup_disk_params["backup_scratch"] = scratch_params

            backup_disk_xml = utils_backup.create_backup_disk_xml(
                    backup_disk_params)
            backup_disk_xmls.append(backup_disk_xml)
        logging.debug("disk list %s", backup_disk_xmls)
        backup_xml = utils_backup.create_backup_xml(backup_params,
                                                    backup_disk_xmls)
        logging.debug("Backup Xml: %s", backup_xml)

        # Prepare checkpoint xml
        checkpoint_name = "checkpoint"
        checkpoint_list.append(checkpoint_name)
        cp_params = {"checkpoint_name": checkpoint_name}
        cp_params["checkpoint_desc"] = params.get("checkpoint_desc",
                                                  "desc of cp")
        disk_param_list = []
        for vm_disk in vm_disks:
            cp_disk_param = {"name": vm_disk}
            if vm_disk != original_disk_target:
                cp_disk_param["checkpoint"] = "no"
            else:
                cp_disk_param["checkpoint"] = "bitmap"
                cp_disk_bitmap = params.get("cp_disk_bitmap")
                if cp_disk_bitmap:
                    cp_disk_param["bitmap"] = cp_disk_bitmap
            disk_param_list.append(cp_disk_param)
        checkpoint_xml = utils_backup.create_checkpoint_xml(cp_params,
                                                            disk_param_list)
        logging.debug("Checkpoint Xml: %s", checkpoint_xml)

        # Generate some random data in vm's test disk
        def dd_data_to_testdisk():
            """
            Generate some data to vm's test disk
            """
            dd_count = "1"
            dd_seek = "10"
            dd_bs = "1M"
            session = vm.wait_for_login()
            utils_disk.dd_data_to_vm_disk(session, test_disk_in_vm, dd_bs,
                                          dd_seek, dd_count)
            session.close()

        dd_data_to_testdisk()

        # Start backup
        backup_options = backup_xml.xml + " " + checkpoint_xml.xml
        if reuse_scratch_file:
            backup_options += " --reuse-external"
        backup_result = virsh.backup_begin(vm_name, backup_options,
                                           debug=True)

        # Start to monitor block-threshold of backup disk's scratch file/dev
        backup_disk_index = get_backup_disk_index(vm_name, original_disk_target)
        if not backup_disk_index:
            test.fail("Backup xml has no index for disks.")
        backup_disk_obj = original_disk_target + "[%s]" % backup_disk_index
        virsh.domblkthreshold(vm_name,
                              original_disk_target + "[%s]" % backup_disk_index,
                              usage_threshold)
        event_cmd = "event %s %s --loop" % (vm_name, event_type)
        virsh_session.sendline(event_cmd)

        # Generate some random data to same position of vm's test disk
        dd_data_to_testdisk()

        # Check if the block-threshold event captured by monitor
        if event_type == "block-threshold":
            event_pattern = (".*block-threshold.*%s.*%s\[%s\].* %s .*" %
                             (vm_name, original_disk_target,
                              backup_disk_index, usage_threshold))
        if not utils_misc.wait_for(lambda: is_event_captured(virsh_session, event_pattern), 10):
            test.fail("Event not captured by event monitor")

        # Abort backup job
        virsh.domjobabort(vm_name, debug=True)

    finally:
        # Remove checkpoints
        if "checkpoint_list" in locals() and checkpoint_list:
            for checkpoint_name in checkpoint_list:
                virsh.checkpoint_delete(vm_name, checkpoint_name)

        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Restoring vm
        vmxml_backup.sync()

        # Remove libvirt secret
        if "luks_secret_uuid" in locals():
            virsh.secret_undefine(luks_secret_uuid, ignore_status=True)

        # Remove iscsi devices
        if scratch_type == "block":
            libvirt.setup_or_cleanup_iscsi(False)

        # Remove scratch file
        if "scratch_path" in locals():
            if scratch_type == "file" and os.path.exists(scratch_path):
                os.remove(scratch_path)
