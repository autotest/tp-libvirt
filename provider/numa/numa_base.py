#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#
import re

from avocado.core import exceptions
from avocado.utils import memory
from avocado.utils import process

from virttest import cpu
from virttest import libvirt_version
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml


class NumaTest(object):
    """
    This class is an utility for testing numa related features

    :param vm: a libvirt_vm.VM class instance
    :param params: dict with the test parameters
    :param test: test object
    """
    def __init__(self, vm, params, test):
        self.params = params
        self.test = test
        self.vm = vm
        cmd = "numactl --hardware"
        status, self.host_numactl_info = utils_misc.cmd_status_output(cmd, shell=True)
        if status != 0:
            test.error("Failed to get information from %s", cmd)
        self.host_numa_info = utils_misc.NumaInfo()
        self.online_nodes = self.host_numa_info.get_online_nodes().copy()
        self.online_nodes_withmem = self.host_numa_info.get_online_nodes_withmem().copy()
        self.virsh_dargs = {'ignore_status': False, 'debug': True}

    def check_numa_nodes_availability(self, expect_nodes_num=2, expect_node_free_mem_min=None):
        """
        Check if the host numa nodes are available for testing

        :param expect_nodes_num: int, the number of host numa nodes
        :param expect_node_free_mem_min: int, the minimum of the node free memory
        """
        if len(self.online_nodes_withmem) < expect_nodes_num:
            self.test.cancel("Expect %d numa nodes at "
                             "least, but found %d" % (expect_nodes_num,
                                                      len(self.online_nodes_withmem)))
        self.test.log.debug("The number of host numa node with "
                            "memory is %s which meets the "
                            "requirement", len(self.online_nodes_withmem))
        if expect_node_free_mem_min:
            for a_node in self.online_nodes_withmem:
                free_mem_min = self.host_numa_info.read_from_node_meminfo(a_node, 'MemFree')
                if int(free_mem_min) < expect_node_free_mem_min:
                    raise exceptions.TestError("Expect the numa node '%s' "
                                               "free memory at least %s, "
                                               "but found %s" % (a_node,
                                                                 expect_node_free_mem_min,
                                                                 free_mem_min))
                self.test.log.debug("Host numa node '%s' free memory "
                                    "is %s which meets the requirement", a_node, free_mem_min)

    def setup(self, node_index=0, expect_nodes_num=2, expect_node_free_mem_min=None):
        self.check_numa_nodes_availability(expect_nodes_num, expect_node_free_mem_min)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        self.params['backup_vmxml'] = vmxml.copy()
        kernel_hp_file = self.params.get('kernel_hp_file')
        if kernel_hp_file and kernel_hp_file.count('/sys/devices/system/node/'):
            self.params['kernel_hp_file'] = kernel_hp_file % self.online_nodes_withmem[node_index]

    def teardown(self):
        if self.vm.is_alive():
            self.vm.destroy()
        backup_vmxml = self.params.get("backup_vmxml")
        if backup_vmxml:
            self.test.log.debug("Teardown: recover the vm xml")
            backup_vmxml.sync()
        hpc_2M = self.params.get('hp_config_2M')
        hpc_1G = self.params.get('hp_config_1G')
        for hpc in [hpc_2M, hpc_1G]:
            if hpc:
                hpc.cleanup()

    def prepare_vm_xml(self):
        """
        Prepare vm xml

        :return: VMXML object
        """
        single_host_node = self.params.get('single_host_node')
        vm_attrs = eval(self.params.get('vm_attrs'))
        numa_memory = self.params.get('numa_memory')
        numa_memnode = self.params.get('numa_memnode')
        memory_backing = eval(self.params.get('memory_backing', '{}'))

        # Setup vm basic attributes
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        vmxml.setup_attrs(**vm_attrs)

        # Setup numa tune attributes
        nodeset = None
        if single_host_node:
            all_nodes = self.online_nodes_withmem
            if single_host_node == 'no':
                # When memory bind to multiple numa nodes,
                # the test only selects the first two numa nodes with memory on the host
                nodeset = ','.join(['%d' % all_nodes[0], '%d' % all_nodes[1]])
            elif single_host_node == 'yes':
                # When memory bind to single numa node, the test only selects
                # the first host numa node.
                nodeset = '%d' % all_nodes[0]
            self.params['nodeset'] = nodeset

        numa_tune_dict = {}
        if numa_memory:
            if numa_memory.count('nodeset'):
                if nodeset:
                    numa_memory = eval(numa_memory % nodeset)
            else:
                numa_memory = eval(numa_memory)
                if nodeset:
                    numa_memory.update({'nodeset': nodeset})
            numa_tune_dict.update({'numa_memory': numa_memory})
        if numa_memnode:
            if numa_memnode.count('%s'):
                numa_memnode = numa_memnode % nodeset
            numa_tune_dict.update({'numa_memnode': eval(numa_memnode)})
        if numa_tune_dict:
            vmxml.setup_attrs(**numa_tune_dict)

        # Setup memory backing attributes
        if memory_backing:
            mem_backing = vm_xml.VMMemBackingXML()
            mem_backing.setup_attrs(**memory_backing)
            vmxml.mb = mem_backing
        return vmxml

    def produce_expected_error(self):
        """
        produce the expected error message

        :return: str, error message to be checked
        """
        err_msg = self.params.get('err_msg', '')
        mem_mode = self.params.get('mem_mode')
        single_host_node = self.params.get('single_host_node')
        if not libvirt_version.version_compare(9, 3, 0) and \
                mem_mode == 'preferred' and \
                single_host_node is not None and \
                single_host_node != 'yes':
            new_err_msg = "NUMA memory tuning in 'preferred' mode only supports single node"
            err_msg = "%s|%s" % (err_msg, new_err_msg) if err_msg else new_err_msg
        return err_msg

    def get_nodeset_from_numad_advisory(self):
        """
        Get the nodeset advised by numad

        :return: str, the nodeset from numad advisory
        """
        log_file = self.params.get("libvirtd_debug_file")
        cmd = "grep -E 'Nodeset returned from numad:' %s" % log_file
        cmdRes = process.run(cmd, shell=True, ignore_status=False)
        # Sample: Nodeset returned from numad: 0-1
        match_obj = re.search(r'Nodeset returned from numad:\s(.*)', cmdRes.stdout_text)
        numad_ret = match_obj.group(1)
        self.test.log.debug("Nodeset returned from numad: %s", numad_ret)
        return numad_ret


