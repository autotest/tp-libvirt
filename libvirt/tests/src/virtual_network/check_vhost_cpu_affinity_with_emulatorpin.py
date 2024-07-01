import logging
import random
import re

from avocado.utils import process
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_vmxml


VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check the vhost* thread's cpu affinity with emulatorpin setting
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vcpu_set = params.get('vcpu_set')
    epin_set = params.get('epin_set')
    epin_reset = random.randint(int(epin_set), 10)
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    iface_attrs = eval(params.get('iface_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.setup_attrs(**vm_attrs)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()

        cpu_affi = libvirt_misc.convert_to_dict(
            virsh.vcpupin(vm_name, **VIRSH_ARGS).stdout_text)

        epin = libvirt_misc.convert_to_dict(
            virsh.emulatorpin(vm_name, **VIRSH_ARGS).stdout_text,
            r'\s+(\S+):\s+(\S+)')

        if cpu_affi['0'] != vcpu_set:
            test.fail(f'Incorrect cpu affinity (vcpu): {cpu_affi["0"]},'
                      f'Should be {vcpu_set}.')

        if epin['*'] != epin_set:
            test.fail(f'Incorrect cpu affinity (emulatorpin): {epin["*"]},'
                      f'Should be {epin_set}.')

        pid = process.run(
            "ps -eL|awk '/vhost/{print $2}'", shell=True).stdout_text.strip()

        taskset_output = process.run(
            f'taskset -cap {pid}', shell=True).stdout_text
        search_pattern = "pid \d+'s current affinity list:\s(\S+)"
        search_result = re.search(search_pattern, taskset_output)
        if not search_result or search_result.group(1) != epin_set:
            test.fail(f'Expect current affinity to be {epin_set}, '
                      f'not {search_result.group(1)}.')

        # Update the emulator pin to other cpu,
        # and check the vhost thread's cpu affinity
        virsh.emulatorpin(vm_name, epin_reset, **VIRSH_ARGS)
        epin_out = libvirt_misc.convert_to_dict(
            virsh.emulatorpin(vm_name, **VIRSH_ARGS).stdout_text,
            r'\s+(\S+):\s+(\S+)')

        if int(epin_out['*']) != epin_reset:
            test.fail(f'Incorrect cpu affinity (emulatorpin) after reset: '
                      f'{epin_out["*"]}, Should be {epin_reset}.')

        taskset_out = process.run(
            f'taskset -cap {pid}', shell=True).stdout_text
        if f'affinity list: {epin_reset}' not in taskset_out:
            test.fail(f'Cpu affinity check by taskset failed: '
                      f'expected {epin_reset}, but got "{taskset_out}"')
    finally:
        bkxml.sync()
