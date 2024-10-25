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
from virttest.migration_template import MigrationTemplate
from virttest.libvirt_xml import vm_xml


class MigrationWithNumaTopology(MigrationTemplate):

    def __init__(self, test, env, params, *args, **dargs):
        super().__init__(test, env, params, *args, **dargs)

    def _check_guest_numa_cpu(self, session):
        """
        Check cpu id on each guest numa node

        :param session: ShellSession object
        """
        node_num = int(self.params.get("node_num"))
        exp_nodes_cpu_list = [utils_misc.cpu_str_to_list(self.params.get("node_%s_cpu" % i)) if self.params.get("node_%s_cpu" % i) else [] for i in range(node_num)]
        guest_numa_info = utils_misc.NumaInfo(session=session)
        act_nodes_cpu_list = []
        for node_index in range(len(guest_numa_info.nodes)):
            cpu_list = list(map(int, guest_numa_info.nodes[node_index].cpus))
            act_nodes_cpu_list.append(cpu_list)
            self.test.log.debug("guest node %s has cpus: %s" % (node_index, cpu_list))
        if exp_nodes_cpu_list != act_nodes_cpu_list:
            self.test.fail("Expect numa nodes cpu list is %s, but get %s" % (exp_nodes_cpu_list, act_nodes_cpu_list))

    def _pre_start_vm(self):
        """
        Operation before start guest on source host:
        Define the guest
        """
        vm_attrs = eval(self.params.get("vm_attrs", "{}"))
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(self.migrate_main_vm_name.strip())
        vmxml.setup_attrs(**vm_attrs)
        vmxml.cpu.topology = eval(self.params.get("topology_dict", "{}"))
        vmxml.sync()

    def _post_start_vm(self):
        """
        Operation after start guest on source host:
        Check guest numa cpu after guest starts
        """
        session = self.main_vm.wait_for_login()
        self._check_guest_numa_cpu(session)

    def _post_migrate(self):
        """
        Operation after migration:
        Check guest numa cpu after migration
        """
        backup_uri, self.main_vm.connect_uri = self.main_vm.connect_uri, self.dest_uri
        self.main_vm.cleanup_serial_console()
        self.main_vm.create_serial_console()
        session = self.main_vm.wait_for_serial_login()
        self._check_guest_numa_cpu(session)
        self.main_vm.connect_uri = backup_uri


def run(test, params, env):
    """
    1. Assign cpu topology with numa node
    2. Start guest
    3. Verify the guest cpu assignment for numa nodes on src host
    4. Do migration
    5. Verify the guest cpu assignment for numa nodes on dest host
    """

    migrationobj = MigrationWithNumaTopology(test, env, params)
    try:
        migrationobj.runtest()
    finally:
        migrationobj.cleanup()
