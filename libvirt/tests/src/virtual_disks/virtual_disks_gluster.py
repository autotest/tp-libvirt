import os
import logging

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import gluster
from virttest import utils_misc
from virttest import virt_vm, remote
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk

from virttest import libvirt_version


def run(test, params, env):
    """
    Test multiple disks attachment.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare disk image.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    gluster_server_name = params.get("gluster_server_name")
    # If gluster_server is specified from config file, just use this gluster server.
    if 'EXAMPLE' not in gluster_server_name:
        params.update({'gluster_server_ip': gluster_server_name})

    def prepare_gluster_disk(disk_img, disk_format):
        """
        Setup glusterfs and prepare disk image.
        """
        # Get the image path
        image_source = vm.get_first_disk_devices()['source']

        # Setup gluster
        host_ip = gluster.setup_or_cleanup_gluster(True, brick_path=brick_path,  **params)
        logging.debug("host ip: %s ", host_ip)
        image_info = utils_misc.get_image_info(image_source)
        image_dest = "/mnt/%s" % disk_img

        if image_info["format"] == disk_format:
            disk_cmd = ("cp -f %s %s" % (image_source, image_dest))
        else:
            # Convert the disk format
            disk_cmd = ("qemu-img convert -f %s -O %s %s %s" %
                        (image_info["format"], disk_format,
                         image_source, image_dest))

        # Mount the gluster disk and create the image.
        process.run("mount -t glusterfs %s:%s /mnt && "
                    "%s && chmod a+rw /mnt/%s && umount /mnt"
                    % (host_ip, vol_name, disk_cmd, disk_img),
                    shell=True)

        return host_ip

    def build_disk_xml(disk_img, disk_format, host_ip):
        """
        Try to rebuild disk xml
        """
        if default_pool:
            disk_xml = Disk(type_name="file")
        else:
            disk_xml = Disk(type_name="network")
        disk_xml.device = "disk"
        driver_dict = {"name": "qemu",
                       "type": disk_format,
                       "cache": "none"}
        if driver_iothread:
            driver_dict.update({"iothread": driver_iothread})
        disk_xml.driver = driver_dict
        disk_xml.target = {"dev": "vdb", "bus": "virtio"}
        if default_pool:
            utils_misc.mount("%s:%s" % (host_ip, vol_name),
                             default_pool, "glusterfs")
            process.run("setsebool virt_use_fusefs on", shell=True)
            source_dict = {"file": "%s/%s" % (default_pool, disk_img)}
            disk_xml.source = disk_xml.new_disk_source(
                **{"attrs": source_dict})
        else:
            source_dict = {"protocol": "gluster",
                           "name": "%s/%s" % (vol_name, disk_img)}
            host_dict = [{"name": host_ip, "port": "24007"}]
            # If mutiple_hosts is True, attempt to add multiple hosts.
            if multiple_hosts:
                host_dict.append({"name": params.get("dummy_host1"), "port": "24007"})
                host_dict.append({"name": params.get("dummy_host2"), "port": "24007"})
            if transport:
                host_dict[0]['transport'] = transport
            disk_xml.source = disk_xml.new_disk_source(
                **{"attrs": source_dict, "hosts": host_dict})
        return disk_xml

    def test_pmsuspend(vm_name):
        """
        Test pmsuspend command.
        """
        if vm.is_dead():
            vm.start()
            vm.wait_for_login()
        # Create swap partition if nessesary.
        if not vm.has_swap():
            swap_path = os.path.join(data_dir.get_data_dir(), 'swap.img')
            vm.create_swap_partition(swap_path)
        ret = virsh.dompmsuspend(vm_name, "disk", **virsh_dargs)
        libvirt.check_exit_status(ret)
        # wait for vm to shutdown

        if not utils_misc.wait_for(lambda: vm.state() == "shut off", 60):
            test.fail("vm is still alive after S4 operation")

        # Wait for vm and qemu-ga service to start
        vm.start()
        # Prepare guest agent and start guest
        try:
            vm.prepare_guest_agent()
        except (remote.LoginError, virt_vm.VMError) as detail:
            test.fail("failed to prepare agent:\n%s" % detail)

        #TODO This step may hang for rhel6 guest
        ret = virsh.dompmsuspend(vm_name, "mem", **virsh_dargs)
        libvirt.check_exit_status(ret)

        # Check vm state
        if not utils_misc.wait_for(lambda: vm.state() == "pmsuspended", 60):
            test.fail("vm isn't suspended after S3 operation")

        ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if not vm.is_alive():
            test.fail("vm is not alive after dompmwakeup")

    # Disk specific attributes.
    pm_enabled = "yes" == params.get("pm_enabled", "no")
    gluster_disk = "yes" == params.get("gluster_disk", "no")
    disk_format = params.get("disk_format", "qcow2")
    vol_name = params.get("vol_name")
    transport = params.get("transport", "")
    default_pool = params.get("default_pool", "")
    pool_name = params.get("pool_name")
    driver_iothread = params.get("driver_iothread")
    dom_iothreads = params.get("dom_iothreads")
    brick_path = os.path.join(test.virtdir, pool_name)
    test_qemu_cmd = "yes" == params.get("test_qemu_cmd", "no")

    # Gluster server multiple hosts flag.
    multiple_hosts = "yes" == params.get("multiple_hosts", "no")

    pre_vm_state = params.get("pre_vm_state", "running")

    # Destroy VM first.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()
    mnt_src = ""

    # This is brought by new feature:block-dev
    if transport == "rdma":
        test.cancel("transport protocol 'rdma' is not yet supported")
    try:
        # Build new vm xml.
        if pm_enabled:
            vm_xml.VMXML.set_pm_suspend(vm_name)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
            logging.debug("Attempting to set guest agent channel")
            vmxml.set_agent_channel()
            vmxml.sync()

        if gluster_disk:
            # Setup glusterfs and disk xml.
            disk_img = "gluster.%s" % disk_format
            host_ip = prepare_gluster_disk(disk_img, disk_format)
            mnt_src = "%s:%s" % (host_ip, vol_name)
            global custom_disk
            custom_disk = build_disk_xml(disk_img, disk_format, host_ip)

        start_vm = "yes" == params.get("start_vm", "yes")

        # set domain options
        if dom_iothreads:
            try:
                vmxml.iothreads = int(dom_iothreads)
                vmxml.sync()
            except ValueError:
                # 'iothreads' may not invalid number in negative tests
                logging.debug("Can't convert '%s' to integer type",
                              dom_iothreads)
        if default_pool:
            disks_dev = vmxml.get_devices(device_type="disk")
            for disk in disks_dev:
                vmxml.del_device(disk)
            vmxml.sync()

        # If hot plug, start VM first, otherwise stop VM if running.
        if start_vm:
            if vm.is_dead():
                vm.start()
        else:
            if not vm.is_dead():
                vm.destroy()

        # If gluster_disk is True, use attach_device.
        attach_option = params.get("attach_option", "")
        if gluster_disk:
            cmd_result = virsh.attach_device(domainarg=vm_name, filearg=custom_disk.xml,
                                             flagstr=attach_option,
                                             dargs=virsh_dargs, debug=True)
            libvirt.check_exit_status(cmd_result)

        # Turn VM into certain state.
        if pre_vm_state == "running":
            logging.info("Starting %s...", vm_name)
            if vm.is_dead():
                vm.start()
        elif pre_vm_state == "transient":
            logging.info("Creating %s...", vm_name)
            vmxml_for_test = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vm.undefine()
            if virsh.create(vmxml_for_test.xml, **virsh_dargs).exit_status:
                vmxml_backup.define()
                test.skip("can't create the domain")

        # Run the tests.
        if pm_enabled:
            # Makesure the guest agent is started
            try:
                vm.prepare_guest_agent()
            except (remote.LoginError, virt_vm.VMError) as detail:
                test.fail("failed to prepare agent: %s" % detail)
            # Run dompmsuspend command.
            test_pmsuspend(vm_name)

        # After block-dev introduced in libvirt 6.0.0 afterwards, gluster+%s.*format information is not provided from qemu output
        if libvirt_version.version_compare(6, 0, 0):
            test_qemu_cmd = False

        if test_qemu_cmd:
            # Check qemu-kvm command line
            cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
            if transport == "rdma":
                cmd += " | grep gluster+%s.*format=%s" % (transport, disk_format)
            else:
                cmd += " | grep gluster.*format=%s" % disk_format
            if driver_iothread:
                cmd += " | grep iothread=iothread%s" % driver_iothread
            if process.run(cmd, ignore_status=True, shell=True).exit_status:
                test.fail("Can't see gluster option '%s' "
                          "in command line" % cmd)
        # Detach hot plugged device.
        if start_vm and not default_pool:
            if gluster_disk:
                ret = virsh.detach_device(vm_name, custom_disk.xml,
                                          flagstr=attach_option, dargs=virsh_dargs, wait_remove_event=True)
                libvirt.check_exit_status(ret)

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
        if utils_misc.is_mounted(mnt_src, default_pool, 'fuse.glusterfs', verbose=True):
            process.run("umount %s" % default_pool,
                        ignore_status=True, shell=True)

        if gluster_disk:
            gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **params)
