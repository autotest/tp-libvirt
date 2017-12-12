import os
import re
import ast
import time
import logging

import aexpect

from autotest.client.shared import error
from autotest.client import utils

from virttest import remote
from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_libguestfs
from virttest import utils_libvirtd
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface

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
        source = ast.literal_eval(iface_source)
        if source:
            iface.source = source
        iface.model = iface_model if iface_model else "virtio"
        iface.mac_address = iface_mac
        driver_dict = {}
        driver_host = {}
        driver_guest = {}
        if iface_driver:
            driver_dict = ast.literal_eval(iface_driver)
        if iface_driver_host:
            driver_host = ast.literal_eval(iface_driver_host)
        if iface_driver_guest:
            driver_guest = ast.literal_eval(iface_driver_guest)
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
        del iface.source
        source = ast.literal_eval(iface_source)
        if source:
            net_ifs = utils_net.get_net_if(state="UP")
            # Check source device is valid or not,
            # if it's not in host interface list, try to set
            # source device to first active interface of host
            if (iface.type_name == "direct" and
                    source.has_key('dev') and
                    source['dev'] not in net_ifs):
                logging.warn("Source device %s is not a interface"
                             " of host, reset to %s",
                             source['dev'], net_ifs[0])
                source['dev'] = net_ifs[0]
            iface.source = source
        backend = ast.literal_eval(iface_backend)
        if backend:
            iface.backend = backend
        driver_dict = {}
        driver_host = {}
        driver_guest = {}
        if iface_driver:
            driver_dict = ast.literal_eval(iface_driver)
        if iface_driver_host:
            driver_host = ast.literal_eval(iface_driver_host)
        if iface_driver_guest:
            driver_guest = ast.literal_eval(iface_driver_guest)
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
            cmd = ("cp -fZ {0} {1} && chown {2}:{2} {1}"
                   "".format(disk_source, dst_disk, unprivileged_user))
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
            driver_dict = ast.literal_eval(iface_driver)
        for driver_opt in driver_dict.keys():
            if not driver_dict[driver_opt] == iface.driver.driver_attr[driver_opt]:
                raise error.TestFail("Can't see driver option %s=%s in vm xml"
                                     % (driver_opt, driver_dict[driver_opt]))
        if iface_target:
            if (not iface.target.has_key("dev") or
                    not iface.target["dev"].startswith(iface_target)):
                raise error.TestFail("Can't see device target dev in vm xml")
            # Check macvtap mode by ip link command
            if iface_target == "macvtap" and iface.source.has_key("mode"):
                cmd = "ip -d link show %s" % iface.target["dev"]
                output = utils.run(cmd).stdout
                logging.debug("ip link output: %s", output)
                mode = iface.source["mode"]
                if mode == "passthrough":
                    mode = "passthru"
                if not re.search("macvtap\s+mode %s" % mode, output):
                    raise error.TestFail("Failed to verify macvtap mode")

    def run_cmdline_test(iface_mac):
        """
        Test for qemu-kvm command line options
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        ret = utils.run(cmd)
        logging.debug("Command line %s", ret.stdout)
        if test_vhost_net:
            if not ret.stdout.count("vhost=on") and not rm_vhost_driver:
                raise error.TestFail("Can't see vhost options in"
                                     " qemu-kvm command line")

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
            iface_driver_dict = ast.literal_eval(iface_driver)
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
            driver_dict.update(ast.literal_eval(iface_driver_host))
        # Test <driver><guest/><driver> xml options.
        if iface_driver_guest:
            driver_dict.update(ast.literal_eval(iface_driver_guest))

        for driver_opt in driver_dict.keys():
            if (not cmd_opt.has_key(driver_opt) or
                    not cmd_opt[driver_opt] == driver_dict[driver_opt]):
                raise error.TestFail("Can't see option '%s=%s' in qemu-kvm "
                                     " command line" %
                                     (driver_opt, driver_dict[driver_opt]))
        if test_backend:
            guest_pid = ret.stdout.rsplit()[1]
            cmd = "lsof %s | grep %s" % (backend["tap"], guest_pid)
            if utils.system(cmd, ignore_status=True):
                raise error.TestFail("Guest process didn't open backend file"
                                     " %s" % backend["tap"])
            cmd = "lsof %s | grep %s" % (backend["vhost"], guest_pid)
            if utils.system(cmd, ignore_status=True):
                raise error.TestFail("Guest process didn't open backend file"
                                     " %s" % backend["tap"])

    def get_guest_ip(session, mac):
        """
        Wrapper function to get guest ip address
        """
        utils_net.restart_guest_network(session, mac)
        # Wait for IP address is ready
        utils_misc.wait_for(
            lambda: utils_net.get_guest_ip_addr(session, mac), 10)
        return utils_net.get_guest_ip_addr(session, mac)

    def check_user_network(session):
        """
        Check user network ip address on guest
        """
        vm_ips = []
        vm_ips.append(get_guest_ip(session, iface_mac_old))
        if attach_device:
            vm_ips.append(get_guest_ip(session, iface_mac))
        logging.debug("IP address on guest: %s", vm_ips)
        if len(vm_ips) != len(set(vm_ips)):
            raise error.TestFail("Duplicated IP address on guest. "
                                 "Check bug: https://bugzilla.redhat."
                                 "com/show_bug.cgi?id=1147238")

        for vm_ip in vm_ips:
            if vm_ip is None or not vm_ip.startswith("10.0.2."):
                raise error.TestFail("Found wrong IP address"
                                     " on guest")
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
        username = params.get("username")
        password = params.get("password")
        src_addr = ast.literal_eval(iface_source)['address']
        add_session = additional_vm.wait_for_serial_login(username=username,
                                                          password=password)
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
            vm_ip = get_guest_ip(vms_sess_dict[vms], vm_mac)
            if not vm_ip:
                raise error.TestFail("Can't get multicast ip"
                                     " address on guest")
            vms_ip_dict.update({vms: vm_ip})
        if len(set(vms_ip_dict.values())) != len(vms_sess_dict):
            raise error.TestFail("Got duplicated multicast ip address")
        logging.debug("Found ips on guest: %s", vms_ip_dict)

        # Run omping server on host
        if not utils_package.package_install(["omping"]):
            raise error.TestError("Failed to install omping"
                                  " on host")
        cmd = ("iptables -F;omping -m %s %s" %
               (src_addr, "192.168.122.1 %s" %
                ' '.join(vms_ip_dict.values())))
        # Run a backgroup job waiting for connection of client
        bgjob = utils.AsyncJob(cmd)

        # Run omping client on guests
        for vms in vms_sess_dict.keys():
            # omping should be installed first
            if not utils_package.package_install(["omping"], vms_sess_dict[vms]):
                raise error.TestError("Failed to install omping"
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
    unprivileged_user = params.get("unprivileged_user")

    # Interface specific attributes.
    iface_type = params.get("iface_type", "network")
    iface_source = params.get("iface_source", "{}")
    iface_driver = params.get("iface_driver")
    iface_model = params.get("iface_model", "virtio")
    iface_target = params.get("iface_target")
    iface_backend = params.get("iface_backend", "{}")
    iface_driver_host = params.get("iface_driver_host")
    iface_driver_guest = params.get("iface_driver_guest")
    attach_device = params.get("attach_iface_device")
    change_option = "yes" == params.get("change_iface_options", "no")
    update_device = "yes" == params.get("update_iface_device", "no")
    additional_guest = "yes" == params.get("additional_guest", "no")
    serial_login = "yes" == params.get("serial_login", "no")
    rm_vhost_driver = "yes" == params.get("rm_vhost_driver", "no")
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
    test_libvirtd = "yes" == params.get("test_libvirtd", "no")
    test_guest_ip = "yes" == params.get("test_guest_ip", "no")
    test_backend = "yes" == params.get("test_backend", "no")

    if iface_driver_host or iface_driver_guest or test_backend:
        if not libvirt_version.version_compare(1, 2, 8):
            raise error.TestNAError("Offloading/backend options not "
                                    "supported in this libvirt version")
    if iface_driver and "queues" in ast.literal_eval(iface_driver):
        if not libvirt_version.version_compare(1, 0, 6):
            raise error.TestNAError("Queues options not supported"
                                    " in this libvirt version")

    if unprivileged_user:
        if not libvirt_version.version_compare(1, 1, 1):
            raise error.TestNAError("qemu-bridge-helper not supported"
                                    " on this host")
        virsh_dargs["unprivileged_user"] = unprivileged_user
        # Create unprivileged user if needed
        cmd = ("grep {0} /etc/passwd || "
               "useradd {0}".format(unprivileged_user))
        utils.run(cmd)
        # Need another disk image for unprivileged user to access
        dst_disk = "/tmp/%s.img" % unprivileged_user

    # Destroy VM first
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    iface_mac_old = vm_xml.VMXML.get_first_mac_by_name(vm_name)
    # iface_mac will update if attach a new interface
    iface_mac = iface_mac_old
    # Additional vm for test
    additional_vm = None
    libvirtd = utils_libvirtd.Libvirtd()

    try:
        # Build the xml and run test.
        try:
            # Prepare interface backend files
            if test_backend:
                if not os.path.exists("/dev/vhost-net"):
                    utils.run("modprobe vhost-net")
                backend = ast.literal_eval(iface_backend)
                backend_tap = "/dev/net/tun"
                backend_vhost = "/dev/vhost-net"
                if not backend:
                    backend["tap"] = backend_tap
                    backend["vhost"] = backend_vhost
                if not start_error:
                    # Create backend files for normal test
                    if not os.path.exists(backend["tap"]):
                        os.rename(backend_tap, backend["tap"])
                    if not os.path.exists(backend["vhost"]):
                        os.rename(backend_vhost, backend["vhost"])
            # Edit the interface xml.
            if change_option:
                modify_iface_xml(update=False)

            if rm_vhost_driver:
                # Check vhost driver.
                kvm_version = os.uname()[2]
                driver_path = ("/lib/modules/%s/kernel/drivers/vhost/"
                               "vhost_net.ko" % kvm_version)
                driver_backup = driver_path + ".bak"
                cmd = ("modprobe -r {0}; lsmod | "
                       "grep {0}".format("vhost_net"))
                if not utils.system(cmd, ignore_status=True):
                    raise error.TestError("Failed to remove vhost_net driver")
                # Move the vhost_net driver
                if os.path.exists(driver_path):
                    os.rename(driver_path, driver_backup)
            else:
                # Load vhost_net driver by default
                cmd = "modprobe vhost_net"
                utils.system(cmd)

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
                timeout = params.get("clone_timeout", 360)
                utils_libguestfs.virt_clone_cmd(vm_name, guest_name,
                                                True, timeout=timeout)
                additional_vm = vm.clone(guest_name)
                additional_vm.start()
                # additional_vm.wait_for_login()

            # Start the VM.
            if unprivileged_user:
                virsh.start(vm_name, **virsh_dargs)
                cmd = ("su - %s -c 'virsh console %s'"
                       % (unprivileged_user, vm_name))
                session = aexpect.ShellSession(cmd)
                session.sendline()
                remote.handle_prompts(session, params.get("username"),
                                      params.get("password"), r"[\#\$]\s*$", 30)
                # Get ip address on guest
                if not get_guest_ip(session, iface_mac):
                    raise error.TestError("Can't get ip address on guest")
            else:
                # Will raise VMStartError exception if start fails
                vm.start()
                if serial_login:
                    session = vm.wait_for_serial_login()
                else:
                    session = vm.wait_for_login()
            if start_error:
                raise error.TestFail("VM started unexpectedly")

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
                    ifname_guest = utils_net.get_linux_ifname(
                        session, iface_mac)
                    check_offloads_option(
                        ifname_guest, ast.literal_eval(
                            iface_driver_host), session)
                if iface_driver_guest:
                    ifname_host = libvirt.get_ifname_host(vm_name,
                                                          iface_mac)
                    check_offloads_option(
                        ifname_host, ast.literal_eval(iface_driver_guest))

            if test_iface_user:
                # Test user type network
                check_user_network(session)
            if test_iface_mcast:
                # Test mcast type network
                check_mcast_network(session)
            # Check guest ip address
            if test_guest_ip:
                if not get_guest_ip(session, iface_mac):
                    raise error.TestFail("Guest can't get a"
                                         " valid ip address")

            session.close()
            # Restart libvirtd and guest, then test again
            if test_libvirtd:
                libvirtd.restart()
                vm.destroy()
                vm.start()
                if test_option_xml:
                    run_xml_test(iface_mac)

            # Detach hot/cold-plugged interface at last
            if attach_device:
                ret = virsh.detach_device(vm_name, iface_xml_obj.xml,
                                          flagstr="", ignore_status=True)
                libvirt.check_exit_status(ret)

        except virt_vm.VMStartError as e:
            logging.info(str(e))
            if not start_error:
                raise error.TestFail('VM failed to start\n%s' % e)

    finally:
        # Recover VM.
        logging.info("Restoring vm...")
        # Restore interface backend files
        if test_backend:
            if not os.path.exists(backend_tap):
                os.rename(backend["tap"], backend_tap)
            if not os.path.exists(backend_vhost):
                os.rename(backend["vhost"], backend_vhost)
        if rm_vhost_driver:
            # Restore vhost_net driver
            if os.path.exists(driver_backup):
                os.rename(driver_backup, driver_path)
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
