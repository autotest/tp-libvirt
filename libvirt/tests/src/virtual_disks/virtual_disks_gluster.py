import os
import logging
import time
from autotest.client.shared import error
from autotest.client import utils
from virttest import virsh
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk


def run(test, params, env):
    """
    Test multiple disks attachment.

    1.Prepare test environment,destroy or suspend a VM.
    2.Perform 'qemu-img create' operation.
    3.Edit disks xml and start the domain.
    4.Perform test operation.
    5.Recover test environment.
    6.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def check_vm_guestagent(session):
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
        for i in range(30):
            if vm.state() == "shut off":
                vm.start()
                break
            elif i == 29:
                raise error.TestFail("vm is still alive after S4 operation")
            time.sleep(1)
        vm.wait_for_login()
        # Wait 5 seconds for starting qemu-ga service
        time.sleep(5)
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
    pool_name = params.get("pool_name")
    transport = params.get("transport", "")
    default_pool = params.get("default_pool", "")
    brick_path = os.path.join(test.virtdir, pool_name)

    pre_vm_state = params.get("pre_vm_state", "running")

    # Destroy VM first.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Build new vm xml.
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if pm_enabled:
            pm_xml = vm_xml.VMPMXML()
            pm_xml.mem_enabled = "yes"
            pm_xml.disk_enabled = "yes"
            vmxml.pm = pm_xml
        if gluster_disk:
            # Delete the origin disk first.
            xml_devices = vmxml.devices
            disk_index = xml_devices.index(xml_devices.by_device_tag("disk")[0])
            disk = xml_devices[disk_index]
            vmxml.del_device(disk)
            image_name = params.get("image_name")
            image_format = params.get("image_format")
            image_source = os.path.join(test.virtdir, "data/%s.%s"
                                        % (image_name, image_format))
            logging.debug("image source:%s" % image_source)
            # Setup gluster.
            host_ip = libvirt.setup_or_cleanup_gluster(True, vol_name,
                                                       brick_path, pool_name)
            logging.debug("host ip: %s " % host_ip)
            dist_img = "gluster.%s" % disk_format
            image_info = utils_misc.get_image_info(image_source)
            if image_info["format"] == disk_format:
                disk_cmd = ("cp -f %s /mnt/%s" % (image_source, dist_img))
            else:
                # Convert the disk format
                disk_cmd = ("qemu-img convert -f %s -O %s %s /mnt/%s" %
                            (image_info["format"], disk_format, image_source, dist_img))
            # Mount the gluster disk and create the image.
            utils.run("mount -t glusterfs %s:%s /mnt; %s; umount /mnt"
                      % (host_ip, vol_name, disk_cmd))
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
                source_dict = {"file": "%s/%s" % (default_pool, dist_img)}
                disk_xml.source = disk_xml.new_disk_source(
                    **{"attrs": source_dict})
            else:
                source_dict = {"protocol": "gluster",
                               "name": "%s/%s" % (vol_name, dist_img)}
                host_dict = {"name": host_ip, "port": "24007"}
                if transport:
                    host_dict.update({"transport": transport})
                disk_xml.source = disk_xml.new_disk_source(
                    **{"attrs": source_dict, "hosts": [host_dict]})
            # Add the new disk xml.
            vmxml.add_device(disk_xml)

        # After compose the disk xml, redefine the VM xml.
        vmxml.sync()
        if pm_enabled:
            vm_xml.VMXML.set_agent_channel(vm_name)

        # Turn VM into certain state.
        if pre_vm_state == "running":
            logging.info("Starting %s..." % vm_name)
            if vm.is_dead():
                vm.start()
        elif pre_vm_state == "transient":
            logging.info("Creating %s..." % vm_name)
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
                raise error.TestFail("Can't see gluster option in command line")

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
