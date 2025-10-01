import os
import logging

from avocado.core import exceptions

from virttest import libvirt_version
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_virtio
from virttest.utils_libvirt import libvirt_pcicontr

LOG = logging.getLogger('avocado.' + __name__)


def get_devices_pci(vm_session, test_devices):
    """
    Get the devices pci

    :param vm_session: VM session
    :param test_devices: test devices to check
    :return: The test devices' pci
    """
    result = {}
    for dev in test_devices:
        s, o = vm_session.cmd_status_output(
            "lspci | awk 'BEGIN{IGNORECASE=1} /%s/ {print $1}'" % dev)
        LOG.debug(o)
        if s:
            exceptions.TestFail("Failed to get pci address!")
        result.update({dev: o.strip().splitlines()})
    return result


def get_added_devices_pci(vm_session, test_devices, orig_devices=None):
    """
    Get the newly added devices pci

    :param vm_session: VM session
    :param test_devices: test devices to check
    :param orig_devices: The original devices
    :return: The pci of the added device
    """
    result = {}
    act_devices = get_devices_pci(vm_session, test_devices)
    if not orig_devices:
        return act_devices
    for k, v in act_devices.items():
        added_dev = list(filter(lambda x: x not in orig_devices.get(k),  v))
        LOG.debug(f"added dev: {added_dev}")
        if not added_dev:
            exceptions.TestFail("Failed to get the added devices' pci address!")
        result[k] = added_dev
    return result


def get_iommu_dev_dir(vm_session, pci_addr):
    """
    Get iommu group's devices

    :param vm_session: VM session
    :param pci_addr: Device pci address
    :return: The directory of iommu group devices
    """
    cmd = ("find /sys/kernel/iommu_groups/ -type l | xargs ls -l | awk -F "
           "'/' '/%s / {print(\"\",$2,$3,$4,$5,$6)}' OFS='/' " % pci_addr)
    s, o = vm_session.cmd_status_output(cmd)
    LOG.debug(o)
    if s:
        exceptions.TestFail("Failed to find iommu group!")
    return o.strip().splitlines()[-1]


def check_vm_iommu_group(vm_session, test_devices, orig_devices=None):
    """
    Check the devices with iommu enabled are located in a separate iommu group

    :param vm_session: VM session
    :param test_devices: test devices to check
    :param orig_devices: The original devices
    :return: The test devices' iommu group info
    """
    act_devices = get_added_devices_pci(vm_session, test_devices, orig_devices)
    result = {}
    for dev in test_devices:
        tmp_path = []
        for pci_addr in act_devices[dev]:
            device_dir = get_iommu_dev_dir(vm_session, pci_addr)
            s, dev_pci = vm_session.cmd_status_output("ls %s" % device_dir)
            LOG.debug(f"dev pci: {dev_pci}")
            if s:
                exceptions.TestFail("Failed to get the device in the iommu group!")
            tmp_path.append(os.path.join(device_dir, pci_addr))
        result.update({dev: tmp_path})
    return result


