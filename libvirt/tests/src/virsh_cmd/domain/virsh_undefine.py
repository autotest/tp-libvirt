import os
import logging
import shutil
import time
import platform

import aexpect

from avocado.utils import process

from virttest import libvirt_vm
from virttest import data_dir
from virttest import virsh
from virttest import remote
from virttest import utils_libvirtd
from virttest import libvirt_storage
from virttest import ssh_key
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.xcepts import LibvirtXMLError
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test virsh undefine command.

    Undefine an inactive domain, or convert persistent to transient.
    1.Prepare test environment.
    2.Backup the VM's information to a xml file.
    3.When the libvirtd == "off", stop the libvirtd service.
    4.Perform virsh undefine operation.
    5.Recover test environment.(libvirts service,VM)
    6.Confirm the test result.
    """

    vm_ref = params.get("undefine_vm_ref", "vm_name")
    extra = params.get("undefine_extra", "")
    option = params.get("undefine_option", "")
    libvirtd_state = params.get("libvirtd", "on")
    status_error = ("yes" == params.get("status_error", "no"))
    undefine_twice = ("yes" == params.get("undefine_twice", 'no'))
    local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
    local_pwd = params.get("local_pwd", "password")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_user = params.get("remote_user", "user")
    remote_pwd = params.get("remote_pwd", "password")
    remote_prompt = params.get("remote_prompt", "#")
    pool_type = params.get("pool_type")
    pool_name = params.get("pool_name", "test")
    pool_target = params.get("pool_target")
    volume_size = params.get("volume_size", "1G")
    vol_name = params.get("vol_name", "test_vol")
    emulated_img = params.get("emulated_img", "emulated_img")
    emulated_size = "%sG" % (int(volume_size[:-1]) + 1)
    disk_target = params.get("disk_target", "vdb")
    wipe_data = "yes" == params.get("wipe_data", "no")
    if wipe_data:
        option += " --wipe-storage"
    nvram_o = None
    if platform.machine() == 'aarch64':
        nvram_o = " --nvram"
        option += nvram_o

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vm_id = vm.get_id()
    vm_uuid = vm.get_uuid()

    # polkit acl related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    # Back up xml file.Xen host has no guest xml file to define a guset.
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Confirm how to reference a VM.
    if vm_ref == "vm_name":
        vm_ref = vm_name
    elif vm_ref == "id":
        vm_ref = vm_id
    elif vm_ref == "hex_vm_id":
        vm_ref = hex(int(vm_id))
    elif vm_ref == "uuid":
        vm_ref = vm_uuid
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)

    volume = None
    pvtest = None
    status3 = None

    elems = backup_xml.xmltreefile.findall('/devices/disk/source')
    existing_images = [elem.get('file') for elem in elems]

    # Backup images since remove-all-storage could remove existing libvirt
    # managed guest images
    if existing_images and option.count("remove-all-storage"):
        for img in existing_images:
            backup_img = img + '.bak'
            logging.info('Backup %s to %s', img, backup_img)
            shutil.copyfile(img, backup_img)

    try:
        save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name
        if option.count("managedsave") and vm.is_alive():
            virsh.managedsave(vm_name)

        if not vm.is_lxc():
            snp_list = virsh.snapshot_list(vm_name)
            if option.count("snapshot"):
                snp_file_list = []
                if not len(snp_list):
                    virsh.snapshot_create(vm_name)
                    logging.debug("Create a snapshot for test!")
                else:
                    # Backup snapshots for domain
                    for snp_item in snp_list:
                        tmp_file = os.path.join(data_dir.get_tmp_dir(), snp_item + ".xml")
                        virsh.snapshot_dumpxml(vm_name, snp_item, to_file=tmp_file)
                        snp_file_list.append(tmp_file)
            else:
                if len(snp_list):
                    test.cancel("This domain has snapshot(s), "
                                "cannot be undefined!")
        if option.count("remove-all-storage"):
            pvtest = utlv.PoolVolumeTest(test, params)
            pvtest.pre_pool(pool_name, pool_type, pool_target, emulated_img,
                            emulated_size=emulated_size)
            new_pool = libvirt_storage.PoolVolume(pool_name)
            if not new_pool.create_volume(vol_name, volume_size):
                test.fail("Creation of volume %s failed." % vol_name)
            volumes = new_pool.list_volumes()
            volume = volumes[vol_name]
            ret = virsh.attach_disk(vm_name, volume, disk_target, "--config",
                                    debug=True)
            if ret.exit_status != 0:
                test.error("Attach disk failed: %s" % ret.stderr)

        # Turn libvirtd into certain state.
        if libvirtd_state == "off":
            utils_libvirtd.libvirtd_stop()

        # Test virsh undefine command.
        output = ""
        if vm_ref != "remote":
            vm_ref = "%s %s" % (vm_ref, extra)
            cmdresult = virsh.undefine(vm_ref, option,
                                       unprivileged_user=unprivileged_user,
                                       uri=uri,
                                       ignore_status=True, debug=True)
            status = cmdresult.exit_status
            output = cmdresult.stdout.strip()
            if status:
                logging.debug("Error status, command output: %s",
                              cmdresult.stderr.strip())
            if undefine_twice:
                status2 = virsh.undefine(vm_ref, nvram_o,
                                         ignore_status=True).exit_status
        else:
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                test.cancel("remote_ip and/or local_ip parameters"
                            " not changed from default values")
            try:
                local_user = params.get("username", "root")
                uri = libvirt_vm.complete_uri(local_ip)
                # setup ssh auto login from remote machine to test machine
                # for the command to execute remotely
                ssh_key.setup_remote_ssh_key(remote_ip, remote_user,
                                             remote_pwd, hostname2=local_ip,
                                             user2=local_user,
                                             password2=local_pwd)
                session = remote.remote_login("ssh", remote_ip, "22",
                                              remote_user, remote_pwd,
                                              remote_prompt)
                cmd_undefine = "virsh -c %s undefine %s" % (uri, vm_name)
                status, output = session.cmd_status_output(cmd_undefine)
                logging.info("Undefine output: %s", output)
            except (process.CmdError, remote.LoginError, aexpect.ShellError) as de:
                logging.error("Detail: %s", de)
                status = 1

        # Recover libvirtd state.
        if libvirtd_state == "off":
            utils_libvirtd.libvirtd_start()

        # Shutdown VM.
        if virsh.domain_exists(vm.name):
            try:
                if vm.is_alive():
                    vm.destroy(gracefully=False)
            except process.CmdError as detail:
                logging.error("Detail: %s", detail)

        # After vm.destroy, virsh.domain_exists returns True due to
        # timing issue and tests fails.
        time.sleep(2)
        # Check if VM exists.
        vm_exist = virsh.domain_exists(vm_name)

        # Check if xml file exists.
        xml_exist = False
        if vm.is_qemu() and os.path.exists("/etc/libvirt/qemu/%s.xml" % vm_name):
            xml_exist = True
        if vm.is_lxc() and os.path.exists("/etc/libvirt/lxc/%s.xml" % vm_name):
            xml_exist = True
        if vm.is_xen() and os.path.exists("/etc/xen/%s" % vm_name):
            xml_exist = True

        # Check if save file exists if use --managed-save
        save_exist = os.path.exists(save_file)

        # Check if save file exists if use --managed-save
        volume_exist = volume and os.path.exists(volume)

        # Test define with acl control and recover domain.
        if params.get('setup_libvirt_polkit') == 'yes':
            if virsh.domain_exists(vm.name):
                virsh.undefine(vm_ref, nvram_o, ignore_status=True)
            cmd = "chmod 666 %s" % backup_xml.xml
            process.run(cmd, ignore_status=False, shell=True)
            s_define = virsh.define(backup_xml.xml,
                                    unprivileged_user=unprivileged_user,
                                    uri=uri, ignore_status=True, debug=True)
            status3 = s_define.exit_status

    finally:
        # Recover main VM.
        try:
            backup_xml.sync()
        except LibvirtXMLError:
            # sync() tries to undefines and define the xml to sync
            # but virsh_undefine test would have undefined already
            # may lead to error out
            backup_xml.define()

        # Recover existing guest images
        if existing_images and option.count("remove-all-storage"):
            for img in existing_images:
                backup_img = img + '.bak'
                logging.info('Recover image %s to %s', backup_img, img)
                shutil.move(backup_img, img)

        # Clean up pool
        if pvtest:
            pvtest.cleanup_pool(pool_name, pool_type,
                                pool_target, emulated_img)
        # Recover VM snapshots.
        if option.count("snapshot") and (not vm.is_lxc()):
            logging.debug("Recover snapshots for domain!")
            for file_item in snp_file_list:
                virsh.snapshot_create(vm_name, file_item)

    # Check results.
    if status_error:
        if not status:
            if libvirtd_state == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                             "and command should succeed")
            else:
                test.fail("virsh undefine return unexpected result.")
        if params.get('setup_libvirt_polkit') == 'yes':
            if status3 == 0:
                test.fail("virsh define with false acl permission" +
                          " should failed.")
    else:
        if status:
            test.fail("virsh undefine failed.")
        if undefine_twice:
            if not status2:
                test.fail("Undefine the same VM twice succeeded.")
        if vm_exist:
            test.fail("VM still exists after undefine.")
        if xml_exist:
            test.fail("Xml file still exists after undefine.")
        if option.count("managedsave") and save_exist:
            test.fail("Save file still exists after undefine.")
        if option.count("remove-all-storage") and volume_exist:
            test.fail("Volume file '%s' still exists after"
                      " undefine." % volume)
        if wipe_data and option.count("remove-all-storage"):
            if not output.count("Wiping volume '%s'" % disk_target):
                test.fail("Command didn't wipe volume storage!")
        if params.get('setup_libvirt_polkit') == 'yes':
            if status3:
                test.fail("virsh define with right acl permission" +
                          " should succeeded")
