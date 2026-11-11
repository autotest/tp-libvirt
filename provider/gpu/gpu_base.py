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
import os
import platform
import re

import requests

from avocado.core import exceptions
from avocado.utils import process

from virttest import libvirt_version
from virttest import test_setup
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml

from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.numa import numa_base
from provider.viommu import viommu_base


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
    if not utils_package.package_install(["pciutils"], session):
        raise exceptions.TestError("Unable to install pciutils")
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
    return dict(zip(["domain", "bus", "slot", "function"], pci_list))

def verify_pf_not_sole_uplink(test):
    """
    Verify that the host has an independent physical interface besides
    the PF that will be selected. Running this avoids any
    disruption to host connectivity if the check needs to cancel.

    :param test: test object, for test.cancel()
    """
    pf_pci = utils_sriov.get_pf_pci()
    pf_info = utils_sriov.get_pf_info_by_pci(pf_pci)
    pf_name = pf_info.get('iface')
    phy_ifaces, _ = utils_net.get_sorted_net_if()
    linked = [i for i in utils_net.get_net_if(state="UP") if i in phy_ifaces]
    if pf_name in linked:
        linked.remove(pf_name)
    if not linked:
        test.cancel("This test needs at least 1 linked interface (excluding PF) available on the host.")

class GPUTest(object):
    """
    Wrapper class for GPU testing
    """
    def __init__(self, vm, test, params, session=None, sriov_helper=None):
        self.vm = vm
        self.test = test
        self.params = params
        self.session = session
        self.remote_virsh_dargs = None
        self.sriov_helper = sriov_helper

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
        try:
            if not utils_package.package_install(["pciutils"], vm_session):
                self.test.error("Unable to install pciutils in guest")
            s, o = vm_session.cmd_status_output("lspci |grep 3D")
            result = process.CmdResult(stdout=o, exit_status=s)
            libvirt.check_exit_status(result, status_error)
        finally:
            vm_session.close()

    def nvidia_smi_check(self, vm_session):
        """
        Run nvidia-smi command and check GPU device's info

        :param vm_session: vm's session
        """
        gpu_pci = get_gpu_pci(session=vm_session)
        self.test.log.info(f"gpu_pci from get_gpu_pci = {gpu_pci}")
        s, o = vm_session.cmd_status_output("nvidia-smi -q")
        self.test.log.info(f"nvidia-smi status={s}, output contains: {o[:200]}")
        if s or not re.search(gpu_pci, o, re.IGNORECASE):
            self.test.fail("Failed to run nvidia-smi command. Status: %s, output: %s."
                           % (s, o))

    def install_latest_driver(self, vm_session, local_rpm=False):
        """
        Install the latest data centre driver

        :param vm_session: vm session object
        :local_rpm: Install the driver using local rpm
        """
        # Remove any pre-existing CUDA repo configs
        vm_session.cmd("rm -f /etc/yum.repos.d/cuda*.repo || true")

        pkgs = ["gcc", "kernel-headers", "kernel-devel", "kernel-64k-devel", "wget"]
        if not utils_package.package_install(pkgs, vm_session, timeout=1200):
            self.test.error(f"Unable to install {pkgs} in guest")
        arch = platform.machine()

        # Detect guest RHEL version
        distro_info = vm_session.cmd_output("grep '^VERSION_ID=' /etc/os-release")
        version_match = re.search(r'VERSION_ID="?(\d+)', distro_info)
        if version_match:
            rhel_version = version_match.group(1)
            distro = f"rhel{rhel_version}"
        else:
            self.test.log.warning("Unable to detect RHEL version from guest, defaulting to rhel9")
            rhel_version = "9"
            distro = "rhel9"

        if local_rpm:
            url = "https://developer.nvidia.com/datacenter-driver-downloads"
            with requests.Session() as session:
                page = session.get(url)
            pkg_download_cmd = re.findall(rf"(wget https://developer.download.nvidia.com/compute/nvidia-driver/\S+/local_installers/nvidia-driver-local-repo-rhel{rhel_version}-\S+{arch}.rpm)", page.text)
            if not pkg_download_cmd:
                self.test.error(f"Unable to find download command for RHEL {rhel_version} on {url}")
            pkg_name = os.path.basename(pkg_download_cmd[0])
            vm_session.cmd(pkg_download_cmd[0], timeout=240)
            vm_session.cmd(f"rpm -i {pkg_name}", timeout=120)
        else:
            pkg_mgr = utils_package.package_manager(vm_session, "epel-release")
            if not pkg_mgr.is_installed("epel-release"):
                vm_session.cmd_output_safe(f"subscription-manager repos --enable=rhel-{rhel_version}-for-{arch}-appstream-rpms")
                vm_session.cmd_output_safe(f"subscription-manager repos --enable=rhel-{rhel_version}-for-{arch}-baseos-rpms")
                vm_session.cmd_output_safe(f"subscription-manager repos --enable=codeready-builder-for-rhel-{rhel_version}-{arch}-rpms")
                vm_session.cmd_output_safe(f"dnf -y install https://dl.fedoraproject.org/pub/epel/epel-release-latest-{rhel_version}.noarch.rpm", timeout=600)

            if vm_session.cmd_status(f"ls /etc/yum.repos.d/cuda-{distro}.repo"):
                repo_url = f"https://developer.download.nvidia.com/compute/cuda/repos/{distro}/sbsa/cuda-{distro}.repo"
                vm_session.cmd(f"dnf config-manager --add-repo {repo_url}")
                vm_session.cmd(f"sed -i 's/^gpgcheck=1/gpgcheck=0/' /etc/yum.repos.d/cuda-{distro}.repo")
                vm_session.cmd("dnf clean all")
            vm_session.cmd("dnf -y module install nvidia-driver:open-dkms --skip-broken", timeout=600)

    def clone_cuda_samples(self, vm_session):
        """
        Install git and clone cuda-samples on the guest

        :param vm_session: vm session object
        """
        if not utils_package.package_install(["git"], vm_session):
            self.test.error("Unable to install git in guest")
        vm_session.cmd_status_output("rm -rf /root/cuda-samples")
        vm_session.cmd("git clone https://github.com/NVIDIA/cuda-samples --depth 1", timeout=600)

    def install_cuda_toolkit(self, vm_session, runfile=False):
        """
        Install cuda toolkit
        :param vm_session: vm session object
        :runfile: Install the cuda toolkit using runfile
        """
        if runfile:
            if not utils_package.package_install(["wget"], vm_session):
                self.test.error("Unable to install wget in guest")
            url = "https://developer.nvidia.com/cuda-downloads"
            with requests.Session() as session:
                page = session.get(url)
            pkg_download_cmd = re.findall(r"(wget https://developer.download.nvidia.com/compute/cuda/\S+/local_installers/cuda_\S+_linux_sbsa.run)", page.text)
            if not pkg_download_cmd:
                self.test.error(f"Unable to find CUDA download command on {url}")
            pkg_name = os.path.basename(pkg_download_cmd[0])
            vm_session.cmd(pkg_download_cmd[0], timeout=600)
            vm_session.cmd(f"sh {pkg_name} --silent", timeout=600)
        else:
            pkgs = "cuda-toolkit"
            if not utils_package.package_install(pkgs, vm_session, timeout=1200):
                self.test.fail(f"Unable to install {pkgs} in guest")

    def setup_controllers(self, vmxml, last_index):
        """
        Setup PCI controllers for GPU and NIC devices

        :param vmxml: VM XML object
        :param last_index: Last controller index
        :return: Tuple of (vmxml, controller index dict)
        """
        cntl_index = {}
        pcie_root_free_slot = libvirt_pcicontr.get_free_pci_slot(vmxml)
        for count in [1, 2]:
            cntl = "pxb_ctrl_attrs" if count == 1 else "pxb_ctrl_attrs_2"
            pxb_ctrl_attrs = self.params.get(cntl)
            if pxb_ctrl_attrs:
                pxb_index = last_index + 1
                self.test.log.debug("pxb_ctrl_attrs=%s", pxb_ctrl_attrs)
                pxb_ctrl_attrs = eval(pxb_ctrl_attrs % (str(pxb_index), pcie_root_free_slot))
                cntl_pxb = libvirt_vmxml.create_vm_device_by_type("controller", pxb_ctrl_attrs)
                vmxml.add_device(cntl_pxb)
                pcie_root_free_slot = "%0#4x" % (int(pcie_root_free_slot, 16) +1)
                cntl_index.update({"pxb_%d" % count: pxb_index})
                last_index = pxb_index
                pcie_root_port_attrs = "pcie_root_port_attrs" if count == 1 else "pcie_root_port_attrs_2"
                pcie_root_port_attrs = self.params.get(pcie_root_port_attrs, '{}')
                if pcie_root_port_attrs:
                    pcie_root_port_index = last_index + 1
                    pcie_root_port_bus = "%0#4x" % int(last_index)
                    pcie_root_port_attrs = eval(pcie_root_port_attrs % (pcie_root_port_index, pcie_root_port_bus))
                    cntl_pcie_root_port = libvirt_vmxml.create_vm_device_by_type("controller", pcie_root_port_attrs)
                    vmxml.add_device(cntl_pcie_root_port)
                    cntl_index.update({"pcie_root_port_%d" % count: pcie_root_port_index})
                    last_index = pcie_root_port_index

                # Upstream port
                    upstream_attrs = "upstream_port_attrs" if count == 1 else "upstream_port_attrs_2"
                    if self.params.get(upstream_attrs):
                        upstream_index = last_index + 1
                        upstream_bus = "%0#4x" % int(last_index)
                        upstream_attrs_eval = eval(self.params.get(upstream_attrs) % (upstream_index, upstream_bus))
                        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", upstream_attrs_eval))
                        cntl_index.update({"upstream_port_%d" % count: upstream_index})
                        last_index = upstream_index

                    # Downstream port
                    downstream_attrs = "downstream_port_attrs" if count == 1 else "downstream_port_attrs_2"
                    if self.params.get(downstream_attrs):
                        downstream_index = last_index + 1
                        downstream_bus = "%0#4x" % int(last_index)
                        downstream_attrs_eval = eval(self.params.get(downstream_attrs) % (downstream_index, downstream_index, downstream_bus))
                        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", downstream_attrs_eval))
                        cntl_index.update({"downstream_port_%d" % count: downstream_index})
                        last_index = downstream_index

        return (vmxml, cntl_index)

    def setup_iommu_device(self, vmxml, cntl_index):
        """
        Setup IOMMU device for GPU and NIC

        :param vmxml: VM XML object
        :param cntl_index: Controller index dict
        :return: Updated vmxml
        """
        smmuv3_desc_defined = self.params.get("smmuv3_desc_defined", "no") == "yes"
        order_list = [1, 2] if not smmuv3_desc_defined else [2, 1]
        for count in order_list:
            iommu_attr = "iommu_dict" if count == 1 else "iommu_dict_2"
            cmdqv_dict = "cmdqv_dict" if count == 1 else "cmdqv_dict_2"
            iommu_dict = self.params.get(iommu_attr)
            cmdqv_dict = self.params.get(cmdqv_dict)
            if iommu_dict:
                pxb_index = cntl_index.get("pxb_%d" % count)
                iommu_dict = eval(iommu_dict % str(pxb_index))
                if cmdqv_dict:
                    cmdqv_dict = eval(cmdqv_dict)
                    iommu_driver = iommu_dict.get("driver")
                    iommu_driver.update(cmdqv_dict)
                iommu_dev = libvirt_vmxml.create_vm_device_by_type("iommu", iommu_dict)
                vmxml.add_device(iommu_dev)

        return vmxml

    def setup_gpu_config(self, vmxml, cntl_index):
        """
        Setup GPU hostdev configuration

        :param vmxml: VM XML object
        :param cntl_index: Controller index dict
        :return: Updated vmxml
        """
        gpu_hostdev_dict = self.params.get('gpu_hostdev_dict', '{}')
        gpu_pci_addr = self.gpu_pci_addr
        if gpu_hostdev_dict:
            attachment_index = cntl_index.get("downstream_port_1") or cntl_index.get("pcie_root_port_1")
            gpu_hostdev_dict = eval(gpu_hostdev_dict % ("%0#4x" % int(attachment_index)))
            gpu_hostdev = libvirt_vmxml.create_vm_device_by_type("hostdev", gpu_hostdev_dict)
            vmxml.add_device(gpu_hostdev)

        return vmxml

    def setup_nic_config(self, vmxml, cntl_index):
        """
        Setup NIC configuration

        :param vmxml: VM XML object
        :param cntl_index: Controller index dict
        :return: Updated vmxml
        """
        if self.sriov_helper:
            pcie_root_port_index = cntl_index.get("pcie_root_port_2")
        if self.params.get('hostdev_dict'):
            iface_dict = self.params.get('hostdev_dict')
            nic_hostdev_dict = eval(iface_dict % ("%0#4x" % int(pcie_root_port_index)))
            nic_hostdev = libvirt_vmxml.create_vm_device_by_type("hostdev", nic_hostdev_dict)
            vmxml.add_device(nic_hostdev)
        elif self.params.get('iface_dict'):
            iface_dict = self.params.get('iface_dict')
            iface_dict = eval(iface_dict % ("%0#4x" % int(pcie_root_port_index)))
            nic_iface = libvirt_vmxml.create_vm_device_by_type("interface", iface_dict)
            vmxml.add_device(nic_iface)

        return vmxml

    def setup_pcie_root(self, vmxml):
        """
        Setup PCIe root controller with pcihole64

        :param vmxml: VM XML object
        :return: Updated vmxml
        """
        pcihole64 = eval(self.params.get('pcihole64', '4294967296'))
        pci_root_dict = {"pcihole64": pcihole64}
        libvirt_vmxml.modify_vm_device(
            vmxml,
            "controller",
            pci_root_dict,
            libvirt_pcicontr.get_pcie_root_index(vmxml),
            sync_vm=False
        )
        return vmxml

    def setup_nvgrace_host_driver(self):
        """
        Load nvgrace drivers and bind GPU to nvgrace-gpu-vfio-pci
        """
        utils_misc.cmd_status_output("modprobe -r nouveau", ignore_status=True)
        status, output = utils_misc.cmd_status_output("modprobe nvgrace-gpu-vfio-pci")
        self.test.log.debug(f"modprobe nvgrace-gpu-vfio-pci: status={status}, output={output}")
        status, output = utils_misc.cmd_status_output("modprobe nvgrace_egm")
        self.test.log.debug(f"modprobe nvgrace_egm: status={status}, output={output}")

        # Check if GPU is already bound to the correct driver
        gpu_info = get_gpus_info()
        current_driver = gpu_info.get(self.gpu_pci, {}).get('driver')

        if current_driver != 'nvgrace_gpu_vfio_pci':
            # Get the GPU vendor:device ID
            gpu_id = gpu_info.get(self.gpu_pci, {}).get('ID')
            if not gpu_id:
                self.test.error(f"Unable to get vendor:device ID for GPU {self.gpu_pci}. Available GPUs: {list(gpu_info.keys())}")

            self.test.log.info(f"Binding GPU {self.gpu_pci} (ID: {gpu_id}) to nvgrace-gpu-vfio-pci via new_id")
            status, output = utils_misc.cmd_status_output(f"echo {gpu_id} > /sys/bus/pci/drivers/nvgrace_gpu_vfio_pci/new_id")
            self.test.log.debug(f"Bind GPU via new_id: status={status}, output={output}")

            # Verify binding succeeded
            gpu_info = get_gpus_info()
            new_driver = gpu_info.get(self.gpu_pci, {}).get('driver')
            if new_driver != 'nvgrace_gpu_vfio_pci':
                self.test.error(f"GPU driver binding failed. Expected nvgrace_gpu_vfio_pci, got {new_driver}")
            self.test.log.info(f"GPU successfully bound to nvgrace_gpu_vfio_pci")
        else:
            self.test.log.info(f"GPU already bound to nvgrace_gpu_vfio_pci")


    def prepare_guest_xml(self, plug_nic=False):
        """
        Prepare guest XML with GPU and NIC configuration

        :param plug_nic: Whether to plug NIC later (hotplug scenario)
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        del vmxml.current_mem
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

        self.setup_pcie_root(vmxml)
        pre_idx = libvirt_pcicontr.get_max_contr_indexes(vmxml, "pci", "pcie-root-port")[-1]
        (vmxml, cntl_index) = self.setup_controllers(vmxml, pre_idx)
        self.params["cntl_index"] = cntl_index
        vmxml = self.setup_iommu_device(vmxml, cntl_index)
        vmxml = self.setup_gpu_config(vmxml, cntl_index)
        if not plug_nic:
            vmxml = self.setup_nic_config(vmxml, cntl_index)
        vmxml.sync()
        self.test.log.debug("After setup, guest xml:\n%s", vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name))


    def setup_hugepage(self):
        """
        Setup hugepage configuration for VM
        """
        memory_backing = eval(self.params.get('memory_backing', '{}'))
        hugepage_size = self.params.get('hugepage_size', '512')
        if memory_backing:
            # Convert hugepage_size from MB to KB, mem_value from MB to bytes
            numa_base.adjust_parameters(self.params,
                                        hugepage_size=int(hugepage_size) * 1024,
                                        hugepage_mem=int(self.params.get("mem_value")) * 1024 *1024)
            hpc = test_setup.HugePageConfig(self.params)
            hpc.setup()
            utils_libvirtd.Libvirtd().restart()
            self.params['hpc'] = hpc

    def setup_default(self, **dargs):
        """
        Default setup

        :param dargs: test keywords
        """
        dev_name = dargs.get('dev_name')
        managed_disabled = dargs.get('managed_disabled', False)
        plug_nic = dargs.get("plug_nic", False)

        if dargs.get("remove_hostdev", "yes") == "yes":
            self.test.log.debug("Removing the existing hostdev device...")
            libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'hostdev')

        self.setup_hugepage()

        if dargs.get("test_hopper_gpu", "no") == "yes":
            self.prepare_guest_xml(plug_nic=plug_nic)

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

        hpc = self.params.get('hpc')
        if hpc:
            self.test.log.debug("Teardown: clean up hugepage setting for %d", hpc.hugepage_size)
            hpc.cleanup()

        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
