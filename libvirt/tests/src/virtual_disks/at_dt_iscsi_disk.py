import os
import re
import time
import base64
import logging
import platform
import locale

from aexpect import ShellError

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest.remote import LoginError
from virttest.virt_vm import VMError
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml.secret_xml import SecretXML
from virttest.libvirt_xml.devices.controller import Controller
from virttest.staging import lv_utils

from virttest import libvirt_version


def clean_up_lvm(device_source, vg_name, lv_name):
    """
    Clean up lvm.

    :param device_source: source file
    :param vg_name: volume group name
    :param lv_name: logical volume name
    """
    libvirt.delete_local_disk("lvm", vgname=vg_name, lvname=lv_name)
    lv_utils.vg_remove(vg_name)
    process.system("pvremove %s" % device_source, ignore_status=True, shell=True)
    process.system("rm -rf /dev/%s" % vg_name, ignore_status=True, shell=True)


def run(test, params, env):
    """
    Attach/Detach an iscsi network/volume disk to domain

    1. For secret usage testing:
        1.1. Setup an iscsi target with CHAP authentication.
        1.2. Define a secret for iscsi target usage
        1.3. Set secret value
    2. Create
    4. Create an iscsi network disk XML
    5. Attach disk with the XML file and check the disk inside the VM
    6. Detach the disk
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    disk_device = params.get("disk_device", "disk")
    disk_type = params.get("disk_type", "network")
    disk_src_protocol = params.get("disk_source_protocol", "iscsi")
    disk_src_host = params.get("disk_source_host", "127.0.0.1")
    disk_src_port = params.get("disk_source_port", "3260")
    disk_src_pool = params.get("disk_source_pool")
    disk_src_mode = params.get("disk_source_mode", "host")
    pool_type = params.get("pool_type", "iscsi")
    pool_src_host = params.get("pool_source_host", "127.0.0.1")
    pool_target = params.get("pool_target", "/dev/disk/by-path")
    disk_target = params.get("disk_target", "vdb")
    disk_target_bus = params.get("disk_target_bus", "virtio")
    disk_readonly = params.get("disk_readonly", "no")
    chap_auth = "yes" == params.get("chap_auth", "no")
    chap_user = params.get("chap_username", "")
    chap_passwd = params.get("chap_password", "")
    secret_usage_target = params.get("secret_usage_target")
    secret_ephemeral = params.get("secret_ephemeral", "no")
    secret_private = params.get("secret_private", "yes")
    status_error = "yes" == params.get("status_error", "no")
    vg_name = params.get("virt_disk_vg_name", "vg_test_0")
    lv_name = params.get("virt_disk_lv_name", "lv_test_0")
    driver_packed = params.get("driver_packed", "on")
    disk_packed = "yes" == params.get("disk_packed", "no")
    scsi_packed = "yes" == params.get("scsi_packed", "no")

    # Indicate the PPC platform
    on_ppc = False
    if platform.platform().count('ppc64'):
        on_ppc = True

    if disk_src_protocol == 'iscsi':
        if not libvirt_version.version_compare(1, 0, 4):
            test.cancel("'iscsi' disk doesn't support in"
                        " current libvirt version.")
    if disk_type == "volume":
        if not libvirt_version.version_compare(1, 0, 5):
            test.cancel("'volume' type disk doesn't support in"
                        " current libvirt version.")
    if pool_type == "iscsi-direct":
        if not libvirt_version.version_compare(4, 7, 0):
            test.cancel("iscsi-direct pool is not supported in"
                        " current libvirt version.")
    if ((disk_packed or scsi_packed) and not libvirt_version.version_compare(6, 3, 0)):
        test.cancel("The virtio packed attribute is not supported in"
                    " current libvirt version.")
    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Fix no more PCI slots issue in certain cases.
    vm_dump_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    machine_type = params.get("machine_type", "pc")
    if machine_type == 'q35':
        vm_dump_xml.remove_all_device_by_type('controller')
        machine_list = vm_dump_xml.os.machine.split("-")
        vm_dump_xml.set_os_attrs(**{"machine": machine_list[0] + "-q35-" + machine_list[2]})
        q35_pcie_dict0 = {'controller_model': 'pcie-root', 'controller_type': 'pci', 'controller_index': 0}
        q35_pcie_dict1 = {'controller_model': 'pcie-root-port', 'controller_type': 'pci'}
        vm_dump_xml.add_device(libvirt.create_controller_xml(q35_pcie_dict0))
        # Add enough controllers to match multiple times disk attaching requirements
        for i in list(range(1, 12)):
            q35_pcie_dict1.update({'controller_index': "%d" % i})
            vm_dump_xml.add_device(libvirt.create_controller_xml(q35_pcie_dict1))
        vm_dump_xml.sync()

    virsh_dargs = {'debug': True, 'ignore_status': True}
    try:
        start_vm = "yes" == params.get("start_vm", "yes")
        if start_vm:
            if vm.is_dead():
                vm.start()
            vm.wait_for_login()
        else:
            if not vm.is_dead():
                vm.destroy()

        if chap_auth:
            # Create a secret xml to define it
            secret_xml = SecretXML(secret_ephemeral, secret_private)
            secret_xml.auth_type = "chap"
            secret_xml.auth_username = chap_user
            secret_xml.usage = disk_src_protocol
            secret_xml.target = secret_usage_target
            with open(secret_xml.xml) as f:
                logging.debug("Define secret by XML: %s", f.read())
            # Define secret
            cmd_result = virsh.secret_define(secret_xml.xml, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
            # Get secret uuid
            try:
                secret_uuid = cmd_result.stdout.strip().split()[1]
            except IndexError:
                test.error("Fail to get new created secret uuid")

            # Set secret value
            encoding = locale.getpreferredencoding()
            secret_string = base64.b64encode(chap_passwd.encode(encoding)).decode(encoding)
            cmd_result = virsh.secret_set_value(secret_uuid, secret_string,
                                                **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
        else:
            # Set chap_user and chap_passwd to empty to avoid setup
            # CHAP authentication when export iscsi target
            chap_user = ""
            chap_passwd = ""

        # Setup iscsi target
        if disk_type == "block":
            iscsi_target = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                          is_login=True,
                                                          image_size="1G",
                                                          chap_user=chap_user,
                                                          chap_passwd=chap_passwd,
                                                          portal_ip=disk_src_host)
        else:
            iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                                   is_login=False,
                                                                   image_size='1G',
                                                                   chap_user=chap_user,
                                                                   chap_passwd=chap_passwd,
                                                                   portal_ip=disk_src_host)
        # Create iscsi pool
        if disk_type == "volume":
            # Create an iscsi pool xml to create it
            pool_src_xml = pool_xml.SourceXML()
            pool_src_xml.host_name = pool_src_host
            pool_src_xml.device_path = iscsi_target
            poolxml = pool_xml.PoolXML(pool_type=pool_type)
            poolxml.name = disk_src_pool
            poolxml.set_source(pool_src_xml)
            poolxml.target_path = pool_target
            if chap_auth:
                pool_src_xml.auth_type = "chap"
                pool_src_xml.auth_username = chap_user
                pool_src_xml.secret_usage = secret_usage_target
                poolxml.set_source(pool_src_xml)
            if pool_type == "iscsi-direct":
                iscsi_initiator = params.get('iscsi_initiator')
                pool_src_xml.iqn_name = iscsi_initiator
                poolxml.set_source(pool_src_xml)
            # Create iscsi/iscsi-direct pool
            cmd_result = virsh.pool_create(poolxml.xml, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
            xml = virsh.pool_dumpxml(disk_src_pool)
            logging.debug("Pool '%s' XML:\n%s", disk_src_pool, xml)

            def get_vol():
                """Get the volume info"""
                # Refresh the pool
                cmd_result = virsh.pool_refresh(disk_src_pool)
                libvirt.check_exit_status(cmd_result)
                # Get volume name
                cmd_result = virsh.vol_list(disk_src_pool, **virsh_dargs)
                libvirt.check_exit_status(cmd_result)
                vol_list = []
                vol_list = re.findall(r"(\S+)\ +(\S+)",
                                      str(cmd_result.stdout.strip()))
                if len(vol_list) > 1:
                    return vol_list[1]
                else:
                    return None

            # Wait for a while so that we can get the volume info
            vol_info = utils_misc.wait_for(get_vol, 10)
            if vol_info:
                vol_name, vol_path = vol_info
            else:
                test.error("Failed to get volume info")
            # Snapshot doesn't support raw disk format, create a qcow2 volume
            # disk for snapshot operation.
            if pool_type == "iscsi":
                process.run('qemu-img create -f qcow2 %s %s' % (vol_path, '100M'),
                            shell=True, verbose=True)
            else:
                # Get iscsi URL to create a qcow2 volume disk
                disk_path = ("iscsi://[%s]/%s/%s" % (disk_src_host,
                             iscsi_target, lun_num))
                blk_source = "/mnt/test.qcow2"
                process.run('qemu-img create -f qcow2 %s %s' % (blk_source,
                            '100M'), shell=True, verbose=True)
                process.run('qemu-img convert -O qcow2 %s %s' % (blk_source,
                            disk_path), shell=True, verbose=True)

        # Create block device
        if disk_type == "block":
            logging.debug("iscsi dev name: %s", iscsi_target)
            lv_utils.vg_create(vg_name, iscsi_target)
            device_source = libvirt.create_local_disk("lvm",
                                                      size="10M",
                                                      vgname=vg_name,
                                                      lvname=lv_name)
            logging.debug("New created volume: %s", lv_name)

        # Create iscsi network disk XML
        disk_params = {'device_type': disk_device,
                       'type_name': disk_type,
                       'target_dev': disk_target,
                       'target_bus': disk_target_bus,
                       'readonly': disk_readonly}
        disk_params_src = {}
        if disk_type == "network":
            disk_params_src = {'source_protocol': disk_src_protocol,
                               'source_name': iscsi_target + "/%s" % lun_num,
                               'source_host_name': disk_src_host,
                               'source_host_port': disk_src_port}
        elif disk_type == "volume":
            if pool_type == "iscsi":
                disk_params_src = {'source_pool': disk_src_pool,
                                   'source_volume': vol_name,
                                   'driver_type': 'qcow2',
                                   'source_mode': disk_src_mode}
            # iscsi-direct pool don't include source_mode option
            else:
                disk_params_src = {'source_pool': disk_src_pool,
                                   'source_volume': vol_name,
                                   'driver_type': 'qcow2'}
        elif disk_type == "block":
            disk_params_src = {'source_file': device_source,
                               'driver_type': 'raw'}
            # Start guest with packed attribute in disk
            if disk_packed:
                disk_params_src['driver_packed'] = driver_packed
            # Start guest with packed attribute in scsi controller
            if scsi_packed:
                scsi_controller = Controller("controller")
                scsi_controller.type = "scsi"
                scsi_controller.model = "virtio-scsi"
                scsi_controller.driver = {'packed': driver_packed}
                vm_dump_xml.add_device(scsi_controller)
                vm_dump_xml.sync()
        else:
            test.cancel("Unsupport disk type in this test")
        disk_params.update(disk_params_src)
        if chap_auth and disk_type != "volume":
            disk_params_auth = {'auth_user': chap_user,
                                'secret_type': disk_src_protocol,
                                'secret_usage': secret_xml.target}
            disk_params.update(disk_params_auth)
        disk_xml = libvirt.create_disk_xml(disk_params)
        attach_option = params.get("attach_option", "")
        cmd_result = virsh.attach_device(domainarg=vm_name, filearg=disk_xml,
                                         flagstr=attach_option,
                                         dargs=virsh_dargs)
        libvirt.check_exit_status(cmd_result, status_error)

        if vm.is_dead():
            cmd_result = virsh.start(vm_name, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

        # Wait for domain is stable
        vm.wait_for_login().close()
        domain_operation = params.get("domain_operation", "")
        if domain_operation == "save":
            save_file = os.path.join(data_dir.get_tmp_dir(), "vm.save")
            cmd_result = virsh.save(vm_name, save_file, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
            cmd_result = virsh.restore(save_file)
            libvirt.check_exit_status(cmd_result)
            if os.path.exists(save_file):
                os.remove(save_file)
        elif domain_operation == "snapshot":
            # Run snapshot related commands: snapshot-create-as, snapshot-list
            # snapshot-info, snapshot-dumpxml, snapshot-create
            # virsh snapshot-revert is not supported on combined internal and external snapshots
            # see more details from,https://bugzilla.redhat.com/show_bug.cgi?id=1733173
            snapshot_name1 = "snap1"
            snapshot_name2 = "snap2"
            cmd_result = virsh.snapshot_create_as(vm_name, snapshot_name1,
                                                  **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            try:
                virsh.snapshot_list(vm_name, **virsh_dargs)
            except process.CmdError:
                test.fail("Failed getting snapshots list for %s" % vm_name)

            try:
                virsh.snapshot_info(vm_name, snapshot_name1, **virsh_dargs)
            except process.CmdError:
                test.fail("Failed getting snapshots info for %s" % vm_name)

            cmd_result = virsh.snapshot_dumpxml(vm_name, snapshot_name1,
                                                **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            cmd_result = virsh.snapshot_create(vm_name, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            cmd_result = virsh.snapshot_current(vm_name, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            virsh.snapshot_create_as(vm_name, snapshot_name2,
                                     ignore_status=False, debug=True)

            cmd_result = virsh.snapshot_revert(vm_name, snapshot_name1,
                                               **virsh_dargs)

            cmd_result = virsh.snapshot_list(vm_name, **virsh_dargs)
            if snapshot_name2 not in cmd_result:
                test.error("Snapshot %s not found" % snapshot_name2)
        elif domain_operation == "start_with_packed":
            expect_xml_line = "packed=\"%s\"" % driver_packed
            libvirt.check_dumpxml(vm, expect_xml_line)
            expect_qemu_line = "packed=%s" % driver_packed
            libvirt.check_qemu_cmd_line(expect_qemu_line)
        elif domain_operation == "":
            logging.debug("No domain operation provided, so skip it")
        else:
            logging.error("Unsupport operation %s in this case, so skip it",
                          domain_operation)

        def find_attach_disk(expect=True):
            """
            Find attached disk inside the VM
            """
            found_disk = False
            if vm.is_dead():
                test.error("Domain %s is not running" % vm_name)
            else:
                try:
                    session = vm.wait_for_login()
                    # Here the script needs wait for a while for the guest to
                    # recognize the hotplugged disk on PPC
                    if on_ppc:
                        time.sleep(10)
                    cmd = "grep %s /proc/partitions" % disk_target
                    s, o = session.cmd_status_output(cmd)
                    logging.info("%s output: %s", cmd, o)
                    session.close()
                    if s == 0:
                        found_disk = True
                except (LoginError, VMError, ShellError) as e:
                    logging.error(str(e))
            if found_disk == expect:
                logging.debug("Check disk inside the VM PASS as expected")
            else:
                test.error("Check disk inside the VM FAIL")

        # Check disk inside the VM, expect is False if status_error=True
        find_attach_disk(not status_error)

        # Detach disk
        cmd_result = virsh.detach_disk(vm_name, disk_target, wait_remove_event=True)
        libvirt.check_exit_status(cmd_result, status_error)

        # Check disk inside the VM
        find_attach_disk(False)

    finally:
        # Clean up snapshot
        # Shut down before cleaning up snapshots
        if vm.is_alive():
            vm.destroy()
        libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
        # Restore vm
        vmxml_backup.sync("--snapshots-metadata")
        # Destroy pool and undefine secret, which may not exist
        try:
            if disk_type == "volume":
                virsh.pool_destroy(disk_src_pool)
            if disk_type == "block":
                clean_up_lvm(iscsi_target, vg_name, lv_name)
            if chap_auth:
                virsh.secret_undefine(secret_uuid)
        except Exception:
            pass
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