def get_host_numa_memory_alloc_info(mem_size):
    """
    Get the numa memory allocation result on the host

    :param mem_size: int, the vm memory size
    :return: str, the matched output in numa_maps
    """
    cmd = 'grep -B1 %s /proc/`pidof qemu-kvm`/smaps' % mem_size
    out = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
    matches = re.findall('(\w+)-', out)
    if not matches:
        raise exceptions.TestError("Fail to find the pattern '(\w+)-' "
                                   "in smaps output '%s'" % out)
    cmd = 'grep %s /proc/`pidof qemu-kvm`/numa_maps' % matches[0]
    return process.run(cmd, shell=True, verbose=True).stdout_text.strip()


def convert_to_string_with_dash(nodeset):
    """
    Convert the node ids into a string with dash

    :param nodeset: str, node ids, like '0,1' or '0,4'
    :return: str, the string with dash if applied
    """
    nodes = nodeset.split(',')
    if len(nodes) > 1 and int(nodes[1]) == int(nodes[0]) + 1:
        return "%s-%s" % (nodes[0], nodes[1])
    return nodeset


def convert_to_list_of_int(cpus_in_short, cpu_num):
    """
    Convert cpu string to a list of integer

    :param cpus_in_short: str, like '0-3', '0-4,^2'
    :param cpu_num: str, number of cpus
    :return: list, the list of cpu id, like [0, 1]
    """
    cpu_list = cpu.cpus_string_to_affinity_list(cpus_in_short, cpu_num)
    return [index for index in range(0, len(cpu_list)) if cpu_list[index] == 'y']


def check_hugepage_availability(memory_backing):
    """
    Check if the configured huge page size is supported on the system

    :param memory_backing: like {'hugepages': {'pages': [{'size': '1048576', 'unit': 'KiB', 'nodeset': '0'}]}}
    :raises: exceptions.TestSkipError: when hugepage is not supported
    """
    unit_mapping = {'G': 1048576, 'M': 1024, 'KiB': 1}
    supported_hugepages = memory.get_supported_huge_pages_size()
    pages_list = memory_backing['hugepages']['pages']
    for a_page in pages_list:
        size = int(a_page['size'])
        unit = a_page['unit']
        size *= unit_mapping[unit]
        if size not in supported_hugepages:
            raise exceptions.TestSkipError("The hugepage size '%s' is "
                                           "not supported on current "
                                           "arch (support: %s)" % (size,
                                                                   supported_hugepages))
