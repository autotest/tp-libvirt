import logging
import time
from avocado.core import exceptions
from avocado.utils import process
from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def set_options(iface_type=None, iface_source=None,
                iface_mac=None, iface_model=None, suffix="",
                operation_type="attach"):
    """
    Set attach-detach-interface options.
    """
    options = ""
    if iface_type is not None:
        options += " --type '%s'" % iface_type
    if iface_source is not None and operation_type == "attach":
        options += " --source '%s'" % iface_source
    if iface_mac is not None:
        options += " --mac '%s'" % iface_mac
    if iface_model and operation_type == "attach":
        options += " --model '%s'" % iface_model
    if suffix:
        options += " %s" % suffix
    return options


def run(test, params, env):
    """
    Test virsh {at|de}tach-interface command.

    1) Prepare test environment and its parameters
    2) Attach the required interface
    3) Perform attach and detach operation
    4) Check if attached interface is correct
    5) Detach the attached interface
    """

    def is_attached(vmxml_devices, iface_type, iface_source, iface_mac):
        """
        Check attached interface exist or not.

        :param vmxml_devices: VMXMLDevices instance
        :param iface_type: interface device type
        :param iface_source : interface source
        :param iface_mac : interface MAC address
        :return: True/False if backing file and interface found
        """
        ifaces = vmxml_devices.by_device_tag('interface')
        for iface in ifaces:
            if iface.type_name != iface_type:
                continue
            if iface.mac_address != iface_mac:
                continue
            if iface_source is not None:
                if iface.xmltreefile.find('source') is not None:
                    if iface.source['network'] != iface_source:
                        continue
                else:
                    continue
            # All three conditions met
            logging.debug("Find %s in given iface XML", iface_mac)
            return True
        logging.debug("Not find %s in given iface XML", iface_mac)
        return False

    def check_result(vm_name, iface_source, iface_type, iface_mac,
                     flags, vm_state, attach=True):
        """
        Check the test result of attach/detach-device command.
        """
        active_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if not attach:
            utils_misc.wait_for(lambda: not is_attached(active_vmxml.devices,
                                                        iface_type, iface_source, iface_mac), 20)
        active_attached = is_attached(active_vmxml.devices, iface_type,
                                      iface_source, iface_mac)
        if vm_state != "transient":
            inactive_vmxml = vm_xml.VMXML.new_from_dumpxml(
                vm_name, options="--inactive")
            inactive_attached = is_attached(inactive_vmxml.devices,
                                            iface_type, iface_source,
                                            iface_mac)

        if flags.count("config"):
            if vm_state != "transient":
                if attach:
                    if not inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML not"
                                                  " updated when --config "
                                                  "options used for attachment")
                else:
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML not"
                                                  " updated when --config "
                                                  "options used for detachment")
        if flags.count("live"):
            if attach:
                if vm_state in ["paused", "running", "transient"]:
                    if not active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --live options used for"
                                                  " attachment")
            else:
                if vm_state in ["paused", "running", "transient"]:
                    if active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --live options used for"
                                                  " detachment")
        if flags.count("current") or flags == "":
            if attach:
                if vm_state in ["paused", "running", "transient"]:
                    if not active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --current options used "
                                                  "for attachment")
                elif vm_state == "shutoff" and not inactive_attached:
                    raise exceptions.TestFail("Inactive domain XML not updated"
                                              " when --current options used for"
                                              " attachment")
            else:
                if vm_state in ["paused", "running", "transient"]:
                    if active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --current options used "
                                                  "for detachment")
                elif vm_state == "shutoff" and inactive_attached:
                    raise exceptions.TestFail("Inactive domain XML not updated "
                                              "when --current options used for "
                                              "detachment")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Test parameters
    uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                      "default"))
    vm_ref = params.get("at_detach_iface_vm_ref", "domname")
    at_options_suffix = params.get("at_dt_iface_at_options", "")
    dt_options_suffix = params.get("at_dt_iface_dt_options", "")
    at_status_error = "yes" == params.get("at_status_error", "no")
    dt_status_error = "yes" == params.get("dt_status_error", "no")
    pre_vm_state = params.get("at_dt_iface_pre_vm_state")

    # Skip if libvirt doesn't support --live/--current.
    if (at_options_suffix.count("--live") or
            dt_options_suffix.count("--live")):
        if not libvirt_version.version_compare(1, 0, 5):
            raise exceptions.TestSkipError("update-device doesn't"
                                           " support --live")
    if (at_options_suffix.count("--current") or
            dt_options_suffix.count("--current")):
        if not libvirt_version.version_compare(1, 0, 5):
            raise exceptions.TestSkipError("virsh update-device "
                                           "doesn't support --current")

    # Interface specific attributes.
    iface_type = params.get("at_detach_iface_type", "network")
    iface_model = params.get("iface_model", "virtio")
    iface_source = params.get("at_detach_iface_source", "default")
    iface_mac = params.get("at_detach_iface_mac", "created")
    virsh_dargs = {'ignore_status': True, 'uri': uri, 'debug': True}

    # Check host version.
    rhel6_host = False
    if not process.run("grep 'Red Hat Enterprise Linux Server "
                       "release 6' /etc/redhat-release",
                       ignore_status=True, shell=True).exit_status:
        rhel6_host = True

    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Get a bridge name for test if iface_type is bridge.
    # If there is no bridge other than virbr0, raise TestSkipError
    if iface_type == "bridge":
        host_bridge = utils_net.Bridge()
        bridge_list = host_bridge.list_br()
        try:
            bridge_list.remove("virbr0")
        except AttributeError:
            pass  # If no virbr0, just pass is ok
        logging.debug("Useful bridges:%s", bridge_list)
        if len(bridge_list):
            iface_source = bridge_list[0]
        else:
            process.run('ip link add name br0 type bridge', ignore_status=False)
            iface_source = 'br0'
            logging.debug("Added bridge br0")

    # Turn VM into certain state.
    if pre_vm_state == "running":
        if (rhel6_host and at_options_suffix == "--config" and
                dt_options_suffix == ""):
            raise exceptions.TestSkipError("For bug921407, "
                                           "won't fix on rhel6 host")
        logging.info("Starting %s..." % vm_name)
        if vm.is_dead():
            vm.start()
            vm.wait_for_login().close()
    elif pre_vm_state == "shutoff":
        logging.info("Shuting down %s..." % vm_name)
        if vm.is_alive():
            vm.destroy(gracefully=False)
    elif pre_vm_state == "paused":
        if (rhel6_host and at_options_suffix == "--config" and
                dt_options_suffix == ""):
            raise exceptions.TestSkipError("For bug921407, "
                                           "won't fix on rhel6 host")
        logging.info("Pausing %s..." % vm_name)
        if vm.is_dead():
            vm.start()
            vm.wait_for_login().close()
        if not vm.pause():
            raise exceptions.TestSkipError("Cann't pause the domain")
    elif pre_vm_state == "transient":
        logging.info("Creating %s..." % vm_name)
        vm.undefine()
        if virsh.create(backup_xml.xml,
                        **virsh_dargs).exit_status:
            backup_xml.define()
            raise exceptions.TestSkipError("Cann't create the domain")
        vm.create_serial_console()
        vm.wait_for_login().close()

    dom_uuid = vm.get_uuid()
    dom_id = vm.get_id()

    # Set attach-interface domain
    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domid":
        vm_ref = dom_id
    elif vm_ref == "domuuid":
        vm_ref = dom_uuid
    elif vm_ref == "hexdomid" and dom_id is not None:
        vm_ref = hex(int(dom_id))

    # Get a mac address if iface_mac is 'created'.
    if iface_mac == "created":
        iface_mac = utils_net.generate_mac_address_simple()

    try:
        # Set attach-interface options and
        # start attach-interface test
        options = set_options(iface_type, iface_source,
                              iface_mac, iface_model, at_options_suffix,
                              "attach")
        ret = virsh.attach_interface(vm_name, options,
                                     **virsh_dargs)
        libvirt.check_exit_status(ret, at_status_error)
        # Check if the command take effect in vm
        # or config file.
        if vm.is_paused():
            vm.resume()
            vm.wait_for_login().close()
        #Sleep a while for vm is stable
        time.sleep(3)
        if not ret.exit_status:
            check_result(vm_name, iface_source,
                         iface_type, iface_mac,
                         at_options_suffix, pre_vm_state)

        # Set detach-interface options
        options = set_options(iface_type, None, iface_mac, None,
                              dt_options_suffix, "detach")

        # Sleep for a while
        time.sleep(10)
        # Start detach-interface test
        if pre_vm_state == "paused":
            if not vm.pause():
                raise exceptions.TestFail("Cann't pause the domain")
        # Virsh detach will take effect if it's executed after vm booted up
        if pre_vm_state == 'transient':
            vm.wait_for_serial_login().close()
        ret = virsh.detach_interface(vm_ref, options,
                                     **virsh_dargs)
        if rhel6_host and pre_vm_state in ['paused', 'running']:
            if (at_options_suffix == "--config" and
                    dt_options_suffix == "--config"):
                dt_status_error = True
        libvirt.check_exit_status(ret, dt_status_error)
        # Check if the command take effect
        # in vm or config file.
        if vm.is_paused():
            vm.resume()
            vm.wait_for_login().close()
        #Sleep a while for vm is stable
        time.sleep(10)
        if not ret.exit_status:
            check_result(vm_name, iface_source,
                         iface_type, iface_mac,
                         dt_options_suffix,
                         pre_vm_state, False)

    finally:
        # Restore the vm
        if vm.is_alive():
            vm.destroy(gracefully=False, free_mac_addresses=False)
        backup_xml.sync()
