import logging as log
import os
import re

from avocado.utils import cpu as cpuutils

from virttest import data_dir
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def update_vm_xml(vmxml, params):
    """
    Update the vm xml with given test parameters

    :param vmxml: the vm xml object
    :param params: dict, the test parameters
    :return:
    """
    placement = params.get('vcpu_placement')
    vcpu_max = params.get('vcpu_max')
    vcpu_current = params.get('vcpu_current')

    if placement:
        vmxml.placement = placement
    if vcpu_max:
        vmxml.vcpu = int(vcpu_max)
    if vcpu_current:
        vmxml.current_vcpu = int(vcpu_current)
    vmxml.cpuset = get_cpuset()
    vmxml.sync()


def get_vcpu_line(vm_name, cmd, extra=''):
    """
    Get the <vcpu xxx> line in the dumpxml

    :param vm_name: the vm name
    :param cmd: the command to get vcpu line
    :param extra: the extra option for virsh command
    :return: str, the line in dumpxml with <vcpu xxx>
    """
    dumpxml_path = os.path.join(data_dir.get_tmp_dir(), '{}.dumpxml'.format(vm_name))
    virsh.dumpxml(vm_name, extra=extra, to_file=dumpxml_path, ignore_status=False)
    _, output = utils_misc.cmd_status_output(cmd + dumpxml_path,
                                             ignore_status=False)
    os.remove(dumpxml_path)
    return output


def get_cpuset():
    """
    Get the first two online cpu ids on the host as the cpuset value for
    <vcpu ...> line used in the guest xml

    :return: str, like '0,1'
    """
    host_cpu_online = cpuutils.cpu_online_list()
    logging.debug("Online cpu list on the host:%s", host_cpu_online)
    if len(host_cpu_online) > 2:
        return "%d,%d" % (host_cpu_online[0], host_cpu_online[1])
    else:
        return host_cpu_online[0]


def get_host_cpu_max_id():
    """
    Get the maximum cpu id on the host

    :return: str, like '3'
    """
    # Get the host cpu max id
    host_cpu_online = cpuutils.cpu_online_list()
    return host_cpu_online[-1]


def get_expected_vcpupin(vm_name, vcpupin_conf, cpu_max_id, vcpupin_option=None):
    """
    Get the expected vcpupin values which are used to be compared with the
    output of virsh vcpupin command

    :param vm_name: the vm name
    :param vcpupin_conf: dict, the configuration for vcpupin
    :param cpu_max_id: str, the maximum host cpu id
    :param vcpupin_option: str, option for virsh vcpupin command
    :return: dict, the new values expected for vcpupin
    """
    vcpupin_new_values = {}
    for vcpu_id, pin_to_cpu_id in vcpupin_conf.items():
        id_exp = None
        if pin_to_cpu_id == 'r':
            pin_to_cpu_id = '0-%s' % cpu_max_id
        elif pin_to_cpu_id == 'x':
            pin_to_cpu_id = '%s' % cpu_max_id
        elif pin_to_cpu_id == 'x-y,^z':
            pin_to_cpu_id = '0-%s,^%s' % (cpu_max_id, cpu_max_id)
            id_exp = '0-%d' % (int(cpu_max_id) - 1)
        elif pin_to_cpu_id == 'x':
            pin_to_cpu_id = '%d' % (int(cpu_max_id) - 3) if int(cpu_max_id) >= 3 else '0'
        elif pin_to_cpu_id == 'y':
            pin_to_cpu_id = '%d' % (int(cpu_max_id) - 2) if int(cpu_max_id) >= 3 else '0'
        elif pin_to_cpu_id == 'z':
            pin_to_cpu_id = '%d' % (int(cpu_max_id) - 1) if int(cpu_max_id) >= 3 else '0'
        elif pin_to_cpu_id == 'x,y':
            pin_to_cpu_id = '0,%d' % cpu_max_id
        elif pin_to_cpu_id == 'x-y,^z,m':
            pin_to_cpu_id = '0-%d,^%d,%s' % (int(cpu_max_id) - 1, int(cpu_max_id) - 2, cpu_max_id)
            if int(cpu_max_id) <= 3:
                id_exp = '0,%d-%s' % (int(cpu_max_id) - 1, cpu_max_id)
            else:
                id_exp = '0-%d,%d-%s' % (int(cpu_max_id) - 3, int(cpu_max_id) - 1, cpu_max_id)
        virsh.vcpupin(vm_name, vcpu=vcpu_id, cpu_list=pin_to_cpu_id,
                      options=vcpupin_option, debug=True, ignore_status=False)

        pin_to_cpu_id = id_exp if id_exp else pin_to_cpu_id
        vcpupin_new_values.update({vcpu_id: pin_to_cpu_id})

    logging.debug("The vcpupin new values are %s" % vcpupin_new_values)
    return vcpupin_new_values


