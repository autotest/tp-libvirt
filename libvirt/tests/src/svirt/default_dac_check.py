import os
import logging

from avocado.utils import process

from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory
from virttest.staging.utils_memory import drop_caches


def run(test, params, env):
    """
        Test the default dac label after starting libvirtd and guests.

        1.Check the default dac label of hugepage file;
        2.Check the default dac lable of /var/run/qemu;
        3.Check the default dac label of guest agent file;
    """

    def mount_hugepages(page_size):
        """
        To mount hugepages

        :param page_size: unit is kB, it can be 4,2048,1048576,etc
        """
        if page_size == 4:
            perm = ""
        else:
            perm = "pagesize=%dK" % page_size

        tlbfs_status = utils_misc.is_mounted("hugetlbfs", "/dev/hugepages",
                                             "hugetlbfs")
        if tlbfs_status:
            utils_misc.umount("hugetlbfs", "/dev/hugepages", "hugetlbfs")
        utils_misc.mount("hugetlbfs", "/dev/hugepages", "hugetlbfs", perm)

    def setup_hugepages(page_size=2048, shp_num=1000):
        """
        To setup hugepages

        :param page_size: unit is kB, it can be 4,2048,1048576,etc
        :param shp_num: number of hugepage, string type
        """
        mount_hugepages(page_size)
        utils_memory.set_num_huge_pages(shp_num)
        utils_libvirtd.libvirtd_restart()

    def modify_domain_xml(vmxml):
        membacking = vm_xml.VMMemBackingXML()
        hugepages = vm_xml.VMHugepagesXML()
        vmxml.memory = int(1024000)
        membacking.hugepages = hugepages
        vmxml.mb = membacking
        logging.debug(vmxml)

    def restore_hugepages(page_size=4):
        """
        To recover hugepages
        :param page_size: unit is libvirt/tests/src/svirt/default_dac_check.pykB, it can be 4,2048,1048576,etc
        """
        mount_hugepages(page_size)
        utils_libvirtd.libvirtd_restart()

    def check_hugepage_file(vm, vmxml, umask):
        drop_caches()
        # Set umask
        process.run("umask %s" % umask, ignore_status=False, shell=True)
        setup_hugepages(2048, 2000)
        modify_domain_xml(vmxml)
        # Start guest
        vm.start()
        vm.wait_for_login()
        # Check the default dac of hugepage file
        hugepage_file_name = "/dev/hugepages/libvirt"
        # Get the mode of hugepge file
        f = os.open(hugepage_file_name, 0)
        stat_re = os.fstat(f)
        hugepage_file_mode = oct(stat_re.st_mode & 0o777)
        logging.debug(hugepage_file_mode)
        os.close(f)
        return hugepage_file_mode

    def check_ownership(filename):
        result = process.run("ls -ld %s" % filename, shell=True).stdout_text.strip().split(' ')
        ownership = "%s:%s" % (result[2], result[3])
        logging.debug(ownership)
        expect_result = "qemu:qemu"
        if ownership != expect_result:
            test.fail("The ownership of %s is %s" % (filename, ownership))

    # Get general variables.
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    status_error = ('yes' == params.get("status_error", "no"))
    start_vm = ("yes" == params.get("start_vm", "no"))
    umask = params.get("umask", "022")
    huge_pages = ('yes' == params.get("huge_pages", "yes"))
    check_type = params.get("check_type")

    vm = env.get_vm(vm_name)
    # Back up xml file.
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        if check_type == "hugepage_file":
            # Destroy domain first
            if vm.is_alive():
                vm.destroy(gracefully=False)
            file_mode = check_hugepage_file(vm, vmxml, umask)
            if file_mode != "0o755":
                test.fail("The dac mode is %s and not correct for hugepage file" % file_mode)
        elif check_type == "default_dir":
            default_dir_list = ["/var/lib/libvirt/qemu", "/var/cache/libvirt/qemu"]
            for dir in default_dir_list:
                check_ownership(dir)
        elif check_type == "socket_file":
            vmxml.set_agent_channel()
            vmxml.sync()
            vm.start()
            vm.wait_for_login()
            live_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            channels = live_xml.get_devices('channel')
            logging.debug(channels[0])
            for channel in channels:
                logging.debug(channel)
                if channel.type_name == "unix":
                    check_ownership(channel.source['path'])
    finally:
        logging.info("Restoring hugepage setting...")
        if huge_pages:
            restore_hugepages()
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
