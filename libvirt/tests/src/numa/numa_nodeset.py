import logging as log

from virttest import libvirt_xml
from virttest import utils_misc
from virttest import utils_test
from virttest import virsh


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def update_xml(vm_name, params):
    numa_info = utils_misc.NumaInfo()
    online_nodes = numa_info.get_all_nodes()
    cpu_mode = params.get('cpu_mode', 'host-model')
    # Prepare a memnode list
    numa_memnode = [{
        'mode': params.get('memory_mode', 'strict'),
        'cellid': params.get('cellid', '0'),
        # Take a first node above available nodes.
        'nodeset': params.get('memory_nodeset', str(int(online_nodes[-1]) + 1))
    }]
    # Prepare a numa cells list
    numa_cells = [{
        'id': '0',
        # cpus attribute is optional and if omitted a CPU-less NUMA node is
        # created as per libvirt.org documentation.
        'memory': '512000',
        'unit': 'KiB'}
    ]
    vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    del vmxml.numa_memory

    vmxml.setup_attrs(**{
        'numa_memnode': numa_memnode,
        'cpu': {'reset_all': True, 'mode': cpu_mode, 'numa_cell': numa_cells}
    })

    logging.debug("vm xml is %s", vmxml)
    vmxml.sync()


def run(test, params, env):
    """
    Test the numatune nodeset for invalid value.
    """
    vm_name = params.get("main_vm")
    error_message = params.get("error_msg")
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    try:
        update_xml(vm_name, params)
        # Try to start the VM with invalid node, fail is expected
        ret = virsh.start(vm_name, ignore_status=True)
        utils_test.libvirt.check_status_output(ret.exit_status,
                                               ret.stderr_text,
                                               expected_fails=[error_message])
    except Exception as e:
        test.error("Unexpected error: {}".format(e))
    finally:
        backup_xml.sync()
