import os
import time

from avocado.utils import crypto
from avocado.utils import process

from virttest import data_dir
from virttest import utils_misc
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.viommu import viommu_base


def run(test, params, env):
    """
    Verify the vm with iommu enabled interface work properly
    after loading/unloading the driver.
    """
    def all_threads_done(threads):
        """
        Check whether all threads have finished
        """
        for thread in threads:
            if thread.is_alive():
                return False
            else:
                continue
        return True

    def all_threads_alive(threads):
        """
        Check whether all threads is alive
        """
        for thread in threads:
            if not thread.is_alive():
                return False
            else:
                continue
        return True

    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    filesize = int(params.get("filesize", 512))
    transfer_timeout = int(params.get("transfer_timeout", 1000))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        test.log.info("TEST_SETUP: Update VM XML.")
        test_obj.setup_iommu_test(iommu_dict=iommu_dict,
                                  cleanup_ifaces=cleanup_ifaces)

        iface_dict = test_obj.parse_iface_dict()
        if cleanup_ifaces:
            libvirt_vmxml.modify_vm_device(
                    vm_xml.VMXML.new_from_dumpxml(vm.name),
                    "interface", iface_dict)

        test.log.info("TEST_STEP: Start the VM.")
        vm.start()
        vm_session = vm.wait_for_login()
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        test.log.info("TEST_STEP: Get interface driver.")
        ethname = utils_net.get_linux_ifname(vm_session, vm.get_mac_address(0))
        output = vm_session.cmd_output(f"readlink -f /sys/class/net/{ethname}/device/driver")
        nic_driver = os.path.basename(output)

        test.log.info("TEST_STEP: Prepare file on host and guest.")
        tmp_dir = data_dir.get_tmp_dir()
        host_path = os.path.join(tmp_dir, "host_file_%s" %
                                 utils_misc.generate_random_string(8))
        guest_path = os.path.join("/home", "guest_file_%s" %
                                  utils_misc.generate_random_string(8))
        cmd = "dd if=/dev/zero of=%s bs=1M count=%d" % (host_path, filesize)
        process.run(cmd)
        file_checksum = crypto.hash_file(host_path, algorithm="md5")
        vm.copy_files_to(host_path, guest_path, timeout=transfer_timeout)
        if vm_session.cmd_status("md5sum %s | grep %s" %
                                 (guest_path, file_checksum)):
            test.cancel("File MD5SUMs changed after copy to guest")

        test.log.info("TEST_STEP: Transfer files between host and guest.")
        threads = []
        file_paths = []
        host_file_paths = []
        for sess_index in range(int(params.get("sessions_num", "5"))):
            sess_path = os.path.join("/home", "dst-%s" % sess_index)
            host_sess_path = os.path.join(tmp_dir, "dst-%s" % sess_index)
            thread1 = utils_misc.InterruptedThread(
                vm.copy_files_to, (host_path, sess_path),
                {"timeout": transfer_timeout})

            thread2 = utils_misc.InterruptedThread(
                vm.copy_files_from, (guest_path, host_sess_path),
                {"timeout": transfer_timeout})
            thread1.start()
            threads.append(thread1)
            thread2.start()
            threads.append(thread2)
            file_paths.append(sess_path)
            host_file_paths.append(host_sess_path)

        utils_misc.wait_for(lambda: all_threads_alive(threads), 60, 10, 1, text="check if all threads are alive")
        time.sleep(5)

        test.log.info("TEST_STEP: Unload and load the driver.")
        vm_serial = vm.wait_for_serial_login(timeout=120, recreate_serial_console=True)
        while not all_threads_done(threads):
            vm_serial.cmd("modprobe -r %s" % nic_driver, timeout=120)
            time.sleep(2)
            vm_serial.cmd("modprobe %s" % nic_driver, timeout=120)
            time.sleep(2)

        for copied_file in file_paths:
            if vm_serial.cmd_status("md5sum %s | grep %s" % (copied_file, file_checksum)):
                test.fail("Guest file MD5SUMs changed after copying %s" % copied_file)
        for copied_file in host_file_paths:
            if process.run("md5sum %s | grep %s" %
                           (copied_file, file_checksum), shell=True, verbose=True,
                           ignore_status=True).exit_status:
                test.fail("Host file MD5SUMs changed after copying %s" % copied_file)

    finally:
        test.log.info("TEST_TEARDOWN: Cleanup the env.")
        for thread in threads:
            thread.join()
        for copied_file in host_file_paths:
            process.system("rm -rf %s" % copied_file)
        test_obj.teardown_iommu_test()
