import os
import aexpect

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_backup
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import backup_xml
from virttest.libvirt_xml import checkpoint_xml

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test pull mode incremental backup from local image with fd transport nbd service.

    steps:
    1. Prepare a running VM with two disks.
    2. Create and pass the socket to libvirt.
    3. Start the full backup.
    4. Start the incremental backup.
    """
    def create_pass_socket():
        """
        Prepare a script to create and pass the socket to libvirt.

        :return: the tuple includes the session and the file list.
        """
        script_path = data_dir.get_data_dir() + "/fdtest-socket.py"
        file_list.append(script_path)
        script = (
            "cat <<EOF > %s\n"
            "import socket\n"
            "import libvirt\n"
            "import os\n"
            "import selinux\n"
            "path = '%s'\n"
            "if os.path.exists(path):\n"
            "    os.unlink(path)\n"
            "selinux.setsockcreatecon_raw('system_u:object_r:svirt_t:s0')\n"
            "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
            "s.bind(path)\n"
            "fdlist = [ s.fileno() ]\n"
            "conn = libvirt.open()\n"
            "dom = conn.lookupByName('%s')\n"
            "dom.FDAssociate('%s', fdlist)\n"
            "print('associated')\n"
            "input()\n"
            "EOF\n"
        ) % (script_path, socket_path, vm_name, fdgroup)
        process.run(script, shell=True, ignore_status=False)
        python_cmd = "python %s" % script_path
        session = aexpect.ShellSession(python_cmd)
        status, output = session.read_until_last_line_matches("associated")
        if status:
            test.fail("Create and pass the socket to libvirt failed with error %s." % output)
        return session, file_list

    def prepare_backup_xml(backup_dict, checkpoint_dict):
        """
        Prepare the backup XML for testing.

        :param backup_dict: the backup XML.
        :param checkpoint_dict: the checkpoint XML.
        :return: the backup options used in virsh command.
        """
        backup_dev = backup_xml.BackupXML()
        backup_dev.setup_attrs(**backup_dict)
        test.log.debug("The backup xml is: %s" % backup_dev)
        checkpoint_dev = checkpoint_xml.CheckpointXML()
        checkpoint_dev.setup_attrs(**checkpoint_dict)
        test.log.debug("The checkpoint xml is: %s" % checkpoint_dev)
        backup_options = backup_dev.xml + " " + checkpoint_dev.xml
        return backup_options

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get("target_disk")
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get("disk_dict", "{}"))
    socket_path = params.get("socket_path")
    fdgroup = params.get("fdgroup")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)
    file_list = []

    try:
        test.log.info("TEST_SETUP1: Prepare running VM with two disks.")
        disk_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test.log.info("TEST_SETUP2: Create and pass the socket to libvirt.")
        session, file_list = create_pass_socket()
        test.log.info("TEST_STEP3: Start backup.")
        for backup_type in ["full", "inc"]:
            scratch_file = data_dir.get_data_dir() + "/%s_%s_scratch" % (target_disk, backup_type)
            backup_file_path = data_dir.get_data_dir() + "/%s_%s_backup" % (target_disk, backup_type)
            file_list.append(backup_file_path)
            checkpoint_name = "check_%s" % backup_type
            backup_dict = eval(params.get("backup_dict", "{}") % scratch_file)
            checkpoint_dict = eval(params.get("checkpoint_dict", "{}") % checkpoint_name)
            backup_options = prepare_backup_xml(backup_dict, checkpoint_dict)
            virsh.backup_begin(vm_name, backup_options, debug=True, ignore_status=False)
            if backup_type == "full":
                nbd_params = {
                    'nbd_protocol': 'unix',
                    'nbd_export': '%s' % target_disk,
                    'nbd_socket': '%s' % socket_path
                }
                status = utils_backup.pull_full_backup_to_file(nbd_params, backup_file_path)
                if status:
                    test.fail("Fail to get backup data: %s" % status)
            else:
                job_info = virsh.domjobinfo(vm_name).stdout_text
                if "Backup" not in job_info:
                    test.fail("The backup job is not running!")
            virsh.domjobabort(vm_name)
        session.close()

    finally:
        clean_checkpoint_metadata = not vm.is_alive()
        utils_backup.clean_checkpoints(vm_name,
                                       clean_metadata=clean_checkpoint_metadata)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        disk_obj.cleanup_disk_preparation(disk_type)
        for file in file_list:
            if os.path.exists(file):
                os.remove(file)
