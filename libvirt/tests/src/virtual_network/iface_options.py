import os
import re
import ast
import time
import logging

import aexpect

from avocado.utils import process

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
from virttest.libvirt_xml import xcepts

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

    if not utils_package.package_install(["lsof"]):
        test.cancel("Failed to install dependency package lsof"
                    " on host")

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
                    'dev' in source and
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
            process.run(cmd, shell=True)
            disk_xml.source = disk_xml.new_disk_source(
                attrs={"file": dst_disk})
            vmxml.devices = xml_devices
            # Remove all channels to avoid of permission problem
            channels = vmxml.get_devices(device_type="channel")
            for channel in channels:
                vmxml.del_device(channel)

            vmxml.xmltreefile.write()
            logging.debug("New VM xml: %s", vmxml)
            process.run("chmod a+rw %s" % vmxml.xml, shell=True)
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
            try:
                vmxml.sync()
            except xcepts.LibvirtXMLError as e:
                if not define_error:
                    test.fail("Define VM fail: %s" % e)

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
            out = process.run("ethtool -k %s | head -18" % if_name, shell=True)
            ret, output = out.exit_status, out.stdout_text
        if ret:
            test.fail("ethtool return error code")
        logging.debug("ethtool output: %s", output)
        for offload in list(driver_options.keys()):
            if offload in offloads:
                if (output.count(offloads[offload]) and
                    not output.count("%s: %s" % (
                        offloads[offload], driver_options[offload]))):
                    test.fail("offloads option %s: %s isn't"
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
            test.fail("Can't find interface with mac"
                      " '%s' in vm xml" % iface_mac)
        driver_dict = {}
        if iface_driver:
            driver_dict = ast.literal_eval(iface_driver)
        for driver_opt in list(driver_dict.keys()):
            if not driver_dict[driver_opt] == iface.driver.driver_attr[driver_opt]:
                test.fail("Can't see driver option %s=%s in vm xml"
                          % (driver_opt, driver_dict[driver_opt]))
        if iface_target:
            if ("dev" not in iface.target or
                    not iface.target["dev"].startswith(iface_target)):
                test.fail("Can't see device target dev in vm xml")
            # Check macvtap mode by ip link command
            if iface_target == "macvtap" and "mode" in iface.source:
                cmd = "ip -d link show %s" % iface.target["dev"]
                output = process.run(cmd, shell=True).stdout_text
                logging.debug("ip link output: %s", output)
                mode = iface.source["mode"]
                if mode == "passthrough":
                    mode = "passthru"
                if not re.search("macvtap\s+mode %s" % mode, output):
                    test.fail("Failed to verify macvtap mode")

    def run_cmdline_test(iface_mac):
        """
        Test for qemu-kvm command line options
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        ret = process.run(cmd, shell=True)
        logging.debug("Command line %s", ret.stdout_text)
        if test_vhost_net:
            if not ret.stdout_text.count("vhost=on") and not rm_vhost_driver:
                test.fail("Can't see vhost options in"
                          " qemu-kvm command line")

        if iface_model == "virtio":
            model_option = "device virtio-net-pci"
        else:
            model_option = "device rtl8139"
        iface_cmdline = re.findall(r"%s,(.+),mac=%s" %
                                   (model_option, iface_mac), ret.stdout_text)
        if not iface_cmdline:
            test.fail("Can't see %s with mac %s in command"
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
            for driver_opt in list(iface_driver_dict.keys()):
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

        for driver_opt in list(driver_dict.keys()):
            if (driver_opt not in cmd_opt or
                    not cmd_opt[driver_opt] == driver_dict[driver_opt]):
                test.fail("Can't see option '%s=%s' in qemu-kvm "
                          " command line" %
                          (driver_opt, driver_dict[driver_opt]))
            logging.info("Find %s=%s in qemu-kvm command line" %
                         (driver_opt, driver_dict[driver_opt]))
        if test_backend:
            guest_pid = ret.stdout_text.rsplit()[1]
            cmd = "lsof %s | grep %s" % (backend["tap"], guest_pid)
            if process.system(cmd, ignore_status=True, shell=True):
                test.fail("Guest process didn't open backend file"
                          " %s" % backend["tap"])
            cmd = "lsof %s | grep %s" % (backend["vhost"], guest_pid)
            if process.system(cmd, ignore_status=True, shell=True):
                test.fail("Guest process didn't open backend file"
                          " %s" % backend["vhost"])

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
            test.fail("Duplicated IP address on guest. "
                      "Check bug: https://bugzilla.redhat."
                      "com/show_bug.cgi?id=1147238")

        for vm_ip in vm_ips:
            if vm_ip is None or not vm_ip.startswith("10.0.2."):
                test.fail("Found wrong IP address"
                          " on guest")
        # Check gateway address
        gateway = utils_net.get_net_gateway(session.cmd_output)
        if gateway != "10.0.2.2":
            test.fail("The gateway on guest is not"
                      " right")
        # Check dns server address
        ns_list = utils_net.get_net_nameserver(session.cmd_output)
        if "10.0.2.3" not in ns_list:
            test.fail("The dns server can't be found"
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
        if process.run(cmd, ignore_status=True, shell=True).exit_status:
            test.fail("Can't find multicast ip address"
                      " on host")
        vms_ip_dict = {}
        # Get ip address on each guest
        for vms in list(vms_sess_dict.keys()):
            vm_mac = vm_xml.VMXML.get_first_mac_by_name(vms)
            vm_ip = get_guest_ip(vms_sess_dict[vms], vm_mac)
            if not vm_ip:
                test.fail("Can't get multicast ip"
                          " address on guest")
            vms_ip_dict.update({vms: vm_ip})
        if len(set(vms_ip_dict.values())) != len(vms_sess_dict):
            test.fail("Got duplicated multicast ip address")
        logging.debug("Found ips on guest: %s", vms_ip_dict)

        # Run omping server on host
        if not utils_package.package_install(["omping"]):
            test.error("Failed to install omping"
                       " on host")
        cmd = ("iptables -F;omping -m %s %s" %
               (src_addr, "192.168.122.1 %s" %
                ' '.join(list(vms_ip_dict.values()))))
        # Run a backgroup job waiting for connection of client
        bgjob = utils_misc.AsyncJob(cmd)

        # Run omping client on guests
        for vms in list(vms_sess_dict.keys()):
            # omping should be installed first
            if not utils_package.package_install(["omping"], vms_sess_dict[vms]):
                test.error("Failed to install omping"
                           " on guest")
            cmd = ("iptables -F; omping -c 5 -T 5 -m %s %s" %
                   (src_addr, "192.168.122.1 %s" %
                    vms_ip_dict[vms]))
            ret, output = vms_sess_dict[vms].cmd_status_output(cmd)
            logging.debug("omping ret: %s, output: %s", ret, output)
            if (not output.count('multicast, xmt/rcv/%loss = 5/5/0%') or
                    not output.count('unicast, xmt/rcv/%loss = 5/5/0%')):
                test.fail("omping failed on guest")
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
    iface_model = params.get("iface_model", "virtio")
    iface_target = params.get("iface_target")
    iface_backend = params.get("iface_backend", "{}")
    iface_driver_host = params.get("iface_driver_host")
    iface_driver_guest = params.get("iface_driver_guest")
    attach_device = params.get("attach_iface_device")
    expect_tx_size = params.get("expect_tx_size")
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
    check_guest_trans = "yes" == params.get("check_guest_trans", "no")

    if iface_driver_host or iface_driver_guest or test_backend:
        if not libvirt_version.version_compare(1, 2, 8):
            test.cancel("Offloading/backend options not "
                        "supported in this libvirt version")
    if iface_driver and "queues" in ast.literal_eval(iface_driver):
        if not libvirt_version.version_compare(1, 0, 6):
            test.cancel("Queues options not supported"
                        " in this libvirt version")

    if unprivileged_user:
        if not libvirt_version.version_compare(1, 1, 1):
            test.cancel("qemu-bridge-helper not supported"
                        " on this host")
        virsh_dargs["unprivileged_user"] = unprivileged_user
        # Create unprivileged user if needed
        cmd = ("grep {0} /etc/passwd || "
               "useradd {0}".format(unprivileged_user))
        process.run(cmd, shell=True)
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
                    process.run("modprobe vhost-net", shell=True)
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
                if define_error:
                    return

            if rm_vhost_driver:
                # remove vhost driver on host and
                # the character file /dev/vhost-net
                cmd = ("modprobe -r {0}; "
                       "rm -f /dev/vhost-net".format("vhost_net"))
                if process.system(cmd, ignore_status=True, shell=True):
                    test.error("Failed to remove vhost_net driver")
            else:
                # Load vhost_net driver by default
                cmd = "modprobe vhost_net"
                process.system(cmd, shell=True)

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
                    test.error("Can't get ip address on guest")
            else:
                # Will raise VMStartError exception if start fails
                vm.start()
                if serial_login:
                    session = vm.wait_for_serial_login()
                else:
                    session = vm.wait_for_login()
            if start_error:
                test.fail("VM started unexpectedly")

            # Attach a interface when vm is running
            if attach_device == 'live':
                iface_mac = utils_net.generate_mac_address_simple()
                iface_xml_obj = create_iface_xml(iface_mac)
                iface_xml_obj.xmltreefile.write()
                ret = virsh.attach_device(vm_name, iface_xml_obj.xml,
                                          flagstr="--live",
                                          ignore_status=True,
                                          debug=True)
                libvirt.check_exit_status(ret, status_error)
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
                    test.fail("Guest can't get a"
                              " valid ip address")
            # Check guest RX/TX ring
            if check_guest_trans:
                ifname_guest = utils_net.get_linux_ifname(session, iface_mac)
                ret, outp = session.cmd_status_output("ethtool -g %s" % ifname_guest)
                if ret:
                    test.fail("ethtool return error code")
                logging.info("ethtool output is %s", outp)
                driver_dict = ast.literal_eval(iface_driver)
                if expect_tx_size:
                    driver_dict['tx_queue_size'] = expect_tx_size
                for outp_p in outp.split("Current hardware"):
                    if 'rx_queue_size' in driver_dict:
                        if re.search("RX:\s*%s" % driver_dict['rx_queue_size'], outp_p):
                            logging.info("Find RX setting RX:%s by ethtool", driver_dict['rx_queue_size'])
                        else:
                            test.fail("Cannot find matching rx setting")
                    if 'tx_queue_size' in driver_dict:
                        if re.search("TX:\s*%s" % driver_dict['tx_queue_size'], outp_p):
                            logging.info("Find TX settint TX:%s by ethtool", driver_dict['tx_queue_size'])
                        else:
                            test.fail("Cannot find matching tx setting")

            session.close()
            # Restart libvirtd and guest, then test again
            if test_libvirtd:
                libvirtd.restart()
                vm.destroy()
                vm.start()
                if test_option_xml:
                    run_xml_test(iface_mac)

            # Detach hot/cold-plugged interface at last
            if attach_device and not status_error:
                ret = virsh.detach_device(vm_name, iface_xml_obj.xml,
                                          flagstr="", ignore_status=True)
                libvirt.check_exit_status(ret)

        except virt_vm.VMStartError as e:
            logging.info(str(e))
            if not start_error:
                test.fail('VM failed to start\n%s' % e)

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
            process.system("modprobe vhost_net", shell=True)
        if unprivileged_user:
            virsh.remove_domain(vm_name, "--remove-all-storage",
                                **virsh_dargs)
        if additional_vm:
            virsh.remove_domain(additional_vm.name,
                                "--remove-all-storage")
            # Kill all omping server process on host
            process.system("pidof omping && killall omping",
                           ignore_status=True, shell=True)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
