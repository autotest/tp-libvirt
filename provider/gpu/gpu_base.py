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

from avocado.core import exceptions
from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
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

    def setup_default(self, **dargs):
        """
        Default setup

        :param dargs: test keywords
        """
        dev_name = dargs.get('dev_name')
        managed_disabled = dargs.get('managed_disabled', False)

        self.test.log.debug("Removing the existing hostdev device...")
        libvirt_vmxml.remove_vm_devices_by_type(self.vm, 'hostdev')

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
