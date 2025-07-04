import logging
import re
import time

from aexpect import ShellSession
from virttest import virsh
from virttest.libvirt_xml import vm_xml

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test libvirt will pass node-id to qemu when hot-plug vcpu
    """
    vm_name = params.get('main_vm')
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    numa_cell = eval(params.get('numa_cell', '[]'))
    qmp_cmd = params.get('qmp_cmd')

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vmxml.copy()

    vm = env.get_vm(vm_name)

    try:
        vmxml.setup_attrs(**vm_attrs)
        cpu = vmxml.cpu
        cpu.xml = '<cpu/>'
        cpu.numa_cell = cpu.dicts_to_cells(numa_cell)
        vmxml.cpu = cpu
        vmxml.sync()

        virsh.dumpxml(vm_name, **VIRSH_ARGS)

        qmp_sess = ShellSession(qmp_cmd)
        time.sleep(5)

        virsh.start(vm_name, **VIRSH_ARGS)
        virsh.setvcpus(vm_name, vm_attrs['vcpu'], **VIRSH_ARGS)

        virsh.dumpxml(vm_name, **VIRSH_ARGS)

        qmp_out = qmp_sess.get_output()
        LOG.debug(qmp_out)
        qmp_sess.close()

        node_vcpu_map = {int(c['id']): c['cpus'].split(',') for c in numa_cell}
        vcpu_range = range(vm_attrs['current_vcpu'], vm_attrs['vcpu'])

        for line in qmp_out.splitlines():
            if 'device_add' in line:
                LOG.debug(line)
                d = re.search(r'(\{.*\})', line)
                if d:
                    d = eval(d.group(1))
                    LOG.debug(f"Found device add with data {d}")
                    arguments = d.get('arguments', {})
                    vcpu_id = arguments.get('id')
                    if vcpu_id is not None:
                        LOG.info(f"VCPU is {vcpu_id}")
                        vcpu_id_digit = vcpu_id.replace('vcpu', '')
                        if int(vcpu_id_digit) not in vcpu_range:
                            LOG.error(
                                f"VCPU {vcpu_id} is supposed to be in range {list(vcpu_range)}")
                    node_id = arguments.get('node-id')
                    if node_id is not None:
                        LOG.info(f"Node ID is {node_id}")
                    if node_id in node_vcpu_map and vcpu_id.replace('vcpu', '') in node_vcpu_map[node_id]:
                        LOG.info(
                            f"VCPU {vcpu_id} on Node {node_id} found in the qmp output")
                    else:
                        LOG.error(
                            f"VCPU {vcpu_id} on Node {node_id} not found in the qmp output"
                        )
    finally:
        bkxml.sync()
