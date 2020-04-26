import os
import logging
import aexpect
import platform
import time
import shutil

from avocado.utils import process

from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest import utils_disk
from virttest import utils_misc
from virttest import utils_npiv as mpath
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml

WAIT_FOR_MPATH_DEVS_TIMEOUT = 60


def run(test, params, env):
    """
    Test steps:
    1. Prepare a multipath device.
    2. Prepare virtual disk xml using this multipath device.
    3. Hot/Cold-plug the disk xml to virtual machine.
    4. Check the attached disk in the virtual machine.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    mpath_conf_path = params.get('mpath_conf_path', '/etc/multipath.conf')
    mpath_conf_bkup_path = params.get('mpath_conf_bkup_path',
                                      '/etc/multipath.conf.bkup')
    mpath_conf_exist = False

    def prepare_multipath_conf():
        """
        Prepare the multipath.conf to make sure iscsi lun can be seen as a
        multipath device.
        :return: True means the multipath.conf exists at first, False means not.
        """
        multipath_conf_exist = False
        mpath_conf_content = ("defaults {\n"
                              "    user_friendly_names yes\n"
                              "    path_grouping_policy multibus\n"
                              "    failback immediate\n"
                              "    no_path_retry fail\n"
                              "}\n")
        if os.path.exists(mpath_conf_bkup_path):
            os.remove(mpath_conf_bkup_path)
        if os.path.exists(mpath_conf_path):
            multipath_conf_exist = True
            shutil.move(mpath_conf_path, mpath_conf_bkup_path)
        with open(mpath_conf_path, 'wt') as mpath_conf_file:
            mpath_conf_file.write(mpath_conf_content)
        return multipath_conf_exist

    def recover_multipath_conf(remove_mpath_conf=False):
        """
        Recover the multipath.conf.
        :param remove_mpath_conf: True to remove multipath.conf.
        """
        if os.path.exists(mpath_conf_bkup_path):
            if os.path.exists(mpath_conf_path):
                os.remove(mpath_conf_path)
            shutil.move(mpath_conf_bkup_path, mpath_conf_path)
        if os.path.exists(mpath_conf_path) and remove_mpath_conf:
            os.remove(mpath_conf_path)

    def check_in_vm(vm, old_parts):
        """
        Check mount/read/write disk in VM.

        :param vm: Virtual machine to be checked.
        :param old_parts: Original disk partitions in VM.
        """
        try:
            session = vm.wait_for_login()
            if platform.platform().count('ppc64'):
                time.sleep(10)
            new_parts = utils_disk.get_parts_list(session)
            added_parts = list(set(new_parts).difference(set(old_parts)))
            logging.info("Added parts:%s", added_parts)
            if len(added_parts) != 1:
                test.fail("The number of new partitions is invalid in VM")
            else:
                added_part = added_parts[0]
            cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   "mkdir -p test && mount /dev/{0} test && echo"
                   " teststring > test/testfile && umount test"
                   .format(added_part))
            status, output = session.cmd_status_output(cmd)
            session.close()
            if status:
                test.fail("Disk operation in VM failed:%s" % output)
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as err:
            test.fail("Error happens when check disk in vm: %s" % err)

    def get_new_mpath_devs(old_mpath_devs):
        """
        Return function that determines new multipath devices

        :param old_mpath_devs: known old multipath devices
        :return: Function returning new multipath devices
        """

        def has_new_mpath_devs():
            """
            Determine new multipath devices

            :return: List of new multipath devices
            """
            cur_mpath_devs = mpath.find_mpath_devs()
            return list(set(cur_mpath_devs).difference(
                set(old_mpath_devs)))

        return has_new_mpath_devs

    storage_size = params.get("storage_size", "1G")
    hotplug_disk = "yes" == params.get("hotplug_disk", "no")
    status_error = "yes" == params.get("status_error")
    # Start VM and get all partions in VM.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        # Setup backend storage
        mpath_conf_exist = prepare_multipath_conf()
        mpath.restart_multipathd()
        old_mpath_devs = mpath.find_mpath_devs()
        libvirt.setup_or_cleanup_iscsi(is_setup=True)
        mpath.restart_multipathd()
        new_mpath_devs = utils_misc.wait_for(
            get_new_mpath_devs(old_mpath_devs), WAIT_FOR_MPATH_DEVS_TIMEOUT, 0.5)
        if not new_mpath_devs:
            test.fail("No newly added multipath devices found.")
        logging.debug("newly added mpath devs are: %s", new_mpath_devs)

        # Add debug information for states of multipathed_path and multipathed_device
        multipathed_path = process.run('multipath -v2', ignore_status=True, shell=True, verbose=True).stdout_text.strip()
        logging.debug("dumped multipathed_path: %s", multipathed_path)
        multipathed_device = process.run('multipath -ll', ignore_status=True, shell=True, verbose=True).stdout_text.strip()
        logging.debug("dumped multipathed_device: %s", multipathed_device)

        # Prepare disk xml
        disk_params = {}
        disk_params['type_name'] = params.get("virt_disk_type", "block")
        disk_params['source_file'] = '/dev/mapper/' + new_mpath_devs[0]
        disk_params['device_type'] = params.get("virt_disk_device", "lun")
        disk_params['sgio'] = params.get("sgio", "filtered")
        disk_params['rawio'] = params.get("rawio", "no")
        disk_params['target_dev'] = params.get("virt_disk_device_target", "sdb")
        disk_params['target_bus'] = params.get("virt_disk_device_bus", "scsi")
        disk_params['driver_name'] = params.get("virt_disk_drive_name", "qemu")
        disk_params['driver_type'] = params.get("virt_disk_device_format", "raw")
        disk_xml = libvirt.create_disk_xml(disk_params)
        # Test disk operation with newly added disk xml
        attach_option = ""
        if not hotplug_disk:
            attach_option = "--config"
        result = virsh.attach_device(vm_name, disk_xml, flagstr=attach_option,
                                     ignore_status=True, debug=True)
        libvirt.check_exit_status(result, status_error)
        if not hotplug_disk:
            vm.destroy(gracefully=False)
            vm.start()
            vm.wait_for_login().close()
        check_in_vm(vm, old_parts)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # Clean up backend storage
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        recover_multipath_conf(not mpath_conf_exist)
        mpath.restart_multipathd()
