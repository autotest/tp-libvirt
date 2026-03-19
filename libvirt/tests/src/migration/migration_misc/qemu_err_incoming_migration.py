from avocado.utils import memory
from avocado.utils import process

from virttest import remote
from virttest import utils_disk
from virttest import utils_libvirtd

from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps

hugepage_num = None


def run(test, params, env):
    """
    This case is to verify that libvirt can report reasonable error when QEMU
    fails with incoming migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        memory_backing = params.get("memory_backing", "{}")
        memory_backing = eval(memory_backing % memory.get_huge_page_size())
        hugepage_file = params.get("kernel_hp_file", "/proc/sys/vm/nr_hugepages")
        nr_hugepages_src = params.get("nr_hugepages_src")
        nr_hugepages_dest = params.get("nr_hugepages_dest")

        test.log.info("Setup steps for cases.")
        migration_obj.setup_connection()
        global hugepage_num
        with open(hugepage_file, 'r') as fp:
            hugepage_num = int(fp.readline().strip())

        process.run(f"sysctl vm.nr_hugepages={nr_hugepages_src}", shell=True)
        remote.run_remote_cmd(f"sysctl vm.nr_hugepages={nr_hugepages_dest}", params)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        mem_backing = vm_xml.VMMemBackingXML()
        mem_backing.setup_attrs(**memory_backing)
        vmxml.mb = mem_backing
        vmxml.sync()
        vm.start()
        vm.wait_for_login().close()

        utils_disk.mount("hugetlbfs", vm_hugepage_mountpoint, "hugetlbfs", session=remote_runner.session)
        utils_libvirtd.Libvirtd("virtqemud", session=remote_runner.session).restart()

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        test.log.info("Cleanup steps for cases.")
        global hugepage_num
        process.run(f"sysctl vm.nr_hugepages={hugepage_num}", shell=True)
        remote.run_remote_cmd(f"sysctl vm.nr_hugepages={hugepage_num}", params)
        utils_disk.umount("hugetlbfs", vm_hugepage_mountpoint, "hugetlbfs", session=remote_runner.session)
        utils_libvirtd.Libvirtd("virtqemud", session=remote_runner.session).restart()
        migration_obj.cleanup_connection()

    vm_name = params.get("migrate_main_vm")
    vm_hugepage_mountpoint = params.get("vm_hugepage_mountpoint")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")

    remote_runner = remote.RemoteRunner(host=server_ip,
                                        username=server_user,
                                        password=server_pwd)
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
