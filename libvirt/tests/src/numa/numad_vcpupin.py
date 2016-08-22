import logging

from autotest.client import utils
from autotest.client.shared import error

from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_misc
from virttest import utils_test
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
        if numa_memory.get('nodeset'):
            used_node = utils_test.libvirt.cpus_parser(numa_memory['nodeset'])
            logging.debug("set node list is %s", used_node)
            if not status_error:
                for i in used_node:
                    if i > max(node_list):
                        raise error.TestNAError("nodeset %s out of range" %
                                                numa_memory['nodeset'])
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

        host_cpu_count = utils.count_cpus()
        for i in range(host_cpu_count):
            ret = virsh.vcpupin(vm_name, 0, i, debug=True, ignore_status=True)
            if ret.exit_status:
                raise error.TestFail("vcpupin failed while numad running, %s"
                                     % bug_url)
            virsh.vcpuinfo(vm_name, debug=True)
    finally:
        utils.run("service numad stop")
        libvirtd.restart()
        backup_xml.sync()
