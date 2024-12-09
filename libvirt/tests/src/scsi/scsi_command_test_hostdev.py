import ast
import re

from avocado.utils import process

from virttest import iscsi
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import hostdev
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    SCSI command passthrough test for hostdev scsi device
    """
    def create_second_target_by_iscsi(emulated_image, second_target):
        """
        This test needs to have two LUNs with same image but not same target name.
        The current libvirt.setup_or_cleanup_iscsi() can't cover it.

        :params emulated_image: an image used for iscsi target LUN
        :params second_target: the second target name
        """
        scsi_disk_nums = process.run(get_scsi_cmd, shell=True).stdout_text
        # Create another target with the same emulated_image
        target_cmd = "targetcli /iscsi/ create %s;" % second_target
        lun_cmd = (" targetcli /iscsi/%s/tpg1/luns/ create /backstores/fileio/device.%s;"
                   % (second_target, emulated_image))
        attributes_cmd = (" targetcli /iscsi/%s/tpg1/ set attribute"
                          " authentication=0"
                          " demo_mode_write_protect=0"
                          " generate_node_acls=1"
                          " cache_dynamic_acls=1") % second_target
        process.run(target_cmd + lun_cmd + attributes_cmd, shell=True, ignore_status=False)
        # Login the second target
        iscsi.iscsi_discover(target_portal)
        iscsi.iscsi_login(second_target, target_portal)
        iscsi_status = process.run(get_scsi_cmd, shell=True).stdout_text
        if int(iscsi_status) != (int(scsi_disk_nums) + 1):
            test.fail("Prepare the second target fail!")

    def prepare_guest_with_hostdev_devices(vmxml, emulated_image):
        """
        Prepare 2 scsi disks and hostdev devices xml.

        :params vmxml: the guest xml
        :params emulated_image: an image used for iscsi target LUN
        """
        for i in range(2):
            if i == 0:
                libvirt.setup_or_cleanup_iscsi(is_setup=True, is_login=True,
                                               emulated_image=emulated_image)
            else:
                create_second_target_by_iscsi(emulated_image, second_target)
            cmd = "lsscsi | grep LIO | awk '{print $1}' | awk -F '[:[]' '{print $2}' | tail -n 1"
            scsi_host_num = process.run(cmd, shell=True, ignore_status=False).stdout_text.split()[0]
            hostdev_dict = eval(params.get("hostdev_dict", "{}") % scsi_host_num)
            hostdev_xml = hostdev.Hostdev()
            hostdev_xml.setup_attrs(**hostdev_dict)
            libvirt.add_vm_device(vmxml, hostdev_xml)
            vmxml.sync()
            test.log.debug("The current guest xml is: %s", vmxml)

    def setup_multipath_service(vm_session):
        """
        Setup multipath service in guest.

        :params vm_session: the vm session
        :params return: return the mpath disk name in guest
        """
        mpath_conf_path = params.get("mpath_conf_path", "/etc/multipath.conf ")
        pkg_list = ast.literal_eval(params.get("pkg_list"))
        if not utils_package.package_install(pkg_list, vm_session):
            test.fail("Failed to install %s package on guest!" % pkg_list)
        vm_session.cmd("touch %s" % mpath_conf_path)
        conf_in_guest = (
                         "cat <<EOF > %s\n"
                         "defaults {\n"
                         "    user_friendly_names yes\n"
                         "    find_multipaths yes\n"
                         "    no_path_retry fail\n"
                         "    enable_foreign '^$'\n"
                         "    reservation_key file\n"
                         "}\n"
                         "EOF"
                        ) % mpath_conf_path
        vm_session.cmd(conf_in_guest)
        vm_session.cmd("systemctl restart multipathd")
        # Check the multipath device status
        status, output = vm_session.cmd_status_output("multipath -ll")
        if status or ("active" not in output):
            test.fail("Setup multipath service failed!")
        mpath_dev = re.search(r"mpath[a-z]", output).group(0)
        test.log.debug("The multipath disk in guest is %s.", mpath_dev)
        return mpath_dev

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    emulated_image = params.get("emulated_image")
    second_target = params.get("second_target")
    target_portal = params.get("target_portal")
    script_path = params.get("script_path")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    get_scsi_cmd = "lsscsi | grep LIO | wc -l"

    try:
        test.log.info("TEST_STEP1: start the guest with two hostdev devices.")
        prepare_guest_with_hostdev_devices(vmxml, emulated_image)
        vm.start()
        test.log.info("TEST_STEP2: check the scsi disks in guest.")
        vm_session = vm.wait_for_serial_login()
        status, output = vm_session.cmd_status_output(get_scsi_cmd)
        if status or int(output) != 2:
            test.fail("Can't get the expected scsi disks in guest!")
        test.log.info("TEST_STEP3: setup multipath service in guest.")
        mpath_dev = setup_multipath_service(vm_session)

        test.log.info("TEST_STEP4: send the scsi commands to scsi disk.")
        script_in_guest = (
                           "cat <<EOF > {0}\n"
                           "#!/bin/bash\n"
                           "mpathpersist --out --register-ignore --param-sark 123aaa /dev/mapper/{1}\n"
                           "mpathpersist --out --reserve --param-rk 123aaa --prout-type 5 /dev/mapper/{1}\n"
                           "mpathpersist --in -k /dev/mapper/{1}\n"
                           "mpathpersist --in -r /dev/mapper/{1}\n"
                           "mpathpersist --out --release --param-rk 123aaa --prout-type 5 /dev/mapper/{1}\n"
                           "mpathpersist --out --register --param-rk 123aaa --prout-type 5 /dev/mapper/{1}\n"
                           "EOF"
                           ).format(script_path, mpath_dev)
        vm_session.cmd(script_in_guest)
        run_script_in_guest = "/bin/bash %s" % script_path
        _, output = vm_session.cmd_status_output(run_script_in_guest)
        if "failed" in output:
            test.fail("Send scsi commands to scsi disk failed with message %s!" % output)
        if "123aaa" not in output:
            test.fail("Register reservation keys failed with message %s!" % output)
        else:
            test.log.debug("Can send scsi commands, the results are: %s", output)
    finally:
        if vm.is_alive():
            vm.destroy()
        backup_xml.sync()
        # Delete the first iscsi target
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        # Delete the second iscsi target
        if second_target in process.run("targetcli ls").stdout_text:
            iscsi.iscsi_logout(second_target)
            process.run("targetcli /iscsi/ delete %s" % second_target, shell=True)