def get_vcpupin_dict(vm_name, vcpu=None, options=None):
    """
    Change vcpupin command output to a dict

    :param vm_name: vm name
    :param vcpu: str, vcpu id to get vcpupin value
    :param options: option for vcpupin command
    :return: new dict
    """

    ret = virsh.vcpupin(vm_name, vcpu=vcpu, options=options,
                        debug=True, ignore_status=False)
    return libvirt_misc.convert_to_dict(ret.stdout.strip(), r'(\d+) +(\S+)')


def compare_2_dicts(test, vcpupin_dict, expected_dict):
    """
    Compare two dicts

    :param test: test object
    :param vcpupin_dict: dict, the dict of vcpupin command output
    :param expected_dict: dict, the expected dict
    """
    same_items = {vcpu_id: vcpupin_dict[vcpu_id]
                  for vcpu_id in vcpupin_dict if vcpu_id in expected_dict and
                  vcpupin_dict[vcpu_id] == expected_dict[vcpu_id]}
    if len(same_items) != len(expected_dict):
        test.fail("The output from vcpupin is '%s', but expect '%s'" % (vcpupin_dict, expected_dict))


def check_vcpuinfo_affinity(test, actual_affinity, expect_affinity):
    """
    Check the vcpu affinity

    :param test: test object
    :param actual_affinity: list, affinity from virsh vcpupin output,
                                  like ['0', '0-2']
    :param expect_affinity: dict, affinity information expected,
                                  like {'0': '0', '1': '0-2'}
    """
    vcpu_id = 0
    for cpu_affinity in actual_affinity:
        if cpu_affinity != expect_affinity[str(vcpu_id)]:
            test.fail("Expect cpu affinity '{}' for vcpu '{}', "
                      "but '{}' found".format(expect_affinity[str(vcpu_id)],
                                              vcpu_id,
                                              cpu_affinity))
        vcpu_id += 1


