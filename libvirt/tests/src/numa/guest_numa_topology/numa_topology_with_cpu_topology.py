import re

from avocado.utils import cpu as cpu_utils

from virttest import cpu
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    online_cpus = int(cpu_utils.online_count())
    if online_cpus < 16:
        test_obj.test.cancel("The test needs 16 online "
                             "host cpus at least, but "
                             "only %d exist" % online_cpus)
    test_obj.setup()
    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object
    :return: VMXML object updated
    """
    numa_cells = eval(test_obj.params.get('numa_cells', '[]'))
    vcpu_num = int(test_obj.params.get('vcpu'))
    cpu_topology = eval(test_obj.params.get('cpu_topology', '{}'))
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(test_obj.vm.name)
    if numa_cells:
        if vmxml.xmltreefile.find('cpu') is not None:
            cpuxml = vmxml.cpu
            if cpuxml.xmltreefile.find('topology') is not None:
                del cpuxml.topology
        else:
            cpuxml = vm_xml.VMCPUXML()
        cpuxml.topology = cpu_topology
        cpuxml.numa_cell = numa_cells
        vmxml.cpu = cpuxml
    vmxml.vcpu = vcpu_num
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def verify_vm_cpu_info(test_obj):
    """
    Verify vm's cpu information

    :param test_obj: NumaTest object
    """
    vm_topology = test_obj.vm.get_cpu_topology_in_vm()
    expect_topology = eval(test_obj.params.get('cpu_topology'))
    expect_topology['cores'] = str(int(expect_topology['cores']) * int(expect_topology['dies']))
    for key in expect_topology:
        if expect_topology[key] != vm_topology[key]:
            test_obj.test.fail('Guest cpu %s is expected '
                               'to be %s, but found %s' % (key,
                                                           expect_topology[key],
                                                           vm_topology[key]))


def verify_vm_numa_node(vm_session, test_obj):
    """
    Verify vm's numa node information

    :param vm_session: vm session
    :param test_obj: NumaTest object
    """
    numa_cell_0 = eval(test_obj.params.get('numa_cell_0'))
    numa_cell_1 = eval(test_obj.params.get('numa_cell_1'))
    vm_numainfo = utils_misc.NumaInfo(session=vm_session)
    vm_cpus = vm_numainfo.get_all_node_cpus()
    vm_mems = vm_numainfo.get_all_node_meminfo()
    numa_cell_list = [numa_cell_0, numa_cell_1]
    for node_num in [0, 1]:
        expected_cpu_list = cpu.cpus_parser(numa_cell_list[node_num]['cpus'])
        expected_cpu_list = ' '.join(['%d' % i for i in expected_cpu_list])
        if vm_cpus[node_num].strip() != expected_cpu_list:
            test_obj.test.fail("Guest node %d cpus are "
                               "expected to be '%s', but found "
                               "'%s'" % (node_num,
                                         expected_cpu_list,
                                         vm_cpus[node_num]).strip())
        else:
            test_obj.test.log.debug("Step: check guest node "
                                    "%d cpus to be '%s': "
                                    "PASS",
                                    node_num,
                                    numa_cell_list[node_num]['cpus'])

        if int(vm_mems[node_num]['MemTotal']) > int(numa_cell_list[node_num]['memory']):
            test_obj.test.fail("Guest node %d memory is "
                               "expected to be less than '%s', but found "
                               "'%s'" % (node_num,
                                         numa_cell_list[node_num]['memory'],
                                         vm_mems[node_num]['MemTotal']))
        else:
            test_obj.test.log.debug("Step: check guest node "
                                    "%d memory to be '%s': "
                                    "PASS",
                                    node_num,
                                    vm_mems[node_num]['MemTotal'])


def verify_dmesg_vm_numa_mem(vm_session, test_obj):
    """
    Verify dmesg for vm's numa memory

    :param vm_session: vm session
    :param test_obj: NumaTest object
    """
    cmd = test_obj.params.get('check_dmesg_cmd')
    pattern_dmesg_numa_node0 = test_obj.params.get('pattern_dmesg_numa_node0')
    pattern_dmesg_numa_node1 = test_obj.params.get('pattern_dmesg_numa_node1')
    status, output = vm_session.cmd_status_output(cmd, timeout=300)
    test_obj.test.log.debug("Output with command '%s': \n%s", cmd, output)
    if status:
        test_obj.test.error("Can't get results with command '%s'" % cmd)
    pats = [pattern_dmesg_numa_node0, pattern_dmesg_numa_node1]
    for search_pattern in pats:
        if not re.search(search_pattern, output):
            test_obj.test.fail("The vm numa node memory in dmesg "
                               "is expected to match '%s', "
                               "but not found" % search_pattern)
        else:
            test_obj.test.log.debug("Step: Check vm numa "
                                    "node memory in dmesg "
                                    "with pattern '%s': "
                                    "PASS", search_pattern)


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.debug("Step: define vm")
    virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    if not test_obj.vm.is_alive():
        test_obj.test.log.debug("Step: start vm")
        test_obj.vm.start()
        test_obj.test.log.debug("After vm is started, vm xml:\n"
                                "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    vm_session = test_obj.vm.wait_for_login()
    verify_vm_cpu_info(test_obj)
    verify_vm_numa_node(vm_session, test_obj)
    verify_dmesg_vm_numa_mem(vm_session, test_obj)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test numa topology together with cpu topology
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
