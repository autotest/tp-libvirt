import os
import ast

from avocado.utils import process
from virttest import data_dir
from virttest import virsh
from virttest import utils_backup
from virttest.utils_libvirtd import Libvirtd

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import backup_xml
from virttest.libvirt_xml import checkpoint_xml
from virttest.utils_test import libvirt


def prepare_backup_xml(test, params, backup_type):
    """
    Prepare the backup xml.

    :return: return the backup options and the scratch file.
    """
    scratch_file = data_dir.get_data_dir() + '/scratch_file_%s' % backup_type
    backup_dict = ast.literal_eval(params.get("backup_dict", "{}") % scratch_file)
    full_checkpoint = params.get("full_checkpoint")
    inc_checkpoint = params.get("inc_checkpoint")
    if backup_type == "inc":
        backup_dict.update({'incremental': full_checkpoint})
        checkpoint_dict = ast.literal_eval(params.get("checkpoint_dict") % inc_checkpoint)
    else:
        checkpoint_dict = ast.literal_eval(params.get("checkpoint_dict") % full_checkpoint)
    backup_dev = backup_xml.BackupXML()
    backup_dev.setup_attrs(**backup_dict)
    test.log.debug("The backup xml is %s." % backup_dev)
    checkpoint_dev = checkpoint_xml.CheckpointXML()
    checkpoint_dev.setup_attrs(**checkpoint_dict)
    backup_options = backup_dev.xml + " " + checkpoint_dev.xml
    return backup_options


def start_full_backup(test, params):
    """
    Start a full backup.

    :return: return the backup file path.
    """
    vm_name = params.get("main_vm")
    backup_file_path = data_dir.get_data_dir() + '/full.backup'
    target_disk = params.get("target_disk")
    nbd_hostname = params.get("nbd_hostname")
    nbd_tcp_port = params.get("nbd_tcp_port")
    nbd_params = {
        'nbd_protocol': "tcp",
        'nbd_hostname': nbd_hostname,
        'nbd_tcp_port': nbd_tcp_port,
        'nbd_export': target_disk
        }
    backup_options = prepare_backup_xml(test, params, backup_type="full")
    virsh.backup_begin(vm_name, backup_options, debug=True, ignore_status=False)
    try:
        utils_backup.pull_full_backup_to_file(nbd_params, backup_file_path)
    except Exception as details:
        test.fail("Fail to get full backup data: %s" % details)
    test.log.debug("Full backup to %s" % backup_file_path)
    return backup_file_path


def start_incremental_backup(test, params):
    """
    Start an incremental backup.

    :return: return the backup file path.
    """
    vm_name = params.get("main_vm")
    backup_file_path = data_dir.get_data_dir() + '/inc.backup'
    nbd_hostname = params.get("nbd_hostname")
    nbd_tcp_port = params.get("nbd_tcp_port")
    target_disk = params.get("target_disk")
    nbd_bitmap_name = "backup-" + target_disk
    original_disk_size = params.get("original_disk_size", "10G")
    nbd_params = {
        'nbd_hostname': nbd_hostname,
        'nbd_tcp_port': nbd_tcp_port,
        'nbd_export': target_disk
        }
    backup_options = prepare_backup_xml(test, params, backup_type="inc")
    virsh.backup_begin(vm_name, backup_options, debug=True, ignore_status=False)
    try:
        utils_backup.pull_incremental_backup_to_file(
                            nbd_params, backup_file_path, nbd_bitmap_name,
                            original_disk_size)
    except Exception as details:
        test.fail("Fail to get incremental backup data: %s" % details)
    return backup_file_path


