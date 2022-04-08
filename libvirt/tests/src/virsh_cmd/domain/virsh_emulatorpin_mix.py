import logging as log
import os.path
import json

from avocado.utils import cpu
from avocado.utils import process
from avocado.utils import path as utils_path

from virttest import virsh
from virttest import libvirt_cgroup
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_test import libvirt

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def prepare_vm(guest_xml, params):
    """
    Configure guest xml before the test.

    :param guest_xml: the guest xml
    :param params: the dict the cases use
    """
    logging.debug("Begin to prepare vm xml")
    iothread_id = params.get("iothread_id")
    vm_name = params.get("main_vm")
    iothreadids = params.get("iothreadids")
    vcpu_attrs = eval(params.get('vcpu_attrs', '{}'))
    if vcpu_attrs:
        guest_xml.setup_attrs(**vcpu_attrs)

    if guest_xml.xmltreefile.find('cputune'):
        guest_xml.del_cputune()

    cputune = vm_xml.VMCPUTuneXML()
    ids_xml = vm_xml.VMIothreadidsXML()
    cputune_attrs = params.get('cputune_attrs')
    if cputune_attrs and cputune_attrs.count("%s"):
        cputune_attrs = cputune_attrs % params.get('cpulist', '0')
    if iothreadids:
        iothreadids = eval(iothreadids)
        ids_xml.setup_attrs(**iothreadids)
        guest_xml.iothreadids = ids_xml
    if cputune_attrs:
        cputune_attrs = eval(cputune_attrs)
        cputune.setup_attrs(**cputune_attrs)
        guest_xml.cputune = cputune

    guest_xml.sync()
    if iothread_id:
        virsh.iothreadadd(vm_name, iothread_id, '--config',
                          ignore_status=False, debug=True)


def run_func(func_name, vm_name, pattern=r'(\d+) +(\S+)'):
    ret = func_name(vm_name, ignore_status=False, debug=True)
    return libvirt_misc.convert_to_dict(ret.stdout_text, pattern)


def get_current_values(vm_name):
    """
    Get current vcpupin and emulatorpin and iothreadpin value of the vm

    :param vm_name: vm name
    :return: (dict, dict, dict)
    """
    return (run_func(virsh.vcpupin, vm_name),
            run_func(virsh.emulatorpin, vm_name, pattern=r"(\*): +(\S+)"),
            run_func(virsh.iothreadinfo, vm_name))


def compare_results(vcpupin_res, emulatorpin_res, iothreadpin_res, iotheadid, test):
    """
    Compare the three values

    :param vcpupin_res: vcpupin dict value
    :param emulatorpin_res: emulatorpin dict value
    :param iothreadpin_res: iotheadinpin dict value
    :param iotheadid: str, the iothreadid info
    :param test: test object
    :raises: test.fail if the value is not expected
    """
    emulatorpin_value = emulatorpin_res['*']
    iothreadpin_value = iothreadpin_res[iotheadid]
    vcpupin_value = vcpupin_res['0']
    if (emulatorpin_value != iothreadpin_value or vcpupin_value != emulatorpin_value):
        test.fail("These values should be same: emulatorpin_value=%s, "
                  "iothreadpin_value=%s, vcpupin_value=%s" % (emulatorpin_value,
                                                              iothreadpin_value,
                                                              vcpupin_value))
    for one_value in vcpupin_res.values():
        if one_value != vcpupin_value:
            test.fail("vcpupin value should be %s, but found %s" % (vcpupin_value,
                                                                    one_value))


def check_emulatorpin(base_dict, change_dict, vm_name, test):
    """
    Check emulatorpin value is as expected

    :param base_dict: dict containing old value
    :param change_dict: dict containing changed value
    :param vm_name: str, the vm name
    :param test: test object
    """

    emulatorpin_current = run_func(virsh.emulatorpin, vm_name,
                                   pattern=r"(\*): +(\S+)")
    check_result(base_dict, change_dict,
                 emulatorpin_current, 'emulatorpin', test)


