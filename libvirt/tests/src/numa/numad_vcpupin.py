import logging

from autotest.client import utils
from autotest.client.shared import error

from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_misc
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test vcpupin while numad is running
    """
    vcpu_placement = params.get("vcpu_placement")
    bug_url = params.get("bug_url", "")
    status_error = "yes" == params.get("status_error", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)

    # Prepare numatune memory parameter dict
    mem_tuple = ('memory_mode', 'memory_placement', 'memory_nodeset')
    numa_memory = {}
    for mem_param in mem_tuple:
        value = params.get(mem_param)
        if value:
            numa_memory[mem_param.split('_')[1]] = value

    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd.start()

    try:
        # Get host numa node list
        host_numa_node = utils_misc.NumaInfo()
        node_list = host_numa_node.online_nodes
        logging.debug("host node list is %s", node_list)
        if len(node_list) < 2:
            test.cancel('Online NUMA nodes less than 2')
        node_a, node_b = min(node_list), max(node_list)
        numa_memory.update({'nodeset': '%d,%d' % (node_a, node_b)})
        # Start numad
        try:
            utils.run("service numad start")
        except error.CmdError, e:
            # Bug 1218149 closed as not a bug, workaround this as in bug
            # comment 12
            logging.debug("start numad failed with %s", e)
            logging.debug("remove message queue of id 0 and try again")
            utils.run("ipcrm msg 0", ignore_status=True)
            utils.run("service numad start")

        # Start vm and do vcpupin
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        vmxml.placement = vcpu_placement
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        vm.start()
        vm.wait_for_login()

        # Test vcpupin to the alive cpus list
        cpus_list = utils.cpu_online_map()
        logging.info("active cpus in host are %s", cpus_list)
        for cpu in cpus_list:
            ret = virsh.vcpupin(vm_name, 0, cpu, debug=True,
                                ignore_status=True)
            if ret.exit_status:
                logging.error("related bug url: %s", bug_url)
                raise error.TestFail("vcpupin failed: %s" % ret.stderr)
            virsh.vcpuinfo(vm_name, debug=True)
    finally:
        utils.run("service numad stop")
        libvirtd.restart()
        backup_xml.sync()
