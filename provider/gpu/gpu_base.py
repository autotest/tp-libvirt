# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: yicui@redhat.com
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import logging
import re
import os
import platform
import requests

from avocado.core import exceptions
from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_test import libvirt

LOG = logging.getLogger("avocado." + __name__)


def get_gpus_info(session=None):
    """
    Get GPUs information

    :param session: The session object to the host
    :raise: exceptions.TestError when command fails
    :return: dict, GPUs' info.
        eg. {'3b:00.0': {'driver': 'ixgbe', 'pci_id': '0000:3b:00.0',
                         'iface': 'ens1f0', 'status': 'up'},
             '3b:00.1': {'driver': 'ixgbe', 'pci_id': '0000:3b:00.1',
                         'iface': 'ens1f1', 'status': 'down'}}

    """
    dev_info = {}
    status, output = utils_misc.cmd_status_output(
        "lspci -D -nn|awk '/3D controller/'",
        shell=True, session=session
    )
    if status or not output:
        raise exceptions.TestError(
            "Unable to get 3D controllers. status: %s,"
            "stdout: %s." % (status, output)
        )
    pattern = r'(\S+:\S+:\S+.\d+)\s.*\s\[(\w+:\w+)\]'
    matches = re.findall(pattern, output)
    for match in matches:
        dev_info[match[0]] = {"pci_id": match[0], "ID": re.sub(":", " ", match[1])}

    for pci in dev_info.keys():
        _, output = utils_misc.cmd_status_output(
            "lspci -v -s %s" % pci, shell=True, session=session
        )
        driver_in_use = re.search("driver in use: (.*)", output)
        if driver_in_use:
            dev_info[pci].update({"driver": driver_in_use[1]})

    LOG.debug(f"GPU info: {dev_info}.")
    return dev_info


def get_gpu_pci(session=None):
    """
    Get the pci id of the first available GPU.

    :param session: The session object to the host
    :return: pci id of GPU, eg. 0000:01:00.0
    """
    return list(get_gpus_info(session=session).values())[0].get("pci_id")


def pci_to_addr(pci_id):
    """
    Get address dict according to pci_id

    :param pci_id: PCI ID of a device(eg. 0000:05:10.1)
    :return: address dict
    """
    pci_list = ["0x%s" % x for x in re.split("[.:]", pci_id)]
    return dict(zip(["domain", "bus", "slot", "function"], pci_list + ["pci"]))


