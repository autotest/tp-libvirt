import logging
import ast
import os
import re

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def verify_membind_value(schemata_file, mb_value):
    """
    Verify memory bindwidth value in schemata

    :param schemata_file: the file in /sys/fs/resctrl
    :param mb_value: the mb value in above file, such as "MB:0= 60;1= 30"
    :return: if the value can be found, return True, otherwise, return False
    """

    found_mb = False
    schemata_content = process.run("cat %s" % schemata_file).stdout_text
    logging.debug("mb_value:%s." % mb_value)
    for line in schemata_content.splitlines():
        logging.debug("line:%s." % line)
        if re.search(mb_value, line):
            found_mb = True
            break
    return found_mb


def check_membind_value(test, schemata_file, mb_value):
    """
    Verify memory bindwidth value in schemata

    :param schemata_file: the file in /sys/fs/resctrl
    :param mb_value: the mb value in above file, such as "MB:0= 60;1= 30"
    """

    found_mb = verify_membind_value(schemata_file, mb_value)
    if not found_mb:
        test.fail("schemata %s for vcpus is not set valid" % schemata_file)


def check_host(test):
    """
    Check the host for memtune and cachetune tests

    :param test: test object
    """
    # 1.Check virsh capabilities
    if utils_misc.get_cpu_info()['Flags'].find('mba ') == -1:
        test.cancel("This machine doesn't support cpu 'mba' flag")

    # 2.Mount resctrl
    process.run("mount -t resctrl resctrl /sys/fs/resctrl",
                verbose=True, shell=True)
    process.run("echo 'L3:0=0ff;1=0ff' > /sys/fs/resctrl/schemata",
                verbose=True, shell=True)

    # 3.Check host MBA info from virsh capabilities output
    cmd = "virsh capabilities | awk '/<memory_bandwidth>/,\
           /<\/memory_bandwidth>/'"
    out = ""
    out = process.run(cmd, shell=True).stdout_text

    if not re.search('node', out):
        test.fail("There is no memory_bandwidth info in capabilities")


def assemble_memorytune(params):
    """
    Assemble the required list according to the given parameters

    :param params: dict, used parameters
    :return: tuple, (list, list ,list)
    """
    # 4.Add memory bandwidth in domain XML
    memorytune_item_list = [ast.literal_eval(x)
                            for x in params.get("memorytune_items",
                                                "").split(';')]
    node_item_list1 = [ast.literal_eval(x)
                       for x in params.get("node_items1",
                                           "").split(';')]
    node_item_list2 = [ast.literal_eval(x)
                       for x in params.get("node_items2",
                                           "").split(';')]
    node_item_list = []
    node_item_list.append(node_item_list1)
    node_item_list.append(node_item_list2)

    mem_monitor_item1 = [ast.literal_eval(x)
                         for x in params.get("mem_monitor_item1",
                                             "").split(';')]
    mem_monitor_item2 = [ast.literal_eval(x)
                         for x in params.get("mem_monitor_item2",
                                             "").split(';')]
    mem_monitor_item_list = []
    mem_monitor_item_list.append(mem_monitor_item1)
    mem_monitor_item_list.append(mem_monitor_item2)

    return (memorytune_item_list, node_item_list, mem_monitor_item_list)


def update_with_memorytune(cputunexml, memorytune_item_list,
                           node_item_list, mem_monitor_item_list):
    """
    Update cputunexml with the memorytune xml for tests

    :param cputunexml: VMCPUTuneXML object
    :param memorytune_item_list: list of memorytune info
    :param node_item_list: list of nodexml info
    :param mem_monitor_item_list: list of monitorxml info
    :return: the updated VMCPUTuneXML object
    """
    for mitem in range(len(memorytune_item_list)):
        logging.debug("node %d " % mitem)
        memorytunexml = vm_xml.MemoryTuneXML()

        memorytunexml.vcpus = memorytune_item_list[mitem]['vcpus']
        for node in node_item_list[mitem]:
            nodexml = memorytunexml.NodeXML()
            nodexml.id = node['id']
            nodexml.bandwidth = node['bandwidth']
            memorytunexml.set_node(nodexml)

        for monitor in mem_monitor_item_list[mitem]:
            monitorxml = memorytunexml.MonitorXML()
            monitorxml.vcpus = monitor['vcpus']
            memorytunexml.set_monitor(monitorxml)
        logging.debug("memorytunexml.xml %s" % memorytunexml.xml)

        cputunexml.set_memorytune(memorytunexml)
        logging.debug("cputunexml.xml %s" % cputunexml.xml)

    return cputunexml


