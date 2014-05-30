import os
import logging
from autotest.client.shared import error
from virttest import libvirt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_libvirtd
from virttest import aexpect
from virttest import libvirt_storage
from virttest.libvirt_xml import vm_xml
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

    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)
    vm_id = vm.get_id()
    vm_uuid = vm.get_uuid()

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
    try:
        save_file = "/var/lib/libvirt/qemu/save/%s.save" % vm_name
        if option.count("managedsave") and vm.is_alive():
            virsh.managedsave(vm_name)

        snp_list = virsh.snapshot_list(vm_name)
        if option.count("snapshot"):
            snp_file_list = []
            if not len(snp_list):
                virsh.snapshot_create(vm_name)
                logging.debug("Create a snapshot for test!")
            else:
                # Backup snapshots for domain
                for snp_item in snp_list:
                    tmp_file = os.path.join(test.tmpdir, snp_item+".xml")
                    virsh.snapshot_dumpxml(vm_name, snp_item, to_file=tmp_file)
                    snp_file_list.append(tmp_file)
        else:
            if len(snp_list):
                raise error.TestNAError("This domain has snapshot(s), "
                                        "cannot be undefined!")
        if option.count("remove-all-storage"):
            pvtest = utlv.PoolVolumeTest(test, params)
            pvtest.pre_pool(pool_name, pool_type, pool_target, emulated_img,
                            emulated_size)
            new_pool = libvirt_storage.PoolVolume(pool_name)
            if not new_pool.create_volume(vol_name, volume_size):
                raise error.TestFail("Create volume %s failed." % vol_name)
            volumes = new_pool.list_volumes()
            volume = volumes[vol_name]
            virsh.attach_disk(vm_name, volume, disk_target, "--config")

        # Turn libvirtd into certain state.
        if libvirtd_state == "off":
            utils_libvirtd.libvirtd_stop()

        # Test virsh undefine command.
        output = ""
        if vm_ref != "remote":
            vm_ref = "%s %s" % (vm_ref, extra)
            cmdresult = virsh.undefine(vm_ref, option,
                                       ignore_status=True, debug=True)
            status = cmdresult.exit_status
            output = cmdresult.stdout.strip()
            if status:
                logging.debug("Error status, command output: %s",
                              cmdresult.stderr.strip())
            if undefine_twice:
                status2 = virsh.undefine(vm_ref,
                                         ignore_status=True).exit_status
        else:
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                raise error.TestNAError("remote_ip and/or local_ip parameters"
                                        " not changed from default values")
            try:
                uri = libvirt_vm.complete_uri(local_ip)
                session = remote.remote_login("ssh", remote_ip, "22",
                                              remote_user, remote_pwd,
                                              remote_prompt)
                cmd_undefine = "virsh -c %s undefine %s" % (uri, vm_name)
                status, output = session.cmd_status_output(cmd_undefine)
                logging.info("Undefine output: %s", output)
            except (error.CmdError, remote.LoginError, aexpect.ShellError), de:
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
            except error.CmdError, detail:
                logging.error("Detail: %s", detail)

        # Check if VM exists.
        vm_exist = virsh.domain_exists(vm_name)

        # Check if xml file exists.
        xml_exist = False
        if os.path.exists("/etc/libvirt/qemu/%s.xml" % vm_name) or\
           os.path.exists("/etc/xen/%s" % vm_name):
            xml_exist = True

        # Check if save file exists if use --managed-save
        save_exist = False
        if os.path.exists(save_file):
            save_exist = True

        # Check if save file exists if use --managed-save
        volume_exist = False
        if volume and os.path.exists(volume):
            volume_exist = True

    finally:
        # Recover main VM.
        backup_xml.sync()

        # Clean up pool
        if pvtest:
            pvtest.cleanup_pool(pool_name, pool_type,
                                pool_target, emulated_img)
        # Recover VM snapshots.
        if option.count("snapshot"):
            logging.debug("Recover snapshots for domain!")
            for file_item in snp_file_list:
                virsh.snapshot_create(vm_name, file_item)

    # Check results.
    if status_error:
        if not status:
            raise error.TestFail("virsh undefine return unexpected result.")
    else:
        if status:
            raise error.TestFail("virsh undefine failed.")
        if undefine_twice:
            if not status2:
                raise error.TestFail("Undefine the same VM twice succeeded.")
        if vm_exist:
            raise error.TestFail("VM still exists after undefine.")
        if xml_exist:
            raise error.TestFail("Xml file still exists after undefine.")
        if option.count("managedsave") and save_exist:
            raise error.TestFail("Save file still exists after undefine.")
        if option.count("remove-all-storage") and volume_exist:
            raise error.TestFail("Volume file '%s' still exists after"
                                 " undefine." % volume)
        if wipe_data and option.count("remove-all-storage"):
            if not output.count("Wiping volume '%s'" % disk_target):
                raise error.TestFail("Command didn't wipe volume storage!")
