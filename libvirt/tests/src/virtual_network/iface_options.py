import os
import re
import ast
import time
import logging
import platform
import shutil
import aexpect

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
from virttest import utils_config
from virttest import data_dir
from virttest import utils_selinux
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml import xcepts
from virttest.staging import utils_memory

from virttest import libvirt_version


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
    host_arch = platform.machine()
    virsh_dargs = {'debug': True, 'ignore_status': False}

    if not utils_package.package_install(["lsof"]):
        test.cancel("Failed to install dependency package lsof"
                    " on host")

    def create_iface_xml(iface_mac, source):
        """
        Create interface xml file

        :param iface_mac: mac of interface
        :param source: source of interface
        """
        iface = Interface(type_name=iface_type)
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
        if test_target:
            iface.target = {"dev": target_dev}
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
        source = iface_source
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
        if test_target:
            logging.debug("iface.target is %s" % target_dev)
            iface.target = {"dev": target_dev}
        if iface.address:
            del iface.address
        if set_ip:
            iface.ips = [ast.literal_eval(x) for x in set_ips]
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
            logging.info("Unprivileged users can't use 'dac' security driver,"
                         " removing from domain xml if present...")
            vmxml.del_seclabel([('model', 'dac')])

            # Set vm memory to 2G if it's larger than 2G
            if vmxml.memory > 2097152:
                vmxml.memory = vmxml.current_mem = 2097152

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
                if define_error:
                    test.fail("Define VM succeed, but it should fail")
            except xcepts.LibvirtXMLError as e:
                if not define_error:
                    test.fail("Define VM fail: %s" % e)

    def check_offloads_option(if_name, driver_options, session=None):
        """
        Check interface offloads by ethtool output
        """
        offloads = {"csum": "tx-checksumming",
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
            else:
                logging.info("Find %s=%s in vm xml" % (driver_opt, driver_dict[driver_opt]))
        if iface_target:
            if ("dev" not in iface.target or
                    not iface.target["dev"].startswith(iface_target)):
                test.fail("Can't see device target dev in vm xml")
            # Check macvtap mode by ip link command
            if iface_target == "macvtap" and "mode" in iface.source:
                cmd = "ip -d link show %s" % iface.target["dev"]
                output = process.run(cmd, shell=True).stdout_text
                if not re.findall("noqueue", output):
                    test.fail("The default qdisc type should be noqueue, but it's not, check output '%s'" % output)
                else:
                    logging.info("Check the qisc type is expected noqueue")
                logging.debug("ip link output: %s", output)
                mode = iface.source["mode"]
                if mode == "passthrough":
                    mode = "passthru"
                if not re.search(r"macvtap\s+mode %s" % mode, output):
                    test.fail("Failed to verify macvtap mode")
        # Check if the "target dev" is set successfully
        # 1. Target dev name with prefix as "vnet" will always be override;
        # 2. Target dev name with prefix as "macvtap" or "macvlan" with direct
        # type interface will be override;
        # 3. Other scenarios, the target dev should be set successfully.
        if test_target:
            if target_dev != iface.target["dev"]:
                if target_dev.startswith("vnet")\
                        or target_dev.startswith("macvtap")\
                        or target_dev.startswith("macvlan"):
                    logging.debug("target dev %s is override" % target_dev)
                else:
                    test.fail("Failed to set target dev to %s" % target_dev)
            else:
                logging.debug("target dev set successfully to %s", iface.target["dev"])

    def run_cmdline_test(iface_mac, host_arch):
        """
        Test qemu command line
        :param iface_mac: expected MAC
        :param host_arch: host architecture, e.g. x86_64
        :raise avocado.core.exceptions.TestError: if preconditions are not met
        :raise avocado.core.exceptions.TestFail: if commandline doesn't match
        :return: None
        """
        cmd = ("ps -ef | grep %s | grep -v grep " % vm_name)
        ret = process.run(cmd, shell=True)
        logging.debug("Command line %s", ret.stdout_text)
        if test_vhost_net:
            if not ret.stdout_text.count("vhost=on") and not rm_vhost_driver:
                test.fail("Can't see vhost options in"
                          " qemu-kvm command line")

        if iface_model == "virtio":
            if host_arch == 's390x':
                model_option = "device virtio-net-ccw"
            else:
                model_option = "device virtio-net-pci"
        elif iface_model == 'rtl8139':
            model_option = "device rtl8139"
        else:
            test.error("Don't know which device driver to expect on qemu cmdline"
                       " for iface_model %s" % iface_model)
        iface_cmdline = re.findall(r"%s,(.+),mac=%s" %
                                   (model_option, iface_mac), ret.stdout_text)
        if not iface_cmdline:
            test.fail("Can't see %s with mac %s in command"
                      " line" % (model_option, iface_mac))

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
                    if "pci" in model_option:
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

        # Only check packed attribute in qemu command line
        if iface_driver and "packed" in ast.literal_eval(iface_driver):
            iface_cmdline = re.findall(r"%s=%s" %
                                       (driver_opt, driver_dict[driver_opt]), ret.stdout_text)

        cmd_opt = {}
        for opt in iface_cmdline[0].split(','):
            tmp = opt.rsplit("=")
            cmd_opt[tmp[0]] = tmp[1]
        logging.debug("Command line options %s", cmd_opt)

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
            logging.debug("Duplicated IP address on guest. Check bug: "
                          "https://bugzilla.redhat.com/show_bug.cgi?id=1147238")
        for vm_ip in vm_ips:
            if not vm_ip or vm_ip != expect_ip:
                logging.debug("vm_ip is %s, expect_ip is %s", vm_ip, expect_ip)
                test.fail("Found wrong IP address"
                          " on guest")
        # Check gateway address
        gateway = str(utils_net.get_default_gateway(False, session))
        if expect_gw not in gateway:
            test.fail("The gateway on guest is %s, while expect is %s" %
                      (gateway, expect_gw))
        # Check dns server address
        ns_list = utils_net.get_guest_nameserver(session)
        if expect_ns not in ns_list:
            test.fail("The dns found is %s, which expect is %s" %
                      (ns_list, expect_ns))

    def check_mcast_network(session, add_session):
        """
        Check multicast ip address on guests

        :param session: vm session
        :param add_session: additional vm session
        """
        src_addr = iface_source['address']
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

    def get_iface_model(iface_model, host_arch):
        """
        Get iface_model. On s390x use default model 'virtio' if non-virtio given
        :param iface_model: value as by test configuration or default
        :param host_arch: host architecture, e.g. x86_64
        :return: iface_model
        """
        if 's390x' == host_arch and 'virtio' not in iface_model:
            return "virtio"
        else:
            return iface_model

    def check_vhostuser_guests(session1, session2):
        """
        Check the vhostuser interface in guests

        param session1: Session of original guest
        param session2: Session of additional guest
        """
        logging.debug("iface details is %s" % libvirt.get_interface_details(vm_name))
        vm1_mac = str(libvirt.get_interface_details(vm_name)[0]['mac'])
        vm2_mac = str(libvirt.get_interface_details(add_vm_name)[0]['mac'])

        utils_net.set_guest_ip_addr(session1, vm1_mac, guest1_ip)
        utils_net.set_guest_ip_addr(session2, vm2_mac, guest2_ip)
        ping_status, ping_output = utils_net.ping(dest=guest2_ip, count='3',
                                                  timeout=5, session=session1)
        logging.info("output:%s" % ping_output)
        if ping_status != 0:
            if ping_expect_fail:
                logging.info("Can not ping guest2 as expected")
            else:
                test.fail("Can not ping guest2 from guest1")
        else:
            if ping_expect_fail:
                test.fail("Ping guest2 successfully not expected")
            else:
                logging.info("Can ping guest2 from guest1")

    def get_ovs_statis(ovs):
        """
        Get ovs-vsctl interface statistics and format in dict

        param ovs: openvswitch instance
        """
        ovs_statis_dict = {}
        ovs_iface_info = ovs.ovs_vsctl(["list", "interface"]).stdout_text.strip()
        ovs_iface_list = re.findall('name\s+: (\S+)\n.*?statistics\s+: {(.*?)}\n',
                                    ovs_iface_info, re.S)
        logging.info("ovs iface list is %s", ovs_iface_list)
        # Dict of iface name and statistics
        for iface_name in vhostuser_names.split():
            for ovs_iface in ovs_iface_list:
                if iface_name == ovs_iface[0].strip('\"'):
                    format_statis = dict(re.findall(r'(\S*?)=(\d*?),', ovs_iface[1]))
                    ovs_statis_dict[iface_name] = format_statis
                    break
        return ovs_statis_dict

    def check_libvirt_log(libvirtd_log_path, log_pattern_list):
        """
        Check if the log patterns exist in libvirtd

        :param libvirtd_log_path: path of libvirtd log
        :param log_pattern_list: checking log pattern list
        """
        with open(libvirtd_log_path) as f:
            lines = "".join(f.readlines())
            for log_pattern in log_pattern_list:
                if re.search(log_pattern, lines):
                    logging.info("Finding msg<%s> in libvirtd log", log_pattern)
                else:
                    test.fail("Can not find msg:<%s> in libvirtd.log" % log_pattern)

    def get_domstats_runtime():
        """
        get domstats runtime dict

        :return: runtime_dict
        """
        runtime_str = process.run("time virsh domstats", shell=True).stderr_text
        it = iter(runtime_str.split())
        runtime_dict = dict(zip(it, it))
        for key in runtime_dict.keys():
            value_list = re.findall(r"\d+\.?\d*", runtime_dict[key])
            if value_list is not None:
                runtime_dict[key] = float(value_list[0]) * 60 + float(value_list[1])
            else:
                test.error("Can not get correct time for command execution")
        logging.info("The runtime dict is %s", runtime_dict)
        return runtime_dict

    def check_domstats(names):
        """
        Check if have keywards in domstats

        params names: The check name list for domstats output
        """
        output = virsh.domstats().stdout.strip()
        logging.debug("domstats output is %s", output)
        for name in names:
            if not re.search('name=%s' % name, output):
                test.fail("Can not find name=%s in domstats output" % name)
        logging.info("Find all vhost-user in domstats")

    def check_tardev_xml(vm_name, set_tar_devs, set_sources, find=True):
        """
        Check if the xml involve the xml with given dev

        param vm_name: name of vm
        param set_tar_devs: setting target dev list of interface
        param set_sources: setting source list of interface
        param find: check if find devs is pass
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_devices = vmxml.get_devices(device_type="interface")
        get_tar_devs = []
        get_sources = []

        # Check if setting source exists in guest xml
        for iface_dev in iface_devices:
            if iface_dev.type_name == 'vhostuser':
                get_sources.append(iface_dev.source)
                get_tar_devs.append(iface_dev.target["dev"])
        logging.debug("set iface sources are %s, get iface sources are %s", set_sources, get_sources)
        logging.debug("set target devs are %s, get target devs are %s", set_tar_devs, get_tar_devs)
        for source in set_sources:
            if source not in get_sources:
                test.fail("Cannot find interface source %s in xml" % source)
        logging.info("Find all interface vhostuser by source")

        # Check if tartget dev exist in guest xml
        if find:
            # Expect to find target dev in xml
            if not set(set_tar_devs).issubset(set(get_tar_devs)):
                test.fail("Cannot find all target dev in xml")
            logging.info("Find all vhost-user in xml target dev")
            # Expect not to find target dev in xml
        else:
            if not set(set_tar_devs).isdisjoint(set(get_tar_devs)):
                test.fail("Still can find the not expected target dev in xml")
            logging.info("Cannot find not expected target in xml")

    status_error = "yes" == params.get("status_error", "no")
    start_error = "yes" == params.get("start_error", "no")
    define_error = "yes" == params.get("define_error", "no")
    unprivileged_user = params.get("unprivileged_user")

    # Interface specific attributes.
    iface_type = params.get("iface_type", "network")
    iface_source = ast.literal_eval(params.get("iface_source", "{}"))
    iface_driver = params.get("iface_driver")
    iface_model = get_iface_model(params.get("iface_model", "virtio"), host_arch)
    iface_target = params.get("iface_target")
    iface_backend = params.get("iface_backend", "{}")
    iface_driver_host = params.get("iface_driver_host")
    iface_driver_guest = params.get("iface_driver_guest")
    ovs_br_name = params.get("ovs_br_name")
    vhostuser_names = params.get("vhostuser_names")
    vhost_client_name = params.get("vhost_client_name")
    vhost_client_type = params.get("vhost_client_type")
    vhost_client_options = params.get("vhost_client_options")
    vhost_client_path = params.get("vhost_client_path")
    attach_device = params.get("attach_iface_device")
    expect_tx_size = params.get("expect_tx_size")
    guest1_ip = params.get("vhostuser_guest1_ip", "192.168.100.1")
    guest2_ip = params.get("vhostuser_guest2_ip", "192.168.100.2")
    test_type = params.get("test_type")
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
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    restart_vm = "yes" == params.get("restart_vm", "no")
    test_guest_ip = "yes" == params.get("test_guest_ip", "no")
    test_backend = "yes" == params.get("test_backend", "no")
    check_guest_trans = "yes" == params.get("check_guest_trans", "no")
    set_ip = "yes" == params.get("set_user_ip", "no")
    set_ips = params.get("set_ips", "").split()
    expect_ip = params.get("expect_ip")
    expect_gw = params.get("expect_gw")
    expect_ns = params.get("expect_ns")
    test_target = "yes" == params.get("test_target", "no")
    target_dev = params.get("target_dev", None)
    expect_target_devs = ast.literal_eval(params.get("expect_target_devs", "[]"))

    # test params for vhostuser test
    huge_page = ast.literal_eval(params.get("huge_page", "{}"))
    numa_cell = ast.literal_eval(params.get("numa_cell", "{}"))
    additional_iface_source = ast.literal_eval(params.get("additional_iface_source", "{}"))
    vcpu_num = params.get("vcpu_num")
    cpu_mode = params.get("cpu_mode")
    hugepage_num = params.get("hugepage_num")
    log_pattern_list = ast.literal_eval(params.get("log_pattern_list", "[]"))
    log_level = params.get("log_level")
    limit_nofile = params.get("limit_nofile")
    testpmd_cmd = params.get("testpmd_cmd")

    # judgement params for vhostuer test
    need_vhostuser_env = "yes" == params.get("need_vhostuser_env", "no")
    ping_expect_fail = "yes" == params.get("ping_expect_fail", "no")
    check_libvirtd_log = "yes" == params.get("check_libvirtd_log", "no")
    check_statistics = "yes" == params.get("check_statistics", "no")
    enable_multiqueue = "yes" == params.get("enable_multiqueue", "no")

    queue_size = None
    if iface_driver:
        driver_dict = ast.literal_eval(iface_driver)
        if "queues" in driver_dict:
            queue_size = int(driver_dict.get("queues"))

    if iface_driver_host or iface_driver_guest or test_backend:
        if not libvirt_version.version_compare(1, 2, 8):
            test.cancel("Offloading/backend options not "
                        "supported in this libvirt version")
    if iface_driver and "queues" in ast.literal_eval(iface_driver):
        if not libvirt_version.version_compare(1, 0, 6):
            test.cancel("Queues options not supported"
                        " in this libvirt version")
    if iface_driver and "packed" in ast.literal_eval(iface_driver):
        if not libvirt_version.version_compare(6, 3, 0):
            test.cancel("Packed options not supported"
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

    libvirtd_log_path = None
    libvirtd_conf = None
    if check_libvirtd_log:
        libvirtd_log_path = os.path.join(data_dir.get_tmp_dir(), "libvirtd.log")
        logging.debug("libvirtd_log_path is %s", libvirtd_log_path)
        libvirtd_conf = utils_config.LibvirtdConfig()
        if log_level:
            libvirtd_conf["log_level"] = log_level
            libvirtd_conf["log_filters"] = '"1:json 1:libvirt 1:qemu 1:monitor 3:remote 4:event"'
        libvirtd_conf["log_outputs"] = '"1:file:%s"' % libvirtd_log_path
        libvirtd.restart()

    # Prepare vhostuser
    ovs = None
    if need_vhostuser_env:
        # Reserve selinux status
        selinux_mode = utils_selinux.get_status()
        # Reserve orig page size
        orig_size = utils_memory.get_num_huge_pages()
        ovs_dir = data_dir.get_tmp_dir()
        ovs = utils_net.setup_ovs_vhostuser(hugepage_num, ovs_dir,
                                            ovs_br_name, vhostuser_names,
                                            queue_size)
    if vhost_client_type == "dpdkvhostuserclient":
        ovs.add_ports(ovs_br_name, vhost_client_name, vhost_client_type,
                      vhost_client_options)

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

            if test_target:
                logging.debug("Setting target device name to %s", target_dev)
                modify_iface_xml(update=False)

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
                iface_source_list = iface_source if isinstance(iface_source, list) else [iface_source]
                for source in iface_source_list:
                    iface_mac = utils_net.generate_mac_address_simple()
                    att_source = additional_iface_source if test_type == "check_performance" else source
                    iface_xml_obj = create_iface_xml(iface_mac, att_source)
                    iface_xml_obj.xmltreefile.write()
                    ret = virsh.attach_device(vm_name, iface_xml_obj.xml,
                                              flagstr="--config",
                                              ignore_status=True)
                    libvirt.check_exit_status(ret)

            # Add hugepage and update cpu for vhostuser testing
            if huge_page:
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                membacking = vm_xml.VMMemBackingXML()
                hugepages = vm_xml.VMHugepagesXML()
                pagexml = hugepages.PageXML()
                pagexml.update(huge_page)
                hugepages.pages = [pagexml]
                membacking.hugepages = hugepages
                vmxml.mb = membacking

                vmxml.vcpu = int(vcpu_num)
                cpu_xml = vm_xml.VMCPUXML()
                cpu_xml.xml = "<cpu><numa/></cpu>"
                cpu_xml.numa_cell = cpu_xml.dicts_to_cells([numa_cell])
                cpu_xml.mode = cpu_mode
                if cpu_mode == "custom":
                    vm_capability = capability_xml.CapabilityXML()
                    cpu_xml.model = vm_capability.model
                vmxml.cpu = cpu_xml

                vmxml.sync()
                logging.debug("xmltreefile:%s", vmxml.xmltreefile)

            # Clone additional vm
            if additional_guest:
                add_vm_name = "%s_%s" % (vm_name, '1')
                # Clone additional guest
                timeout = params.get("clone_timeout", 360)
                utils_libguestfs.virt_clone_cmd(vm_name, add_vm_name,
                                                True, timeout=timeout)
                additional_vm = vm.clone(add_vm_name)
                # Update iface source if needed
                if additional_iface_source:
                    add_vmxml = vm_xml.VMXML.new_from_dumpxml(add_vm_name)
                    add_xml_devices = add_vmxml.devices
                    add_iface_index = add_xml_devices.index(
                        add_xml_devices.by_device_tag("interface")[0])
                    add_iface = add_xml_devices[add_iface_index]
                    add_iface.source = additional_iface_source
                    add_vmxml.devices = add_xml_devices
                    add_vmxml.xmltreefile.write()
                    add_vmxml.sync()

                    logging.debug("add vm xmltreefile:%s", add_vmxml.xmltreefile)
                additional_vm.start()
                # additional_vm.wait_for_login()
                username = params.get("username")
                password = params.get("password")
                add_session = additional_vm.wait_for_serial_login(username=username,
                                                                  password=password)

            if testpmd_cmd:
                if not shutil.which('dpdk-testpmd') and not utils_package.package_install('dpdk-tools'):
                    test.error("Cannot find cmd dpdk-testpmd and failed to install dpdk-tools package")
                testpmd_session = aexpect.ShellSession(testpmd_cmd)

            # Start the VM.
            if unprivileged_user:
                virsh.start(vm_name, **virsh_dargs)
                cmd = ("su - %s -c 'virsh console %s'"
                       % (unprivileged_user, vm_name))
                session = aexpect.ShellSession(cmd)
                session.sendline()
                remote.handle_prompts(session, params.get("username"),
                                      params.get("password"), r"[\#\$]\s*$", 60)
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
                iface_xml_obj = create_iface_xml(iface_mac, iface_source)
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
                run_cmdline_test(iface_mac, host_arch)
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
                check_mcast_network(session, add_session)
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
                        if re.search(r"RX:\s*%s" % driver_dict['rx_queue_size'], outp_p):
                            logging.info("Find RX setting RX:%s by ethtool", driver_dict['rx_queue_size'])
                        else:
                            test.fail("Cannot find matching rx setting")
                    if 'tx_queue_size' in driver_dict:
                        if re.search(r"TX:\s*%s" % driver_dict['tx_queue_size'], outp_p):
                            logging.info("Find TX settint TX:%s by ethtool", driver_dict['tx_queue_size'])
                        else:
                            test.fail("Cannot find matching tx setting")
            if queue_size and not status_error:
                logging.debug("The queue size set in the xml is %s" % queue_size)
                ifname_guest = utils_net.get_linux_ifname(session, iface_mac)
                max, cur = utils_net.get_channel_info(session, ifname_guest)
                logging.debug("Get the channel info as: max %s  current %s" % (max, cur))
                if int(max.get("Combined")) != queue_size:
                    test.fail("The multiqueue did not set correctly on the vm, refer to %s!" % max)
                if not utils_net.set_channel(session, ifname_guest, "combined", queue_size):
                    test.fail("Setting Combined to %s on vm failed!" % queue_size)
            if test_target:
                logging.debug("Check if the target dev is set")
                run_xml_test(iface_mac)

            # Check vhostuser guest
            if test_type == "multi_guests":
                check_vhostuser_guests(session, add_session)

            if test_type == "check_performance":
                # get runtime of domstats
                runtime_dict1 = get_domstats_runtime()

            # Check libvirtd log
            if check_libvirtd_log:
                check_libvirt_log(libvirtd_log_path, log_pattern_list)

            # change libvirtd.service
            if test_type == "check_performance":
                # Get the NOFILE limit value
                process.run("prlimit -p `pidof libvirtd` |grep NOFILE", shell=True)
                # Let libvirtd run with a big NOFILE limit, check runtime
                libvirtd_service_file = "/usr/lib/systemd/system/libvirtd.service"
                backup_file = os.path.join(data_dir.get_tmp_dir(), "libvirtd.service-bak")
                shutil.copy(libvirtd_service_file, backup_file)
                ori_value = process.getoutput("cat %s |grep LimitNOFILE" % libvirtd_service_file, shell=True)
                with open(libvirtd_service_file, 'r') as fr:
                    alllines = fr.readlines()
                with open(libvirtd_service_file, 'w+') as fw:
                    for line in alllines:
                        newline = re.sub(ori_value, limit_nofile, line)
                        fw.writelines(newline)
                process.run("systemctl daemon-reload")
                libvirtd.restart()
                process.run("prlimit -p `pidof libvirtd` |grep NOFILE", shell=True)

                # get runtime of domstats again and compare
                runtime_dict2 = get_domstats_runtime()
                for key in runtime_dict1.keys():
                    if abs(runtime_dict1[key] - runtime_dict2[key]) > 0.1:
                        test.fail("The difference of 2 runtime is larger than 0.1s")

                # check the libvirtd log again with big NOFILE limit
                check_libvirt_log(libvirtd_log_path, log_pattern_list)

            # Check statistics
            if check_statistics:
                session.sendline("ping %s" % guest2_ip)
                add_session.sendline("ping %s" % guest1_ip)
                time.sleep(5)
                vhost_name = vhostuser_names.split()[0]
                ovs_statis_dict = get_ovs_statis(ovs)[vhost_name]
                domif_info = {}
                domif_info = libvirt.get_interface_details(vm_name)
                virsh.domiflist(vm_name, debug=True)
                domif_stat_result = virsh.domifstat(vm_name, vhost_name)
                if domif_stat_result.exit_status != 0:
                    test.fail("domifstat cmd fail with msg:%s" % domif_stat_result.stderr)
                else:
                    domif_stat = domif_stat_result.stdout.strip()
                logging.debug("vhost_name is %s, domif_stat is %s", vhost_name, domif_stat)
                domif_stat_dict = dict(re.findall("%s (\S*) (\d*)" % vhost_name, domif_stat))
                logging.debug("ovs_statis is %s, domif_stat is %s", ovs_statis_dict, domif_stat_dict)
                ovs_cmp_dict = {'tx_bytes': ovs_statis_dict['rx_bytes'], 'tx_drop': ovs_statis_dict['rx_dropped'],
                                'tx_errs': ovs_statis_dict['rx_errors'], 'tx_packets': ovs_statis_dict['rx_packets'],
                                'rx_bytes': ovs_statis_dict['tx_bytes'], 'rx_drop': ovs_statis_dict['tx_dropped']}
                logging.debug("ovs_cmp_dict is %s", ovs_cmp_dict)
                for dict_key in ovs_cmp_dict.keys():
                    if domif_stat_dict[dict_key] != ovs_cmp_dict[dict_key]:
                        test.fail("Find ovs %s result (%s) different with domifstate result (%s)"
                                  % (dict_key, ovs_cmp_dict[dict_key], domif_stat_dict[dict_key]))
                    else:
                        logging.info("ovs %s value %s is same with domifstate", dict_key, domif_stat_dict[dict_key])

            # Check multi_queue
            if enable_multiqueue:
                ifname_guest = utils_net.get_linux_ifname(session, iface_mac)
                for comb_size in (queue_size, queue_size-1):
                    logging.info("Setting multiqueue size to %s" % comb_size)
                    session.cmd_status("ethtool -L %s combined %s" % (ifname_guest, comb_size))
                    ret, outp = session.cmd_status_output("ethtool -l %s" % ifname_guest)
                    logging.debug("ethtool cmd output:%s" % outp)
                    if not ret:
                        pre_comb = re.search("Pre-set maximums:[\s\S]*?Combined:.*?(\d+)", outp).group(1)
                        cur_comb = re.search("Current hardware settings:[\s\S]*?Combined:.*?(\d+)", outp).group(1)
                        if int(pre_comb) != queue_size or int(cur_comb) != int(comb_size):
                            test.fail("Fail to check the combined size: setting: %s,"
                                      "Pre-set: %s, Current-set: %s, queue_size: %s"
                                      % (comb_size, pre_comb, cur_comb, queue_size))
                        else:
                            logging.info("Getting correct Pre-set and Current set value")
                    else:
                        test.error("ethtool list fail: %s" % outp)

            # Check dpdkvhostuserclient test
            if vhost_client_type == "dpdkvhostuserclient":
                check_domstats(expect_target_devs)
                check_tardev_xml(vm_name, expect_target_devs, iface_source)

            if testpmd_cmd:
                check_tardev_xml(vm_name, expect_target_devs, iface_source, find=False)
                testpmd_session.close()

            session.close()
            if additional_guest:
                add_session.close()

            # Restart libvirtd and guest, then test again
            if restart_libvirtd:
                libvirtd.restart()

            if restart_vm:
                vm.destroy(gracefully=True)
                vm.start()
                if test_option_xml:
                    run_xml_test(iface_mac)

            # Detach hot/cold-plugged interface at last
            if attach_device and not status_error:
                ret = virsh.detach_device(vm_name, iface_xml_obj.xml,
                                          flagstr="", ignore_status=True,
                                          debug=True)
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
            virsh.remove_domain(vm_name, **virsh_dargs)
            process.run('rm -f %s' % dst_disk, shell=True)
        if additional_vm:
            virsh.remove_domain(additional_vm.name,
                                "--remove-all-storage")
            # Kill all omping server process on host
            process.system("pidof omping && killall omping",
                           ignore_status=True, shell=True)
        if vm.is_alive():
            vm.destroy(gracefully=True)
        vmxml_backup.sync()

        if need_vhostuser_env:
            utils_net.clean_ovs_env(selinux_mode=selinux_mode, page_size=orig_size,
                                    clean_ovs=True)

        if libvirtd_conf:
            libvirtd_conf.restore()
            libvirtd.restart()

        if libvirtd_log_path and os.path.exists(libvirtd_log_path):
            os.unlink(libvirtd_log_path)

        if test_type == "check_performance":
            shutil.copy(backup_file, libvirtd_service_file)
            process.run("systemctl daemon-reload")
            libvirtd.restart()

        if testpmd_cmd:
            process.system("killall dpdk-testpmd", ignore_status=True, shell=True)