def check_iothreadpin(base_dict, change_dict, vm_name, test):
    """
    Check iothreadpin value is as expected

    :param base_dict: dict containing old value
    :param change_dict: dict containing changed value
    :param vm_name: str, the vm name
    :param test: test object
    """
    iothreadpin_current = run_func(virsh.iothreadinfo, vm_name)
    check_result(base_dict, change_dict,
                 iothreadpin_current, 'iothreadpin', test)


def check_vcpupin(base_dict, change_dict, vm_name, test):
    """
    Check vcpupin value is as expected

    :param base_dict: dict containing old value
    :param change_dict: dict containing changed value
    :param vm_name: str, the vm name
    :param test: test object
    """
    vcpupin_current = run_func(virsh.vcpupin, vm_name)
    check_result(base_dict, change_dict,
                 vcpupin_current, 'vcpupin', test)


def check_result(base_dict, change_dict, current_value, value_category, test):
    """
    Common function to check the values

    :param base_dict: dict, values before changed
    :param change_dict: dict, changed values
    :param current_value: dict, current virsh command result
    :param value_category: str, like 'vcpupin', 'iothreadpin', 'emulatorpin'
    :param test: test object
    :raises: test.fail if the values is not expected
    """
    for target_dict in (change_dict, base_dict):
        if not target_dict:
            continue
        # Verify the changed value is as expected
        # Verify other unchanged values are same as before
        for item, physical_cpu in target_dict.items():
            if target_dict == base_dict and change_dict and item in change_dict:
                logging.debug("Skip to check duplicate item '%s'", item)
                continue
            if physical_cpu != current_value[item]:
                test.fail("%s value is expected as '%s', "
                          "but found '%s'" % (value_category,
                                              physical_cpu,
                                              current_value[item]))
        logging.debug("Checking in check_result() is successful for %s", target_dict)


def check_to_skip_case(params, test):
    """
    Check if the case should be skipped

    :param params: the dict the cases use
    :param test: test object
    :raises: test.cancel if skip criteria are matched
    """
    need_2_numa_node = "yes" == params.get("need_2_numa_node", "no")
    if need_2_numa_node:
        host_numa_node = utils_misc.NumaInfo()
        node_list = host_numa_node.online_nodes_withmem
        logging.debug("host online nodes with memory %s", node_list)
        if len(node_list) <= 1:
            test.cancel("This case requires at least 2 numa host nodes, "
                        "but found '%s' numa host node" % len(node_list))
    use_taskset = "yes" == params.get("use_taskset", "no")
    if use_taskset:
        try:
            utils_path.find_command('taskset')
        except utils_path.CmdNotFoundError:
            test.cancel("This case requires the 'taskset' command available")