def update_with_cachetune(cputunexml, params):
    """
    Update cputunexml with the cachetune xml for tests

    :param cputunexml: VMCPUTuneXML object
    :param params: dict, used parameters
    :return: the updated VMCPUTuneXML object
    """
    cachetune_item_list = [ast.literal_eval(x)
                           for x in params.get("cachetune_items",
                                               "").split(';')]
    cache_item_list = [ast.literal_eval(x)
                       for x in params.get("cache_items",
                                           "").split(';')]
    monitor_item_list = [ast.literal_eval(x)
                         for x in params.get("monitor_items",
                                             "").split(';')]
    for citem in range(len(cachetune_item_list)):
        logging.debug("cache %d " % citem)
        cachetunexml = vm_xml.CacheTuneXML()
        logging.debug("cachetunexml: %s" % cachetunexml)
        cachetunexml.vcpus = cachetune_item_list[citem]['vcpus']
        for cache in cache_item_list:
            cachexml = cachetunexml.CacheXML()
            cachexml.id = cache['id']
            cachexml.level = cache['level']
            cachexml.type = cache['type']
            cachexml.size = cache['size']
            cachexml.unit = cache['unit']
            cachetunexml.set_cache(cachexml)

        for monitor in monitor_item_list:
            monitorxml = cachetunexml.MonitorXML()
            monitorxml.level = monitor['level']
            monitorxml.vcpus = monitor['vcpus']
            cachetunexml.set_monitor(monitorxml)
        cputunexml.set_cachetune(cachetunexml)

    return cputunexml


def check_scheduler_policy(check_policy, test):
    """
    Check scheduler policy for emulator/vcpu/iothread

    :param check_policy: dict, the parameters for checking policy
    :param test: test object
    """
    # Get pid of qemu-kvm
    qemu_kvm_pid = process.run('pidof qemu-kvm', shell=True).stdout_text.strip()
    run_cmd = 'head -v /proc/{}/task/*/comm'.format(qemu_kvm_pid)
    cmd_output = process.run(run_cmd, shell=True).stdout_text.strip()
    # Get pids for iothread/emulator/vcpu according to the given parameter
    for one_item, one_policy in check_policy.items():
        pat = '==> /proc/{}/task/(\d+)/comm <==\n{}'.format(qemu_kvm_pid, one_item)
        match_obj = re.findall(pat, cmd_output)
        if match_obj:
            one_pid = [id for id in match_obj if id != qemu_kvm_pid]
            if not one_pid:
                test.error("Can't find the pid for {} process".format(one_item))
            check_scheduler_policy_by_pid(one_pid[0], one_policy, test)
        else:
            test.error("Can't find the pid for {} process".format(one_item))


def check_scheduler_policy_by_pid(pid, expect_policy, test):
    """
    Check scheduler policy according to given pid

    :param pid: str, the pid to be checked
    :param expect_policy: str, the expected policy
    :param test:  test object
    """
    cmd = 'chrt -p %s' % pid
    chrt_output = process.run(cmd, shell=True).stdout_text.strip()
    pat = "pid {}'s current scheduling policy:\s+(.*)\n".format(pid)
    match_obj = re.findall(pat, chrt_output)
    if match_obj:
        cur_policy = match_obj[0]
        if cur_policy != expect_policy:
            test.fail("Expect the policy for pid {} to be {}, "
                      "but found {}".format(pid, expect_policy, cur_policy))
        else:
            logging.debug("Found the expected policy {} for "
                          "pid {}".format(cur_policy, pid))
    else:
        test.error("No policy information is found for pid {}".format(pid))


