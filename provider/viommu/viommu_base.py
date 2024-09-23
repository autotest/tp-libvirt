import os

from avocado.core import exceptions

from virttest import libvirt_version
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_virtio
from virttest.utils_libvirt import libvirt_pcicontr


def check_vm_iommu_group(vm_session, test_devices):
    """
    Check the devices with iommu enabled are located in a separate iommu group

    :param vm_session: VM session
    :param test_devices: test devices to check
    """
    result = {}
    for dev in test_devices:
        s, o = vm_session.cmd_status_output(
            "lspci | awk 'BEGIN{IGNORECASE=1} /%s/ {print $1}'" % dev)
        if s:
            exceptions.TestFail("Failed to get pci address!")

        cmd = ("find /sys/kernel/iommu_groups/ -type l | xargs ls -l | awk -F "
               "'/' '/%s / {print(\"\",$2,$3,$4,$5,$6)}' OFS='/' " % o.splitlines()[0])
        s, o = vm_session.cmd_status_output(cmd)
        if s:
            exceptions.TestFail("Failed to find iommu group!")
        device_dir = o.strip().splitlines()[-1]
        s, dev_pci = vm_session.cmd_status_output("ls %s" % device_dir)
        if s:
            exceptions.TestFail("Failed to get the device in the iommu group!")
        result.update({dev: os.path.join(device_dir, dev_pci.strip())})
    return result


class VIOMMUTest(object):
    def __init__(self, vm, test, params, session=None):
        self.vm = vm
        self.test = test
        self.params = params
        self.session = session
        self.remote_virsh_dargs = None
        self.controller_dicts = eval(self.params.get("controller_dicts", "[]"))
        self.dev_slot = 0

        libvirt_version.is_libvirt_feature_supported(self.params)
        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        self.orig_config_xml = new_xml.copy()

    def parse_iface_dict(self):
        """
        Parse iface_dict from params

        :return: The updated iface_dict
        """
        mac_addr = utils_net.generate_mac_address_simple()
        iface_dict = eval(self.params.get("iface_dict", "{}"))
        self.test.log.debug("iface_dict2: %s.", iface_dict)

        if self.controller_dicts and iface_dict:
            iface_bus = "%0#4x" % int(self.controller_dicts[-1].get("index"))
            iface_attrs = {"bus": iface_bus}
            if isinstance(self.dev_slot, int):
                self.dev_slot += 1
                iface_attrs.update({"slot": self.dev_slot})
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

        for contr_dict in self.controller_dicts:
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
                        contr_dict["type"], pre_controller)
                if not pre_idx:
                    self.test.error(
                        f"Unable to get index of {pre_controller} controller!")
                contr_dict.pop("pre_controller")
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(self.vm.name), "controller",
                contr_dict, 100)
            contr_dict["index"] = libvirt_pcicontr.get_max_contr_indexes(
                vm_xml.VMXML.new_from_dumpxml(self.vm.name),
                contr_dict["type"], contr_dict["model"])[-1]
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
                    self.dev_slot = 1
                    dev_attrs.update({"slot": self.dev_slot})

            disk_dict.update({"address": {"attrs": dev_attrs}})
            if disk_dict["target"]["bus"] == "scsi":
                disk_dict["address"]["attrs"].update({"type": "drive", "controller": dev_bus})

            if self.controller_dicts[-1]["model"] == "pcie-root-port":
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
