import logging

from virttest import virsh
from virttest.libvirt_xml import domcapability_xml
from virttest.libvirt_xml import vm_xml


VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def compare_xml(ele_a, ele_b):
    if ele_a.tag != ele_b.tag:
        return False
    if (ele_a.text or '').strip() != (ele_b.text or '').strip():
        return False
    if (ele_a.tail or '').strip() != (ele_b.tail or '').strip():
        return False
    if ele_a.attrib != ele_b.attrib:
        return False
    if len(ele_a) != len(ele_b):
        return False

    children_a = sorted(ele_a, key=lambda x: (x.tag, sorted(x.attrib.items())))
    children_b = sorted(ele_b, key=lambda x: (x.tag, sorted(x.attrib.items())))

    return all(compare_xml(a, b) for a, b in zip(children_a, children_b))


def run(test, params, env):
    """
    Test Start VM with cpu mode in "virsh domcapabilities"
    """
    vm_name = params.get('main_vm')

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        domcap = domcapability_xml.DomCapabilityXML()
        LOG.debug(domcap.xml)

        # Update vm xml with cpu mode from domcapabilities
        cpu = vmxml.cpu
        cpuxml_cap = virsh.hypervisor_cpu_baseline(
            domcap.xml, **VIRSH_ARGS).stdout_text
        cpu.xml = cpuxml_cap
        vmxml.cpu = cpu
        vmxml.sync()

        # cpu xml before vm start up
        cpu_a = vmxml.cpu

        virsh.dumpxml(vm_name, **VIRSH_ARGS)
        virsh.start(vm_name, **VIRSH_ARGS)

        cpu_b = vm_xml.VMXML.new_from_dumpxml(vm_name).cpu
        LOG.debug(f'cpu xml after vm start up:\n{cpu_b}')
        # attr 'check' is different after vm start up, but it's expected
        cpu_a.check = cpu_b.check

        # Compare cpu xml before and after vm start up, it should be the same
        if not compare_xml(cpu_a.xmltreefile.getroot(),
                           cpu_b.xmltreefile.getroot()):
            test.fail('cpu xml changed after vm start')

    finally:
        bkxml.sync()