class GPUTest(object):
    """
    Wrapper class for GPU testing
    """
    def __init__(self, vm, test, params, session=None):
        self.vm = vm
        self.test = test
        self.params = params
        self.session = session
        self.remote_virsh_dargs = None

        libvirt_version.is_libvirt_feature_supported(self.params)
        self.gpu_pci = get_gpu_pci(session=self.session)
        if not self.gpu_pci:
            test.cancel("NO available gpu found.")
        self.gpu_pci_addr = pci_to_addr(self.gpu_pci)
        self.gpu_dev_name = utils_sriov.get_device_name(self.gpu_pci)

        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        self.orig_config_xml = new_xml.copy()

    def parse_hostdev_dict(self):
        """
        Parse hostdev_dict from params

        :return: The updated iface_dict
        """
        gpu_pci_addr = self.gpu_pci_addr
        if self.params.get("gpu_hostdev_dict"):
            hostdev_dict = eval(self.params.get('gpu_hostdev_dict'))
        else:
            hostdev_dict = eval(self.params.get('hostdev_dict', '{}'))
        return hostdev_dict

    def check_gpu_dev(self, vm, status_error=False):
        """
        Check GPU device in guest

        :param vm: vm object
        :param status_error: True if expect not existing, otherwise False
        """
        vm_session = vm.wait_for_login(timeout=240)
        s, o = vm_session.cmd_status_output("lspci |grep 3D")
        vm_session.close()
        result = process.CmdResult(stdout=o, exit_status=s)
        libvirt.check_exit_status(result, status_error)

    def nvidia_smi_check(self, vm_session):
        """
        Run nvidia-smi command and check GPU device's info

        :param vm_session: vm's session
        """
        gpu_pci = get_gpu_pci(session=vm_session)
        s, o = vm_session.cmd_status_output("nvidia-smi -q")
        if s or not re.search(gpu_pci, o):
            self.test.fail("Failed to run nvidia-smi command. Status: %s, output: %s."
                           % (s, o))

    def install_latest_driver(self, vm_session, local_rpm=False):
        """
        Install the latest data centre driver

        :param vm_session: vm session object
        :local_rpm: Install the driver using local rpm
        """
        pkgs = ["gcc", "kernel*headers*", "kernel*devel*"]
        if not utils_package.package_install(pkgs, vm_session):
            self.test.error(f"Unable to install {pkgs} in guest!")
        arch = platform.machine()
        if local_rpm:
            url = "https://developer.nvidia.com/datacenter-driver-downloads"
            page = requests.Session().get(url)
            pkg_download_cmd = re.findall(rf"(wget https://developer.download.nvidia.com/compute/nvidia-driver/\S+/local_installers/nvidia-driver-local-repo-rhel9-\S+{arch}.rpm)", page.text)
            if not pkg_download_cmd:
                self.test.error("Unable to get download command!")
            pkg_name = os.path.basename(pkg_download_cmd[0])
            vm_session.cmd(pkg_download_cmd[0], timeout=240)
            vm_session.cmd(f"rpm -i {pkg_name}", timeout=120)
        else:
            # TODO: Get distro from guest. epel repo should be covered in ci
            distro = "rhel9"
            pkg_mgr = utils_package.package_manager(vm_session, "epel-release")
            if not pkg_mgr.is_installed("epel-release"):
                vm_session.cmd_output_safe(f"subscription-manager repos --enable=rhel-9-for-{arch}-appstream-rpms")
                vm_session.cmd_output_safe(f"subscription-manager repos --enable=rhel-9-for-{arch}-baseos-rpms")
                vm_session.cmd_output_safe(f"subscription-manager repos --enable=codeready-builder-for-rhel-9-{arch}-rpms")
                vm_session.cmd_output_safe("dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-9.noarch.rpm", timeout=600)

            if vm_session.cmd_status(f"ls /etc/yum.repos.d/cuda-{distro}.repo"):
                repo_url = f"https://developer.download.nvidia.com/compute/cuda/repos/{distro}/sbsa/cuda-{distro}.repo"
                vm_session.cmd(f"dnf config-manager --add-repo {repo_url}")
                vm_session.cmd(f"sed -i 's/^gpgcheck=1/gpgcheck=0/' /etc/yum.repos.d/cuda-{distro}.repo")
                vm_session.cmd("dnf clean all")
            vm_session.cmd("dnf -y module install nvidia-driver:open-dkms --skip-broken", timeout=600)

    def install_cuda_toolkit(self, vm_session, runfile=False):
        """
        Install cuda toolkit
        :param vm_session: vm session object
        :runfile: Install the cuda toolkit using runfile
        """
        if runfile:
            url = "https://developer.nvidia.com/cuda-downloads"
            page = requests.Session().get(url)
            pkg_download_cmd = re.findall(r"(wget https://developer.download.nvidia.com/compute/cuda/\S+/local_installers/cuda_\S+_linux_sbsa.run)", page.text)
            if not pkg_download_cmd:
                self.test.error("Unable to get download command!")
            pkg_name = os.path.basename(pkg_download_cmd[0])
            vm_session.cmd(pkg_download_cmd[0], timeout=240)
            vm_session.cmd(f"sh {pkg_name} --silent", timeout=600)
        else:
            pkgs = "cuda-toolkit"
            if not utils_package.package_install(pkgs, vm_session):
                self.test.fail(f"Unable to install {pkgs} in guest!")

    def setup_gpu_controllers(self, vmxml):

        pre_idx = libvirt_pcicontr.get_max_contr_indexes(vmxml, "pci", "pcie-root-port")[-1]
        #pre_contr_bus = "%0#4x" % int(pre_idx)
        #contr_attrs = {"bus": pre_contr_bus}
        pcihole64 = eval(self.params.get('pcihole64', '4294967296'))
        pci_root_dict = {"pcihole64": pcihole64}
        libvirt_vmxml.modify_vm_device(
            vmxml,
            "controller",
            pci_root_dict, 
            sync_vm=False
        )
        pxb_ctrl_attrs = self.params.get('pxb_ctrl_attrs', '{}')
        if pxb_ctrl_attrs:
            pxb_index = pre_idx + 1
            pxb_slot = libvirt_pcicontr.get_free_pci_slot(vmxml)
            pxb_ctrl_attrs = eval(pxb_ctrl_attrs % (str(pxb_index), pxb_slot))
            libvirt_vmxml.modify_vm_device(
                vmxml, 
                "controller",
                pxb_ctrl_attrs, 
                sync_vm=False
            )
            pcie_root_port_attrs = self.params.get('pcie_root_port_attrs', '{}')
            if pcie_root_port_attrs:
                pcie_root_port_index = pxb_index + 1
                pcie_root_port_bus = "%0#4x" % int(pxb_index)
                pcie_root_port_attrs = eval(pcie_root_port_attrs % (pcie_root_port_index, pcie_root_port_bus))
                libvirt_vmxml.modify_vm_device(
                    vmxml, 
                    "controller",
                    pcie_root_port_attrs,
                    sync_vm=False)
            # gpu_address = '{"type": "pci", "domain": "0x0000", "bus": "0x02", "slot": "0x20", "function":"0x0"}'
            # gpu_hostdev_dict = {'type':'pci','address': {'attrs':{"type": "pci", "domain": "0x0000", "bus": "%s", "slot": "0x00", "function":"0x0"}}}}
            gpu_hostdev_dict = self.params.get('gpu_hostdev_dict', '{}')
            if gpu_hostdev_dict:
                gpu_hostdev_dict = eval(gpu_hostdev_dict % ("%0#4x" % int(pcie_root_port_index)))
                libvirt_vmxml.modify_vm_device(vmxml, "hostdev", gpu_hostdev_dict, sync_vm=False)
            iommu_dict = self.params.get('iommu_dict', '{}')
            if iommu_dict:
                iommu_dict = eval(iommu_dict % str(pxb_index))
                libvirt_vmxml.modify_vm_device(vmxml, "iommu", iommu_dict, sync_vm=False)

        return vmxml

    def setup_gpu_config(self):

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        vm_attrs = eval(self.params.get('vm_attrs', '{}'))
        if vm_attrs:
            vmxml.setup_attrs(**vm_attrs)

        memory_backing = eval(self.params.get('memory_backing', '{}'))
        if memory_backing:
            mem_backing = vm_xml.VMMemBackingXML()
            mem_backing.setup_attrs(**memory_backing)
            vmxml.mb = mem_backing
        features_xml = vmxml.features        
        for feature_name in ["ras", "acpi", "apic"]:
            if features_xml.has_feature(feature_name):
                features_xml.remove_feature(feature_name)
        features_xml.acpi = True
        features_xml.apic = True
        features_xml.ras = "on"        
        vmxml.features = features_xml

        vmxml = self.setup_gpu_controllers(vmxml)
        vmxml.sync()
        self.test.log.debug("After setup, guest xml:\n%s", vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name))

    def setup_default(self, **dargs):
        """
        Default setup

        :param dargs: test keywords
        """
        dev_name = dargs.get('dev_name')
        managed_disabled = dargs.get('managed_disabled', False)

        if dargs.get("remove_hostdev", "yes") == "yes":
            self.test.log.debug("Removing the existing hostdev device...")
            libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'hostdev')

        if dargs.get("test_hopper_gpu", "no") == "yes":
            self.setup_gpu_config()

        if managed_disabled:
            virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)

    def teardown_default(self, **dargs):
        """
        Default cleanup

        :param dargs: test keywords
        """
        dev_name = dargs.get('dev_name')
        managed_disabled = dargs.get('managed_disabled', False)
        self.test.log.info("TEST_TEARDOWN: Recover test environment.")
        if self.vm.is_alive():
            self.vm.destroy(gracefully=False)
        self.orig_config_xml.sync()

        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
