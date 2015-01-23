import os
import re
import time
import logging
from autotest.client.shared import error
from autotest.client import utils
from virttest import aexpect
from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_libguestfs
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml import vm_xml, xcepts
from provider import libvirt_version


def run(test, params, env):
    """
    Test interafce xml options.

    1.Prepare test environment,destroy or suspend a VM.
    2.Edit xml and start the domain.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': False}

    def create_iface_xml(iface_mac):
        """
        Create interface xml file
        """
        iface = Interface(type_name=iface_type)
        source = eval(iface_source)
        if iface_source:
            iface.source = source
        iface.model = iface_model if iface_model else "virtio"
        iface.mac_address = iface_mac
        driver_dict = {}
        driver_host = {}
        driver_guest = {}
        if iface_driver:
            driver_dict = eval(iface_driver)
        if iface_driver_host:
            driver_host = eval(iface_driver_host)
        if iface_driver_guest:
            driver_guest = eval(iface_driver_guest)
        iface.driver = iface.new_driver(driver_attr=driver_dict,
                                        driver_host=driver_host,
                                        driver_guest=driver_guest)
        logging.debug("Create new interface xml: %s", iface)
        return iface

    def modify_iface_xml(update, status_error=False):
        """
        Modify interface xml options
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        xml_devices = vmxml.devices
        iface_index = xml_devices.index(
            xml_devices.by_device_tag("interface")[0])
        iface = xml_devices[iface_index]
        if iface_model:
            iface.model = iface_model
        else:
            del iface.model
        if iface_type:
            iface.type_name = iface_type
        source = eval(iface_source)
        if iface_source:
            iface.source = source
        else:
            del iface.source
        driver_dict = {}
        driver_host = {}
        driver_guest = {}
        if iface_driver:
            driver_dict = eval(iface_driver)
        if iface_driver_host:
            driver_host = eval(iface_driver_host)
        if iface_driver_guest:
            driver_guest = eval(iface_driver_guest)
        iface.driver = iface.new_driver(driver_attr=driver_dict,
                                        driver_host=driver_host,
                                        driver_guest=driver_guest)
        if iface.address:
            del iface.address

        logging.debug("New interface xml file: %s", iface)
        if unprivileged_user:
            # Create disk image for unprivileged user
            disk_index = xml_devices.index(
                xml_devices.by_device_tag("disk")[0])
            disk_xml = xml_devices[disk_index]
            logging.debug("source: %s", disk_xml.source)
            disk_source = disk_xml.source.attrs["file"]
            cmd = ("cp -f %s %s && chmod a+rw %s" %
                   (disk_source, dst_disk, dst_disk))
            utils.run(cmd)
            disk_xml.source = disk_xml.new_disk_source(
                attrs={"file": dst_disk})
            vmxml.devices = xml_devices
            # Remove all channels to avoid of permission problem
            channels = vmxml.get_devices(device_type="channel")
            for channel in channels:
                vmxml.del_device(channel)

            vmxml.xmltreefile.write()
            logging.debug("New VM xml: %s", vmxml)
            utils.run("chmod a+rw %s" % vmxml.xml)
            virsh.define(vmxml.xml, **virsh_dargs)
        # Try to modify interface xml by update-device or edit xml
        elif update:
            iface.xmltreefile.write()
            ret = virsh.update_device(vm_name, iface.xml,
                                      ignore_status=True)
            libvirt.check_exit_status(ret, status_error)
        else:
            vmxml.devices = xml_devices
            vmxml.xmltreefile.write()
            vmxml.sync()

    def check_offloads_option(if_name, driver_options, session=None):
        """
        Check interface offloads by ethtool output
        """
        offloads = {"csum": "tx-checksumming",
                    "gso": "generic-segmentation-offload",
                    "tso4": "tcp-segmentation-offload",
                    "tso6": "tx-tcp6-segmentation",
                    "ecn": "tx-tcp-ecn-segmentation",
                    "ufo": "udp-fragmentation-offload"}
        if session:
            ret, output = session.cmd_status_output("ethtool -k %s | head"
                                                    " -18" % if_name)
        else:
            out = utils.run("ethtool -k %s | head -18" % if_name)
            ret, output = out.exit_status, out.stdout
        if ret:
            raise error.TestFail("ethtool return error code")
        logging.debug("ethtool output: %s", output)
        for offload in driver_options.keys():
            if offloads.has_key(offload):
                if (output.count(offloads[offload]) and
                        not output.count("%s: %s" % (
                            offloads[offload], driver_options[offload]))):
                    raise error.TestFail("offloads option %s: %s isn't"
                                         " correct in ethtool output" %
                                         (offloads[offload],
                                          driver_options[offload]))

    def run_xml_test(iface_mac):
        """
        Test for interface options in vm xml
        """
        # Get the interface object according the mac address
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_devices = vmxml.get_devices(device_type="interface")
        iface = None
        for iface_dev in iface_devices:
            if iface_dev.mac_address == iface_mac:
                iface = iface_dev
        if not iface:
            raise error.TestFail("Can't find interface with mac"
                                 " '%s' in vm xml" % iface_mac)
        driver_dict = {}
        if iface_driver:
            driver_dict = eval(iface_driver)
        for driver_opt in driver_dict.keys():
            if not driver_dict[driver_opt] == iface.driver.driver_attr[driver_opt]:
                raise error.TestFail("Can't see driver option %s=%s in vm xml"
                                     % (driver_opt, driver_dict[driver_opt]))

    def run_cmdline_test(iface_mac):
        """
        Test for qemu-kvm command line options
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        if test_vhost_net:
            cmd += " | grep 'vhost=on'"
        ret = utils.run(cmd)
        if ret.exit_status:
            raise error.TestFail("Can't parse qemu-kvm command line")

        logging.debug("Command line %s", ret.stdout)
        if iface_model == "virtio":
            model_option = "device virtio-net-pci"
        else:
            model_option = "device rtl8139"
        iface_cmdline = re.findall(r"%s,(.+),mac=%s" %
                                   (model_option, iface_mac), ret.stdout)
        if not iface_cmdline:
            raise error.TestFail("Can't see %s with mac %s in command"
                                 " line" % (model_option, iface_mac))

        cmd_opt = {}
        for opt in iface_cmdline[0].split(','):
            tmp = opt.rsplit("=")
            cmd_opt[tmp[0]] = tmp[1]
        logging.debug("Command line options %s", cmd_opt)

        driver_dict = {}
        # Test <driver> xml options.
        if iface_driver:
            iface_driver_dict = eval(iface_driver)
            for driver_opt in iface_driver_dict.keys():
                if driver_opt == "name":
                    continue
                elif driver_opt == "txmode":
                    if iface_driver_dict["txmode"] == "iothread":
                        driver_dict["tx"] = "bh"
                    else:
                        driver_dict["tx"] = iface_driver_dict["txmode"]
                elif driver_opt == "queues":
                    driver_dict["mq"] = "on"
                    driver_dict["vectors"] = str(int(
                        iface_driver_dict["queues"]) * 2 + 2)
                else:
                    driver_dict[driver_opt] = iface_driver_dict[driver_opt]
        # Test <driver><host/><driver> xml options.
        if iface_driver_host:
            driver_dict.update(eval(iface_driver_host))
        # Test <driver><guest/><driver> xml options.
        if iface_driver_guest:
            driver_dict.update(eval(iface_driver_guest))

        for driver_opt in driver_dict.keys():
            if (not cmd_opt.has_key(driver_opt) or
                    not cmd_opt[driver_opt] == driver_dict[driver_opt]):
                raise error.TestFail("Can't see option '%s=%s' in qemu-kvm "
                                     " command line" %
                                     (driver_opt, driver_dict[driver_opt]))

    def check_user_network(session):
        """
        Check user network ip address on guest
        """
        vm_ips = []
        utils_net.restart_guest_network(session)
        wait_func = lambda: utils_net.get_guest_ip_addr(session,
                                                        iface_mac_old)
        # Wait for IP address is ready
        utils_misc.wait_for(wait_func, 10)
        vm_ips.append(utils_net.get_guest_ip_addr(session,
                                                  iface_mac_old))
        if attach_device:
            # Get the additional interafce ip address
            wait_func = lambda: utils_net.get_guest_ip_addr(session,
                                                            iface_mac)
            # Wait for IP address is ready
            utils_misc.wait_for(wait_func, 10)
            vm_ips.append(utils_net.get_guest_ip_addr(session,
                                                      iface_mac))
        logging.debug("IP address on guest: %s", vm_ips)
        if len(vm_ips) != len(set(vm_ips)):
            raise error.TestFail("Duplicated IP address on guest. "
                                 "Check bug: https://bugzilla.redhat."
                                 "com/show_bug.cgi?id=1147238")

        for vm_ip in vm_ips:
            if vm_ip is None or not vm_ip.startswith("10.0.2."):
                raise error.TestFail("Found wrong IP address"
                                     "on guest")
        # Check gateway address
        gateway = utils_net.get_net_gateway(session.cmd_output)
        if gateway != "10.0.2.2":
            raise error.TestFail("The gateway on guest is not"
                                 " right")
        # Check dns server address
        ns_list = utils_net.get_net_nameserver(session.cmd_output)
        if "10.0.2.3" not in ns_list:
            raise error.TestFail("The dns server can't be found"
                                 " on guest")

    def check_mcast_network(session):
        """
        Check multicast ip address on guests
        """
        src_addr = eval(iface_source)['address']
        add_session = additional_vm.wait_for_serial_login()
        vms_sess_dict = {vm_name: session,
                         additional_vm.name: add_session}

        # Check mcast address on host
        cmd = "netstat -g | grep %s" % src_addr
        if utils.run(cmd, ignore_status=True).exit_status:
            raise error.TestFail("Can't find multicast ip address"
                                 " on host")
        vms_ip_dict = {}
        # Get ip address on each guest
        for vms in vms_sess_dict.keys():
            vm_mac = vm_xml.VMXML.get_first_mac_by_name(vms)
            utils_net.restart_guest_network(vms_sess_dict[vms], vm_mac)
            vm_ip = utils_net.get_guest_ip_addr(vms_sess_dict[vms],
                                                vm_mac)
            if not vm_ip:
                raise error.TestFail("Can't get multicast ip"
                                     " address on guest")
            vms_ip_dict.update({vms: vm_ip})
        if len(set(vms_ip_dict.values())) != len(vms_sess_dict):
            raise error.TestFail("Got duplicated multicast ip address")
        logging.debug("Found ips on guest: %s", vms_ip_dict)

        # Run omping server on host
        if not utils_misc.yum_install(["omping"]):
            raise error.TestNAError("Failed to install omping"
                                    " on host")
        cmd = ("iptables -F;omping -m %s %s" %
               (src_addr, "192.168.122.1 %s" %
                ' '.join(vms_ip_dict.values())))
        # Run a backgroup job waiting for connection of client
        bgjob = utils.AsyncJob(cmd)

        # Run omping client on guests
        for vms in vms_sess_dict.keys():
            # omping should be installed first
            if not utils_misc.yum_install(["omping"], vms_sess_dict[vms]):
                raise error.TestNAError("Failed to install omping"
                                        " on guest")
            cmd = ("iptables -F; omping -c 5 -T 5 -m %s %s" %
                   (src_addr, "192.168.122.1 %s" %
                    vms_ip_dict[vms]))
            ret, output = vms_sess_dict[vms].cmd_status_output(cmd)
            logging.debug("omping ret: %s, output: %s", ret, output)
            if (not output.count('multicast, xmt/rcv/%loss = 5/5/0%') or
                    not output.count('unicast, xmt/rcv/%loss = 5/5/0%')):
                raise error.TestFail("omping failed on guest")
        # Kill the backgroup job
        bgjob.kill_func()

    status_error = "yes" == params.get("status_error", "no")
    start_error = "yes" == params.get("start_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    unprivileged_user = params.get("unprivileged_user")

    # Interface specific attributes.
    iface_type = params.get("iface_type", "network")
    iface_source = params.get("iface_source", "{}")
    iface_driver = params.get("iface_driver")
    iface_model = params.get("iface_model")
    iface_driver_host = params.get("iface_driver_host")
    iface_driver_guest = params.get("iface_driver_guest")
    attach_device = params.get("attach_iface_device")
    change_option = "yes" == params.get("change_iface_options", "no")
    update_device = "yes" == params.get("update_iface_device", "no")
    additional_guest = "yes" == params.get("additional_guest", "no")
    serail_login = "yes" == params.get("serail_login", "no")
    test_option_cmd = "yes" == params.get(
                      "test_iface_option_cmd", "no")
    test_option_xml = "yes" == params.get(
                      "test_iface_option_xml", "no")
    test_vhost_net = "yes" == params.get(
                     "test_vhost_net", "no")
    test_option_offloads = "yes" == params.get(
                           "test_option_offloads", "no")
    test_iface_user = "yes" == params.get(
                      "test_iface_user", "no")
    test_iface_mcast = "yes" == params.get(
                       "test_iface_mcast", "no")

    if iface_driver_host or iface_driver_guest:
        if not libvirt_version.version_compare(1, 2, 8):
            raise error.TestNAError("Offloading options not supported "
                                    " in this libvirt version")

    if unprivileged_user:
        virsh_dargs["unprivileged_user"] = unprivileged_user
        # Create unprivileged user if needed
        cmd = ("grep {0} /etc/passwd || "
               "useradd {0}".format(unprivileged_user))
        utils.run(cmd)
        # Need another disk image for unprivileged user to access
        dst_disk = "/tmp/%s.img" % unprivileged_user

    if serail_login:
        # Set serial console for serial login
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        # Set console option
        vm.set_kernel_console("ttyS0", "115200")
        # Shutdown here for sync fs
        vm.shutdown()
    else:
        if vm.is_alive():
            vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    iface_mac_old = vm_xml.VMXML.get_first_mac_by_name(vm_name)
    # iface_mac will update if attach a new interface
    iface_mac = iface_mac_old
    # Additional vm for test
    additional_vm = None

    try:
        # Build the xml and run test.
        try:
            # Edit the interface xml.
            if change_option:
                modify_iface_xml(update=False)
            # Check vhost driver.
            if test_vhost_net:
                if not os.path.exists("/dev/vhost-net"):
                    raise error.TestError("Can't find vhost-net dev")
                cmd = ("modprobe -r {0}; lsmod | "
                       "grep {0}".format("vhost_net"))
                if not utils.system(cmd, ignore_status=True):
                    raise error.TestError("Can't remove vhost_net driver")

            # Attach a interface when vm is shutoff
            if attach_device == 'config':
                iface_mac = utils_net.generate_mac_address_simple()
                iface_xml_obj = create_iface_xml(iface_mac)
                iface_xml_obj.xmltreefile.write()
                ret = virsh.attach_device(vm_name, iface_xml_obj.xml,
                                          flagstr="--config",
                                          ignore_status=True)
                libvirt.check_exit_status(ret)

            # Clone additional vm
            if additional_guest:
                guest_name = "%s_%s" % (vm_name, '1')
                # Clone additional guest
                utils_libguestfs.virt_clone_cmd(vm_name, guest_name, True)
                additional_vm = vm.clone(guest_name)
                additional_vm.start()
                #additional_vm.wait_for_login()

            # Start the VM.
            if unprivileged_user:
                virsh.start(vm_name, **virsh_dargs)
                cmd = ("su - %s -c 'virsh console %s'"
                       % (unprivileged_user, vm_name))
                session = aexpect.ShellSession(cmd)
                session.sendline()
                remote.handle_prompts(session, params.get("username"),
                                      params.get("password"), "[\#\$]", 30)
                utils_net.restart_guest_network(session)
                # Get ip address on guest
                if not utils_net.get_guest_ip_addr(session, iface_mac):
                    raise error.TestError("Can't get ip address on guest")
            else:
                # Will raise VMStartError exception if start fails
                vm.start()
                if serail_login:
                    session = vm.wait_for_serial_login()
                else:
                    session = vm.wait_for_login()
            if start_error:
                raise error.TestFail("VM started unexpectedly")

            if test_vhost_net:
                if utils.system("lsmod | grep vhost_net", ignore_status=True):
                    raise error.TestFail("vhost_net module can't be"
                                         " loaded automatically")

            # Attach a interface when vm is running
            if attach_device == 'live':
                iface_mac = utils_net.generate_mac_address_simple()
                iface_xml_obj = create_iface_xml(iface_mac)
                iface_xml_obj.xmltreefile.write()
                ret = virsh.attach_device(vm_name, iface_xml_obj.xml,
                                          flagstr="--live",
                                          ignore_status=True)
                libvirt.check_exit_status(ret)
                # Need sleep here for attachment take effect
                time.sleep(5)

            # Update a interface options
            if update_device:
                modify_iface_xml(update=True, status_error=status_error)

            # Run tests for qemu-kvm command line options
            if test_option_cmd:
                run_cmdline_test(iface_mac)
            # Run tests for vm xml
            if test_option_xml:
                run_xml_test(iface_mac)
            # Run tests for offloads options
            if test_option_offloads:
                if iface_driver_host:
                    ifname_guest = utils_net.get_linux_ifname(session, iface_mac)
                    check_offloads_option(ifname_guest,
                                          eval(iface_driver_host), session)
                if iface_driver_guest:
                    ifname_host = libvirt.get_ifname_host(vm_name, iface_mac)
                    check_offloads_option(ifname_host, eval(iface_driver_guest))

            if test_iface_user:
                # Test user type network
                check_user_network(session)
            if test_iface_mcast:
                # Test mcast type network
                check_mcast_network(session)

            # Detach hot/cold-plugged interface at last
            if attach_device:
                ret = virsh.detach_device(vm_name, iface_xml_obj.xml,
                                          flagstr="", ignore_status=True)
                libvirt.check_exit_status(ret)

            session.close()
        except virt_vm.VMStartError, e:
            logging.info(str(e))
            if start_error:
                pass
            else:
                raise error.TestFail('VM Failed to start for some reason!')
        except xcepts.LibvirtXMLError, e:
            logging.info(str(e))
            if define_error:
                pass
            else:
                raise error.TestFail("Failed to define VM")

    finally:
        # Recover VM.
        logging.info("Restoring vm...")
        if unprivileged_user:
            virsh.remove_domain(vm_name, "--remove-all-storage",
                                **virsh_dargs)
        if additional_vm:
            virsh.remove_domain(additional_vm.name,
                                "--remove-all-storage")
            # Kill all omping server process on host
            utils.system("pidof omping && killall omping",
                         ignore_status=True)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