def run(test, params, env):
    """
    Test:<memorytune>
    1. Check virsh capabilities report right MBA info
    2  Mount resctrl
    3. Check host MBA info from virsh capabilities output
    4. Add memory bandwidth in domain XML and start vm
    5. check resctrl dir and verify libvirt set right values
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    test_vm = env.get_vm(vm_name)
    schemata_file1 = params.get("schemata_file1", "")
    schemata_file2 = params.get("schemata_file2", "")
    mb_value1 = params.get("mb_value1", "")
    mb_value2 = params.get("mb_value2", "")
    vcpu_max_num = int(params.get("vcpu_max_num"))
    vcpu_current_num = int(params.get("vcpu_current_num"))
    topology_correction = "yes" == params.get("topology_correction", "no")
    cachetune_items = params.get("cachetune_items")
    test_memtune = params.get("test_memtune", 'no') == 'yes'
    memorytune_item_list, node_item_list, mem_monitor_item_list = None, None, None
    vcpupins = params.get("vcpupins")
    placement = params.get('placement')
    iothreads = params.get('iothreads')
    vcpuscheds = params.get('vcpuscheds')
    emulatorsched = params.get('emulatorsched')
    check_policy = params.get('check_policy')

    if test_memtune:
        check_host(test)
        (memorytune_item_list,
         node_item_list,
         mem_monitor_item_list) = assemble_memorytune(params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        # change the vcpu number from 2 to 5
        vmxml.set_vm_vcpus(vm_name, vcpu_max_num, vcpu_current_num,
                           topology_correction=topology_correction)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        if placement:
            vmxml.placement = placement
        if iothreads:
            vmxml.iothreads = int(iothreads)
        cputunexml = vm_xml.VMCPUTuneXML()

        if memorytune_item_list:
            cputunexml = update_with_memorytune(cputunexml,
                                                memorytune_item_list,
                                                node_item_list,
                                                mem_monitor_item_list)

        if cachetune_items:
            cputunexml = update_with_cachetune(cputunexml, params)
        if vcpupins:
            cputunexml.vcpupins = eval(vcpupins)
        if vcpuscheds:
            cputunexml.vcpuscheds = eval(vcpuscheds)
        if emulatorsched:
            cputunexml.emulatorsched = emulatorsched
        logging.debug("cputunexml: %s" % cputunexml)
        vmxml.cputune = cputunexml
        logging.debug("vm xml: %s", vmxml)
        vmxml.sync()
        test_vm.start()
        logging.debug("VM xml after starting:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))
        if test_memtune:
            # 5.Check resctrl dir and verify libvirt set right values
            check_membind_value(test, schemata_file1, mb_value1)
            check_membind_value(test, schemata_file2, mb_value2)
            found_mb = verify_membind_value(schemata_file1, mb_value1)
            if not found_mb:
                test.fail("The first schemata %s for vcpus is not set valid" %
                          schemata_file1)

            found_mb = verify_membind_value(schemata_file2, mb_value2)
            if not found_mb:
                test.fail("The second schemata %s for vcpus is not set valid" %
                          schemata_file2)

            # 6. Check domstats memory
            if libvirt_version.version_compare(6, 0, 0):
                result = virsh.domstats(vm_name, "--memory", ignore_status=True,
                                        debug=True)
                libvirt.check_exit_status(result)
                output = result.stdout.strip()
                logging.debug("domstats output is %s", output)

            # 7. Destroy the vm and verify the libvirt dir exist
            test_vm.destroy(gracefully=False)
            if os.path.exists(schemata_file1) or os.path.exists(schemata_file2):
                test.fail("The schemata file should be deleted after vm destroy")
        if check_policy:
            check_scheduler_policy(eval(check_policy), test)

    finally:
        if test_vm.is_alive():
            test_vm.destroy(gracefully=False)
        if test_memtune:
            process.run("umount /sys/fs/resctrl",
                        verbose=True, shell=True)
        backup_xml.sync()
