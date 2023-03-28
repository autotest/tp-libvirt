# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Dan Zheng<dzheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object
    :return: VMXML object updated
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(test_obj.vm.name)
    numa_cells = eval(test_obj.params.get('numa_cells', '[]'))
    vcpu_num = int(test_obj.params.get('vcpu'))
    cpu_mode = test_obj.params.get('cpu_mode')
    if vmxml.xmltreefile.find('cpu'):
        cpuxml = vmxml.cpu
    else:
        cpuxml = vm_xml.VMCPUXML()
    cpuxml.mode = cpu_mode
    cpuxml.numa_cell = numa_cells
    vmxml.cpu = cpuxml
    vmxml.vcpu = vcpu_num
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    def _check_distance(actual_distance, expect_distance):
        if actual_distance != expect_distance:
            test_obj.test.fail("Expect the distance is %s, "
                               "but found %s" % (expect_distance,
                                                 actual_distance))
        else:
            test_obj.test.log.debug("Step: Checking cpu distance in the vm is "
                                    "PASS with '%s'", expect_distance)

    numa_cell_0_distance = eval(test_obj.params.get('numa_cell_0_distance', '{}'))
    numa_cell_1_distance = eval(test_obj.params.get('numa_cell_1_distance', '{}'))
    distance_sibling_0_cell_0_expected = numa_cell_0_distance['sibling'][0]['value']
    distance_sibling_1_cell_0_expected = numa_cell_0_distance['sibling'][1]['value']
    distance_sibling_0_cell_1_expected = numa_cell_1_distance['sibling'][0]['value']
    distance_sibling_1_cell_1_expected = numa_cell_1_distance['sibling'][1]['value']

    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.debug("Step: define vm")
    virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    test_obj.vm.start()
    test_obj.test.log.debug("After vm is started, "
                            "vm xml:\n%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    session = test_obj.vm.wait_for_login()
    vm_numainfo = utils_misc.NumaInfo(session=session)
    distance_list = vm_numainfo.get_node_distance('0')
    _check_distance(distance_list[0], distance_sibling_0_cell_0_expected)
    _check_distance(distance_list[1], distance_sibling_1_cell_0_expected)
    distance_list = vm_numainfo.get_node_distance('1')
    _check_distance(distance_list[0], distance_sibling_0_cell_1_expected)
    _check_distance(distance_list[1], distance_sibling_1_cell_1_expected)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test for numa topology with distance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
