#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Dan Zheng <dzheng@redhat.com>
#


from virttest import libvirt_version
from virttest import virsh
from virttest import test_setup
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    if test_obj.params.get('memory_backing'):
        hpc = test_setup.HugePageConfig(test_obj.params)
        hpc.setup()
        test_obj.params['hp_config_obj'] = hpc
    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object
    :return: VMXML object updated
    """

    single_host_node = test_obj.params.get('single_host_node') == 'yes'
    vm_attrs = eval(test_obj.params.get('vm_attrs'))
    mem_device_attrs = eval(test_obj.params.get('mem_device_attrs'))
    overlap_emulatorpin_nodeset = test_obj.params.get('overlap_emulatorpin_nodeset') == 'yes'
    memory_backing = eval(test_obj.params.get('memory_backing', '{}'))
    numa_memory = test_obj.params.get('numa_memory')
    numa_memnode = test_obj.params.get('numa_memnode')

    libvirt_vmxml.remove_vm_devices_by_type(test_obj.vm, 'memory')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(test_obj.vm.name)
    vmxml.setup_attrs(**vm_attrs)
    libvirt_vmxml.modify_vm_device(vmxml, 'memory', mem_device_attrs)
    all_cpus = test_obj.host_numa_info.get_all_node_cpus()
    all_nodes = test_obj.online_nodes_withmem
    if single_host_node:
        #  When emulator pin to single host node,
        #  it only selects the first numa node
        test_obj.params['emulatorpin_nodes'] = all_nodes[0:1]
        cpus_emulatorpin = ','.join(['%s' % host_cpu for host_cpu in
                                     all_cpus[all_nodes[0]].strip().split(' ')])
    else:
        #  When emulator pin to multiple host nodes,
        #  it only selects the first two numa nodes
        test_obj.params['emulatorpin_nodes'] = all_nodes[0:2]
        merged_cpu_list = all_cpus[all_nodes[0]].strip().split(' ') + all_cpus[all_nodes[1]].strip().split(' ')
        cpus_emulatorpin = ','.join(['%s' % host_cpu
                                     for host_cpu in merged_cpu_list])
    test_obj.params['cpus_emulatorpin'] = cpus_emulatorpin
    cputune = vm_xml.VMCPUTuneXML()
    cputune.emulatorpin = cpus_emulatorpin
    vmxml.cputune = cputune

    if overlap_emulatorpin_nodeset:
        #  When memory bind with overlap to emulator pin,
        #  the test only selects the first two numa nodes
        #  with memory on the host, like '0,1'
        nodeset = ','.join(['%d' % all_nodes[0], '%d' % all_nodes[1]])
    else:
        #  When memory bind with non-overlap, the test only selects
        #  another node different from emulator pin. An exception is
        #  when emulator pin to multiple host nodes, here the nodeset
        #  will overlap to emulator pin.
        nodeset = '%d' % all_nodes[1]
    test_obj.params['nodeset'] = nodeset
    numa_memory = eval(numa_memory % nodeset)
    numa_memnode = eval(numa_memnode % nodeset)
    vmxml.setup_attrs(**{'numa_memory': numa_memory, 'numa_memnode': numa_memnode})

    if memory_backing:
        mem_backing = vm_xml.VMMemBackingXML()
        mem_backing.setup_attrs(**memory_backing)
        vmxml.mb = mem_backing
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def produce_expected_error(test_obj):
    """
    produce the expected error message

    :param test_obj: NumaTest object
    :return: str, error message to be checked
    """
    mem_mode_preferred = test_obj.params.get('mem_mode') == 'preferred'
    overlap_emulatorpin_nodeset = test_obj.params.get('overlap_emulatorpin_nodeset') == 'yes'
    err_msg = ''
    if not libvirt_version.version_compare(9, 3, 0) and \
            mem_mode_preferred and \
            overlap_emulatorpin_nodeset:
        err_msg = "NUMA memory tuning in 'preferred' mode only supports single node"

    return err_msg


def convert_to_string_with_comma(nodeset_list):
    """
    Convert the node ids into a string with comma

    :param nodeset_list: list of node ids, like ['0', '4']
    :return: str, the string converted
    """
    return '%d,%d' % (nodeset_list[0], nodeset_list[1]) \
        if len(nodeset_list) > 1 else str(nodeset_list[0])


def get_intersect_in_string(nodeset_list, emulatorpin_nodes):
    """
    Get the intersection nodes between the two given node list

    :param nodeset_list: list, node list numa memory pin to
    :param emulatorpin_nodes: list, node list emulator pin to
    """
    intersect_list = list(set(nodeset_list).intersection(set(emulatorpin_nodes)))
    return convert_to_string_with_comma(intersect_list)


def check_qemu_cmdline(test_obj):
    """
    Check qemu command line

    :param test_obj: NumaTest object
    """
    memory_backing = test_obj.params.get('memory_backing')
    mem_mode_restrictive = test_obj.params.get('mem_mode') == 'restrictive'
    single_host_node = test_obj.params.get('single_host_node') == 'yes'
    overlap_emulatorpin_nodeset = test_obj.params.get('overlap_emulatorpin_nodeset') == 'yes'
    nodeset = test_obj.params.get('nodeset')
    emulatorpin_nodes = test_obj.params['emulatorpin_nodes']
    test_obj.test.log.debug("The memory pins to nodes: %s, "
                            "emulator pin to nodes:%s", nodeset, emulatorpin_nodes)
    nodeset_list = [int(num) for num in nodeset.split(',')]
    test_obj.test.log.debug("The nodeset list for memory pins:%s", nodeset_list)
    nodeset = convert_to_string_with_comma(nodeset_list)

    if memory_backing:
        if mem_mode_restrictive:
            libvirt.check_qemu_cmd_line('"qom-type":"thread-context".*host-nodes', expect_exist=False)
        elif single_host_node and not overlap_emulatorpin_nodeset:
            pat_in_qemu_cmdline = test_obj.params.get('qemu_line_hugepage_without_context') % (nodeset, nodeset)
            libvirt.check_qemu_cmd_line(pat_in_qemu_cmdline)
            libvirt.check_qemu_cmd_line('"qom-type":"thread-context"', expect_exist=False)
        else:
            intersect = get_intersect_in_string(nodeset_list, emulatorpin_nodes)
            test_obj.test.log.debug("The intersection between the nodes emulator pin and memory pin: %s", intersect)
            pat_in_qemu_cmdline = test_obj.params.get('qemu_line_hugepage_with_context') % (intersect, nodeset, intersect, nodeset)
            libvirt.check_qemu_cmd_line(pat_in_qemu_cmdline)
    else:
        if mem_mode_restrictive:
            libvirt.check_qemu_cmd_line('"qom-type":"thread-context".*host-nodes', expect_exist=False)
        else:
            pat_in_qemu_cmdline = test_obj.params.get('qemu_line_default_mem_without_context') % (nodeset, nodeset)
            libvirt.check_qemu_cmd_line(pat_in_qemu_cmdline)
            libvirt.check_qemu_cmd_line('"qom-type":"thread-context"', expect_exist=False)
    test_obj.test.log.debug("Step: check qemu command line PASS")


def verify_attach_memory_device(test_obj, session):
    """
    Verify the free memory is usable in the attached memory device

    :param test_obj: NumaTest object
    :param session:  vm session
    """
    mem_device_attrs = eval(test_obj.params.get('mem_device_attrs', '{}'))
    mem_device = Memory()
    mem_device.setup_attrs(**mem_device_attrs)
    virsh.attach_device(test_obj.vm.name, mem_device.xml, **test_obj.virsh_dargs)
    status, output = libvirt_memory.consume_vm_freememory(session)
    if status:
        test_obj.test.fail("Fail to consume guest memory. Error:%s" % output)
    test_obj.test.log.debug("Step: consume guest memory PASS")


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.debug("Step: define vm and check result")
    virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    test_obj.virsh_dargs.update({'ignore_status': True})
    test_obj.test.log.debug("Step: start vm")
    ret = virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    err_msg_expected = produce_expected_error(test_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    #  Some negative cases will fail to define the vm, so they will return
    #  to skip following steps
    if err_msg_expected:
        return
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    session = test_obj.vm.wait_for_login()
    test_obj.test.log.debug("Step: check the qemu command line")
    check_qemu_cmdline(test_obj)
    test_obj.test.log.debug("Step: attach a memory device and check vm's free memory is usable")
    verify_attach_memory_device(test_obj, session)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    if test_obj.params.get('memory_backing'):
        hpc = test_obj.params.get('hp_config_obj')
        hpc.cleanup()
    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test for numa memory binding with emulator thread pin
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
