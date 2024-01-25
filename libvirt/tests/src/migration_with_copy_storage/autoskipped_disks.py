import os

from avocado.utils import process

from virttest import data_dir
from virttest import remote
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    To verify that libvirt will skip specific disks when do vm live migration
    with copy storage. This case starts vm with specific disks, then do vm live
    migration with copy storage; migration may fail or succeed according to the
    disk configuration.

    """
    def setup_common():
        """
        Common setup step.

        """
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")

        test.log.info("Common setup step.")
        migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))
        migration_obj.remote_add_or_remove_port(migrate_desturi_port)

        first_image_path = os.path.join(data_dir.get_data_dir(), 'images')
        first_disk_dict = {'source': {'attrs': {'file': os.path.join(first_image_path,
                           os.path.basename(vm.get_first_disk_devices()['source']))}}}

        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_inactive_dumpxml(vm_name),
                'disk', first_disk_dict)

    def setup_test():
        """
        Setup step.

        """
        second_disk_dict = eval(params.get("second_disk_dict"))
        nfs_mount_dir = params.get("nfs_mount_dir")
        second_disk_name = params.get("second_disk_name")

        test.log.info("Setup step.")
        source_file = vm.get_first_disk_devices()['source']
        if test_case in ["disk_with_shareable", "disk_with_readonly", "cdrom_with_startuppolicy"]:
            if nfs_mount_dir:
                second_disk_img = os.path.join(nfs_mount_dir, second_disk_name)
            else:
                second_disk_img = os.path.join(os.path.dirname(source_file), second_disk_name)
            if os.path.exists(second_disk_img):
                os.remove(second_disk_img)
            if test_case in ["disk_with_shareable", "disk_with_readonly"]:
                libvirt_disk.create_disk("file", disk_format="raw", path=second_disk_img)
                second_disk_dict.update({"source": {"attrs": {"file": "%s" % second_disk_img}}})
            else:
                startup_policy = eval(params.get("startup_policy"))
                if nfs_mount_dir:
                    cdrom_iso = nfs_mount_dir + "/test.iso"
                else:
                    cdrom_iso = os.path.dirname(source_file) + "/test.iso"
                process.run("dd if=/dev/urandom of=%s bs=1M count=10" % cdrom_iso, shell=True)
                process.run("mkisofs -o %s %s" % (second_disk_img, cdrom_iso), shell=True)
                startup_policy.update({"file": "%s" % second_disk_img})
                second_disk_dict.update({"source": {"attrs": startup_policy}})

        if test_case == "disk_with_shareable":
            base_steps.prepare_disks_remote(params, vm)
        else:
            server_ip = params.get("server_ip")
            server_user = params.get("server_user")
            server_pwd = params.get("server_pwd")

            remote_session = remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')

            image_info = utils_misc.get_image_info(source_file)
            disk_size = image_info.get("vsize")
            disk_format = image_info.get("format")
            utils_misc.make_dirs(os.path.dirname(source_file), remote_session)
            libvirt_disk.create_disk("file", path=source_file,
                                     size=disk_size, disk_format=disk_format,
                                     session=remote_session)
            remote_session.close()

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", second_disk_dict))
        vmxml.sync()

        vm.start()
        vm.wait_for_login().close()

    vm_name = params.get("migrate_main_vm")
    test_case = params.get("test_case")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_common()
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        base_steps.cleanup_disks_remote(params, vm)
        migration_obj.cleanup_connection()