def test_change_vcpupin_emulatorpin_iothreadpin(test, guest_xml, cpu_max_index, vm, params):
    """
    - Check default values are correct
    - Perform virsh vcpupin, check iothreadpin and emulatorpin are not impacted
    - Perform virsh emulatorpin, check vcpupin and iothreadpin are not impacted
    - Perform virsh iotheadpin, check vcpupin and emulatorpin are not impacted

    :param test: test object
    :param guest_xml: vm xml
    :param cpu_max_index: int, cpu maximum index on host
    :param vm: vm instance
    :param params: test dict
    :return: None
    """
    def _check_result(vcpupin_result, emulatorpin_result, iothreadpin_result,
                      changed_vcpupin, changed_emulatorpin, changed_iothreadpin):
        """
        Internal common function to check the command result

        :param vcpupin_result: dict, the vcpupin command result
        :param emulatorpin_result: dict, the emulatorpin command result
        :param iothreadpin_result: dict, the iothreadpin command result
        :param changed_vcpupin: dict, the changed value for vcpupin
        :param changed_emulatorpin: dict, the changed value for emulatorpin
        :param changed_iothreadpin: dict, the changed value for iothreadpin
        :return: None
        """
        check_vcpupin(vcpupin_result, changed_vcpupin, vm.name, test)
        check_emulatorpin(emulatorpin_result, changed_emulatorpin, vm.name, test)
        check_iothreadpin(iothreadpin_result, changed_iothreadpin, vm.name, test)

    prepare_vm(guest_xml, params)
    vm.start()
    logging.debug("After vm starts, vm xml is:"
                  "%s\n", vm_xml.VMXML.new_from_dumpxml(vm.name))

    logging.debug("Get default vcpupin/emulatorpin/iothreadpin values of the vm")
    vcpupin_result, emulatorpin_result, iothreadpin_result = get_current_values(vm.name)
    logging.debug("Check and compare default vcpupin/emulatorpin/iothreadpin values")
    compare_results(vcpupin_result, emulatorpin_result,
                    iothreadpin_result, params.get("iothread_id"), test)

    # Change vcpupin, then check vcpupin, and emulatorpin/iothreadpin
    # should not be effected.
    logging.debug("Now change vcpupin value to the guest")
    cpu_list = "0-%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
    virsh.vcpupin(vm.name, "0", cpu_list, None, debug=True, ignore_status=False)
    changed_vcpupin = {'0': cpu_list}
    _check_result(vcpupin_result, emulatorpin_result, iothreadpin_result,
                  changed_vcpupin, None, None)

    # Change emulatorpin, then check emulatorpin, and vcpupin/iothreadpin
    # should not be effected
    logging.debug("Now change emulatorpin value to the guest")
    vcpupin_result, emulatorpin_result, iothreadpin_result = get_current_values(vm.name)
    cpu_list = "0,%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
    virsh.emulatorpin(vm.name, cpu_list, ignore_status=False, debug=True)
    changed_emulatorpin = {'*': cpu_list}
    _check_result(vcpupin_result, emulatorpin_result, iothreadpin_result,
                  None, changed_emulatorpin, None)

    # Change iothreadpin, then check iothreadpin, and vcpupin/emulatorpin
    # should not be effected
    logging.debug("Now change iothreadpin value to the guest")
    vcpupin_result, emulatorpin_result, iothreadpin_result = get_current_values(vm.name)
    cpu_list = "%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
    iothread_id = params.get("iothread_id")
    virsh.iothreadpin(vm.name, iothread_id, cpu_list, ignore_status=False, debug=True)
    changed_iothreadpin = {iothread_id: cpu_list}
    _check_result(vcpupin_result, emulatorpin_result, iothreadpin_result,
                  None, None, changed_iothreadpin)


def test_start_with_emulatorpin(test, guest_xml, cpu_max_index, vm, params):
    """
    Start vm with emulatorpin info and check values with virsh command and guest xml

    :param test: test object
    :param guest_xml: vm xml
    :param cpu_max_index: int, cpu maximum index on host
    :param vm: vm object
    :param params: test dict
    :return: None
    :raises: test.fail if emulatorpin info is not expected
    """
    # Config emulatorpin info in the guest xml
    cpulist = '%s' % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
    params['cpulist'] = cpulist
    prepare_vm(guest_xml, params)

    vm.start()
    logging.debug("After vm starts, vm xml is:"
                  "%s\n", vm_xml.VMXML.new_from_dumpxml(vm.name))
    # Check emulatorpin value from virsh command
    emulatorpin_current = run_func(virsh.emulatorpin, vm.name,
                                   pattern=r"(\*): +(\S+)")
    if emulatorpin_current['*'] != cpulist:
        test.fail("Expect the emulatorpin from virsh cmd is '%s', "
                  "but found '%s'".format(cpulist, emulatorpin_current['*']))
    # Check emulatorpin value from guest xml
    guest_xml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    if guest_xml.cputune.emulatorpin != cpulist:
        test.fail("Expect the emulatorpin from guest xml is '%s', "
                  "but found '%s'".format(cpulist, guest_xml.cputune.emulatorpin))


