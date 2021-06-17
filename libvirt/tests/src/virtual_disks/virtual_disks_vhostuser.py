import logging
import os

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest import utils_disk

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk
from virttest.libvirt_xml import vm_xml

from virttest import libvirt_version


def create_backend_image_file(image_path):
    """
    Create backend image file

    :param image_path: image file path
    """
    libvirt.create_local_disk("file", image_path, size="100M")
    chown_cmd = "chown qemu:qemu %s" % image_path
    process.run(chown_cmd, ignore_status=False, shell=True)


def start_vhost_sock_service(image_path, sock_path):
    """
    Start one vhost sock service

    :param image_path: image file path
    :param sock_path: sock file path
    """
    start_sock_service_cmd = (
        'systemd-run --uid qemu --gid qemu /usr/bin/qemu-storage-daemon'
        ' --blockdev \'{"driver":"file","filename":"%s","node-name":"libvirt-1-storage","auto-read-only":true,"discard":"unmap"}\''
        ' --blockdev \'{"node-name":"libvirt-1-format","read-only":false,"driver":"raw","file":"libvirt-1-storage"}\''
        ' --export vhost-user-blk,id=vhost-user-blk0,node-name=libvirt-1-format,addr.type=unix,addr.path=%s,writable=on'
        ' --chardev stdio,mux=on,id=char0; sleep 3'
        % (image_path, sock_path))
    cmd_output = process.run(start_sock_service_cmd, ignore_status=False, shell=True).stdout_text.strip()
    ch_seccontext_cmd = "chcon -t svirt_image_t %s" % sock_path
    process.run(ch_seccontext_cmd, ignore_status=False, shell=True)
    set_bool_mmap_cmd = "setsebool domain_can_mmap_files 1 -P"
    process.run(set_bool_mmap_cmd, ignore_status=False, shell=True)
    return cmd_output


def create_vhostuser_disk(params):
    """
    Create one vhost disk

    :param params: dict wrapped with params
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    sock_path = params.get("source_file")
    disk_src_dict = {"attrs": {"type": "unix",
                     "path": sock_path}}
    vhostuser_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    vhostuser_disk.snapshot = "no"
    return vhostuser_disk


def run(test, params, env):
    """
    Test disk encryption option.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test image.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Disk specific attributes.
    image_path = params.get("virt_disk_device_source", "/var/lib/libvirt/images/test.img")
    sock_path = params.get("source_file", "tmp/vhost.sock")
    device_target = params.get("target_dev", "vdb")

    if not libvirt_version.version_compare(7, 0, 0):
        test.cancel("Cannot support vhostuser disk feature in "
                    "this libvirt version.")

    hotplug = "yes" == params.get("virt_disk_device_hotplug")
    status_error = "yes" == params.get("status_error")
    vsock_service_id = None

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Start VM and get all partitions in VM
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        create_backend_image_file(image_path)
        vsock_service_id = start_vhost_sock_service(image_path, sock_path)
        # Prepare the disk.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        mb_params = {'source_type': 'memfd', 'access_mode': 'shared'}
        vmxml.mb = libvirt_disk.create_mbxml(mb_params)
        vmxml.sync()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("memory backing VM xml is:\n%s" % vmxml)
        disk_xml = create_vhostuser_disk(params)
        logging.debug("vhostuser disk xml is:\n%s" % disk_xml)
        if not hotplug:
            # Sync VM xml.
            vmxml.add_device(disk_xml)
            vmxml.sync()
        vm.start()
        vm.wait_for_login()
        if status_error:
            if hotplug:
                logging.debug("attaching disk, expecting error...")
                result = virsh.attach_device(vm_name, disk_xml.xml)
                libvirt.check_exit_status(result, status_error)
            else:
                test.fail("VM started unexpectedly.")
        else:
            if hotplug:
                virsh.attach_device(vm_name, disk_xml.xml, ignore_status=True,
                                    debug=True)
                if not libvirt_disk.check_in_vm(vm, device_target, old_parts):
                    test.fail("Check encryption disk in VM failed")
                virsh.detach_device(vm_name, disk_xml.xml, ignore_status=True,
                                    debug=True, wait_remove_event=True)
                if not libvirt_disk.check_in_vm(vm, device_target, old_parts, is_equal=True):
                    test.fail("can not detach device successfully")
            else:
                if not libvirt_disk.check_in_vm(vm, device_target, old_parts):
                    test.fail("Check encryption disk in VM failed")
    except virt_vm.VMStartError as e:
        if status_error:
            if hotplug:
                test.fail("In hotplug scenario, VM should "
                          "start successfully but not."
                          "Error: %s", str(e))
            else:
                logging.debug("VM failed to start as expected."
                              "Error: %s", str(e))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
    except Exception as ex:
        test.fail("unexpected exception happen: %s" % str(ex))
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()

        # Kill all qemu-storage-daemon process on host
        process.run("pidof qemu-storage-daemon && killall qemu-storage-daemon",
                    ignore_status=True, shell=True)

        if vsock_service_id:
            stop_vsock_service_cmd = "systemctl stop %s" % vsock_service_id
            process.run(stop_vsock_service_cmd, ignore_status=True, shell=True)
        # Clean up images
        for file_path in [image_path, sock_path]:
            if os.path.exists(file_path):
                os.remove(file_path)
