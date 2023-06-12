#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

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
        self.host_numa_info = utils_misc.NumaInfo()
        self.online_nodes_withmem = self.host_numa_info.get_online_nodes_withmem()
        self.virsh_dargs = {'ignore_status': False, 'debug': True}

    def check_numa_nodes_availability(self, expect_nodes_num=2):
        if len(self.online_nodes_withmem) < expect_nodes_num:
            self.test.cancel("Expect %d numa nodes at "
                             "least, but found %d" % (expect_nodes_num,
                                                      len(self.online_nodes_withmem)))

    def setup(self):
        self.check_numa_nodes_availability()
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        self.params['backup_vmxml'] = vmxml.copy()

    def teardown(self):
        if self.vm.is_alive():
            self.vm.destroy()
        backup_vmxml = self.params.get("backup_vmxml")
        if backup_vmxml:
            self.test.log.debug("Teardown: recover the vm xml")
            backup_vmxml.sync()

    def prepare_vm_xml(self):
        """
        Prepare vm xml

        :return: VMXML object
        """
        single_host_node = self.params.get('single_host_node') == 'yes'
        vm_attrs = eval(self.params.get('vm_attrs'))
        numa_memory = self.params.get('numa_memory')
        numa_memnode = self.params.get('numa_memnode')
        memory_backing = eval(self.params.get('memory_backing', '{}'))

        # Setup vm basic attributes
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        vmxml.setup_attrs(**vm_attrs)

        # Setup numa tune attributes
        all_nodes = self.online_nodes_withmem
        if not single_host_node:
            # When memory bind to multiple numa nodes,
            # the test only selects the first two numa nodes with memory on the host
            nodeset = ','.join(['%d' % all_nodes[0], '%d' % all_nodes[1]])
        else:
            # When memory bind to single numa node, the test only selects
            # the first host numa node.
            nodeset = '%d' % all_nodes[0]
        self.params['nodeset'] = nodeset
        numa_tune_dict = {}
        if numa_memory:
            numa_memory = eval(numa_memory % nodeset)
            numa_tune_dict.update({'numa_memory': numa_memory})
        if numa_memnode:
            numa_memnode = eval(numa_memnode % nodeset)
            numa_tune_dict.update({'numa_memnode': numa_memnode})
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
        single_host_node = self.params.get('single_host_node') == 'yes'
        if not libvirt_version.version_compare(9, 3, 0) and \
                mem_mode == 'preferred' and \
                not single_host_node:
            new_err_msg = "NUMA memory tuning in 'preferred' mode only supports single node"
            err_msg = "%s|%s" % (err_msg, new_err_msg) if err_msg else new_err_msg
        return err_msg


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
