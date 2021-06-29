import os
import re
import logging
import time
import platform
import shutil

from virttest import data_dir
from virttest import libvirt_version
from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_package
from virttest import utils_misc
from virttest import utils_libguestfs
from virttest import utils_libvirtd
from virttest import libvirt_version

from virttest.libvirt_xml.devices.tpm import Tpm
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_test import libvirt
from virttest.virt_vm import VMStartError

from avocado.utils import service
from avocado.utils import process
from avocado.utils import astring
from avocado.utils import path as utils_path


def run(test, params, env):
    """
    Test the tpm virtual devices
    1. prepare a guest with different tpm devices
    2. check whether the guest can be started
    3. check the xml and qemu cmd line, even swtpm for vtpm
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

    tpm_model = params.get("tpm_model")
    backend_type = params.get("backend_type")
    backend_version = params.get("backend_version")
    device_path = params.get("device_path")
    tpm_num = int(params.get("tpm_num", 1))
    # After first start of vm with vtpm, do operations, check it still works
    vm_operate = params.get("vm_operate")
    # Sub-operation(e.g.domrename) under vm_operate(e.g.restart)
    vm_oprt = params.get("vm_oprt")
    secret_uuid = params.get("secret_uuid")
    secret_value = params.get("secret_value")
    # Change encryption state: from plain to encrypted, or reverse.
    encrypt_change = params.get("encrypt_change")
    secret_uuid = params.get("secret_uuid")
    prepare_secret = ("yes" == params.get("prepare_secret", "no"))
    remove_dev = ("yes" == params.get("remove_dev", "no"))
    multi_vms = ("yes" == params.get("multi_vms", "no"))
    # Remove swtpm state file
    rm_statefile = ("yes" == params.get("rm_statefile", "no"))
    test_suite = ("yes" == params.get("test_suite", "no"))
    restart_libvirtd = ("yes" == params.get("restart_libvirtd", "no"))
    no_backend = ("yes" == params.get("no_backend", "no"))
    status_error = ("yes" == params.get("status_error", "no"))
    err_msg = params.get("xml_errmsg", "")
    loader = params.get("loader", "")
    nvram = params.get("nvram", "")
    uefi_disk_url = params.get("uefi_disk_url", "")
    download_file_path = os.path.join(data_dir.get_data_dir(), "uefi_disk.qcow2")
    persistent_state = ("yes" == params.get("persistent_state", "no"))

    libvirt_version.is_libvirt_feature_supported(params)

    # Tpm emulator tpm-tis_model for aarch64 supported since libvirt 7.1.0
    if platform.machine() == 'aarch64' and tpm_model == 'tpm-tis' \
        and backend_type == 'emulator' \
            and not libvirt_version.version_compare(7, 1, 0):

        test.cancel("Tpm emulator tpm-tis_model for aarch64 "
                    "is not supported on current libvirt")

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
                    if tpm_v == "1.2":
                        test.error("Failed to install tpm-tools on host")
                    else:
                        logging.debug("Failed to install tpm-tools on host")
    # Check host env for vtpm testing
    elif backend_type == "emulator":
        if not utils_misc.compare_qemu_version(4, 0, 0, is_rhev=False):
            test.cancel("vtpm(emulator backend) is not supported "
                        "on current qemu version.")
        # Install swtpm pkgs on host for vtpm emulation
        if not utils_package.package_install(["swtpm", "swtpm-tools"]):
            test.error("Failed to install swtpm swtpm-tools on host")

    def replace_os_disk(vm_xml, vm_name, nvram):
        """
        Replace os(nvram) and disk(uefi) for x86 vtpm test

        :param vm_xml: current vm's xml
        :param vm_name: current vm name
        :param nvram: nvram file path of vm
        """
        # Add loader, nvram in <os>
        nvram = nvram.replace("<VM_NAME>", vm_name)
        dict_os_attrs = {"loader_readonly": "yes",
                         "secure": "yes",
                         "loader_type": "pflash",
                         "loader": loader,
                         "nvram": nvram}
        vm_xml.set_os_attrs(**dict_os_attrs)
        logging.debug("Set smm=on in VMFeaturesXML")
        # Add smm in <features>
        features_xml = vm_xml.features
        features_xml.smm = "on"
        vm_xml.features = features_xml
        vm_xml.sync()
        # Replace disk with an uefi image
        if not utils_package.package_install("wget"):
            test.error("Failed to install wget on host")
        if uefi_disk_url.count("EXAMPLE"):
            test.error("Please provide the URL %s" % uefi_disk_url)
        else:
            download_cmd = ("wget %s -O %s" % (uefi_disk_url, download_file_path))
            process.system(download_cmd, verbose=False, shell=True)
        vm = env.get_vm(vm_name)
        uefi_disk = {'disk_source_name': download_file_path}
        libvirt.set_vm_disk(vm, uefi_disk)

    vm_names = params.get("vms").split()
    vm_name = vm_names[0]
    vm = env.get_vm(vm_name)
    domuuid = vm.get_uuid()
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    os_xml = getattr(vm_xml, "os")
    host_arch = platform.machine()
    if backend_type == "emulator" and host_arch == 'x86_64':
        if not utils_package.package_install("OVMF"):
            test.error("Failed to install OVMF or edk2-ovmf pkgs on host")
        if os_xml.xmltreefile.find('nvram') is None:
            replace_os_disk(vm_xml, vm_name, nvram)
            vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
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

    def check_dumpxml(vm_name):
        """
        Check whether the added devices are shown in the guest xml

        :param vm_name: current vm name
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
        if pattern not in astring.to_text(xml_after_adding_device):
            test.fail("Can not find the %s tpm device xml "
                      "in the guest xml file." % tpm_model)
        # Check backend type
        pattern = '<backend type="%s"' % backend_type
        if pattern not in astring.to_text(xml_after_adding_device):
            test.fail("Can not find the %s backend type xml for tpm dev "
                      "in the guest xml file." % backend_type)
        # Check backend version
        if backend_version:
            check_ver = backend_version if backend_version != 'none' else '2.0'
            pattern = '"emulator" version="%s"' % check_ver
            if pattern not in astring.to_text(xml_after_adding_device):
                test.fail("Can not find the %s backend version xml for tpm dev "
                          "in the guest xml file." % check_ver)
        # Check device path
        if backend_type == "passthrough":
            pattern = '<device path="/dev/tpm0"'
            if pattern not in astring.to_text(xml_after_adding_device):
                test.fail("Can not find the %s device path xml for tpm dev "
                          "in the guest xml file." % device_path)
        # Check encryption secret
        if prepare_secret:
            pattern = '<encryption secret="%s" />' % encryption_uuid
            if pattern not in astring.to_text(xml_after_adding_device):
                test.fail("Can not find the %s secret uuid xml for tpm dev "
                          "in the guest xml file." % encryption_uuid)
        logging.info('------PASS on guest dumpxml check------')

    def check_qemu_cmd_line(vm, vm_name, domid):
        """
        Check whether the added devices are shown in the qemu cmd line

        :param vm: current vm
        :param vm_name: current vm name
        :param domid: domain id for checking vtpm socket file
        """
        logging.info("------Checking qemu cmd line------")
        if not vm.get_pid():
            test.fail('VM pid file missing.')
        with open('/proc/%s/cmdline' % vm.get_pid()) as cmdline_file:
            cmdline = cmdline_file.read()
            logging.debug("Qemu cmd line info:\n %s", cmdline)
        # Check tpm model
        pattern_list = ["-device.%s" % tpm_model]
        # Check backend type
        if backend_type == "passthrough":
            dev_num = re.search(r"\d+", device_path).group()
            backend_segment = "id=tpm-tpm%s" % dev_num
        else:
            # emulator backend
            backend_segment = "id=tpm-tpm0,chardev=chrtpm"
        pattern_list.append("-tpmdev.%s,%s" % (backend_type, backend_segment))
        # Check chardev socket for vtpm
        if backend_type == "emulator":
            pattern_list.append("-chardev.socket,id=chrtpm,"
                                "path=.*/run/libvirt/qemu/swtpm/%s-%s-swtpm.sock" % (domid, vm_name))
        for pattern in pattern_list:
            if not re.search(pattern, cmdline):
                if not remove_dev:
                    test.fail("Can not find the %s for tpm device "
                              "in qemu cmd line." % pattern)
            elif remove_dev:
                test.fail("%s still exists after remove vtpm and restart" % pattern)
        logging.info("------PASS on qemu cmd line check------")

    def check_swtpm(domid, domuuid, vm_name):
        """
        Check swtpm cmdline and files for vtpm.

        :param domid: domain id for checking vtpm files
        :param domuuid: domain uuid for checking vtpm state file
        :param vm_name: current vm name
        """
        logging.info("------Checking swtpm cmdline and files------")
        # Check swtpm cmdline
        swtpm_pid = utils_misc.get_pid("%s-swtpm.pid" % vm_name)
        if not swtpm_pid:
            if not remove_dev:
                test.fail('swtpm pid file missing.')
            else:
                return
        elif remove_dev:
            test.fail('swtpm pid file still exists after remove vtpm and restart')
        with open('/proc/%s/cmdline' % swtpm_pid) as cmdline_file:
            cmdline = cmdline_file.read()
            logging.debug("Swtpm cmd line info:\n %s", cmdline)
        pattern_list = ["--daemon", "--ctrl", "--tpmstate", "--log", "--tpm2", "--pid"]
        if prepare_secret:
            pattern_list.extend(["--key", "--migration-key"])
        for pattern in pattern_list:
            if not re.search(pattern, cmdline):
                test.fail("Can not find the %s for tpm device "
                          "in swtpm cmd line." % pattern)
        # Check swtpm files
        file_list = ["/var/run/libvirt/qemu/swtpm/%s-%s-swtpm.sock" % (domid, vm_name)]
        file_list.append("/var/lib/libvirt/swtpm/%s/tpm2" % domuuid)
        file_list.append("/var/log/swtpm/libvirt/qemu/%s-swtpm.log" % vm_name)
        file_list.append("/var/run/libvirt/qemu/swtpm/%s-%s-swtpm.pid" % (domid, vm_name))
        for swtpm_file in file_list:
            if not os.path.exists(swtpm_file):
                test.fail("Swtpm file: %s does not exist" % swtpm_file)
        logging.info("------PASS on Swtpm cmdline and files check------")

    def get_tpm2_tools_cmd(session=None):
        """
        Get tpm2-tools pkg version and return corresponding getrandom cmd

        :session: guest console session
        :return: tpm2_getrandom cmd usage
        """
        cmd = 'rpm -q tpm2-tools'
        get_v_tools = session.cmd(cmd) if session else process.run(cmd).stdout_text
        v_tools_list = get_v_tools.strip().split('-')
        if session:
            logging.debug("The tpm2-tools version is %s", v_tools_list[2])
        v_tools = int(v_tools_list[2].split('.')[0])
        return "tpm2_getrandom 8" if v_tools < 4 else "tpm2_getrandom -T device:/dev/tpm0 8 --hex"

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
        tpm_real_v = tpm_v
        if tpm_v != "2.0":
            if not service_mgr.start('tcsd'):
                # service_mgr.start() return 'True' if succeed
                if tpm_v == "1.2":
                    test.fail("Host tcsd.serivce start failed")
                else:
                    # Means tpm_v got nothing from dmesg, log failure here and
                    # go to next 'if' to try tpm2.0 tools.
                    logging.info("Host tcsd.serivce start failed")
            else:
                tpm_real_v = "1.2"
                logging.info("Host tpm version info:")
                result = process.run("tpm_version", ignore_status=False)
                logging.debug("[host]# tpm_version\n %s", result.stdout)
                time.sleep(2)
                service_mgr.stop('tcsd')
        if tpm_v != "1.2":
            # Try tpm2.0 tools
            if not utils_package.package_install("tpm2-tools"):
                test.error("Failed to install tpm2-tools on host")
            tpm2_getrandom_cmd = get_tpm2_tools_cmd()
            if process.run(tpm2_getrandom_cmd, ignore_status=True).exit_status:
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
            tpm2_getrandom_cmd = get_tpm2_tools_cmd()
            if not process.run(tpm2_getrandom_cmd, ignore_status=True).exit_status:
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
                logging.debug("Command output: %s", output)
                if status:
                    if expect_fail:
                        test.cancel("tpm-crb passthrough only works with host tpm2.0, "
                                    "but your host tpm version is 1.2")
                    else:
                        test.fail("Failed to start tcsd.service in guest")
                else:
                    dev_output = session.cmd_output("ls /dev/|grep tpm")
                    logging.debug("Command output: %s", dev_output)
                    status, output = session.cmd_status_output("tpm_version")
                    logging.debug("Command output: %s", output)
                    if status:
                        test.fail("Guest tpm can not work")
        else:
            # If expect_version is tpm2.0, install and test by tpm2-tools
            if not utils_package.package_install(["tpm2-tools"], session, 360):
                test.error("Failed to install tpm2-tools package in guest")
            else:
                tpm2_getrandom_cmd = get_tpm2_tools_cmd(session)
                status1, output1 = session.cmd_status_output("ls /dev/|grep tpm")
                logging.debug("Command output: %s", output1)
                status2, output2 = session.cmd_status_output(tpm2_getrandom_cmd)
                logging.debug("Command output: %s", output2)
                if status1 or status2:
                    if not expect_fail:
                        test.fail("Guest tpm can not work")
                    else:
                        d_status, d_output = session.cmd_status_output("date")
                        if d_status:
                            test.fail("Guest OS doesn't work well")
                        logging.debug("Command output: %s", d_output)
                elif expect_fail:
                    test.fail("Expect fail but guest tpm still works")
        logging.info("------PASS on guest tpm device work check------")

    def run_test_suite_in_guest(session):
        """
        Run kernel test suite for guest tpm.

        :param session: Guest session to be tested
        """
        logging.info("------Checking kernel test suite for guest tpm------")
        boot_info = session.cmd('uname -r').strip().split('.')
        kernel_version = '.'.join(boot_info[:2])
        # Download test suite per current guest kernel version
        parent_path = "https://cdn.kernel.org/pub/linux/kernel"
        if float(kernel_version) < 5.3:
            major_version = "5"
            file_version = "5.3"
        else:
            major_version = boot_info[0]
            file_version = kernel_version
        src_url = "%s/v%s.x/linux-%s.tar.xz" % (parent_path, major_version, file_version)
        download_cmd = "wget %s -O %s" % (src_url, "/root/linux.tar.xz")
        output = session.cmd_output(download_cmd, timeout=480)
        logging.debug("Command output: %s", output)
        # Install neccessary pkgs to build test suite
        if not utils_package.package_install(["tar", "make", "gcc", "rsync", "python2"], session, 360):
            test.fail("Failed to install specified pkgs in guest OS.")
        # Unzip the downloaded test suite
        status, output = session.cmd_status_output("tar xvJf /root/linux.tar.xz -C /root")
        if status:
            test.fail("Uzip failed: %s" % output)
        # Specify using python2 to run the test suite per supporting
        test_path = "/root/linux-%s/tools/testing/selftests" % file_version
        sed_cmd = "sed -i 's/python -m unittest/python2 -m unittest/g' %s/tpm2/test_*.sh" % test_path
        output = session.cmd_output(sed_cmd)
        logging.debug("Command output: %s", output)
        # Build and and run the .sh files of test suite
        status, output = session.cmd_status_output("make -C %s TARGETS=tpm2 run_tests" % test_path, timeout=360)
        logging.debug("Command output: %s", output)
        if status:
            test.fail("Failed to run test suite in guest OS.")
        for test_sh in ["test_smoke.sh", "test_space.sh"]:
            pattern = "ok .* selftests: tpm2: %s" % test_sh
            if not re.search(pattern, output) or ("not ok" in output):
                test.fail("test suite check failed.")
        logging.info("------PASS on kernel test suite check------")

    def persistent_test(vm, vm_xml):
        """
        Test for vtpm with persistent_state.
        """
        vm.undefine("--nvram")
        virsh.create(vm_xml.xml, **virsh_dargs)
        domuuid = vm.get_uuid()
        state_file = "/var/lib/libvirt/swtpm/%s/tpm2/tpm2-00.permall" % domuuid
        process.run("ls -Z %s" % state_file)
        session = vm.wait_for_login()
        test_guest_tpm("2.0", session, False)
        session.close()
        virsh.dom_list("--transient", debug=True)
        vm.destroy()
        if not os.path.exists(state_file):
            test.fail("Swtpm state file: %s does not exist after destroy vm'" % state_file)
        process.run("ls -Z %s" % state_file)

    def reuse_by_vm2(tpm_dev):
        """
        Try to add same tpm to a second guest, when it's being used by one guest.

        :param tpm_dev: tpm device to be added into guest xml
        """
        logging.info("------Trying to add same tpm to a second domain------")
        vm2_xml.remove_all_device_by_type('tpm')
        vm2_xml.add_device(tpm_dev)
        vm2_xml.sync()
        ret = virsh.start(vm2_name, ignore_status=True, debug=True)
        if backend_type == "passthrough":
            if ret.exit_status:
                logging.info("Expected failure when try to passthrough a tpm"
                             " that being used by another guest")
                return
            test.fail("Reuse a passthroughed tpm should not succeed.")
        elif ret.exit_status:
            # emulator backend
            test.fail("Vtpm for each guest should not interfere with each other")

    try:
        tpm_real_v = None
        sec_uuids = []
        new_name = ""
        virsh_dargs = {"debug": True, "ignore_status": False}
        vm_xml.remove_all_device_by_type('tpm')
        tpm_dev = Tpm()
        if tpm_model:
            tpm_dev.tpm_model = tpm_model
        if not no_backend:
            backend = tpm_dev.Backend()
            if backend_type != 'none':
                backend.backend_type = backend_type
                if backend_type == "passthrough":
                    tpm_real_v = get_host_tpm_bef(tpm_v)
                    logging.debug("The host tpm real version is %s", tpm_real_v)
                    if device_path:
                        backend.device_path = device_path
                if backend_type == "emulator":
                    if backend_version != 'none':
                        backend.backend_version = backend_version
                    if persistent_state:
                        backend.persistent_state = "yes"
                    if prepare_secret:
                        auth_sec_dict = {"sec_ephemeral": "no",
                                         "sec_private": "yes",
                                         "sec_desc": "sample vTPM secret",
                                         "sec_usage": "vtpm",
                                         "sec_name": "VTPM_example"}
                        encryption_uuid = libvirt.create_secret(auth_sec_dict)
                        if secret_value != 'none':
                            virsh.secret_set_value(encryption_uuid, "open sesame", encode=True, debug=True)
                        sec_uuids.append(encryption_uuid)
                        if encrypt_change != 'encrpt':
                            # plain_to_encrypt will not add encryption on first start
                            if secret_uuid == 'invalid':
                                encryption_uuid = encryption_uuid[:-1]
                            backend.encryption_secret = encryption_uuid
                        if secret_uuid == "change":
                            auth_sec_dict["sec_desc"] = "sample2 vTPM secret"
                            auth_sec_dict["sec_name"] = "VTPM_example2"
                            new_encryption_uuid = libvirt.create_secret(auth_sec_dict)
                            virsh.secret_set_value(new_encryption_uuid, "open sesame", encode=True, debug=True)
                            sec_uuids.append(new_encryption_uuid)
                    if secret_uuid == 'nonexist':
                        backend.encryption_secret = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            tpm_dev.backend = backend
        logging.debug("tpm dev xml to add is:\n %s", tpm_dev)
        for num in range(tpm_num):
            vm_xml.add_device(tpm_dev, True)
        if persistent_state:
            persistent_test(vm, vm_xml)
            return
        else:
            ret = virsh.define(vm_xml.xml, ignore_status=True, debug=True)
        expected_match = ""
        if not err_msg:
            expected_match = "Domain .*%s.* defined from %s" % (vm_name, vm_xml.xml)
        libvirt.check_result(ret, err_msg, "", False, expected_match)
        if err_msg:
            # Stop test when get expected failure
            return
        if vm_operate != "restart":
            check_dumpxml(vm_name)
        # For default model, no need start guest to test
        if tpm_model:
            expect_fail = False
            try:
                vm.start()
            except VMStartError as detail:
                if secret_value == 'none' or secret_uuid == 'nonexist':
                    logging.debug("Expected failure: %s", detail)
                    return
                else:
                    test.fail(detail)
            domuuid = vm.get_uuid()
            if vm_operate or restart_libvirtd:
                # Make sure OS works before vm operate or restart libvirtd
                session = vm.wait_for_login()
                test_guest_tpm("2.0", session, False)
                session.close()
                if restart_libvirtd:
                    utils_libvirtd.libvirtd_restart()
                swtpm_statedir = "/var/lib/libvirt/swtpm/%s" % domuuid
                if vm_operate == "resume":
                    virsh.suspend(vm_name, **virsh_dargs)
                    time.sleep(3)
                    virsh.resume(vm_name, **virsh_dargs)
                elif vm_operate == "snapshot":
                    virsh.snapshot_create_as(vm_name, "sp1 --memspec file=/tmp/testvm_sp1", **virsh_dargs)
                elif vm_operate in ["restart", "create"]:
                    vm.destroy()
                    if vm_operate == "create":
                        virsh.undefine(vm_name, options="--nvram", **virsh_dargs)
                        if os.path.exists(swtpm_statedir):
                            test.fail("Swtpm state dir: %s still exist after vm undefine" % swtpm_statedir)
                        virsh.create(vm_xml.xml, **virsh_dargs)
                    else:
                        if vm_oprt == "domrename":
                            new_name = "vm_" + utils_misc.generate_random_string(5)
                            virsh.domrename(vm_name, new_name, **virsh_dargs)
                            new_vm = libvirt_vm.VM(new_name, vm.params, vm.root_dir, vm.address_cache)
                            vm = new_vm
                            vm_name = new_name
                        elif secret_value == 'change':
                            logging.info("Changing secret value...")
                            virsh.secret_set_value(encryption_uuid, "new sesame", encode=True, debug=True)
                        elif not restart_libvirtd:
                            # remove_dev or do other vm operations during restart
                            vm_xml.remove_all_device_by_type('tpm')
                            if secret_uuid == "change" or encrypt_change:
                                # Change secret uuid, or change encrytion state:from plain to encrypted, or on the contrary
                                if encrypt_change == 'plain':
                                    # Change from encrypted state to plain：redefine a tpm dev without encryption
                                    tpm_dev = Tpm()
                                    tpm_dev.tpm_model = tpm_model
                                    backend = tpm_dev.Backend()
                                    backend.backend_type = backend_type
                                    backend.backend_version = backend_version
                                else:
                                    # Use a new secret's uuid
                                    if secret_uuid == "change":
                                        encryption_uuid = new_encryption_uuid
                                    backend.encryption_secret = encryption_uuid
                                tpm_dev.backend = backend
                                logging.debug("The new tpm dev xml to add for restart vm is:\n %s", tpm_dev)
                                vm_xml.add_device(tpm_dev, True)
                            if encrypt_change in ['encrpt', 'plain']:
                                # Avoid sync() undefine removing the state file
                                vm_xml.define()
                            else:
                                vm_xml.sync()
                        if rm_statefile:
                            swtpm_statefile = "%s/tpm2/tpm2-00.permall" % swtpm_statedir
                            logging.debug("Removing state file: %s", swtpm_statefile)
                            os.remove(swtpm_statefile)
                        ret = virsh.start(vm_name, ignore_status=True, debug=True)
                        libvirt.check_exit_status(ret, status_error)
                        if status_error and ret.exit_status != 0:
                            return
                    if not remove_dev:
                        check_dumpxml(vm_name)
                elif vm_operate == 'managedsave':
                    virsh.managedsave(vm_name, **virsh_dargs)
                    time.sleep(5)
                    if secret_value == 'change':
                        logging.info("Changing secret value...")
                        virsh.secret_set_value(encryption_uuid, "new sesame", encode=True, debug=True)
                        if rm_statefile:
                            swtpm_statefile = "%s/tpm2/tpm2-00.permall" % swtpm_statedir
                            logging.debug("Removing state file: %s", swtpm_statefile)
                            os.remove(swtpm_statefile)
                    ret = virsh.start(vm_name, ignore_status=True, debug=True)
                    libvirt.check_exit_status(ret, status_error)
                    if status_error and ret.exit_status != 0:
                        return
            domid = vm.get_id()
            check_qemu_cmd_line(vm, vm_name, domid)
            if backend_type == "passthrough":
                if tpm_real_v == "1.2" and tpm_model == "tpm-crb":
                    expect_fail = True
                expect_version = tpm_real_v
                test_host_tpm_aft(tpm_real_v)
            else:
                # emulator backend
                if remove_dev:
                    expect_fail = True
                expect_version = backend_version
                check_swtpm(domid, domuuid, vm_name)
            session = vm.wait_for_login()
            if test_suite:
                run_test_suite_in_guest(session)
            else:
                test_guest_tpm(expect_version, session, expect_fail)
            session.close()
            if multi_vms:
                reuse_by_vm2(tpm_dev)
                if backend_type != "passthrough":
                    #emulator backend
                    check_dumpxml(vm2_name)
                    domid = vm2.get_id()
                    domuuid = vm2.get_uuid()
                    check_qemu_cmd_line(vm2, vm2_name, domid)
                    check_swtpm(domid, domuuid, vm2_name)
                    session = vm2.wait_for_login()
                    test_guest_tpm(backend_version, session, expect_fail)
                    session.close()

    finally:
        # Remove renamed domain if it exists
        if new_name:
            virsh.remove_domain(new_name, "--nvram", debug=True)
        if os.path.exists("/var/log/swtpm/libvirt/qemu/%s-swtpm.log" % new_name):
            os.remove("/var/log/swtpm/libvirt/qemu/%s-swtpm.log" % new_name)
        # Remove snapshot if exists
        if vm_operate == "snapshot":
            snapshot_lists = virsh.snapshot_list(vm_name)
            if len(snapshot_lists) > 0:
                libvirt.clean_up_snapshots(vm_name, snapshot_lists)
                for snap in snapshot_lists:
                    virsh.snapshot_delete(vm_name, snap, "--metadata")
                if os.path.exists("/tmp/testvm_sp1"):
                    os.remove("/tmp/testvm_sp1")
        # Clear guest os
        if test_suite:
            session = vm.wait_for_login()
            logging.info("Removing dir /root/linux-*")
            output = session.cmd_output("rm -rf /root/linux-*")
            logging.debug("Command output:\n %s", output)
            session.close()
        if vm_operate == "create":
            vm.define(vm_xml.xml)
        vm_xml_backup.sync(options="--nvram --managed-save")
        # Remove swtpm log file in case of impact on later runs
        if os.path.exists("/var/log/swtpm/libvirt/qemu/%s-swtpm.log" % vm.name):
            os.remove("/var/log/swtpm/libvirt/qemu/%s-swtpm.log" % vm.name)
        if os.path.exists("/var/lib/libvirt/swtpm/%s" % domuuid):
            shutil.rmtree("/var/lib/libvirt/swtpm/%s" % domuuid)
        for sec_uuid in set(sec_uuids):
            virsh.secret_undefine(sec_uuid, ignore_status=True, debug=True)
        if vm2:
            if len(vm_names) > 1:
                vm2_xml_backup.sync(options="--nvram")
            else:
                virsh.remove_domain(vm2_name, "--nvram --remove-all-storage", debug=True)
            if os.path.exists("/var/log/swtpm/libvirt/qemu/%s-swtpm.log" % vm2.name):
                os.remove("/var/log/swtpm/libvirt/qemu/%s-swtpm.log" % vm2.name)
