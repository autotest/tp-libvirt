import ctypes
import os
import re

from avocado.utils import crypto, process
from virttest import error_context, utils_misc, utils_test

from provider import win_dev


@error_context.context_aware
def run(test, params, env):
    """

    1) Start guest.
    2) Confirm guest enabled msi inside guest. Check msi and irq number of guest.
    3) Disable msi of guest.
    4) Reboot guest.
    5) Check if msi is disabled and irq number should be equal to 1.
    6) Check network and vhost process (transfer data).
    7) Check md5 value of both sides.

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def get_file_md5sum(file_name, session, timeout):
        """
        return: Return the md5sum value of the guest.
        """
        test.log.info("Get md5sum of the file:'%s'", file_name)
        s, o = session.cmd_status_output("md5sum %s" % file_name, timeout=timeout)
        if s != 0:
            test.error("Get file md5sum failed as %s" % o)
        matches = re.findall(r"\w{32}", o)
        if not matches:
            test.error("Failed to parse md5sum from output: %s" % o)
        return matches[0]

    def irq_check(session, device_name, devcon_folder, timeout):
        """
        Check virtio device's irq number, irq number should be greater than zero.

        :param session: use for sending cmd
        :param device_name: name of the specified device
        :param devcon_folder: Full path for devcon.exe
        :param timeout: Timeout in seconds.
        """
        hwid = win_dev.get_hwids(session, device_name, devcon_folder, timeout)[0]
        get_irq_cmd = params["get_irq_cmd"] % (devcon_folder, hwid)
        irq_list = re.findall(r":\s+(\d+)", session.cmd_output(get_irq_cmd), re.M)
        if not irq_list:
            test.error("device %s's irq checked fail" % device_name)
        return irq_list

    def win_set_msi_fguest(enable=True):
        """
        Disable or enable MSI from guest.
        """
        hwid = win_dev.get_hwids(session, device_name, devcon_folder, login_timeout)[0]
        session.cmd(params["msi_cmd"] % (hwid, 1 if enable else 0))

    tmp_dir = params["tmp_dir"]
    filesize = int(params.get("filesize"))
    dd_cmd = params["dd_cmd"]
    delete_cmd = params["delete_cmd"]
    file_md5_check_timeout = int(params.get("file_md5_check_timeout"))
    login_timeout = int(params.get("login_timeout", 360))
    lspci_cmd = params.get("lspci_cmd")
    msi_cmd = params.get("msi_cmd")
    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    session = vm.wait_for_serial_login()

    if params.get("os_type") == "linux":
        error_context.context("Check the pci msi in guest", test.log.info)
        pci_id = session.cmd_output_safe(lspci_cmd).strip()
        status = session.cmd_output_safe(msi_cmd % pci_id).strip()
        enable_status = re.search(r"Enable\+", status, re.M | re.I)
        if not enable_status:
            test.fail(
                "Could not determine MSI status from lspci output: %s" % status
            )
        if enable_status.group() == "Enable+":
            error_context.context("Disable pci msi in guest", test.log.info)
            utils_test.update_boot_option(vm, args_added="pci=nomsi")
            session_msi = vm.wait_for_serial_login(timeout=login_timeout)
            pci_id = session_msi.cmd_output_safe(lspci_cmd).strip()
            status = session_msi.cmd_output_safe(msi_cmd % pci_id).strip()
            session_msi.close()
            change_status = re.search(r"Enable\-", status, re.M | re.I)
            if not change_status:
                test.fail(
                    "Could not determine MSI status from lspci output: %s" % status
                )
            if change_status.group() != "Enable-":
                test.fail("virtio device's status is not correct")
            else:
                error_context.context(
                    "virtio device's status is correct", test.log.info
                )
        else:
            test.fail("virtio device's status is not correct")
    else:
        driver = params.get("driver_name")
        driver_verifier = params.get("driver_verifier", driver)
        device_name = params.get("device_name")

        devcon_folder = utils_misc.set_winutils_letter(session, params["devcon_folder"])
        error_context.context("Boot guest with %s device" % driver, test.log.info)
        session = utils_test.qemu.windrv_check_running_verifier(
            session, vm, test, driver_verifier, login_timeout
        )
        error_context.context("Check %s's irq number" % device_name, test.log.info)
        irq_list = irq_check(session, device_name, devcon_folder, login_timeout)
        irq_nums = len(irq_list)
        if irq_nums <= 1:
            test.fail(
                "%s should have multiple IRQs with MSI enabled, but found %d"
                % (device_name, irq_nums)
            )
        elif max(ctypes.c_int32(int(irq)).value for irq in irq_list) >= 0:
            test.fail(
                "%s should have negative MSI vectors, but found: %s"
                % (device_name, irq_list)
            )
        error_context.context("Disable MSI in guest", test.log.info)
        win_set_msi_fguest(enable=False)
        session = vm.reboot(session=session)
        error_context.context("Check %s's irq number" % device_name, test.log.info)
        irq_list = irq_check(session, device_name, devcon_folder, login_timeout)
        irq_nums = len(irq_list)
        if irq_nums != 1:
            test.fail(
                "%s should have exactly 1 IRQ with MSI disabled, but found %d"
                % (device_name, irq_nums)
            )
        elif min(ctypes.c_int32(int(irq)).value for irq in irq_list) <= 0:
            test.fail("%s has invalid IRQ value: %s" % (device_name, irq_list))

    # prepare test data
    guest_path = tmp_dir + "src-%s" % utils_misc.generate_random_string(8)
    host_path = os.path.join(
        test.tmpdir, "tmp-%s" % utils_misc.generate_random_string(8)
    )
    test.log.info("Test setup: Creating %dMB file on host", filesize)
    process.run(dd_cmd % host_path, shell=True)

    try:
        src_md5 = crypto.hash_file(host_path, algorithm="md5")
        test.log.info("md5 value of data from src: %s", src_md5)
        # transfer data
        error_context.context("Transfer data from host to %s" % vm.name, test.log.info)
        vm.copy_files_to(host_path, guest_path)
        dst_md5 = get_file_md5sum(guest_path, session, timeout=file_md5_check_timeout)
        test.log.info("md5 value of data in %s: %s", vm.name, dst_md5)
        if dst_md5 != src_md5:
            test.fail("File changed after transfer host -> %s" % vm.name)
    finally:
        os.remove(host_path)
        session.cmd(
            delete_cmd % guest_path, timeout=login_timeout, ignore_all_errors=True
        )
        session.close()
