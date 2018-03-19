import logging

from virttest import libvirt_xml
from virttest import virsh
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test undefine after set preferred numa tuning
    """
    bug_url = params.get("bug_url", "")
    vm_name = params.get("main_vm")
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
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        result = virsh.undefine(vm_name, debug=True, ignore_status=True)
        if result.exit_status:
            test.fail("Undefine vm failed, check %s" % bug_url)
    finally:
        libvirtd.restart()
        backup_xml.sync()
