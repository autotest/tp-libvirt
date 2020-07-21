import os
import re
import logging
import shutil

import aexpect

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_disk
from virttest import utils_misc
from virttest import virt_vm, remote
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml import pool_xml
from virttest.xml_utils import XMLTreeFile

from virttest import libvirt_version


def run(test, params, env):
    """
    Test startupPolicy for CD-ROM/floppy/Volume disks.

    Steps:
    1. Prepare disk media image.
    2. Setup startupPolicy for a disk.
    3. Start the domain.
    4. Save the domain.
    5. Remove the disk source file and restore the domain.
    6. Update startupPolicy for  a disk.
    7. Destroy the domain.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    startup_policy = params.get("policy")

    def create_iscsi_pool():
        """
        Setup iSCSI target,and create one iSCSI pool.
        """
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                               is_login=False,
                                                               image_size='1G',
                                                               chap_user="",
                                                               chap_passwd="",
                                                               portal_ip=disk_src_host)
        # Define an iSCSI pool xml to create it
        pool_src_xml = pool_xml.SourceXML()
        pool_src_xml.host_name = pool_src_host
        pool_src_xml.device_path = iscsi_target
        poolxml = pool_xml.PoolXML(pool_type=pool_type)
        poolxml.name = pool_name
        poolxml.set_source(pool_src_xml)
        poolxml.target_path = "/dev/disk/by-path"

        # Create iSCSI pool.
        virsh.pool_destroy(pool_name, **virsh_dargs)
        cmd_result = virsh.pool_create(poolxml.xml, **virsh_dargs)
        libvirt.check_exit_status(cmd_result)

    def create_volume(pvt, created_vol_name=None):
        """
        Create iSCSI volume.

        :param pvt: PoolVolumeTest object
        :param created_vol_name: Created volume name
        """
        try:
            if pool_type == "iscsi":
                create_iscsi_pool()
            else:
                pvt.pre_pool(pool_name, pool_type, pool_target, emulated_image)
                pvt.pre_vol(vol_name=created_vol_name, vol_format=vol_format,
                            capacity=capacity, allocation=None,
                            pool_name=pool_name)
        except Exception as pool_exception:
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **virsh_dargs)
            test.error("Error occurred when prepare" +
                       "pool xml with message %s:\n" % str(pool_exception))

        def get_vol():
            """Get the volume info"""
            # Refresh the pool
            cmd_result = virsh.pool_refresh(pool_name)
            libvirt.check_exit_status(cmd_result)
            # Get volume name
            cmd_result = virsh.vol_list(pool_name, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
            vol_list = []
            vol_list = re.findall(r"(\S+)\ +(\S+)",
                                  str(cmd_result.stdout.strip()))
            try:
                return vol_list[1]
            except IndexError:
                return None
        # Wait for a while so that we can get the volume info
        vol_info = utils_misc.wait_for(get_vol, 10)
        if vol_info:
            tmp_vol_name, tmp_vol_path = vol_info
        else:
            test.error("Failed to get volume info")
        process.run('qemu-img create -f qcow2 %s %s' % (tmp_vol_path, '100M'),
                    shell=True)
        return vol_info

    def check_disk_source(vm_name, target_dev, expect_value):
        """
        Check the disk source: file and startupPolicy.

        :param vm_name: Domain name
        :param target_dev: Disk's target device
        :param expect_value: Expect value of source file and source startupPolicy
        """
        logging.debug("Expect source file is '%s'", expect_value[0])
        logging.debug("Expect source startupPolicy is '%s'", expect_value[1])
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks = vmxml.get_disk_all()
        source_value = []
        try:
            disk_source = disks[target_dev].find('source')
            source_value.append(disk_source.get('file'))
            source_value.append(disk_source.get('startupPolicy'))
        except KeyError:
            test.error("No %s in domain %s" % (target_dev, vm_name))
        logging.debug("Actual source file is '%s'", source_value[0])
        logging.debug("Actual source startupPolicy is '%s'", source_value[1])
        if source_value == expect_value:
            logging.debug("Domain disk XML check pass")
        else:
            test.error("Domain disk XML check fail")

    def create_disk_xml():
        """
        Create a disk xml file for attaching to a domain.
        """
        if disk_type == "file":
            process.run("qemu-img create %s %s" % (media_file, image_size), shell=True)
        disk_params = {'device_type': device_type,
                       'type_name': disk_type,
                       'target_dev': target_dev,
                       'target_bus': target_bus}
        if disk_type == "file":
            disk_params_src = {'source_protocol': "file",
                               'source_file': media_file,
                               'source_startupPolicy': startup_policy}
        elif disk_type == "volume":
            disk_params_src = {'source_pool': pool_name,
                               'source_volume': vol_name,
                               'driver_type': 'qcow2',
                               'source_startupPolicy': startup_policy}
            if pool_type == "iscsi":
                disk_params_src.update({'source_mode': "host"})
        disk_params.update(disk_params_src)
        disk_xml = libvirt.create_disk_xml(disk_params)
        shutil.copyfile(disk_xml, disk_xml_file)
        return disk_xml

    def check_in_vm(old_parts):
        """
        Check mount/read/write disk in VM.

        :param old_parts: pre-operated partitions in VM.
        :return: True if check successfully.
        """
        try:
            session = vm.wait_for_login()
            new_parts = utils_disk.get_parts_list(session)
            logging.debug("new parted:%s", new_parts)
            added_parts = list(set(new_parts).difference(set(old_parts)))
            logging.info("Added parts:%s", added_parts)
            if len(added_parts) != 1:
                logging.error("The number of new partitions is invalid in VM")
                return False
            added_part = added_parts[0]
            if not added_part:
                logging.error("Can't see added partition in VM")
                return False
            if 'sr' not in added_part and 'fd' not in added_part:
                cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                       "mkdir -p test && mount /dev/{0} test && echo"
                       " teststring > test/testfile && umount test"
                       .format(added_part))
                status, output = session.cmd_status_output(cmd)
                logging.info("Check disk operation in VM:\n%s", output)
                if status != 0:
                    return False
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def check_policy_update(origin_policy, policy_list, xml_policy_file, device_type, flag_str):
        """
        Check updated policy after executing virsh update-device.

        :param origin_policy: the inherit startup policy value.
        :param policy_list: updated policy list.
        :param xml_policy_file: xml file for startupPolicy.
        :param device_type: device type,cdrom or disk.,etc
        :param flag_str: it can be --config,--live and --persistent.
        """
        for policy in policy_list:
            xmltreefile = XMLTreeFile(xml_policy_file)
            try:
                policy_item = xmltreefile.find('/source')
                policy_item.set('startupPolicy', policy)
            except AttributeError as elem_attr:
                test.error("Fail to find startupPolicy attribute.%s", str(elem_attr))
            xmltreefile.write(xml_policy_file, encoding="UTF-8")
            ret = virsh.update_device(vm_name, xml_policy_file, flagstr=flag_str, debug=True)
            if all([device_type == "disk", policy == "requisite"]):
                libvirt.check_exit_status(ret, True)
                return
            else:
                libvirt.check_exit_status(ret)

            def check_policy_value(active_policy, inactive_policy):
                """
                Check policy value in dumpxml with active or inactive option

                :param active_policy: active policy attribute value
                :param inactive_policy: inactive policy attribute value
                """
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                disk_list = vmxml.devices.by_device_tag("disk")
                disk = disk_list[len(disk_list)-1]
                if not active_policy == disk.source.attrs["startupPolicy"]:
                    test.error("Actual policy:%s in active state is not equal to expected:%s"
                               % (active_policy, disk.source.attrs["startupPolicy"]))
                vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                disk_list = vmxml.devices.by_device_tag("disk")
                disk = disk_list[len(disk_list)-1]
                if not inactive_policy == disk.source.attrs["startupPolicy"]:
                    test.error("Actual policy:%s in inactive state is not equal to expected: %s"
                               % (inactive_policy, disk.source.attrs["startupPolicy"]))
            if flag_str == "--live":
                check_policy_value(policy, origin_policy)
            elif flag_str == "--config":
                check_policy_value(origin_policy, policy)
            elif flag_str == "--persistent":
                check_policy_value(policy, policy)

    def check_source_update(xml_policy_file):
        """
        Update source and policy at the same time,then check those changes.

        :param xml_policy_file: VM xml policy file
        """
        xmltreefile = XMLTreeFile(xml_policy_file)
        policy_item = xmltreefile.find('/source')

        def configure_startup_policy(update=False, policy='optional'):
            """
            Configure startupPolicy attribute value.

            :param update: update value or not
            :param policy: policy value
            :return: flag_option and boolean value
            """
            if update:
                del policy_item.attrib["startupPolicy"]
            else:
                policy_item.set("startupPolicy", policy)
            flag_option = "--live"
            xmltreefile.write(xml_policy_file, encoding="UTF-8")
            return flag_option, False

        # Update source and startUpPolicy attribute value.
        def update_source_policy(update=True, policy='optional'):
            """
            Update startupPolicy source value.

            :param update: update value or not
            :param policy: policy value
            :return: flag_option and boolean value
            """
            source_file = policy_item.get('file')
            if update:
                new_source_file = source_file+".empty"
            else:
                new_source_file = source_file+".new"
            shutil.copyfile(source_file, new_source_file)
            policy_item.set("file", new_source_file)
            policy_item.set("startupPolicy", policy)
            flag_option = "--persistent"
            xmltreefile.write(xml_policy_file, encoding="UTF-8")
            return flag_option, False

        function_list = [configure_startup_policy, update_source_policy,
                         configure_startup_policy, update_source_policy]
        function_parameter = [False, False, True, True]
        # Loop all above scenarios to update device.
        for index in list(range(len(function_list))):
            try:
                func = function_list[index]
                para = function_parameter[index]
                flag_option, update_error = func(para)
                ret = virsh.update_device(vm_name, xml_policy_file, flagstr=flag_option, debug=True)
                libvirt.check_exit_status(ret, expect_error=update_error)
            except AttributeError as elem_attr:
                test.error("Fail to remove startupPolicy attribute:%s" % str(elem_attr))
            except Exception as update_device_exception:
                test.error("Fail to update device:%s" % str(update_device_exception))
            finally:
                source_file = policy_item.get('file')
                new_source_file = source_file+".new"
                if os.path.exists(new_source_file):
                    os.remove(new_source_file)

    def rename_file(source_file, target_file, revert=False):
        """
        Rename a file or revert it.

        :param source_file: The source file name.
        :param target_file: The target file name.
        :param revert: It can be True or False.
        """
        try:
            if not revert:
                os.rename(source_file, target_file)
                logging.debug("Rename %s to %s", source_file, target_file)
            else:
                os.rename(target_file, source_file)
                logging.debug("Rename %s to %s", target_file, source_file)
        except OSError as err:
            test.fail("Rename image failed: %s" % str(err))

    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Start VM and get all partitions in VM.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Get start,restore configuration parameters.
    start_error = "yes" == params.get("start_error", "no")
    restore_error = "yes" == params.get("restore_error", "no")
    virsh_dargs = {'debug': True, 'ignore_status': True}
    attach_option = params.get("attach_option")

    # Create disk xml and attach it.
    device_type = params.get("device_type")
    disk_type = params.get("disk_type", "network")
    disk_src_host = params.get("disk_source_host", "127.0.0.1")
    target_dev = params.get("target_dev")
    target_bus = params.get("disk_target_bus", "virtio")
    image_size = params.get("image_size", "1.44M")
    emulated_image = "emulated-iscsi"

    # Storage pool and volume related paramters.
    pool_name = params.get("pool_name", "iscsi_pool")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target", "/dev/disk/by-path")
    pool_src_host = params.get("pool_source_host", "127.0.0.1")
    vol_name = params.get("volume_name")
    capacity = params.get("volume_size", "1048576")
    vol_format = params.get("volume_format")

    # Source file parameters.
    media_name = params.get("media_name")
    media_file = os.path.join(data_dir.get_tmp_dir(), media_name)
    media_file_new = media_file + ".new"
    save_file = os.path.join(data_dir.get_tmp_dir(), "vm.save")
    snapshot_name = "s1"

    # Policy related paramters.
    disk_xml_file = os.path.join(data_dir.get_tmp_dir(), "attach_disk.xml")
    disk_xml_policy_file = os.path.join(data_dir.get_tmp_dir(), "attach_policy_disk.xml")
    update_policy = "yes" == params.get("update_policy", "no")
    policy_only = "yes" == params.get("policy_only", "no")
    update_policy_list = params.get("update_policy_list").split()
    expect_value = [None, startup_policy]

    try:
        if disk_type == "volume":
            pvt = libvirt.PoolVolumeTest(test, params)
            vol_name, vol_path = create_volume(pvt, vol_name)
            vol_path_new = vol_path + ".new"

        # Create disk xml.
        create_disk_xml()
        if vm.is_alive():
            vm.destroy()
        try:
            # Backup disk xml file for policy update if update_policy=True.
            if update_policy:
                shutil.copyfile(disk_xml_file, disk_xml_policy_file)
            result = virsh.attach_device(domainarg=vm_name, filearg=disk_xml_file,
                                         flagstr="--config", **virsh_dargs)
            # For iSCSI pool volume,startupPolicy attribute is not valid for it.
            # Moreover,setting disk 'requisite' is allowed only for cdrom or floppy.
            if pool_type == "iscsi" or all([device_type == "disk", startup_policy == "requisite"]):
                libvirt.check_exit_status(result, expect_error=True)
                return
            else:
                libvirt.check_exit_status(result, expect_error=False)
        except Exception as attach_device_exception:
            logging.debug("Attach device throws exception:%s", str(attach_device_exception))
            os.remove(media_file)
            test.error("Attach %s fail" % device_type)
        # Check update policy operations.
        if disk_type == "file" and update_policy:
            vm.start()
            if policy_only:
                check_policy_update(startup_policy, update_policy_list,
                                    disk_xml_policy_file, device_type, attach_option)
            else:
                check_source_update(disk_xml_policy_file)
        elif disk_type == "file":
            # Step 1. Start domain and destroy it normally
            vm.start()
            vm.destroy()

            # Step 2. Remove the source_file then start the domain
            rename_file(media_file, media_file_new)
            result = virsh.start(vm_name, **virsh_dargs)
            libvirt.check_exit_status(result, expect_error=start_error)

            # For libvirt version >=2.0.0, feature is updated and startup policy attribute
            # can not exist alone without source protocol.
            if not start_error and not libvirt_version.version_compare(2, 0, 0):
                check_disk_source(vm_name, target_dev, expect_value)

            # Step 3. Move back the source file and start the domain(if needed).
            rename_file(media_file, media_file_new, revert=True)
            if not vm.is_alive():
                vm.start()

            # Step 4. Save the domain normally, then remove the source file
            # and restore it back
            vm.save_to_file(save_file)
            rename_file(media_file, media_file_new)
            result = virsh.restore(save_file, **virsh_dargs)
            libvirt.check_exit_status(result, expect_error=restore_error)
            if not restore_error and not libvirt_version.version_compare(2, 0, 0):
                check_disk_source(vm_name, target_dev, expect_value)

            # Step 5. Move back the source file and restore the domain(if needed)
            rename_file(media_file, media_file_new, revert=True)
            if not vm.is_alive():
                result = virsh.restore(save_file, **virsh_dargs)
                libvirt.check_exit_status(result, expect_error=False)
        elif disk_type == "volume":
            # Step 1. Start domain and destroy it normally.
            vm.start()
            # Step 1 Start VM successfully.
            if not check_in_vm(old_parts):
                test.fail("Check disk partitions in VM failed")

            # Step 2 Destroy VM, move the volume to other place, refresh the pool, then start the guest.
            vm.destroy()
            rename_file(vol_path, vol_path_new)
            cmd_result = virsh.pool_refresh(pool_name)
            libvirt.check_exit_status(cmd_result)
            result = virsh.start(vm_name, **virsh_dargs)
            libvirt.check_exit_status(result, expect_error=start_error)

            # Step 3 Move back the source file and start.
            rename_file(vol_path, vol_path_new, revert=True)
            cmd_result = virsh.pool_refresh(pool_name)
            libvirt.check_exit_status(cmd_result)
            if not vm.is_alive():
                vm.start()

            # Step 4 Save the domain normally, then remove the source file,then restore domain.
            vm.save_to_file(save_file)
            rename_file(vol_path, vol_path_new)
            cmd_result = virsh.pool_refresh(pool_name)
            libvirt.check_exit_status(cmd_result)
            result = virsh.restore(save_file, **virsh_dargs)
            libvirt.check_exit_status(result, expect_error=restore_error)

            # Step 5, Create snapshot,move the source to other place,then revert snapshot.
            if device_type == "disk":
                rename_file(vol_path, vol_path_new, revert=True)
                cmd_result = virsh.pool_refresh(pool_name)
                libvirt.check_exit_status(cmd_result)
                if restore_error:
                    result = virsh.restore(save_file, **virsh_dargs)
                    libvirt.check_exit_status(result)
                ret = virsh.snapshot_create_as(vm_name, snapshot_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                rename_file(vol_path, vol_path_new)
                ret = virsh.snapshot_revert(vm_name, snapshot_name, **virsh_dargs)
                # Clean up snapshot.
                libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()

        if disk_type == "volume":
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **virsh_dargs)
        if os.path.exists(save_file):
            os.remove(save_file)
        if os.path.exists(disk_xml_file):
            os.remove(disk_xml_file)
        if os.path.exists(media_file):
            os.remove(media_file)
        if os.path.exists(disk_xml_policy_file):
            os.remove(disk_xml_policy_file)
