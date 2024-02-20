import logging
import re
import time

from virttest import virsh
from virttest.libvirt_xml import VMXML

LOG = logging.getLogger('avocado.' + __name__)


def get_cpus(session):
    """
    Retrieves the CPU extended info

    :param session: VM session
    """
    output = session.cmd_output("lscpu --extended")
    LOG.debug("VM cpus:\n%s", output)
    return [x for x in output.split("\n") if re.match(r"^\s+\d\s", x)]


def run(test, params, env):
    """
    Confirm hotplugged VPCU is available and placed in topology.

    :param test: QEMU test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    vm_name = params.get("main_vm")
    max_vcpus = int(params.get("max_vcpus"))
    current_vcpus = int(params.get("current_vcpus"))
    cores = int(params.get("cores"))
    threads = int(params.get("threads"))
    sockets = int(params.get("sockets"))
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    orig_xml = vmxml.copy()
    hotplug = current_vcpus != max_vcpus

    try:
        VMXML.set_vm_vcpus(vm_name, max_vcpus, current_vcpus,
                           sockets, cores, threads, add_topology=True)
        vm.start()
        with vm.wait_for_login() as session:
            cpus = get_cpus(session)
            if current_vcpus != len(cpus):
                test.fail("Unexpected number of cpus in guest: %s" % len(cpus))
            if hotplug:
                virsh.setvcpus(vm_name, max_vcpus)
                time.sleep(5)
                cpus = get_cpus(session)
                if max_vcpus != len(cpus):
                    test.fail("Unexpected number of cpus in guest: %s" % len(cpus))
    finally:
        orig_xml.sync()