def test_change_with_disabled_cpuset(test, guest_xml, cpu_max_index, vm, params):
    """
    Check whether the commands which does not require cgroup will work well
    when disable them in configure file.

    :param test: test object
    :param guest_xml: vm xml
    :param cpu_max_index: int, cpu maximum index on host
    :param vm: vm object
    :param params: test dict
    :return: None
    :raises: test.fail if emulatorpin or iothreadpin does not work correctly
    """
    prepare_vm(guest_xml, params)
    vm.start()

    # Check whether cpuset is disabled
    cg_obj = libvirt_cgroup.CgroupTest(vm.get_pid())
    cg_path = cg_obj.get_cgroup_path("cpuset")
    if cg_obj.is_cgroup_v2_enabled():
        if os.path.exists(os.path.join(cg_path, 'vcpu0', 'cpuset.cpus')):
            test.fail("There should be no cpuset related files in %s."
                      % os.path.join(cg_path, 'vcpu0'))
    else:
        if 'machine.slice' in cg_path:
            test.fail("There should be no subdir 'machine.slice' in cgroup path: %s."
                      % cg_path)

    # Change iothreadpin, then check the command works well
    logging.info("Now change iothreadpin value to the guest")
    iothreadpin_result = run_func(virsh.iothreadinfo, vm.name)
    cpu_list = "%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
    changed_id = params.get("changed_id")
    virsh.iothreadpin(vm.name, changed_id, cpu_list, ignore_status=False, debug=True)
    changed_iothreadpin = {changed_id: cpu_list}
    check_iothreadpin(iothreadpin_result, changed_iothreadpin, vm.name, test)

    # Change emulatorpin, then check the command works well
    logging.info("Now change emulatorpin value to the guest")
    emulatorpin_result = run_func(virsh.emulatorpin, vm.name, pattern=r"(\*): +(\S+)")
    cpu_list = "%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
    virsh.emulatorpin(vm.name, cpu_list, ignore_status=False, debug=True)
    changed_emulatorpin = {'*': cpu_list}
    check_emulatorpin(emulatorpin_result, changed_emulatorpin, vm.name, test)

    # Check if current affinity mask is expected
    cmd_option = params.get("cmd_option")
    result = virsh.qemu_monitor_command(vm.name, cmd_option, "--pretty").stdout_text
    iothread_pids = json.loads(result)
    iothread1_pid = iothread_pids['return'][0]['thread-id']
    iothread2_pid = iothread_pids['return'][1]['thread-id']
    # In some environments qemu-monitor-command give the reverse order of iothread
    if iothread_pids['return'][0]['id'] != 'iothread1':
        iothread1_pid, iothread2_pid = iothread2_pid, iothread1_pid
    res_iothread_1 = process.run("taskset -p %s" % iothread1_pid,
                                 shell=True, verbose=True).stdout_text.split(":")[-1].strip()
    res_iothread_2 = process.run("taskset -p %s" % iothread2_pid,
                                 shell=True, verbose=True).stdout_text.split(":")[-1].strip()
    # Calculate the expected affinity mask
    aff_mask = str(pow(10, int(cpu_list)//4) * pow(2, int(cpu_list) % 4))
    if res_iothread_1 != '1':
        test.fail("Iothreadpin does not work well, "
                  "this affinity mask should be 1 but not be changed to %s"
                  % res_iothread_1)
    if res_iothread_2 != aff_mask:
        test.fail("Iothreadpin does not work well, "
                  "this affinity mask should be changed to %s but now: %s"
                  % (aff_mask, res_iothread_2))
    res_emulatorpin = process.run("taskset -p `pidof qemu-kvm`",
                                  shell=True, verbose=True).stdout_text.split(":")[-1].strip()
    if res_emulatorpin != aff_mask:
        test.fail("Emulatorpin does not work well, "
                  "this affinity mask should be changed to %s but now: %s"
                  % (aff_mask, res_emulatorpin))


def run(test, params, env):
    """
    Test commands: virsh.vcpupin, virsh.iothreadpin, virsh.emulatorpin.
    """
    check_to_skip_case(params, test)

    vm_name = params.get("main_vm")
    qemu_conf_dict = eval(params.get("qemu_conf_dict", "{}"))
    vm = env.get_vm(vm_name)

    original_vm_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_copy = original_vm_xml.copy()

    if qemu_conf_dict:
        qemu_conf = libvirt.customize_libvirt_config(qemu_conf_dict, config_type="qemu")
    host_cpus = cpu.online_cpus_count()
    cpu_max_index = int(host_cpus) - 1
    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)

    try:
        if vm.is_alive():
            vm.destroy()
        run_test(test, original_vm_xml, cpu_max_index, vm, params)
    finally:
        vm_xml_copy.sync()
        if vm.is_alive():
            vm.destroy()
        if 'qemu_conf' in locals():
            libvirt.customize_libvirt_config(None, is_recover=True,
                                             config_type="qemu",
                                             config_object=qemu_conf)
