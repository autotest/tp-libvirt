import os
import ast
import logging
import augeas

from virttest.libvirt_xml import vm_xml
from virttest import virsh
from virttest import data_dir
from avocado.utils import service
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test lock_managers by creating snapshot(BZ1191901)
    1) Set auto disk lease environment with lock_manager(virtlockd or sanlock)
    2) Start guest and create a snapshot(internal or external). Check if there
    is deadlock.
    3) Clean up environment
    """

    def set_conf(file_path, conf_dict, operation):
        """Set or remove configrations in conf file by augeas.
        Args:
            file_path: conf file path.
            conf_dict: configrations dictionary. e.g. {configration:value}
            operation: "set" or "remove"
        Return:
            None
        """
        augtool = augeas.Augeas()
        for key in conf_dict:
            aug_path = os.path.join('/files', file_path, key)
            if operation == "set":
                augtool.set(aug_path, conf_dict[key])
                logging.debug("Set configration %s as %s", aug_path, conf_dict[key])
            if operation == "remove":
                augtool.remove(aug_path)
                logging.debug("Remove configration %s", aug_path)
        augtool.save()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    snap_name = params.get("snap_name")
    snap_type = params.get("snap_type")
    snap_option = params.get("snap_option", "")
    snap_timeout = params.get("snap_timeout")
    snap_path = os.path.join(data_dir.get_tmp_dir(), vm_name + "." + snap_name)
    snap_diskspec = vm.get_first_disk_devices()['target'] + ",file=" + snap_path
    lock_type = params.get("lock_type")
    lock_conf_file = params.get("lock_conf_file")
    lock_confs = ast.literal_eval(params.get("lock_confs"))
    qemu_conf_file = params.get("qemu_conf_file")
    qemu_confs = ast.literal_eval(params.get("qemu_confs"))
    set_conf(qemu_conf_file, qemu_confs, "set")
    service_mgr = service.ServiceManager()

    logging.debug("Set %s environment, start service", lock_type)
    set_conf(lock_conf_file, lock_confs, "set")
    service_mgr.start(lock_type)
    service_mgr.restart("libvirtd")
    try:
        logging.debug("Start testing")
        vm.start()
        if snap_type == "internal":
            result = virsh.snapshot_create_as(vm_name,
                                              snap_name,
                                              timeout=snap_timeout,
                                              debug=True)
        if snap_type == "external":
            result = virsh.snapshot_create_as(vm_name,
                                              snap_name + " " + snap_option + " --diskspec " + snap_diskspec,
                                              timeout=snap_timeout,
                                              debug=True)
        libvirt.check_exit_status(result)
    finally:
        logging.debug("Cleaning test environment")
        if vm.is_alive():
            vm.destroy()
        if snap_type == "internal":
            virsh.snapshot_delete(vm_name, snap_name, debug=True)
        if snap_type == "external":
            os.remove(snap_path)
        backup_xml.sync()
        set_conf(qemu_conf_file, qemu_confs, "remove")
        set_conf(lock_conf_file, lock_confs, "remove")
        service_mgr.restart("libvirtd")
        service_mgr.stop(lock_type)
