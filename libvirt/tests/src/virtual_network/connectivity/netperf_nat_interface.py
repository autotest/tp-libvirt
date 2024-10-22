from provider.virtual_network import network_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Verify the guest can work well under the netperf stress test
    """
    vms = params.get('vms').split()
    vm_objs = [env.get_vm(vm_i) for vm_i in vms]
    network_attrs = eval(params.get('network_attrs'))
    iface_attrs = eval(params.get('iface_attrs'))

    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        libvirt_network.create_or_del_network(network_attrs)
        test.log.debug(f'Network xml:\n'
                       f'{virsh.net_dumpxml(network_attrs["name"]).stdout_text}')
        vmxml_lists = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))
        [libvirt_vmxml.modify_vm_device(vmxml_i, 'interface', iface_attrs)
         for vmxml_i in vmxml_lists]

        test.log.info('TEST_STEP: Start the VM(s)')
        [vm_inst.start() for vm_inst in vm_objs]
        [vm_inst.wait_for_login() for vm_inst in vm_objs]
        network_base.exec_netperf_test(params, env)

    finally:
        [backup_xml.sync() for backup_xml in bkxmls]
        libvirt_network.create_or_del_network(network_attrs, is_del=True)
