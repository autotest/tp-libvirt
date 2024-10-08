# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: Red Hat Inc. 2024
# Author: Sebastian Mitterle <smitterl@redhat.com>
import logging

from avocado.core.exceptions import TestError, TestFail
from virttest import virsh
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.libvirt_xml.vm_xml import VMXML

from provider import vfio
from provider.vfio.mdev_handlers import MdevHandler

LOG = logging.getLogger("avocado." + __name__)


class VMHandler(object):
    """Handles usage of VMs in this test"""

    def __init__(self, vms):
        """
        Initializes the instance fields and stores VMXML
        for later backup.

        :param vms: list of VM instances as obtained from env
        """
        self.vms = vms
        self.vmxml_backups = []
        for vm in self.vms:
            self.vmxml_backups.append(VMXML.new_from_inactive_dumpxml(vm.name))

    def run(self, action, vm_idx, device, handlers):
        """
        Delegates actions to VMs.

        :param action: the name of the method to call on the VM instance
        :param vm_idx: the index of the VM to call the method on
        :param device: the device identifier if action involves a device
        :param handlers: a reference to the NodeDevHandlerAggregator instance that
                         holds all device handlers
        """
        if action in ["reboot", "start", "destroy", "shutdown", "pause", "resume"]:
            getattr(self.vms[vm_idx], action)()
        elif action in ["attach", "detach"]:
            hostdev_xml = handlers.get_hostdev_xml(device)
            getattr(virsh, action + "_device")(
                self.vms[vm_idx].name,
                hostdev_xml,
                flagstr="--current",
                ignore_status=False,
                debug=True,
            )

        elif action == "check_present":
            session = self.vms[vm_idx].wait_for_login()
            try:
                handlers.run("check_present", device, session)
            except TestFail as e:
                raise e
            finally:
                session.close()
        else:
            raise TestError(f"Unknown action: {action}")

    def clean_up(self):
        """Restores the VMXML"""
        for xml in self.vmxml_backups:
            xml.sync()


class NodeDevHandlerAggregator(object):
    """
    Holds NodeDevHandler singletons per device identifier
    and delegates actions
    """

    def __init__(self):
        """Initializes the instance"""
        self.handlers = []

    def add_once(self, device):
        """
        Creates and stores a single NodeDevHandler instance per device identifier.

        :param device: the device identifier as used in the commands, e.g. "ap-0.0.000e"
                       more info in NodeDevHandler.__init__
        """
        device_type, device_id = device.split("-")
        if [h for h in self.handlers if h.type == device_type and h.id == device_id]:
            pass
        else:
            self.handlers.append(NodeDevHandler(device_type, device_id))

    def run(self, action, device, session=None):
        """
        Delegates the action to the given device handler

        :param action: method implemented by the device handler, e.g. 'start'
        :param device: the device identifier as used in the commands, e.g. "ap-0.0.000e"
                       more info in NodeDevHandler.__init__
        :param session: logged-in guest session, e.g. for checking if device is present inside
        """
        device_type, device_id = device.split("-")
        handler = [
            h for h in self.handlers if h.type == device_type and h.id == device_id
        ][0]
        if session:
            getattr(handler, action)(session=session)
        else:
            getattr(handler, action)()

    def get_hostdev_xml(self, device):
        """
        Retrieves the existing host device XML file reference for a given device

        :param device: the device identifier as used in the commands, e.g. "ap-0.0.000e"
                       more info in NodeDevHandler.__init__
        """
        device_type, device_id = device.split("-")
        handler = [
            h for h in self.handlers if h.type == device_type and h.id == device_id
        ][0]
        return handler.hostdev_xml.xml


