import os
import logging
from autotest.client.shared import error
from autotest.client import utils
from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk


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

    def prepare_gluster_disk(disk_img, disk_format):
        """
        Setup glusterfs and prepare disk image.
        """
        # Get the image path and name from parameters
        data_path = data_dir.get_data_dir()
        image_name = params.get("image_name")
        image_format = params.get("image_format")
        image_source = os.path.join(data_path,
                                    image_name + '.' + image_format)

        # Setup gluster.
        host_ip = libvirt.setup_or_cleanup_gluster(True, vol_name,
                                                   brick_path, pool_name)
        logging.debug("host ip: %s ", host_ip)
        image_info = utils_misc.get_image_info(image_source)
        if image_info["format"] == disk_format:
            disk_cmd = ("cp -f %s /mnt/%s" % (image_source, disk_img))
        else:
            # Convert the disk format
            disk_cmd = ("qemu-img convert -f %s -O %s %s /mnt/%s" %
                        (image_info["format"], disk_format, image_source, disk_img))

        # Mount the gluster disk and create the image.
        utils.run("mount -t glusterfs %s:%s /mnt; %s; umount /mnt"
                  % (host_ip, vol_name, disk_cmd))

        return host_ip

    def build_disk_xml(disk_img, disk_format, host_ip):
        """
        Try to rebuild disk xml
        """
        # Delete existed disks first.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disks_dev = vmxml.get_devices(device_type="disk")
        for disk in disks_dev:
            vmxml.del_device(disk)

        if default_pool:
            disk_xml = Disk(type_name="file")
        else:
            disk_xml = Disk(type_name="network")
        disk_xml.device = "disk"
        driver_dict = {"name": "qemu",
                       "type": disk_format,
                       "cache": "none"}
        disk_xml.driver = driver_dict
        disk_xml.target = {"dev": "vda", "bus": "virtio"}
        if default_pool:
            utils.run("mount -t glusterfs %s:%s %s; setsebool virt_use_fusefs on" %
                      (host_ip, vol_name, default_pool))
            virsh.pool_refresh("default")
            source_dict = {"file": "%s/%s" % (default_pool, disk_img)}
            disk_xml.source = disk_xml.new_disk_source(
                **{"attrs": source_dict})
        else:
            source_dict = {"protocol": "gluster",
                           "name": "%s/%s" % (vol_name, disk_img)}
            host_dict = {"name": host_ip, "port": "24007"}
            if transport:
                host_dict.update({"transport": transport})
            disk_xml.source = disk_xml.new_disk_source(
                **{"attrs": source_dict, "hosts": [host_dict]})
        # Add the new disk xml.
        vmxml.add_device(disk_xml)
        vmxml.sync()

    def check_vm_guestagent(session):
        """
        Try to start guestgent if it's not started in vm.
        """
        # Check if qemu-ga already started automatically
        cmd = "rpm -q qemu-guest-agent || yum install -y qemu-guest-agent"
        stat_install, output = session.cmd_status_output(cmd, 300)
        logging.debug(output)
        if stat_install != 0:
            raise error.TestError("Fail to install qemu-guest-agent, make"
                                  "sure that you have usable repo in guest")

        # Check if qemu-ga already started
        stat_ps = session.cmd_status("ps aux |grep [q]emu-ga | grep -v grep")
        if stat_ps != 0:
            # Check guest version to start qemu-guest-agent service.
            # Rhel 6.x: service qemu-ga start
            # Rhel 7.x, fedora: service qemu-guest-agent start
            cmd = ("grep 'release 6' /etc/redhat-release ; "
                   "if [ $? eq 0 ]; then service qemu-ga start; "
                   "else service qemu-guest-agent start; fi")
            session.cmd(cmd)
            # Check if the qemu-ga really started
            stat_ps = session.cmd_status("ps aux |grep [q]emu-ga | grep -v grep")
            if stat_ps != 0:
                raise error.TestError("Fail to run qemu-ga in guest")

    def test_pmsuspend(vm_name):
        """
        Test pmsuspend command.
        """
        if vm.is_dead():
            vm.start()
            vm.wait_for_login()
        # Create swap partition if nessesary.
        if not vm.has_swap():
            vm.create_swap_partition()
        ret = virsh.dompmsuspend(vm_name, "disk", **virsh_dargs)
        libvirt.check_exit_status(ret)
        # wait for vm to shutdown
        wait_fun = lambda: vm.state() == "shut off"
        if not utils_misc.wait_for(wait_fun, 30):
            raise error.TestFail("vm is still alive after S4 operation")

        # Wait for vm and qemu-ga service to start
        vm.start()
        session = vm.wait_for_login()
        check_vm_guestagent(session)
        session.close()

        #TODO This step may hang for rhel6 guest
        ret = virsh.dompmsuspend(vm_name, "mem", **virsh_dargs)
        libvirt.check_exit_status(ret)
        ret = virsh.dompmwakeup(vm_name, **virsh_dargs)
        libvirt.check_exit_status(ret)
        if not vm.is_alive():
            raise error.TestFail("vm is not alive after dompmwakeup")

    # Disk specific attributes.
    pm_enabled = "yes" == params.get("pm_enabled", "no")
    gluster_disk = "yes" == params.get("gluster_disk")
    disk_format = params.get("disk_format", "qcow2")
    vol_name = params.get("vol_name")
    transport = params.get("transport", "")
    default_pool = params.get("default_pool", "")
    pool_name = params.get("pool_name")
    brick_path = os.path.join(test.virtdir, pool_name)

    pre_vm_state = params.get("pre_vm_state", "running")

    # Destroy VM first.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Build new vm xml.
        if pm_enabled:
            vm_xml.VMXML.set_pm_suspend(vm_name)
            vm_xml.VMXML.set_agent_channel(vm_name)

        if gluster_disk:
            # Setup glusterfs and disk xml.
            disk_img = "gluster.%s" % disk_format
            host_ip = prepare_gluster_disk(disk_img, disk_format)
            build_disk_xml(disk_img, disk_format, host_ip)

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
                raise error.TestNAError("Cann't create the domain")

        session = vm.wait_for_login()
        # Run the tests.
        if pm_enabled:
            # Makesure the guest agent is started
            check_vm_guestagent(session)
            # Run dompmsuspend command.
            test_pmsuspend(vm_name)
        if transport:
            # Check qemu-kvm command line
            cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
            if transport == "tcp":
                cmd += " | grep gluster.*format=%s" % disk_format
            else:
                cmd += " | grep gluster+%s.*format=%s" % (transport, disk_format)
            if utils.run(cmd, ignore_status=True).exit_status:
                raise error.TestFail("Can't see gluster option '%s' "
                                     "in command line" % cmd)

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
        if default_pool:
            utils.run("umount %s" % default_pool)
        if gluster_disk:
            libvirt.setup_or_cleanup_gluster(False, vol_name, brick_path)
