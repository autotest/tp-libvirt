#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Dan Zheng <dzheng@redhat.com>
#


import json
import os
import re


from avocado.utils import cpu as cpu_utils
from avocado.utils import process

from virttest import cpu
from virttest import libvirt_cgroup
from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import virsh
from virttest.staging import service
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_misc

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    vcpu_placement = test_obj.params.get('vcpu_placement')
    if vcpu_placement == 'auto':
        numad_service = service.Factory.create_service("numad")
        numad_service.restart()
    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object

    :return: VMXML object updated
    """
    mem_mode = test_obj.params.get('mem_mode')
    if mem_mode == 'none':
        test_obj.params['numa_memory'] = None
    vmxml = test_obj.prepare_vm_xml()
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def produce_expected_error(test_obj):
    """
    produce the expected error message

    :param test_obj: NumaTest object

    :return: str, error message to be checked
    """
    err_msg = test_obj.produce_expected_error()
    mem_mode = test_obj.params.get('mem_mode')
    vm_attrs = eval(test_obj.params.get('vm_attrs'))
    vcpu_placement = vm_attrs.get('placement')
    nodeset = test_obj.params.get('memory_binding_nodeset')

    if mem_mode != 'none' and vcpu_placement == 'static' and nodeset == 'nodeset_undefined':
        new_err_msg = "nodeset for NUMA memory tuning must be set if 'placement' is 'static'"
        err_msg = "%s|%s" % (err_msg, new_err_msg) if err_msg else new_err_msg
    return err_msg


def verify_cgroup_cpu_affinity(test_obj):
    """
    Verify cgroup information

    :param test_obj: NumaTest object
    """
    vm_pid = test_obj.vm.get_pid()
    cg = libvirt_cgroup.CgroupTest(vm_pid)
    is_cgroup2 = cg.is_cgroup_v2_enabled()
    surfix_emulator = 'emulator/cpuset.cpus' if is_cgroup2 else 'cpuset.cpus'
    surfix_vcpu0 = 'vcpu0/cpuset.cpus' if is_cgroup2 else 'cpuset.cpus'
    emulator_cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'),
                                                    surfix_emulator)
    vcpu0_cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'),
                                                 surfix_vcpu0)
    cmd_emulator = 'cat %s' % re.escape(emulator_cpuset_mems_cgroup_path)
    cmd_vcpu0 = 'cat %s' % re.escape(vcpu0_cpuset_mems_cgroup_path)
    for cmd in [cmd_emulator, cmd_vcpu0]:
        cpuset_mems = process.run(cmd, shell=True).stdout_text.strip()
        sub_verify_cpus(test_obj, cpu.cpus_parser(cpuset_mems))

    test_obj.test.log.debug("Step: verify cgroup information about cpu affinity PASS")


def verify_cgroup_mem_binding(test_obj):
    """
    Verify cgroup information

    :param test_obj: NumaTest object
    """
    mem_mode = test_obj.params.get('mem_mode')
    nodeset = test_obj.params.get('nodeset')
    vcpu_placement = eval(test_obj.params.get('vm_attrs')).get('placement')
    vm_pid = test_obj.vm.get_pid()
    cg = libvirt_cgroup.CgroupTest(vm_pid)
    is_cgroup2 = cg.is_cgroup_v2_enabled()
    surfix_emulator = 'emulator/cpuset.mems' if is_cgroup2 else 'cpuset.mems'
    surfix_vcpu0 = 'vcpu0/cpuset.mems' if is_cgroup2 else 'cpuset.mems'
    emulator_cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'),
                                                    surfix_emulator)
    vcpu0_cpuset_mems_cgroup_path = os.path.join(cg.get_cgroup_path(controller='cpuset'),
                                                 surfix_vcpu0)
    cmd_emulator = 'cat %s' % re.escape(emulator_cpuset_mems_cgroup_path)
    cmd_vcpu0 = 'cat %s' % re.escape(vcpu0_cpuset_mems_cgroup_path)
    for cmd in [cmd_emulator, cmd_vcpu0]:
        cpuset_mems = process.run(cmd, shell=True).stdout_text.strip()
        if (vcpu_placement == 'auto' and
                mem_mode in ['strict', 'restrictive'] and
                not nodeset):
            expected_nodeset = test_obj.params['nodeset_numad']
        elif (vcpu_placement == 'auto' and
                mem_mode in ['interleave', 'preferred'] and
                not nodeset):
            expected_nodeset = ''
        elif vcpu_placement != 'auto' and mem_mode == 'none':
            expected_nodeset = ''
        elif vcpu_placement == 'auto' and mem_mode == 'none':
            expected_nodeset = test_obj.params['nodeset_numad']
        elif mem_mode in ['strict', 'restrictive'] and nodeset:
            expected_nodeset = nodeset
        elif mem_mode in ['interleave', 'preferred'] and nodeset:
            expected_nodeset = ''
        if cpuset_mems != expected_nodeset:
            test_obj.test.fail("Expect cpuset.mems=%s, but "
                               "found %s" % (expected_nodeset, cpuset_mems))
    test_obj.test.log.debug("Step: verify cgroup information about "
                            "memory binding node PASS")


def verify_affinity_by_vcpupin(test_obj, test_active=True):
    """
    Verify cpu affinity by running vcpupin command

    :param test_obj: NumaTest object
    :param test_active: boolean, True for testing without any virsh option
    """
    verify_affinity_by_virsh_cmd(test_obj,
                                 virsh.vcpupin,
                                 r'\s+(\d+)\s+(\S+)',
                                 test_active=test_active)
    test_obj.test.log.debug("Step: verify affinity by vcpupin PASS")


def verify_affinity_by_emulatorpin(test_obj, test_active=True):
    """
    Verify cpu affinity by running emulatorpin command

    :param test_obj: NumaTest object
    :param test_active: boolean, True for testing without any virsh option
    """
    verify_affinity_by_virsh_cmd(test_obj,
                                 virsh.emulatorpin,
                                 r"(\*): +(\S+)",
                                 '*',
                                 test_active=test_active)
    test_obj.test.log.debug("Step: verify affinity by emulatorpin PASS")


def verify_affinity_by_iothreadinfo(test_obj, test_active=True):
    """
    Verify cpu affinity by running iothreadinfo command

    :param test_obj: NumaTest object
    :param test_active: boolean, True for testing without any virsh option
    """
    if not test_obj.params.get("iothreads"):
        return
    verify_affinity_by_virsh_cmd(test_obj,
                                 virsh.iothreadinfo,
                                 r'\s+(\d+)\s+(\S+)',
                                 '1',
                                 test_active=test_active)
    test_obj.test.log.debug("Step: verify affinity by iothreadinfo PASS")


def verify_affinity_by_virsh_cmd(test_obj, virsh_cmd,
                                 convert_pattern, key='0',
                                 test_active=True):
    """
    Verify cpu affinity by running a virsh command

    :param test_obj: NumaTest object
    :param virsh_cmd: virsh command to run
    :param convert_pattern: str, pattern to convert virsh output
    :param key: str, the key to read from the virsh output list
    :param test_active: boolean, True for testing without any virsh option
    """
    if test_active:
        res = virsh_cmd(test_obj.vm.name,
                        **test_obj.virsh_dargs).stdout_text.strip()
        cpu_affinity_active = libvirt_misc.convert_to_dict(res,
                                                           pattern=convert_pattern)
        sub_verify_cpus(test_obj, cpu.cpus_parser(cpu_affinity_active[key]))

    res = virsh_cmd(test_obj.vm.name,
                    options='--config',
                    **test_obj.virsh_dargs).stdout_text.strip()
    cpu_affinity_config = libvirt_misc.convert_to_dict(res, pattern=convert_pattern)
    sub_verify_cpus(test_obj, cpu.cpus_parser(cpu_affinity_config[key]), test_active=False)


def sub_verify_cpus(test_obj, actual_cpus, test_active=True):
    """
    Common function to verify cpus

    :param test_obj: NumaTest object
    :param actual_cpus: list of int, like [0, 4, 3]
    :param test_active: boolean, True for testing without any virsh option
    """
    vm_attrs = eval(test_obj.params.get('vm_attrs'))
    vcpu_placement = vm_attrs.get('placement')
    if vcpu_placement == 'auto':
        expected_cpus = get_cpus_by_numad_advisory(test_obj) if test_active else cpu_utils.online_list()
    else:
        expected_cpus = cpu.cpus_parser(vm_attrs['cpuset'])

    actual_cpus = sorted(actual_cpus)
    if expected_cpus != actual_cpus:
        test_obj.test.fail("Expect the cpu affinity list "
                           "to be %s, but found %s" % (expected_cpus, actual_cpus))


def verify_numatune_by_cmd(test_obj, test_active=True):
    """
    Verify numatune result by running virsh numatune command

    :param test_obj: NumaTest object
    :param test_active: boolean, True for testing without any virsh option
    """
    mem_mode = test_obj.params.get('mem_mode')
    nodeset = test_obj.params.get('nodeset')
    vcpu_placement = eval(test_obj.params.get('vm_attrs')).get('placement')

    if test_active:
        res = virsh.numatune(test_obj.vm.name, **test_obj.virsh_dargs).stdout_text.strip()
        info_active = libvirt_misc.convert_to_dict(res, pattern=r'(\S+)\s+:\s+(\S+)')

    res = virsh.numatune(test_obj.vm.name,
                         options='--config',
                         **test_obj.virsh_dargs).stdout_text.strip()
    info_config = libvirt_misc.convert_to_dict(res, pattern=r'(\S+)\s+:\s+(\S+)')
    msg1 = "Expect numa_nodeset=%s, but found %s"
    msg2 = "Expect numa_mode=%s, but found %s"
    expected_value = 'strict' if mem_mode == 'none' else mem_mode

    if test_active and info_active['numa_mode'] != expected_value:
        test_obj.test.fail(msg2 % (expected_value, info_active['numa_mode']))
    if info_config['numa_mode'] != expected_value:
        test_obj.test.fail(msg2 % (expected_value, info_config['numa_mode']))

    if vcpu_placement == 'auto' and mem_mode != 'none' and not nodeset:
        expected_value = test_obj.params['nodeset_numad']
        if test_active and info_active['numa_nodeset'] != expected_value:
            test_obj.test.fail(msg1 % (expected_value, info_active['numa_nodeset']))
        if info_config.get('numa_nodeset'):
            test_obj.test.fail(msg1 % ('', info_config['numa_nodeset']))
    elif vcpu_placement != 'auto' and mem_mode == 'none':
        if test_active and info_active.get('numa_nodeset'):
            test_obj.test.fail(msg1 % ('', info_active['numa_nodeset']))
        if info_config.get('numa_nodeset'):
            test_obj.test.fail(msg1 % ('', info_config['numa_nodeset']))
    elif vcpu_placement == 'auto' and mem_mode == 'none':
        expected_value = test_obj.params['nodeset_numad']
        if test_active and info_active['numa_nodeset'] != expected_value:
            test_obj.test.fail(msg1 % (expected_value, info_active['numa_nodeset']))
        if info_config.get('numa_nodeset'):
            test_obj.test.fail(msg1 % ('', info_config['numa_nodeset']))
    elif mem_mode != 'none' and nodeset:
        expected_value = nodeset
        if test_active and info_active['numa_nodeset'] != expected_value:
            test_obj.test.fail(msg1 % (expected_value, info_active['numa_nodeset']))
        if info_config['numa_nodeset'] != expected_value:
            test_obj.test.fail(msg1 % (expected_value, info_config['numa_nodeset']))
    test_obj.test.log.debug("Step: verify numatune information by numatune command PASS")


def verify_numatune_by_xml(test_obj):
    """
    Verify numatune value by searching in vm xml

    :param test_obj: NumaTest object
    """
    mem_mode = test_obj.params.get('mem_mode')
    nodeset = test_obj.params.get('nodeset')
    vcpu_placement = eval(test_obj.params.get('vm_attrs')).get('placement')
    test_obj.virsh_dargs.update({'ignore_status': False})
    vmxml = vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name)

    if vcpu_placement == 'static' and mem_mode == 'none':
        xpaths_text = [{'element_attrs': ["./numatune"]}]
        if libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text, ignore_status=True):
            test_obj.test.fail("numatune should not exist in vm xml")
    elif vcpu_placement == 'auto' and mem_mode == 'none':
        xpaths_text = [{'element_attrs': ["./numatune/memory[@mode='strict']",
                                          "./numatune/memory[@placement='auto']"]}]
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text)
        xpaths_text = [{'element_attrs': ["./numatune/memory[@nodeset]"]}]
        if libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text, ignore_status=True):
            test_obj.test.fail("nodeset should not exist in vm xml")
    elif vcpu_placement == 'auto' and mem_mode != 'none' and not nodeset:
        xpaths_text = [{'element_attrs': ["./numatune/memory[@mode='%s']" % mem_mode,
                                          "./numatune/memory[@placement='auto']"]}]
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text)
        xpaths_text = [{'element_attrs': ["./numatune/memory[@nodeset]"]}]
        if libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text, ignore_status=True):
            test_obj.test.fail("nodeset should not exist in vm xml")
    elif mem_mode != 'none' and nodeset:
        xpaths_text = [{'element_attrs': ["./numatune/memory[@mode='%s']" % mem_mode,
                                          "./numatune/memory[@nodeset='%s']" % nodeset]}]
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text)
        xpaths_text = [{'element_attrs': ["./numatune/memory[@placement]"]}]
        if libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, xpaths_text, ignore_status=True):
            test_obj.test.fail("placement should not exist in vm xml")

    test_obj.test.log.debug("Step: verify numatune information by vm xml PASS")


def verify_nodeset_from_numad(test_obj):
    """
    Verify nodeset information in runtime log file to be same
    as that numad service advises

    :param test_obj: NumaTest object
    """
    vcpu_placement = eval(test_obj.params.get('vm_attrs')).get('placement')
    if vcpu_placement != 'auto':
        return
    nodeset_numad = test_obj.get_nodeset_from_numad_advisory()
    test_obj.params['nodeset_numad'] = nodeset_numad
    log_file = '/run/libvirt/qemu/%s.xml' % test_obj.vm.name
    cmd = "grep nodeset=\\'%s\\' %s" % (nodeset_numad, log_file)
    process.run(cmd, shell=True, verbose=True, ignore_status=False)
    test_obj.test.log.debug("Step: verify nodeset information in log file PASS")


def verify_cpus_mems_allowed_list(test_obj):
    """
    Verify cpus_allowed_list and mems_allowed_list in runtime

    :param test_obj: NumaTest object
    """
    cmd = "cat /proc/`pidof qemu-kvm`/status"
    ret = process.run(cmd, shell=True, verbose=True,
                      ignore_status=False).stdout_text.strip()
    cpus = re.findall(r"Cpus_allowed_list:\s+(\S+)", ret)
    mems = re.findall(r"Mems_allowed_list:\s+(\S+)", ret)
    verify_cpus_allowed_list(test_obj, cpu.cpus_parser(cpus[0]))
    verify_mems_allowed_list(test_obj, cpu.cpus_parser(mems[0]))
    test_obj.test.log.debug("Step: verify Cpus_allowed_list and "
                            "Mems_allowed_list in runtime PASS")


def get_cpus_by_numad_advisory(test_obj):
    """
    Get host cpus on nodes which numad service advises

    :param test_obj: NumaTest object
    :return: list, cpus in order
    """
    nodeset_numad = test_obj.params['nodeset_numad']
    node_list = cpu.cpus_parser(nodeset_numad)
    cpus = []
    for one_node in node_list:
        cpus_from_numactl = re.findall("node %s cpus: (.*)" % one_node,
                                       test_obj.host_numactl_info)
        if cpus_from_numactl:
            cpus_from_numactl = cpus_from_numactl[0].strip().split(' ')
            cpus_from_numactl = [int(item) for item in cpus_from_numactl]
        else:
            test_obj.test.error("Can not find node %s's cpus" % nodeset_numad)
        cpus += cpus_from_numactl
    test_obj.test.log.debug("The cpus on the node advised by numad: %s", cpus)
    return sorted(cpus)


def verify_cpus_allowed_list(test_obj, cpus):
    """
    Verify cpus_allowed_list in runtime

    :param test_obj: NumaTest object
    :param cpus: list of cpus
    """
    vm_attrs = eval(test_obj.params.get('vm_attrs'))
    vcpu_placement = vm_attrs.get('placement')
    if vcpu_placement == 'auto':
        cpus_from_numactl = get_cpus_by_numad_advisory(test_obj)
        cpus_from_runtime = sorted(cpus)
        if cpus_from_runtime != cpus_from_numactl:
            test_obj.test.fail("Expect cpus to be %s, "
                               "but found %s" % (cpus_from_numactl,
                                                 cpus_from_runtime))
    else:
        expected_cpus = cpu.cpus_parser(vm_attrs['cpuset'])
        if cpus != expected_cpus:
            test_obj.test.fail("Expect cpus to be %s, "
                               "but found %s" % (expected_cpus,
                                                 cpus))
    test_obj.test.log.debug("Step: verify Cpus_allowed_list PASS")


def verify_mems_allowed_list(test_obj, nodes):
    """
    Verify mems_allowed_list in runtime

    :param test_obj: NumaTest object
    :param nodes: list of numa nodes
    """
    vcpu_placement = eval(test_obj.params.get('vm_attrs')).get('placement')
    mem_mode = test_obj.params.get('mem_mode')
    nodeset = test_obj.params.get('nodeset')
    if (vcpu_placement == 'auto' and mem_mode in ['strict', 'restrictive'] and
            not nodeset) or (vcpu_placement == 'auto' and mem_mode == 'none'):
        expected_nodeset = test_obj.params.get("nodeset_numad")
    elif (vcpu_placement == 'auto' and mem_mode in ['interleave', 'preferred'] and
            not nodeset) or (vcpu_placement == 'static' and mem_mode == 'none'):
        expected_nodeset = test_obj.online_nodes_withmem
    elif (mem_mode in ['strict', 'restrictive'] and nodeset):
        expected_nodeset = nodeset
    elif (mem_mode in ['interleave', 'preferred'] and nodeset):
        expected_nodeset = test_obj.online_nodes_withmem
    test_obj.test.log.debug("The expected_nodeset before parsing: %s", expected_nodeset)
    if not isinstance(expected_nodeset, list):
        expected_nodeset = cpu.cpus_parser(expected_nodeset)
    if nodes != expected_nodeset:
        test_obj.test.fail("Expect nodeset to be %s, "
                           "but found %s" % (expected_nodeset, nodes))
    test_obj.test.log.debug("Step: verify Mems_allowed_list PASS")


def verify_affinity_by_taskset(test_obj, pid):
    """
    Verify affinity by running taskset command

    :param test_obj: NumaTest object
    :param pid: str, process id
    """
    cmd = "taskset --cpu-list -p %s" % pid
    ret = process.run(cmd, shell=True, verbose=True,
                      ignore_status=False).stdout_text.strip()
    cpus = re.findall(r"current affinity list:\s+(\S+)", ret)
    if not cpus:
        test_obj.test.error("Fail to get current affinity list in %s" % ret)
    actual_cpus = [int(item) for item in cpu.cpus_parser(cpus[0])]
    sub_verify_cpus(test_obj, actual_cpus)
    test_obj.test.log.debug("Step: verify affinity by taskset command PASS")


def verify_affinity_by_taskset_from_vmid(test_obj):
    """
    Verify affinity by running taskset command with vm id

    :param test_obj: NumaTest object
    """
    vm_pid = process.run('pidof qemu-kvm', shell=True,
                         verbose=True).stdout_text.strip()
    verify_affinity_by_taskset(test_obj, vm_pid)
    test_obj.test.log.debug("Step: verify affinity by taskset command from vmid PASS")


def verify_affinity_by_taskset_from_threadid(test_obj):
    """
    Verify affinity by running taskset command with threadid

    :param test_obj: NumaTest object
    """
    res = virsh.qemu_monitor_command(test_obj.vm.name, 'cpus',
                                     options='--hmp info',
                                     **test_obj.virsh_dargs).stdout_text.strip()

    thread_ids = libvirt_misc.convert_to_dict(res,
                                              pattern=r'CPU\s+#(\d+):\s+thread_id=(\d+)')
    verify_affinity_by_taskset(test_obj, thread_ids['0'])
    test_obj.test.log.debug("Step: verify affinity by taskset command from threadid PASS")


def verify_affinity_by_taskset_from_iothreadid(test_obj):
    """
    Verify affinity by running taskset command with iotheadid

    :param test_obj: NumaTest object
    """
    if not test_obj.params.get('iothreads'):
        return
    res = virsh.qemu_monitor_command(test_obj.vm.name,
                                     '{"execute":"query-iothreads"}',
                                     options='--pretty',
                                     **test_obj.virsh_dargs).stdout_text.strip()
    output = json.loads(res)
    verify_affinity_by_taskset(test_obj, output['return'][0]['thread-id'])
    test_obj.test.log.debug("Step: verify affinity by taskset command from iothreadid PASS")


def verify_affinity_by_vcpuinfo(test_obj):
    """
    Verify affinity by running vcpuinfo command

    :param test_obj: NumaTest object
    """
    res = virsh.vcpuinfo(test_obj.vm.name,
                         options='--pretty',
                         **test_obj.virsh_dargs).stdout_text.strip()
    cpus = re.findall(r'CPU Affinity:\s+(.*) .*out of', res)
    actual_cpus = [int(item) for item in cpu.cpus_parser(cpus[0])]
    sub_verify_cpus(test_obj, actual_cpus)
    test_obj.test.log.debug("Step: verify affinity by vcpuinfo command PASS")


def check_start_failure(test_obj, ret):
    """
    Check vm start failure message

    :param test_obj: NumaTest object
    :param ret: CmdResult object
    """
    if ret.exit_status:
        vm_attrs = eval(test_obj.params.get('vm_attrs'))
        vcpu_placement = vm_attrs.get('placement')
        err_msg = "NUMA memory tuning in 'preferred' mode only supports single node"
        if (vcpu_placement == 'auto' and
                ret.stderr_text.count(err_msg) and
                not libvirt_version.version_compare(9, 3, 0)):
            test_obj.test.cancel(ret)
        else:
            test_obj.test.fail(ret)


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    def _cgroup_tests():
        """
        A group test for cgroup
        """
        verify_cgroup_cpu_affinity(test_obj)
        verify_cgroup_mem_binding(test_obj)

    def _affinity_numatune_tests(test_active=True):
        """
        A group test for affinity and numatune related

        :param test_active: boolean, True for running vm, False for shutoff vm
        """
        verify_affinity_by_vcpupin(test_obj, test_active=test_active)
        verify_affinity_by_emulatorpin(test_obj, test_active=test_active)
        verify_affinity_by_iothreadinfo(test_obj, test_active=test_active)
        verify_numatune_by_cmd(test_obj, test_active=test_active)

    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.debug("Step: define vm and check results")
    test_obj.virsh_dargs.update({'ignore_status': True})
    ret = virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    err_msg_expected = produce_expected_error(test_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    if err_msg_expected:
        return
    test_obj.test.log.debug("After vm is defined, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    test_obj.test.log.debug("Step: start vm")
    ret = virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    check_start_failure(test_obj, ret)
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    test_obj.vm.wait_for_login().close()
    verify_numatune_by_xml(test_obj)
    verify_nodeset_from_numad(test_obj)
    verify_cpus_mems_allowed_list(test_obj)
    verify_affinity_by_taskset_from_vmid(test_obj)
    verify_affinity_by_taskset_from_threadid(test_obj)
    verify_affinity_by_taskset_from_iothreadid(test_obj)
    verify_affinity_by_vcpuinfo(test_obj)
    _cgroup_tests()
    _affinity_numatune_tests()
    utils_libvirtd.Libvirtd().restart()
    _cgroup_tests()
    _affinity_numatune_tests()
    virsh.destroy(test_obj.vm.name, **test_obj.virsh_dargs)
    _affinity_numatune_tests(test_active=False)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
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