def test_vcpupin_live_active_vm(test, vm, cpu_max_id, params):
    """
    Test case for executing vcpupin --live with running vm

    :param test: test object
    :param vm: vm object
    :param cpu_max_id: maximum id of host cpu id
    :param params: test parameters
    """
    logging.debug("Step 1: get the default vcpupin output and default vcpu line in vm xml")
    default_vcpupin_dict = get_vcpupin_dict(vm.name)
    cmd_for_inactive_dumpxml = params.get('cmd_for_inactive_dumpxml')
    default_vcpu_line = get_vcpu_line(vm.name,
                                      cmd_for_inactive_dumpxml)

    logging.debug("Step 2: execute virsh vcpupin --live, "
                  "and return expected new vcpupin values")
    vcpupin_conf = eval(params.get("vcpupin_conf"))
    vcpupin_new_values = get_expected_vcpupin(vm.name, vcpupin_conf, cpu_max_id, vcpupin_option='--live')

    logging.debug("Step 3: check vcpupin command output with no option is same as new vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name), vcpupin_new_values)

    logging.debug("Step 4: check vcpupin command output with --config is same as default vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name, options='--config'), default_vcpupin_dict)

    logging.debug("Step 5: check vcpu line in inactive vm xml is not changed")
    inactive_vcpu_line = get_vcpu_line(vm.name, cmd_for_inactive_dumpxml, extra='--inactive')
    if default_vcpu_line != inactive_vcpu_line:
        test.fail("The vcpu in dumpxml with --inactive is "
                  "expected to be same with the initial "
                  "values:'{}', but found with "
                  "'{}'".format(default_vcpu_line, inactive_vcpu_line))


def test_vcpupin_live_config_active_vm(test, vm, cpu_max_id, params):
    """
    Test case for executing vcpupin --live --config with running vm

    :param test: test object
    :param vm: vm object
    :param cpu_max_id: maximum id of host cpu id
    :param params: test parameters
    """
    logging.debug("Step 1: get the default vcpupin output")
    default_vcpupin_dict = get_vcpupin_dict(vm.name)

    logging.debug("Step 2: execute virsh vcpupin --live --config "
                  "and return expected new vcpupin values")
    vcpupin_conf = eval(params.get("vcpupin_conf"))
    vcpupin_new_values = get_expected_vcpupin(vm.name, vcpupin_conf,
                                              cpu_max_id,
                                              vcpupin_option='--live --config')

    logging.debug("Step 3: check vcpupin command output with no option "
                  "is same as new vcpupin values")
    logging.debug("Step 4: check vcpupin command output with --config "
                  "is same as new vcpupin values")
    # Compare the vcpupin command output same as the new values set
    for vcpu_id in vcpupin_conf.keys():
        compare_2_dicts(test, get_vcpupin_dict(vm.name, vcpu=vcpu_id),
                        vcpupin_new_values)
        compare_2_dicts(test,
                        get_vcpupin_dict(vm.name,
                                         vcpu=vcpu_id,
                                         options='--config'),
                        vcpupin_new_values)

    logging.debug("Step 5: Restart vm")
    expected_dict = dict(default_vcpupin_dict, **vcpupin_new_values)
    logging.debug(expected_dict)
    vm.destroy()
    vm.start()
    vm.wait_for_login().close()

    logging.debug("Step 6: check vcpupin command output is changed "
                  "according to new vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name), expected_dict)


def test_vcpupin_current_active_vm(test, vm, cpu_max_id, params):
    """
    Test case for executing vcpupin --current with running vm

    :param test: test object
    :param vm: vm object
    :param cpu_max_id: maximum id of host cpu id
    :param params: test parameters
    """
    logging.debug("Step 1: get the default vcpupin output")
    default_vcpupin_dict = get_vcpupin_dict(vm.name)

    logging.debug("Step 2: execute virsh vcpupin --current "
                  "and return expected new vcpupin values")
    vcpupin_conf = eval(params.get("vcpupin_conf"))
    vcpupin_new_values = get_expected_vcpupin(vm.name,
                                              vcpupin_conf,
                                              cpu_max_id,
                                              vcpupin_option='--current')

    expected_dict = dict(default_vcpupin_dict, **vcpupin_new_values)
    logging.debug("Step 3: check vcpupin command output with no option "
                  "is changed according to new vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name), expected_dict)

    logging.debug("Step 4: check vcpupin command output with --live "
                  "is changed according to new vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name, options='--live'),
                    expected_dict)

    logging.debug("Step 5: check vcpupin command output with --current "
                  "is changed according to new vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name, options='--current'),
                    expected_dict)

    logging.debug("Step 6: check vcpupin command output with --config "
                  "is same as default vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name, options='--config'),
                    default_vcpupin_dict)

    logging.debug("Step 7: Restart vm")
    vm.destroy()
    vm.start()
    vm.wait_for_login().close()

    logging.debug("Step 8: check vcpupin command output with --current "
                  "is same as default vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name, options='--current'),
                    default_vcpupin_dict)


def test_vcpupin_current_inactive_vm(test, vm, cpu_max_id, params):
    """
    Test case for executing vcpupin --current with shutoff vm

    :param test: test object
    :param vm: vm object
    :param cpu_max_id: maximum id of host cpu id
    :param params: test parameters
    """
    logging.debug("Step 1: Destory vm if any")
    if vm.is_alive():
        vm.destroy()

    logging.debug("Step 2: execute virsh vcpupin --current "
                  "and return expected new vcpupin values")
    vcpupin_conf = eval(params.get("vcpupin_conf"))
    vcpupin_new_values = get_expected_vcpupin(vm.name,
                                              vcpupin_conf,
                                              cpu_max_id,
                                              vcpupin_option='--current')

    logging.debug("Step 3: check the vcpupin output with no "
                  "option is aligned with the new vcpupin values")
    compare_2_dicts(test, get_vcpupin_dict(vm.name), vcpupin_new_values)

    logging.debug("Step 4: start vm")
    vm.start()
    vm.wait_for_login().close()

    logging.debug("Step 5: check vcpuinfo affinity is aligned "
                  "with new vcpupin values")
    vcpu_max = params.get('vcpu_max', '4')
    vcpu_current = params.get('vcpu_current', '2')
    # Replace the max cpu id in the pattern
    affinity_pattern = params.get('affinity_pattern')
    output = virsh.vcpuinfo(vm.name, options='--pretty',
                            debug=True, ignore_status=False).stdout.rstrip()
    affinity = re.findall(affinity_pattern, output)
    if not affinity or len(affinity) != int(vcpu_current):
        test.fail("%s vcpu info with affinity is expected, "
                  "but %s found:%s" % (vcpu_current, len(affinity), affinity))
    check_vcpuinfo_affinity(test, affinity, vcpupin_new_values)

    logging.debug("Step 6: hotplug vcpu")
    virsh.setvcpus(vm.name, str(vcpu_max), ignore_status=False, debug=True)

    logging.debug("Step 7: check vcpuinfo affinity is changed "
                  "and aligned with new vcpupin values")
    output = virsh.vcpuinfo(vm.name, options='--pretty',
                            debug=True, ignore_status=False).stdout.rstrip()
    affinity = re.findall(affinity_pattern, output)
    if not affinity or len(affinity) != int(vcpu_max):
        test.fail("%s vcpu info with affinity is expected, "
                  "but %s found:%s" % (vcpu_max, len(affinity), affinity))
    check_vcpuinfo_affinity(test, affinity, vcpupin_new_values)


def run(test, params, env):
    """
    Run some tests for vcpupin with --live, --config
    """
    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    # Backup for recovery.
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        if vm.is_alive():
            vm.destroy()
        update_vm_xml(vmxml, params)
        vm.start()
        logging.debug(vm_xml.VMXML.new_from_dumpxml(vm_name))
        # Get the host cpu max id
        cpu_max_id = get_host_cpu_max_id()
        run_test(test, vm, cpu_max_id, params)
    finally:
        vmxml_backup.sync()
