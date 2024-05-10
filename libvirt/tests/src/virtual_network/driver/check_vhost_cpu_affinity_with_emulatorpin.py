import logging
import random
import re

from avocado.core import exceptions
from avocado.utils import process
from virttest import cpu
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_vmxml

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def get_online_cpus():
    """
    Get online cpus

    :return: a list of online cpus
    """
    cpu_range = cpu.get_cpu_info().get('On-line CPU(s) list')
    online_cpus = [
        (lambda x: range(x[0], x[-1] + 1))(list(map(int, r.split('-'))))
        for r in cpu_range.split(',')
    ]
    online_cpus = [cpu for r in online_cpus for cpu in r]
    LOG.debug(f'Online cpus: {online_cpus}')

    return online_cpus


def check_cpu_affinity(vm_name, pid, epin_set):
    """
    Check cpu affinity with virsh emulatorpin and taskset

    :param vm_name: vm name
    :param pid: pid of vhost
    :param epin_set: pre-set emulatorpin
    """
    epin = libvirt_misc.convert_to_dict(
        virsh.emulatorpin(vm_name, **VIRSH_ARGS).stdout_text,
        r'\s+(\S+):\s+(\S+)')

    if epin['*'] != epin_set:
        raise exceptions.TestFail(f'Incorrect cpu affinity (emulatorpin): '
                                  f'{epin["*"]}, Should be {epin_set}.')

    taskset_output = process.run(
        f'taskset -cap {pid}', shell=True).stdout_text
    search_pattern = "pid \d+'s current affinity list:\s(\S+)"
    search_result = re.search(search_pattern, taskset_output)
    if not search_result or search_result.group(1) != epin_set:
        raise exceptions.TestFail(f'Expect current affinity to be {epin_set}, '
                                  f'not {search_result.group(1)}.')


def run(test, params, env):
    """
    Check the vhost* thread's cpu affinity with emulatorpin setting
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vcpu_set = params.get('vcpu_set')
    iface_attrs = eval(params.get('iface_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    online_cpus = get_online_cpus()
    epin_set = str(random.choice(online_cpus))
    online_cpus.remove(int(epin_set))
    epin_reset = str(random.choice(online_cpus))
    vm_attrs = eval(params.get('vm_attrs', '{}'))

    try:
        vmxml.setup_attrs(**vm_attrs)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()

        cpu_affi = libvirt_misc.convert_to_dict(
            virsh.vcpupin(vm_name, **VIRSH_ARGS).stdout_text)

        if cpu_affi['0'] != vcpu_set:
            test.fail(f'Incorrect cpu affinity (vcpu): {cpu_affi["0"]},'
                      f'Should be {vcpu_set}.')

        pid = process.run(
            "ps -eL|awk '/vhost/{print $2}'", shell=True).stdout_text.strip()

        check_cpu_affinity(vm_name, pid, epin_set)

        # Update the emulator pin to other cpu,
        # and check the vhost thread's cpu affinity
        virsh.emulatorpin(vm_name, epin_reset, **VIRSH_ARGS)

        check_cpu_affinity(vm_name, pid, epin_reset)

    finally:
        bkxml.sync()
