#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import utils_misc
from virttest import migration_template as mt
from virttest.libvirt_xml import vm_xml


class MigrationWithNumaTopology(mt.MigrationTemplate):

    def __init__(self, test, env, params, *args, **dargs):
        super().__init__(test, env, params, *args, **dargs)

    @staticmethod
    @mt.vm_session_handler
    def check_guest_numa_cpu(vm, test, params):
        """
        Check cpu id on each guest numa node

        :param vm: guest vm object
        :param test: test object
        :param params: test parameters
        """
        node_num = int(params.get("node_num"))
        exp_nodes_cpu_list = [utils_misc.cpu_str_to_list(params.get(
            "node_%s_cpu" % i)) if params.get("node_%s_cpu" % i) else [] for i in range(node_num)]
        guest_numa_info = utils_misc.NumaInfo(session=vm.session)
        act_nodes_cpu_list = []
        for node_index in range(len(guest_numa_info.nodes)):
            cpu_list = list(map(int, guest_numa_info.nodes[node_index].cpus))
            act_nodes_cpu_list.append(cpu_list)
            test.log.debug("guest node %s has cpus: %s" %
                           (node_index, cpu_list))
        if exp_nodes_cpu_list != act_nodes_cpu_list:
            test.fail("Expect numa nodes cpu list is %s, but get %s" %
                      (exp_nodes_cpu_list, act_nodes_cpu_list))

    def _pre_start_vm(self):
        """
        Operation before start guest on source host:
        Define the guest
        """
        vm_attrs = eval(self.params.get("vm_attrs", "{}"))
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.main_vm.name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.cpu.topology = eval(self.params.get("topology_dict", "{}"))
        vmxml.sync()

    def _post_start_vm(self):
        """
        Operation after start guest on source host:
        Check guest numa cpu after guest starts
        """
        self.check_guest_numa_cpu(self.main_vm, self.test, self.params)

    def _post_migrate(self):
        """
        Operation after migration:
        Check guest numa cpu after migration
        """
        self.check_guest_numa_cpu(self.main_vm, self.test, self.params)

    def _post_migrate_back(self):
        """
        Operation after back migration:
        Check guest numa cpu after back migration
        """
        self.check_guest_numa_cpu(self.main_vm, self.test, self.params)


def run(test, params, env):
    """
    1. Assign cpu topology with numa node
    2. Start guest
    3. Verify the guest cpu assignment for numa nodes on src host
    4. Do migration
    5. Verify the guest cpu assignment for numa nodes on dest host
    6. Do back migration
    7. Verify the guest cpu assignment for numa nodes on src host
    """

    migrationobj = MigrationWithNumaTopology(test, env, params)
    try:
        migrationobj.runtest()
    finally:
        migrationobj.cleanup()
