import logging as log

from avocado.core import exceptions
from avocado.utils import process

from virttest import libvirt_xml
from virttest import utils_misc
from virttest import libvirt_cgroup


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def change_cpu_state_and_check(cpu, state):
    """
    Change the required cpu state to on/off and check if change is
    properly applied, fail otherwise.

   :param cpu: number of cpu to change the state as string
   :param state: required state to change and check. Can be "on"/"off".
   """
    value = '0' if state == "off" else '1'
    ret = process.run('echo {} > /sys/devices/system/cpu/cpu{}/online'.
                      format(value, cpu), shell=True)
    if ret.exit_status:
        exceptions.TestFail('Cannot turn {} cpu {} due to: {}'.
                            format(state, cpu, ret.stderr_text))
    ret = process.run('cat /sys/devices/system/cpu/cpu{}/online'.
                      format(cpu), shell=True)
    if ret.exit_status:
        exceptions.TestFail('Cannot determine the cpu {} status due to: {}'.
                            format(cpu, ret.stderr_text))
    else:
        if value not in ret.stdout_text:
            exceptions.TestFail('Cannot turn {} cpu {}.'.format(state, cpu))
        else:
            logging.debug('CPU {} successfully turned {}.'.format(cpu, state))


def run(test, params, env):
    """
    Start VM with numatune when set host cpu in unrelated numa node is offline.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    turned_off_cpu = ''
    cpu_index = params.get('cpu_index', "1")

    try:
        numa_info = utils_misc.NumaInfo()
        # Find a suitable cpu on first node with memory
        online_nodes = numa_info.get_online_nodes_withmem()
        node_0_cpus = numa_info.get_all_node_cpus()[online_nodes[0]].strip().split(' ')
        turned_off_cpu = node_0_cpus[int(cpu_index)]
        # CPU offline will change default cpuset and this change will not
        # be reverted after re-online that cpu on v1 cgroup.
        # Need to revert cpuset manually on v1 cgroup.
        if not libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled():
            logging.debug("Need to keep original value in cpuset file under "
                          "cgroup v1 environment for later recovery")
            default_cpuset = libvirt_cgroup.CgroupTest(None).get_cpuset_cpus(vm_name)
        change_cpu_state_and_check(turned_off_cpu, 'off')

        if vm.is_alive():
            vm.destroy()
        if len(online_nodes) < 2:
            test.cancel("Cannot proceed with the test as there is not enough"
                        "NUMA nodes(>= 2) with memory.")
        memory_mode = params.get("memory_mode", "strict")
        # Update the numatune for second node with memory
        numa_memory = {'mode': memory_mode,
                       'nodeset': online_nodes[1]}
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
    except (exceptions.TestFail, exceptions.TestCancel):
        raise
    except Exception as e:
        test.error("Unexpected failure: {}.".format(e))
    finally:
        backup_xml.sync()
        if turned_off_cpu:
            change_cpu_state_and_check(turned_off_cpu, 'on')
            if not libvirt_cgroup.CgroupTest(None).is_cgroup_v2_enabled():
                logging.debug("Reset cpuset file under cgroup v1 environment")
                try:
                    libvirt_cgroup.CgroupTest(None)\
                        .set_cpuset_cpus(default_cpuset, vm_name)
                except Exception as e:
                    test.error("Revert cpuset failed: {}".format(str(e)))