def test_save_vm(test, params, backup_file_list):
    """
    Test save vm with incremental backup.

    :return: return the list of backup files.
    """
    if backup_file_list is None:
        backup_file_list = []
    vm_name = params.get("main_vm")
    expected_error = params.get("expected_error")
    test.log.info("Start full backup.")
    backup_file_path = start_full_backup(test, params)
    backup_file_list.append(backup_file_path)

    test.log.info("Do save before abort the backup job.")
    save_file = data_dir.get_data_dir() + '/%s.save' % vm_name
    save_result = virsh.save(vm_name, save_file, debug=True)
    libvirt.check_result(save_result, expected_error)
    abort_result = virsh.domjobabort(vm_name)
    libvirt.check_exit_status(abort_result)

    test.log.info("Do save after abort the backup job.")
    virsh.save(vm_name, save_file, debug=True, ignore_status=False)
    virsh.restore(save_file, debug=True, ignore_status=False)
    if os.path.exists(save_file):
        os.remove(save_file)

    test.log.info("Start incremental backup.")
    backup_file_path = start_incremental_backup(test, params)
    backup_file_list.append(backup_file_path)
    return backup_file_list


def test_managedsave(test, params, backup_file_list):
    """
    Test managedsave vm with incremental backup.

    :return: return the list of backup files
    """
    if backup_file_list is None:
        backup_file_list = []
    vm_name = params.get("main_vm")
    expected_error = params.get("expected_error")
    test.log.info("Start full backup.")
    backup_file_path = start_full_backup(test, params)
    backup_file_list.append(backup_file_path)

    test.log.info("Do managedsave before abort the backup job.")
    save_result = virsh.managedsave(vm_name, debug=True)
    libvirt.check_result(save_result, expected_error)
    virsh.domjobabort(vm_name)

    test.log.info("Do managedsave after abort the backup job.")
    virsh.managedsave(vm_name, debug=True, ignore_status=False)
    virsh.start(vm_name)
    test.log.info("Start incremental backup.")
    backup_file_path = start_incremental_backup(test, params)
    backup_file_list.append(backup_file_path)
    return backup_file_list


def test_restart_service(test, params, backup_file_list):
    """
    Test restart libvirtd/virtqemud service after backup.

    :return: return the list of backup files.
    """
    if backup_file_list is None:
        backup_file_list = []
    vm_name = params.get("main_vm")
    expected_error = params.get("expected_error")
    test.log.info("Start full backup.")
    backup_file_path = start_full_backup(test, params)
    backup_file_list.append(backup_file_path)

    test.log.info("Restart libvirtd/virtqemud service.")
    Libvirtd().restart()

    test.log.info("Start incremental backup")
    backup_options = prepare_backup_xml(test, params, backup_type="inc")
    result = virsh.backup_begin(vm_name, backup_options, debug=True)
    libvirt.check_result(result, expected_error)
    return backup_file_list


def test_kill_qemu_during_libvirtd_restart(test, params, backup_file_list):
    """
    Kill qemu process between libvirtd stop/start when there is an existing pull-mode backup job.

    :return: return the list of backup files.
    """
    if backup_file_list is None:
        backup_file_list = []
    vm_name = params.get("main_vm")
    test.log.info("Start full backup.")
    backup_file_path = start_full_backup(test, params)
    backup_file_list.append(backup_file_path)

    test.log.info("Stop libvirt daemon and kill qemu process.")
    Libvirtd().stop()
    process.run("kill -9 `pidof qemu-kvm`", shell=True, ignore_status=False)

    test.log.info("Start libvirt daemon and start the guest again.")
    Libvirtd().start()
    dom_state = virsh.domstate(vm_name).stdout.strip()
    if "shut off" not in dom_state:
        test.fail("The guest doesn't shutoff as expected!")
    start_result = virsh.start(vm_name, debug=True)
    libvirt.check_exit_status(start_result)
    return backup_file_list


def run(test, params, env):
    """
    Test vm lifecycle with incremental backup
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case = params.get('test_case', '')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    test_functions = {
        'save_vm': test_save_vm,
        'managedsave': test_managedsave,
        'restart_service': test_restart_service,
        'kill_qemu_during_libvirtd_restart': test_kill_qemu_during_libvirtd_restart
        }
    run_test = test_functions.get(case)
    if not run_test:
        test.error(f"Unknown test case: {case}")

    try:
        backup_file_list = None
        if not vm.is_alive():
            vm.start()
        backup_file_list = run_test(test, params, backup_file_list)
    finally:
        utils_backup.clean_checkpoints(vm_name)
        vmxml_backup.sync()
        if backup_file_list:
            for file in backup_file_list:
                if os.path.exists(file):
                    os.remove(file)
