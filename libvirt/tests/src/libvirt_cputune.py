import logging
import ast
import os
import re
import time

from virttest import utils_misc

from avocado.utils import process
from avocado.core import exceptions

from virttest.libvirt_xml import vm_xml
from virttest.compat_52lts import decode_to_text as to_text


def verify_membind_value(schemata_file, mb_value):
    """
    Verify memory bindwidth value in schemata
    :param schemata_file: the file in /sys/fs/resctrl
    :param mb_value: the mb value in above file, such as "MB:0= 60;1= 30"
    :param found_mb: whether the value is found
    """

    found_mb = False
    schemata_content = to_text(process.system_output("cat %s" % schemata_file))
    logging.debug("mb_value:%s." % mb_value)
    for line in schemata_content.splitlines():
        logging.debug("line:%s." % line)
        if re.search(mb_value, line):
            found_mb = True
            break
    return found_mb


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

    # 1.Check virsh capabilities
    if not utils_misc.get_cpu_info()['Flags'].find('mba '):
        test.cancel("This machine doesn't support cpu 'mba' flag")

    # 2.Mount resctrl
    process.run("mount -t resctrl resctrl -o mba_MBps /sys/fs/resctrl",
                shell=True)
    process.run("echo 'L3:0=0ff;1=0ff' > /sys/fs/resctrl/schemata",
                shell=True)

    # 3.Check host MBA info from virsh capabilities output
    cmd = "virsh capabilities | awk '/<memory_bandwidth>/,\
           /<\/memory_bandwidth>/'"
    out = ""
    out = to_text(process.system_output(cmd, shell=True))

    if not re.search('node', out):
        raise exceptions.TestFail("There is no memory_bandwidth info"
                                  "in capablities")

    # 4.Add memory bandwidth in domain XML
    memorytune_item_list = [ast.literal_eval(x)
                            for x in params.get("memorytune_items",
                                                "").split()]
    node_item_list1 = [ast.literal_eval(x)
                       for x in params.get("node_items1",
                                           "").split()]
    node_item_list2 = [ast.literal_eval(x)
                       for x in params.get("node_items2",
                                           "").split()]
    node_item_list = []
    node_item_list.append(node_item_list1)
    node_item_list.append(node_item_list2)
    cachetune_items = params.get("cachetune_items")

    try:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        backup_xml = vmxml.copy()

        cputunexml = vm_xml.VMCPUTuneXML()
        logging.debug("cputunexml.xmltreefile: %s" % cputunexml.xmltreefile)

        if memorytune_item_list:
            i = 0
            for i in range(len(memorytune_item_list)):
                logging.debug("node %d " % i)
                memorytunexml = vm_xml.MemoryTuneXML()

                memorytunexml.vcpus = memorytune_item_list[i]['vcpus']
                j = 0
                for node in node_item_list[i]:
                    j = j + 1
                    logging.debug("node %d: %s " % (j, str(node)))
                    nodexml = memorytunexml.NodeXML()
                    nodexml.id = node['id']
                    nodexml.bandwidth = node['bandwidth']
                    memorytunexml.set_node(nodexml)

                logging.debug("memorytunexml.xml %s" % memorytunexml.xml)

                cputunexml.set_memorytune(memorytunexml)
                logging.debug("cputunexml.xml %s" % cputunexml.xml)
            i = i + 1

        if cachetune_items:
            cachetune_item_list = [ast.literal_eval(x)
                                   for x in params.get("cachetune_items",
                                                       "").split()]
            cache_item_list = [ast.literal_eval(x)
                               for x in params.get("cache_items",
                                                   "").split()]
            monitor_item_list = [ast.literal_eval(x)
                                 for x in params.get("monitor_items",
                                                     "").split()]
            i = 0
            for i in range(len(cachetune_item_list)):
                logging.debug("cache %d " % i)
                cachetunexml = vm_xml.CacheTuneXML()
                logging.debug("cachetunexml.xmltreefile: %s" %
                              cachetunexml.xmltreefile)
                cachetunexml.vcpus = cachetune_item_list[i]['vcpus']
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

        vmxml.cputune = cputunexml
        logging.debug("vm xml: %s", vmxml)
        vmxml.sync()
        test_vm.start()

        time.sleep(10)

        # 5.Check resctrl dir and verify libvirt set right values
        found_mb = verify_membind_value(schemata_file1, mb_value1)
        if not found_mb:
            test.fail("schemata %s for vcpus is not set valid" %
                      schemata_file1)
        found_mb = verify_membind_value(schemata_file2, mb_value2)
        if not found_mb:
            test.fail("schemata %s for vcpus is not set valid" %
                      schemata_file2)

        # 6. Destroy the vm and verify the libvirt dir exist
        test_vm.destroy(gracefully=False)
        if os.path.exists(schemata_file1) or os.path.exists(schemata_file2):
            test.fail("The schemata file should be deleted after vm destroy")

    finally:
        if test_vm.is_alive():
            test_vm.destroy(gracefully=False)
        backup_xml.sync()