class NodeDevHandler(object):
    """An object to handle actions on node devices"""

    device_address_counter = 10

    def __init__(self, device_type, device_id):
        """
        Holds the initial identifiers and prepares
        fields for future use.
        It also prepares the device identifiers for
        the host device xml and check inside the guest.

        Examples:
        vfio_ap: device_type=ap, device_id=00.000e
        vfio_ccw: device_type=ccw, device_id=0.0.5200
        pci_ism: device_type=ism, device_id=0000.00.00.0
        pci_roce: device_type=roce, device_id=0000.00.00.2
        """
        self.type = device_type
        if self.type not in ["ap", "ccw", "ism", "roce"]:
            raise TestError(f"Unknown device type {self.type}")
        self.id = device_id
        self.nodedev_name = None
        self.hostdev_xml = None
        self.mdev_handler = None
        self.guest_id = None
        self.devno = None
        self.uid = None
        self.fid = None
        self.previously_started = False

        if self.type in ["ism", "roce"]:
            self.uid = f"{NodeDevHandler.device_address_counter:#0{6}x}"
            self.fid = f"{NodeDevHandler.device_address_counter:#0{10}x}"
            self.guest_id = f"{NodeDevHandler.device_address_counter:#0{6}x}:00:00.0".replace("0x", "")
            NodeDevHandler.device_address_counter += 1
        elif self.type in ["ccw"]:
            self.devno = f"{NodeDevHandler.device_address_counter:#0{6}x}"
            NodeDevHandler.device_address_counter += 1

    def start(self):
        """Starts the device"""
        if self.type == "ap":
            if not self.previously_started:
                self.mdev_handler = MdevHandler.from_type("vfio_ap-passthrough")
            self.nodedev_name = self.mdev_handler.create_nodedev("nodedev", [self.id])
            self.hostdev_xml = vfio.get_hostdev_xml(self.mdev_handler.uuid, "vfio-ap")
            self.previously_started = True
        elif self.type == "ccw":
            if not self.previously_started:
                self.mdev_handler = MdevHandler.from_type("vfio_ccw-io")
                self.mdev_handler.expected_device_address = f"0.0.{self.devno}".replace(
                    "0x", ""
                )
            self.nodedev_name = self.mdev_handler.create_nodedev(
                api="nodedev", devid=self.id
            )
            hostdev_xml = vfio.get_hostdev_xml(self.mdev_handler.uuid, "vfio-ccw")
            addr = hostdev_xml.Address.new_from_dict(
                {
                    "type_name": "ccw",
                    "cssid": "0xfe",
                    "ssid": "0x0",
                    "devno": self.devno,
                }
            )
            hostdev_xml.address = addr
            hostdev_xml.xmltreefile.write()
            self.hostdev_xml = hostdev_xml
            self.previously_started = True
        else:
            if not self.previously_started:
                self.nodedev_name = "pci_" + self.id.replace(":", "_").replace(".", "_")
                pci_xml = NodedevXML.new_from_dumpxml(self.nodedev_name)
                hostdev_xml = vfio.get_hostdev_xml(pci_xml.cap.get_address_dict())
                addr = hostdev_xml.Address.new_from_dict(
                    {
                        "type_name": "pci",
                        "domain": "0x0000",
                        "bus": "0x00",
                        "slot": "0x00",
                        "function": "0x0",
                    }
                )
                zpci = addr.Zpci.new_from_dict({"uid": self.uid, "fid": self.fid})
                addr.set_zpci_attrs(zpci)
                hostdev_xml.address = addr
                hostdev_xml.xmltreefile.write()
                self.hostdev_xml = hostdev_xml
            self.previously_started = True

    def stop(self, ignore_status=False):
        """
        Stops the device

        :param ignore_status: if to ignore if device can't be stopped
                              this avoids issues during test teardown
        """
        if self.type in ["ism", "roce"]:
            return
        virsh.nodedev_destroy(
            self.nodedev_name, debug=True, ignore_status=ignore_status
        )

    def check_present(self, session):
        """
        Checks if the device is present inside the guest.

        :param session: the guest console session
        """
        if self.type in ["ap", "ccw"]:
            self.mdev_handler.check_device_present_inside_guest(session)
        elif self.type in ["ism", "roce"]:
            name_part = "ISM" if self.type == "ism" else "Ethernet"
            vfio.check_pci_device_present(self.guest_id, name_part, session)

    def clean_up(self):
        """Cleans the handler up"""
        if self.mdev_handler:
            self.mdev_handler.clean_up()


def run(test, params, env):
    """
    These tests execute workflows over passthrough devices
    and VMs.

    The test steps are configured in the test configuration via
    the parameter `cmds`. It holds all steps to be executed.

    All steps are terminated with ",".
    There are two kind of steps:
    action;device_identifier - this is an action to be executed on the node
                               device by the NodeDevHandler instance, e.g. start
                               or stop the device
    action;vm_idx[;device_identifier] - this is an action prefixed with "vm_";
                               the action is executed on the VM or delegated,
                               s. VMHandler; the vm_idx determines which VM is used
                               and the optional parameter `device_identifier` is passed
                               for actions that involve both, VM and the node device,
                               e.g. `vm_attach;0;ap-0.0.000e`
    """
    vm_handler = VMHandler(env.get_all_vms())

    try:
        cmds = params.get("cmds").split(",")
        handlers = NodeDevHandlerAggregator()
        parts = None
        action = None
        vm_idx = None
        device = None
        hostdev_xml = None
        for cmd in cmds:
            if not cmd.strip():
                continue
            LOG.debug(f"Run {cmd}.")
            parts = cmd.split(";")
            action = parts[0]
            if not action.startswith("vm"):
                device = parts[1]
                handlers.add_once(device)
                handlers.run(action, device)
            else:
                action = parts[0].replace("vm_", "")
                vm_idx = parts[1]
                if len(parts) > 2:
                    device = parts[2]
                vm_handler.run(action, int(vm_idx), device, handlers)

    finally:
        if vm_handler:
            vm_handler.clean_up()
        if handlers:
            for handler in handlers.handlers:
                handler.stop(ignore_status=True)
                handler.clean_up()
