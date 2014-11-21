import os
import re
import time
import logging
from autotest.client.shared import error
from autotest.client import utils
from virttest import aexpect, virt_vm, virsh, remote
from virttest import utils_net
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
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def create_iface_xml(iface_mac):
        """
        Create interface xml file
        """
        iface = Interface(type_name=iface_type)
        iface.source = eval(iface_source)
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
        # Try to modify interface xml by update-device or edit xml
        if update:
            iface.xmltreefile.write()
            # Validate the xml file
            if not iface.get_validates():
                raise error.TestFail("Failed to validate xml file")

            ret = virsh.update_device(vm_name, iface.xml,
                                      **virsh_dargs)
            libvirt.check_exit_status(ret, status_error)
        else:
            vmxml.devices = xml_devices
            vmxml.xmltreefile.write()
            vmxml.sync()

    def check_offloads_option(if_name, driver_options, session=None):
        offloads = {"csum": "tx-checksumming",
                    "gso": "generic-segmentation-offload",
                    "tso4": "tcp-segmentation-offload",
                    "tso6": "tx-tcp6-segmentation",
                    "ecn": "tx-tcp-ecn-segmentation",
                    "ufo": "udp-fragmentation-offload"}
        if session:
            ret, output = session.cmd_status_output("ethtool -k %s | head -18"
                                                    % if_name)
        else:
            out = utils.run("ethtool -k %s | head -18" % if_name)
            ret, output = out.exit_status, out.stdout
        if ret:
            raise error.TestFail("ethtool return error code")
        logging.debug("ethtool output: %s", output)
        for offload in driver_options.keys():
            if offloads.has_key(offload):
                if not output.count("%s: %s" % (offloads[offload],
                                                driver_options[offload])):
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

    status_error = "yes" == params.get("status_error", "no")
    start_error = "yes" == params.get("start_error", "no")
    define_error = "yes" == params.get("define_error", "no")

    # Interface specific attributes.
    iface_type = params.get("iface_type", "network")
    iface_source = params.get("iface_source", "{'network': 'default'}")
    iface_driver = params.get("iface_driver")
    iface_model = params.get("iface_model")
    iface_driver_host = params.get("iface_driver_host")
    iface_driver_guest = params.get("iface_driver_guest")
    change_option = "yes" == params.get("change_iface_options", "no")
    attach_device = "yes" == params.get("attach_iface_device", "no")
    update_device = "yes" == params.get("update_iface_device", "no")
    test_option_cmd = "yes" == params.get(
                      "test_iface_option_cmd", "no")
    test_option_xml = "yes" == params.get(
                      "test_iface_option_xml", "no")
    test_vhost_net = "yes" == params.get(
                     "test_vhost_net", "no")
    test_option_offloads = "yes" == params.get(
                           "test_option_offloads", "no")

    if iface_driver_host or iface_driver_guest:
        if not libvirt_version.version_compare(1, 2, 8):
            raise error.TestNAError("Offloading options not supported "
                                    " in this libvirt version")

    # Destroy VM first.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)

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

        # Start the VM.
        vm.start()
        session = vm.wait_for_login()
        if start_error:
            raise error.TestFail("VM started unexpectedly")

        if test_vhost_net:
            if utils.system("lsmod | grep vhost_net", ignore_status=True):
                raise error.TestFail("vhost_net module can't be"
                                     " loaded automatically")

        # Attach a interface when vm is running
        if attach_device:
            iface_mac = utils_net.generate_mac_address_simple()
            iface_xml_obj = create_iface_xml(iface_mac)
            iface_xml_obj.xmltreefile.write()
            ret = virsh.attach_device(vm_name, iface_xml_obj.xml,
                                      flagstr="", **virsh_dargs)
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
                ret = virsh.domiflist(vm_name, **virsh_dargs)
                libvirt.check_exit_status(ret)
                iface_list = libvirt.get_interface_details(ret.stdout)
                for iface in iface_list:
                    if iface["mac"] == iface_mac:
                        check_offloads_option(iface["interface"],
                                              eval(iface_driver_host))
            if iface_driver_guest:
                if_name = utils_net.get_linux_ifname(session, iface_mac)
                check_offloads_option(if_name, eval(iface_driver_guest),
                                      session)

        # Detach hot-plugged interface at last
        if attach_device:
            ret = virsh.detach_device(vm_name, iface_xml_obj.xml,
                                      flagstr="", **virsh_dargs)
            libvirt.check_exit_status(ret)

    except virt_vm.VMStartError, e:
        logging.error(str(e))
        if start_error:
            pass
        else:
            raise error.TestFail('VM Failed to start for some reason!')
    except xcepts.LibvirtXMLError, e:
        logging.error(str(e))
        if define_error:
            pass
        else:
            raise error.TestFail("Failed to define VM")

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
