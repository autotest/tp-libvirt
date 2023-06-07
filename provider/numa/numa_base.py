#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

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
