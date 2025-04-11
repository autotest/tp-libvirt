# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from avocado.utils import cpu

from virttest import libvirt_cgroup
from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def set_cpu_state(operate_cpu, set_value):
    """
    Set cpu online or offline

    :params: operate_cpu: specific cpu index
    :params: set_value: 1 for online, 0 for offline
    """
    if set_value == "0":
        cpu.online(operate_cpu)
    elif set_value == "1":
        cpu.offline(operate_cpu)


def run(test, params, env):
    """
    Verify numa tuned guest vm is not affected when cpu is offline
    """

    def setup_test():
        """
        Prepare init xml
        """
        numa_info = utils_misc.NumaInfo()
        online_nodes = numa_info.get_online_nodes_withmem()
        test.log.debug("Get online node with memory:%s", online_nodes)

        if online_nodes < 2:
            test.cancel("Expect %d numa nodes at "
                        "least, but found %d" % (2,
                                                 online_nodes))
        node_cpus = numa_info.get_all_node_cpus()[
            online_nodes[offline_node_index]].strip().split(' ')

        params.update({'nodeset': online_nodes[nodeset_index]})
        params.update({'off_cpu': node_cpus[cpu_index]})
        set_cpu_state(params.get('off_cpu'), offline)
        is_cgroupv2 = libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled()
        if not is_cgroupv2:
            test.log.debug("Need to keep original value in cpuset file under "
                           "cgroup v1 environment for later recovery")
            default_cpuset = libvirt_cgroup.CgroupTest(None).\
                get_cpuset_cpus(vm_name)
            params.update({'default_cpuset': default_cpuset})

    def run_test():
        """
        Start vm and check result
        """
        test.log.info("TEST_STEP1: Set hugepage and guest boot ")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs_new = eval(vm_attrs % params['nodeset'])
        vmxml.setup_attrs(**vm_attrs_new)

        result = virsh.define(vmxml.xml, debug=True, ignore_status=True)
        if libvirt_version.version_compare(9, 4, 0) and \
                tuning == "restrictive" and binding == "guest":
            libvirt.check_result(result, expected_fails=err_msg,
                                 check_both_on_error=True)
            return
        else:
            libvirt.check_exit_status(result)

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        test.log.debug("The new xml is:\n%s", vmxml)

        test.log.info("TEST_STEP2: Start vm")
        vm.start()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        is_cgroupv2 = libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled()
        if not is_cgroupv2:
            test.log.debug("Reset cpuset file under cgroup v1 environment")
            libvirt_cgroup.CgroupTest(None).set_cpuset_cpus(
                params['default_cpuset'], vm_name)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    vm_attrs = params.get("vm_attrs")
    nodeset_index = int(params.get('nodeset_index'))
    offline_node_index = int(params.get('offline_node_index'))
    cpu_index = int(params.get('cpu_index'))
    offline = params.get("offline")
    err_msg = params.get("err_msg")
    tuning = params.get("tuning")
    binding = params.get("binding")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
