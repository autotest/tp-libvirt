import logging as log
import re

from avocado.core.exceptions import TestFail
from avocado.utils import process

from virttest import libvirt_cgroup
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import virsh


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run_all_commands(vm_name, config_type, cpu_range):
    """
    Run all the following virsh commands with default values or check their
    outputs with or without config parameter defined:
    numatune, vcpupin, emulatorpin, iothreadinfo

    :param vm_name: name of the VM to be executed on
    :param config_type: one of the value: 'default','config', ''
    commands with the command as a key and error message as a value
    """
    commands = {virsh.numatune: [check_numatune, '--config'],
                virsh.vcpupin: [check_vcpupin, '--config'],
                virsh.emulatorpin: [check_emulatorpin, '--config'],
                virsh.iothreadinfo: [check_iothreadinfo, '--config']}
    for command in commands:
        # Run commands prior test
        if config_type == 'default':
            command(vm_name, debug=True, ignore_status=False)
        # Check auto placement with config
        elif config_type == 'config':
            commands[command][0](vm_name, cpu_range, commands[command][1])
        # Check auto placement without config
        else:
            commands[command][0](vm_name, cpu_range, '')


def get_cpu_range():
    """
    Get the range of all CPUs available on the system as a string

    :return: range of CPUs available as a string, for example: '1-63'
    """
    numa_info = utils_misc.NumaInfo()
    cpus_dict = numa_info.get_all_node_cpus()
    cpus = []
    for key in cpus_dict:
        cpus_node_list = [int(cpu) for cpu in cpus_dict[key].split(' ')
                          if cpu.isnumeric()]
        logging.debug('Following cpus found for node {}: {}'.
                      format(key, cpus_node_list))
        cpus += cpus_node_list
    cpu_range = '{}-{}'.format(min(cpus), max(cpus))
    logging.debug('The expected available cpu range is {}'.format(cpu_range))
    return cpu_range


def check_numatune(vm_name, cpu_range, config=''):
    """
    Check the output of the numatune command with auto placement.

    :param vm_name: name of the VM to be executed on
    :param cpu_range: range of CPUs available as a string
    :param config: config parameter as a string, empty by default
    """
    result = virsh.numatune(vm_name, options=config, debug=True,
                            ignore_status=False)
    look_for = re.search(r'numa_nodeset\s*\:.*', result.stdout_text)
    if look_for:
        look_for = look_for.group().split(':')
        logging.debug('Looking for numa_nodeset in stdout and {} found.'
                      ''.format(look_for))
        target = re.sub('\s', '', look_for[-1])
        if target:
            raise TestFail('Nodeset should be empty , but {} found there.'.
                           format(target))
        else:
            logging.debug('numa_nodeset is empty as expected.')


def get_nodeset(test, vm_name):
    """
    Check the output of the numatune for numa_nodeset.

    :param test: Avocado test object
    :param vm_name: name of the VM to be executed on
    :return nodeset: nodeset used for memory as a string
    """
    result = virsh.numatune(vm_name, debug=True, ignore_status=False)
    look_for = re.search(r'numa_nodeset\s*\:.*', result.stdout_text)
    if look_for:
        look_for = look_for.group().split(':')
        logging.debug('Looking for numa_nodeset in stdout and {} found.'
                      ''.format(look_for))
        target = re.sub('\s', '', look_for[-1])
        if target:
            return target
        else:
            test.fail("Cannot find numa_nodeset value in virsh numatune "
                      "output(Probably empty).")
    else:
        test.fail("Cannot find numa_nodeset in virsh numatune output.")


def check_vcpupin(vm_name, cpu_range, config=''):
    """
    Check the output of the vcpupin command with auto placement.

    :param vm_name: name of the VM to be executed on
    :param cpu_range: range of CPUs available as a string
    :param config: config parameter as a string, empty by default
    """
    numa_info = utils_misc.NumaInfo()
    result = virsh.vcpupin(vm_name, options=config, debug=True,
                           ignore_status=False)
    range_found = False
    for node in numa_info.get_online_nodes_withcpu():
        if re.search('{}\s*{}'.format(node, cpu_range), result.stdout_text):
            logging.debug('Expected cpu range: {} found in stdout for '
                          'node: {}.'.format(cpu_range, node))
            range_found = True
        else:
            logging.debug('Node {} has no cpu range'.format(node))
        if not range_found:
            raise TestFail('Expected cpu range: {} not found in stdout of '
                           'vcpupin command.'.format(cpu_range))


