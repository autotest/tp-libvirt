import re

from virttest import utils_misc
from virttest import utils_qemu
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml import base


def get_qemu_machines_info(qemu_bin):
    """
    Get the machine types supported by qemu

    :param qemu_bin: qemu binary
    :return: machine types
    """
    qemu_info = utils_qemu.get_machines_info(qemu_bin)
    machine_types = {}
    for k, v in qemu_info.items():
        if k == 'none':
            continue
        _v = {}
        if v.count("alias"):
            alias_m = re.findall('alias of (.*)\)', v)[0]
            _v.update({'canonical': alias_m})
            if qemu_info[alias_m].count("deprecated"):
                _v.update({'deprecated': 'yes'})
        if v.count("deprecated"):
            _v.update({'deprecated': 'yes'})
        machine_types.update({k: _v})
    return machine_types


def get_cap_guest_machine():
    """
    Get guest machine info from virsh capabilities

    :return: guest machine info
    """
    cap_guest = base.LibvirtXMLBase()
    cap_guest.xml = capability_xml.CapabilityXML().get_section_string('guest')
    machine_list = []
    for arch in cap_guest.xmltreefile.findall('arch'):
        machines = {}
        for m in arch.findall('machine'):
            values = dict(m.items())
            values.pop('maxCpus')
            machines.update({m.text: values})
        machine_list.append(machines)
    return machine_list


def test_machine_types(test, params):
    """
    Check machine types in virsh capabilities

    :param test: test object
    :param params: Dictionary with the test parameters
    """
    qemu_bin = utils_misc.get_binary('qemu-kvm', params)
    updated_qemu_info = get_qemu_machines_info(qemu_bin)
    test.log.debug(f"qemu info: {updated_qemu_info}")

    machine_list = get_cap_guest_machine()
    test.log.debug(f"guest machines: {machine_list}")
    for machine in machine_list:
        if updated_qemu_info != machine:
            test.fail("Unable to get the correct machines!")


def run(test, params, env):
    """
    This file includes to test scenarios for checking outputs of
    virsh capabilities under certain configuration
    """
    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    run_test(test, params)
