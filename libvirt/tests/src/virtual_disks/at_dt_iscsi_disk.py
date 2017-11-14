import os
import re
import time
import base64
import logging
import platform

from aexpect import ShellError

from autotest.client.shared import error

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest.remote import LoginError
from virttest.virt_vm import VMError
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml.secret_xml import SecretXML

from provider import libvirt_version


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
    # Indicate the PPC platform
    on_ppc = False
    if platform.platform().count('ppc64'):
        on_ppc = True

    if disk_src_protocol == 'iscsi':
        if not libvirt_version.version_compare(1, 0, 4):
            raise error.TestNAError("'iscsi' disk doesn't support in"
                                    " current libvirt version.")
    if disk_type == "volume":
        if not libvirt_version.version_compare(1, 0, 5):
            raise error.TestNAError("'volume' type disk doesn't support in"
                                    " current libvirt version.")
    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

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
            logging.debug("Define secret by XML: %s", open(secret_xml.xml).read())
            # Define secret
            cmd_result = virsh.secret_define(secret_xml.xml, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
            # Get secret uuid
            try:
                secret_uuid = cmd_result.stdout.strip().split()[1]
            except IndexError:
                raise error.TestError("Fail to get new created secret uuid")

            # Set secret value
            secret_string = base64.b64encode(chap_passwd)
            cmd_result = virsh.secret_set_value(secret_uuid, secret_string,
                                                **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
        else:
            # Set chap_user and chap_passwd to empty to avoid setup
            # CHAP authentication when export iscsi target
            chap_user = ""
            chap_passwd = ""

        # Setup iscsi target
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
            poolxml.target_path = "/dev/disk/by-path"
            # Create iscsi pool
            cmd_result = virsh.pool_create(poolxml.xml, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            def get_vol():
                """Get the volume info"""
                # Refresh the pool
                cmd_result = virsh.pool_refresh(disk_src_pool)
                libvirt.check_exit_status(cmd_result)
                # Get volume name
                cmd_result = virsh.vol_list(disk_src_pool, **virsh_dargs)
                libvirt.check_exit_status(cmd_result)
                vol_list = []
                vol_list = re.findall(r"(\S+)\ +(\S+)[\ +\n]",
                                      str(cmd_result.stdout))
                if len(vol_list) > 1:
                    return vol_list[1]
                else:
                    return None

            # Wait for a while so that we can get the volume info
            vol_info = utils_misc.wait_for(get_vol, 10)
            if vol_info:
                vol_name, vol_path = vol_info
            else:
                raise error.TestError("Failed to get volume info")
            # Snapshot doesn't support raw disk format, create a qcow2 volume
            # disk for snapshot operation.
            process.run('qemu-img create -f qcow2 %s %s' % (vol_path, '100M'),
                        shell=True)

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
            disk_params_src = {'source_pool': disk_src_pool,
                               'source_volume': vol_name,
                               'driver_type': 'qcow2',
                               'source_mode': disk_src_mode}
        else:
            error.TestNAError("Unsupport disk type in this test")
        disk_params.update(disk_params_src)
        if chap_auth:
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
            save_file = os.path.join(test.tmpdir, "vm.save")
            cmd_result = virsh.save(vm_name, save_file, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)
            cmd_result = virsh.restore(save_file)
            libvirt.check_exit_status(cmd_result)
            if os.path.exists(save_file):
                os.remove(save_file)
        elif domain_operation == "snapshot":
            # Run snapshot related commands: snapshot-create-as, snapshot-list
            # snapshot-info, snapshot-dumpxml, snapshot-create
            snapshot_name1 = "snap1"
            snapshot_name2 = "snap2"
            cmd_result = virsh.snapshot_create_as(vm_name, snapshot_name1,
                                                  **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            try:
                virsh.snapshot_list(vm_name, **virsh_dargs)
            except process.CmdError:
                error.TestFail("Failed getting snapshots list for %s" % vm_name)

            try:
                virsh.snapshot_info(vm_name, snapshot_name1, **virsh_dargs)
            except process.CmdError:
                error.TestFail("Failed getting snapshots info for %s" % vm_name)

            cmd_result = virsh.snapshot_dumpxml(vm_name, snapshot_name1,
                                                **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            cmd_result = virsh.snapshot_create(vm_name, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            cmd_result = virsh.snapshot_current(vm_name, **virsh_dargs)
            libvirt.check_exit_status(cmd_result)

            snapshot_file = os.path.join(test.tmpdir, snapshot_name2)
            sn_create_op = ("%s --disk-only --diskspec %s,file=%s"
                            % (snapshot_name2, disk_target, snapshot_file))
            cmd_result = virsh.snapshot_create_as(vm_name, sn_create_op,
                                                  **virsh_dargs)

            libvirt.check_exit_status(cmd_result)
            cmd_result = virsh.snapshot_revert(vm_name, snapshot_name1,
                                               **virsh_dargs)

            cmd_result = virsh.snapshot_list(vm_name, **virsh_dargs)
            if snapshot_name2 not in cmd_result:
                raise error.TestError("Snapshot %s not found" % snapshot_name2)
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
                raise error.TestError("Domain %s is not running" % vm_name)
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
                except (LoginError, VMError, ShellError), e:
                    logging.error(str(e))
            if found_disk == expect:
                logging.debug("Check disk inside the VM PASS as expected")
            else:
                raise error.TestError("Check disk inside the VM FAIL")

        # Check disk inside the VM, expect is False if status_error=True
        find_attach_disk(not status_error)

        # Detach disk
        cmd_result = virsh.detach_disk(vm_name, disk_target)
        libvirt.check_exit_status(cmd_result, status_error)

        # Check disk inside the VM
        find_attach_disk(False)

    finally:
        # Clean up snapshot
        libvirt.clean_up_snapshots(vm_name, domxml=vmxml_backup)
        # Restore vm
        if vm.is_alive():
            vm.destroy()
        vmxml_backup.sync("--snapshots-metadata")
        # Destroy pool and undefine secret, which may not exist
        try:
            if disk_type == "volume":
                virsh.pool_destroy(disk_src_pool)
            if chap_auth:
                virsh.secret_undefine(secret_uuid)
        except:
            pass
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