class VIOMMUTest(object):
    def __init__(self, vm, test, params, session=None):
        self.vm = vm
        self.test = test
        self.params = params
        self.session = session
        self.remote_virsh_dargs = None
        self.controller_dicts = eval(self.params.get("controller_dicts", "[]"))

        libvirt_version.is_libvirt_feature_supported(self.params)
        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        self.orig_config_xml = new_xml.copy()

    def parse_iface_dict(self):
        """
        Parse iface_dict from params, supporting both single dict and list of dicts

        :return: The updated iface_dict or list of iface_dicts based on type of input
        """
        iface_dict = self.params.get("iface_dict", "{}")

        if isinstance(iface_dict, list):
            result = []
            for single_iface_dict in iface_dict:
                processed_dict = self._process_single_iface_dict(single_iface_dict)
                result.append(processed_dict)
            return result
        else:
            return self._process_single_iface_dict(iface_dict)

    def _process_single_iface_dict(self, single_iface_dict):
        """
        Process a single interface dictionary

        :param single_iface_dict: Single interface dictionary to process
        :return: The processed interface dictionary
        """
        # generate mac address, if it is needed in iface_dict
        mac_addr = utils_net.generate_mac_address_simple()
        iface_dict = eval(single_iface_dict)

        if self.controller_dicts and iface_dict:
            iface_bus = "%0#4x" % int(self.controller_dicts[-1].get("index"))
            iface_attrs = {"bus": iface_bus}
            if self.controller_dicts[-1].get("model") == "pcie-to-pci-bridge":
                iface_slot = libvirt_pcicontr.get_free_pci_slot(
                        vm_xml.VMXML.new_from_dumpxml(self.vm.name),
                        controller_bus=iface_bus)
                iface_attrs.update({"slot": iface_slot})

            iface_dict.update({"address": {"attrs": iface_attrs}})
        self.test.log.debug("iface_dict: %s.", iface_dict)
        return iface_dict

    def prepare_controller(self):
        """
        Prepare controller(s)

        :return: Updated controller attrs
        """
        if not self.controller_dicts:
            return
        for i in range(len(self.controller_dicts)):
            contr_dict = self.controller_dicts[i]
            pre_controller = contr_dict.get("pre_controller")
            if pre_controller:
                pre_contrs = list(
                    filter(None, [c.get("index") for c in self.controller_dicts
                                  if c["type"] == contr_dict["type"] and
                                  c["model"] == pre_controller]))
                if pre_contrs:
                    pre_idx = pre_contrs[0]
                else:
                    pre_idx = libvirt_pcicontr.get_max_contr_indexes(
                        vm_xml.VMXML.new_from_dumpxml(self.vm.name),
                        contr_dict["type"], pre_controller)[-1]
                pre_contr_bus = "%0#4x" % int(pre_idx)
                contr_attrs = {"bus": pre_contr_bus}
                contr_slot = libvirt_pcicontr.get_free_pci_slot(
                        vm_xml.VMXML.new_from_dumpxml(self.vm.name),
                        controller_bus=pre_contr_bus)
                if contr_dict.get("model") in ["pcie-to-pci-bridge", "pcie-switch-upstream-port", "pcie-switch-downstream-port'"]:
                    contr_slot = "0"
                contr_attrs.update({"slot": contr_slot})
                self.test.log.debug(contr_slot)
                if contr_dict.get("model") in ["pcie-switch-upstream-port"]:
                    pci_devices = list(vm_xml.VMXML.new_from_dumpxml(self.vm.name).xmltreefile.find("devices"))
                    used_function = []
                    for dev in pci_devices:
                        address = dev.find("address")
                        if address is not None and address.get("bus") == pre_contr_bus:
                            used_function.append(address.get("function"))
                    self.test.log.debug(contr_dict)
                    self.test.log.debug(f"Used function: {used_function}")
                    available_function = sorted(list(filter(lambda x: x not in used_function,  ["%0#x" % d for d in range(8)])))
                    self.test.log.debug(f"available_function: {available_function}")
                    contr_function = available_function[0]
                    contr_attrs.update({"function": contr_function})

                contr_dict.update({"address": {"attrs": contr_attrs}})
                contr_dict.pop("pre_controller")
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(self.vm.name), "controller",
                contr_dict, 100)
            contr_dict["index"] = libvirt_pcicontr.get_max_contr_indexes(
                vm_xml.VMXML.new_from_dumpxml(self.vm.name),
                contr_dict["type"], contr_dict["model"])[-1]
            self.controller_dicts[i] = contr_dict

        self.test.log.debug(f"Prepared controllers according to {self.controller_dicts}")
        return self.controller_dicts

    def update_disk_addr(self, disk_dict):
        """
        Update disk address

        :param disk_dict: The original disk attrs
        :return: The updated disk attrs
        """
        if self.controller_dicts:
            dev_bus = self.controller_dicts[-1].get("index")
            dev_attrs = {"bus": "0"}
            if disk_dict["target"]["bus"] != "scsi":
                dev_attrs = {"bus": dev_bus}
                dev_attrs.update({"type": self.controller_dicts[-1].get("type")})
                if self.controller_dicts[-1].get("model") == "pcie-to-pci-bridge":
                    dev_slot = libvirt_pcicontr.get_free_pci_slot(
                        vm_xml.VMXML.new_from_dumpxml(self.vm.name),
                        controller_bus=dev_bus)
                    dev_attrs.update({"slot": dev_slot})

            disk_dict.update({"address": {"attrs": dev_attrs}})
            if disk_dict["target"]["bus"] == "scsi":
                disk_dict["address"]["attrs"].update({"type": "drive", "controller": dev_bus})

            if self.controller_dicts[-1]["model"] in ["pcie-root-port", "pcie-switch-downstream-port"]:
                self.controller_dicts.pop()
        return disk_dict

    def setup_iommu_test(self, **dargs):
        """
        iommu test environment setup

        :param dargs: Other test keywords
        """
        iommu_dict = dargs.get("iommu_dict", {})
        dev_type = dargs.get("dev_type", "interface")
        cleanup_ifaces = "yes" == dargs.get("cleanup_ifaces", "yes")

        if cleanup_ifaces:
            libvirt_vmxml.remove_vm_devices_by_type(self.vm, "interface")
            libvirt_vmxml.remove_vm_devices_by_type(self.vm, "hostdev")
        if iommu_dict:
            self.test.log.info("TEST_SETUP: Add iommu device.")
            libvirt_virtio.add_iommu_dev(self.vm, iommu_dict)

    def teardown_iommu_test(self, **dargs):
        """
        Cleanup iommu test environment

        :param dargs: Other test keywords
        """
        self.test.log.info("TEST_TEARDOWN: Recover test environment.")
        if self.vm.is_alive():
            self.vm.destroy(gracefully=False)
        self.orig_config_xml.sync()
