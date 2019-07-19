import re
import logging
import time

from virttest.libvirt_xml.devices.tpm import Tpm
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml import LibvirtXMLError
from virttest import virsh
from virttest import utils_package
from virttest import utils_misc
from virttest import utils_libguestfs

from provider import libvirt_version
from avocado.utils import service
from avocado.utils import process
from avocado.utils import path as utils_path


def run(test, params, env):
    """
    Test the tpm virtual devices
    1. prepare a guest with different tpm devices
    2. check whether the guest can be started
    3. check the xml and qemu cmd line
    4. check tpm usage in guest os
    """
    # Tpm passthrough supported since libvirt 1.0.5.
    if not libvirt_version.version_compare(1, 0, 5):
        test.cancel("Tpm device is not supported "
                    "on current libvirt version.")
    # Tpm passthrough supported since qemu 2.12.0-49.
    if not utils_misc.compare_qemu_version(2, 9, 0, is_rhev=False):
        test.cancel("Tpm device is not supported "
                    "on current qemu version.")

    status_error = ("yes" == params.get("status_error", "no"))
    tpm_model = params.get("tpm_model")
    backend_type = params.get("backend_type")
    backend_version = params.get("backend_version")
    device_path = params.get("device_path")
    tpm_num = int(params.get("tpm_num", 1))
    multi_vms = ("yes" == params.get("multi_vms", "no"))

    # Check tpm chip on host for passthrough testing
    if backend_type == "passthrough":
        dmesg_info = process.getoutput("dmesg|grep tpm -wi", shell=True)
        logging.debug("dmesg info about tpm:\n %s", dmesg_info)
        dmesg_error = re.search("No TPM chip found|TPM is disabled", dmesg_info)
        if dmesg_error:
            test.cancel(dmesg_error.group())
        else:
            # Try to check host tpm chip version
            tpm_v = None
            if re.search("2.0 TPM", dmesg_info):
                tpm_v = "2.0"
                if not utils_package.package_install("tpm2-tools"):
                    # package_install() return 'True' if succeed
                    test.error("Failed to install tpm2-tools on host")
            else:
                if re.search("1.2 TPM", dmesg_info):
                    tpm_v = "1.2"
                # If "1.2 TPM" or no version info in dmesg, try to test a tpm1.2 at first
                if not utils_package.package_install("tpm-tools"):
                    test.error("Failed to install tpm-tools on host")

    vm_names = params.get("vms").split()
    vm_name = vm_names[0]
    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    if vm.is_alive():
        vm.destroy()

    vm2 = None
    if multi_vms:
        if len(vm_names) > 1:
            vm2_name = vm_names[1]
            vm2 = env.get_vm(vm2_name)
            vm2_xml = VMXML.new_from_inactive_dumpxml(vm2_name)
            vm2_xml_backup = vm2_xml.copy()
        else:
            # Clone additional vms if needed
            try:
                utils_path.find_command("virt-clone")
            except utils_path.CmdNotFoundError:
                if not utils_package.package_install(["virt-install"]):
                    test.cancel("Failed to install virt-install on host")
            vm2_name = "vm2_" + utils_misc.generate_random_string(5)
            ret_clone = utils_libguestfs.virt_clone_cmd(vm_name, vm2_name,
                                                        True, timeout=360, debug=True)
            if ret_clone.exit_status:
                test.error("Need more than one domains, but error occured when virt-clone.")
            vm2 = vm.clone(vm2_name)
            vm2_xml = VMXML.new_from_inactive_dumpxml(vm2_name)
        if vm2.is_alive():
            vm2.destroy()

    service_mgr = service.ServiceManager()

    def check_dumpxml():
        """
        Check whether the added devices are shown in the guest xml
        """
        logging.info("------Checking guest dumpxml------")
        if tpm_model:
            pattern = '<tpm model="%s">' % tpm_model
        else:
            # The default tpm model is "tpm-tis"
            pattern = '<tpm model="tpm-tis">'
        # Check tpm model
        xml_after_adding_device = VMXML.new_from_dumpxml(vm_name)
        logging.debug("xml after add tpm dev is %s", xml_after_adding_device)
        if pattern not in str(xml_after_adding_device):
            test.fail("Can not find the %s tpm device xml "
                      "in the guest xml file." % tpm_model)
        # Check backend type
        pattern = '<backend type="%s"' % backend_type
        if pattern not in str(xml_after_adding_device):
            test.fail("Can not find the %s backend type xml for tpm dev "
                      "in the guest xml file." % backend_type)
        # Check backend version
        if backend_version:
            pattern = "\'emulator\' version=\"%s\"" % backend_version
            if pattern not in str(xml_after_adding_device):
                test.fail("Can not find the %s backend version xml for tpm dev "
                          "in the guest xml file." % backend_version)
        # Check device path
        if backend_type == "passthrough":
            pattern = '<device path="/dev/tpm0"'
            if pattern not in str(xml_after_adding_device):
                test.fail("Can not find the %s device path xml for tpm dev "
                          "in the guest xml file." % device_path)
        logging.info('------PASS on guest dumpxml check------')

    def check_qemu_cmd_line():
        """
        Check whether the added devices are shown in the qemu cmd line
        """
        logging.info("------Checking qemu cmd line------")
        if not vm.get_pid():
            test.fail('VM pid file missing.')
        with open('/proc/%s/cmdline' % vm.get_pid()) as cmdline_file:
            cmdline = cmdline_file.read()
            logging.debug("Qemu cmd line info:\n %s", cmdline)
        # Check tpm model
        pattern = "-device.%s" % tpm_model
        if not re.search(pattern, cmdline):
            test.fail("Can not find the %s tpm device "
                      "in qemu cmd line." % tpm_model)
        # Check backend type
        if backend_type == "passthrough":
            pattern_list = ["-tpmdev.passthrough"]
            dev_num = re.search(r"\d+", device_path).group()
            pattern_list.append("id=tpm-tpm%s" % dev_num)
            for pattern in pattern_list:
                if not re.search(pattern, cmdline):
                    test.fail("Can not find the %s tpm device "
                              "in qemu cmd line." % pattern)
        logging.info("------PASS on qemu cmd line check------")

    def get_host_tpm_bef(tpm_v):
        """
        Test host tpm function and identify its real version before passthrough
        Since sometimes dmesg info doesn't include tpm msg, need use tpm-tool or
        tpm2-tools to try the function.

        :param tpm_v: host tpm version get from dmesg info
        :return: host tpm version
        """
        logging.info("------Checking host tpm device before passthrough------")
        # Try tcsd tool for suspected tpm1.2 chip on host
        if tpm_v != "2.0":
            if not service_mgr.start('tcsd'):
                # service_mgr.start() return 'True' if succeed
                if tpm_v == "1.2":
                    test.fail("Host tcsd.serivce start failed")
                else:
                    # Means tpm_v got nothing from dmesg, log failure here and
                    # go to 'elif' to try tpm2.0 tools.
                    logging.info("Host tcsd.serivce start failed")
            else:
                tpm_real_v = "1.2"
                logging.info("Host tpm version info:")
                result = process.run("tpm_version", ignore_status=False)
                logging.debug("[host]# tpm_version\n %s", result.stdout)
                time.sleep(2)
                service_mgr.stop('tcsd')
        elif tpm_v != "1.2":
            # Try tpm2.0 tools
            if not utils_package.package_install("tpm2-tools"):
                test.error("Failed to install tpm2-tools on host")
            if process.run("tpm2_getrandom 5", ignore_status=True).exit_status:
                test.cancel("Both tcsd and tpm2-tools can not work, "
                            "pls check your host tpm version and test env.")
            else:
                tpm_real_v = "2.0"
        logging.info("------PASS on host tpm device check------")
        return tpm_real_v

    def test_host_tpm_aft(tpm_real_v):
        """
        Test host tpm function after passthrough

        :param tpm_real_v: host tpm real version indentified from testing
        """
        logging.info("------Checking host tpm device after passthrough------")
        if tpm_real_v == "1.2":
            if service_mgr.start('tcsd'):
                time.sleep(2)
                service_mgr.stop('tcsd')
                test.fail("Host tpm should not work after passthrough to guest.")
            else:
                logging.info("Expected failure: Tpm is being used by guest.")
        elif tpm_real_v == "2.0":
            if not process.run("tpm2_getrandom 7", ignore_status=True).exit_status:
                test.fail("Host tpm should not work after passthrough to guest.")
            else:
                logging.info("Expected failure: Tpm is being used by guest.")
        logging.info("------PASS on host tpm device check------")

    def test_guest_tpm(expect_version, session, expect_fail):
        """
        Test tpm function in guest

        :param expect_version: guest tpm version, as host version, or emulator specified
        :param session: Guest session to be tested
        :param expect_fail: guest tpm is expectedly fail to work
        """
        logging.info("------Checking guest tpm device work------")
        if expect_version == "1.2":
            # Install tpm-tools and test by tcsd method
            if not utils_package.package_install(["tpm-tools"], session, 360):
                test.error("Failed to install tpm-tools package in guest")
            else:
                status, output = session.cmd_status_output("systemctl start tcsd")
                logging.debug(output)
                if status:
                    if expect_fail:
                        test.cancel("tpm-crb passthrough only works with host tpm2.0, "
                                    "but your host tpm version is 1.2")
                    else:
                        test.fail("Failed to start tcsd.service in guest")
                else:
                    status, output = session.cmd_status_output("tpm_version")
                    logging.debug(output)
                    if status:
                        test.fail("Guest tpm can not work")
        else:
            # If expect_version is tpm2.0, install and test by tpm2-tools
            if not utils_package.package_install(["tpm2-tools"], session, 360):
                test.error("Failed to install tpm2-tools package in guest")
            else:
                status, output = session.cmd_status_output("tpm2_getrandom 11")
                logging.debug(output)
                if status:
                    test.fail("Guest tpm can not work")
        logging.info("------PASS on guest tpm device work check------")

    def reuse_by_vm2(tpm_dev):
        """
        Try to passthrough tpm to a second guest, when it's being used by one guest.

        :param tpm_dev: tpm device to be added into guest xml
        """
        logging.info("------Trying to passthrough tpm to a second domain------")
        vm2_xml.remove_all_device_by_type('tpm')
        vm2_xml.add_device(tpm_dev)
        vm2_xml.sync()
        ret = virsh.start(vm2_name, ignore_status=True, debug=True)
        if ret:
            logging.info("Expected failure when try to passthrough a tpm"
                         " that being used by another guest")
            return
        else:
            test.fail("Reuse a passthroughed tpm should not succeed.")

    try:
        tpm_real_v = None
        if backend_type == "passthrough":
            tpm_real_v = get_host_tpm_bef(tpm_v)
            logging.debug("The host tpm real version is %s", tpm_real_v)
        vm_xml.remove_all_device_by_type('tpm')
        tpm_dev = Tpm()
        if tpm_model:
            tpm_dev.tpm_model = tpm_model
        backend = tpm_dev.Backend()
        backend.backend_type = backend_type
        if device_path:
            backend.device_path = device_path
        tpm_dev.backend = backend
        logging.debug("tpm dev xml to add is %s", tpm_dev)
        for num in range(tpm_num):
            vm_xml.add_device(tpm_dev, True)
        try:
            vm_xml.sync()
        except LibvirtXMLError as e:
            if tpm_num > 1 and backend_type == "passthrough":
                logging.info("Expected failure when define a guest with multi tpm passthrough"
                             " configured in xml.")
                # Stop test when get expected failure
                return
            else:
                test.fail("Test failed in vmxml.sync(), detail:%s." % e)
        if tpm_num > 1 and backend_type == "passthrough":
            test.fail("Passthrough multi tpm should not succeed.")
        check_dumpxml()
        # For default model, no need start guest to test
        if tpm_model:
            expect_fail = False
            virsh.start(vm_name, ignore_status=False, debug=True)
            check_qemu_cmd_line()
            session = vm.wait_for_login()
            if backend_type == "passthrough":
                if tpm_real_v == "1.2" and tpm_model == "tpm-crb":
                    expect_fail = True
                test_guest_tpm(tpm_real_v, session, expect_fail)
                if multi_vms:
                    reuse_by_vm2(tpm_dev)
                    # Stop test when get expected failure
                    return
                test_host_tpm_aft(tpm_real_v)
            else:
                # emulator backend
                test_guest_tpm(backend_version, session, expect_fail)
            session.close()

    finally:
        vm_xml_backup.sync()
        if vm2:
            if len(vm_names) > 1:
                vm2_xml_backup.sync()
            else:
                virsh.remove_domain(vm2_name, "--remove-all-storage", debug=True)