def check_emulatorpin(vm_name, cpu_range, config=''):
    """
    Check the output of the emulatorpin command with auto placement.

    :param vm_name: name of the VM to be executed on
    :param cpu_range: range of CPUs available as a string
    :param config: config parameter as a string, empty by default
    """
    result = virsh.emulatorpin(vm_name, options=config, debug=True,
                               ignore_status=False)
    if re.search('\*:\s*{}'.format(cpu_range), result.stdout_text):
        logging.debug('Expected cpu range: {} found in stdout for '
                      'emulatorpin.'.format(cpu_range))
    else:
        raise TestFail('Expected cpu range: {} not found in stdout of '
                       'emulatorpin command.'.format(cpu_range))


def check_iothreadinfo(vm_name, cpu_range, config=''):
    """
    Check the output of the iothreadinfo command with auto placement.

    :param vm_name: name of the VM to be executed on
    :param cpu_range: range of CPUs available as a string
    :param config: config parameter as a string, empty by default
    """
    numa_info = utils_misc.NumaInfo()
    result = virsh.iothreadinfo(vm_name, options=config, debug=True,
                                ignore_status=False)
    range_found = False
    for node in numa_info.get_online_nodes_withcpu():
        if re.search('{}\s*{}'.format(node, cpu_range),
                     result.stdout_text):
            logging.debug(
                'Expected cpu range: {} found in stdout for '
                'node: {}.'.format(cpu_range, node))
            range_found = True
        else:
            logging.debug('Node {} has no cpu range'.format(node))
    if not range_found:
        raise TestFail('Expected cpu range: {} not found in stdout of '
                       'iothreadinfo command.'.format(cpu_range))


def check_cgget_output(test, vm, expected_value):
    """
    Get the cgget output and check it for required value

    :param test: test object
    :param vm: virtual machine
    :param cgget_message: expected message string in output
    """
    vm_pid = vm.get_pid()
    cg = libvirt_cgroup.CgroupTest(vm_pid)
    cg_path = cg.get_cgroup_path(controller='cpuset').split('emulator')[0]
    for item in ['emulator', 'vcpu0']:
        cmd = 'cat {}{}/cpuset.mems'.format(re.escape(cg_path), item)
        result = process.run(cmd, shell=True, ignore_status=False, verbose=True)
        if expected_value != result.stdout_text.strip():
            test.fail('{} is not found in the cpuset.mems file'.format(expected_value))


def run(test, params, env):
    vcpu_placement = params.get("vcpu_placement")
    iothreads = params.get('iothreads')
    bind_test = params.get('bind_test', "no") == "yes"
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)

    mem_tuple = ('memory_mode', 'memory_placement')
    numa_memory = {}
    for mem_param in mem_tuple:
        value = params.get(mem_param)
        if value:
            numa_memory[mem_param.split('_')[1]] = value
    try:
        if vm.is_alive():
            vm.destroy()
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.numa_memory = numa_memory
        if not bind_test:
            vmxml.placement = vcpu_placement
            vmxml.iothreads = int(iothreads)

        logging.debug("vm xml is %s", vmxml)
        vmxml.sync()
        vm.start()
        vm.wait_for_login()
        if not bind_test:
            cpu_range = get_cpu_range()
            # Check after VM start
            run_all_commands(vm_name, 'default', cpu_range)
            run_all_commands(vm_name, 'config', cpu_range)
            # Check after destroying the VM - results should remain same as with
            # --config parameter
            vm.destroy(gracefully=False)
            # with --config parameter
            run_all_commands(vm_name, 'config', cpu_range)
            # without --config parameter
            run_all_commands(vm_name, '', cpu_range)
        else:
            nodeset = get_nodeset(test, vm_name)
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
            nodeset_after = get_nodeset(test, vm_name)
            if nodeset_after != nodeset:
                test.fail("The used nodeset is not the same after the libvirtd "
                          "restart.{} is not as expected: {}".
                          format(nodeset_after, nodeset))
            check_cgget_output(test, vm, nodeset)

    except Exception as e:
        test.fail('Unexpected failure during the test: {}'.format(e))
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
