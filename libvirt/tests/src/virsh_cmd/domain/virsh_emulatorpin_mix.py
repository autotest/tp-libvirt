import logging

from avocado.utils import cpu

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc


def prepare_vm(guest_xml, params):
    """
    Configure guest xml before the test.

    :param guest_xml: the guest xml
    :param params: the dict the cases use
    """
    vcpu_placement = params.get("vcpu_placement", "static")
    iothread_id = params.get("iothread_id", "1")
    vcpu_current = params.get("vcpu_current", 3)
    vcpu_max = params.get("vcpu_max", 4)
    vm_name = params.get("main_vm")

    guest_xml.placement = vcpu_placement
    guest_xml.current_vcpu = int(vcpu_current)
    guest_xml.vcpu = vcpu_max
    guest_xml.sync()
    virsh.iothreadadd(vm_name, iothread_id, '--config', ignore_status=False, debug=True)


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
    vcpu_placement = params.get("vcpu_placement", "static")

    if vcpu_placement == 'auto':
        host_numa_node = utils_misc.NumaInfo()
        node_list = host_numa_node.online_nodes_withmem
        logging.debug("host online nodes with memory %s", node_list)
        if len(node_list) <= 1:
            test.cancel("This case requires at least 2 numa host nodes, "
                        "but found '%s' numa host node" % len(node_list))


def run(test, params, env):
    """
    Test commands: virsh.vcpupin, virsh.iothreadpin, virsh.emulatorpin.

    Steps:
    - Configure the test VM
    - Check default values are correct
    - Perform virsh vcpupin, check iothreadpin and emulatorpin are not impacted
    - Perform virsh emulatorpin, check vcpupin and iothreadpin are not impacted
    - Perform virsh iotheadpin, check vcpupin and emulatorpin are not impacted
    - Recover test environment
    """

    start_vm = params.get("start_vm", "yes") == "yes"
    change_vcpupin = params.get("change_vcpupin", "no") == 'yes'
    change_emulatorpin = params.get("change_emulatorpin", "no") == 'yes'
    change_iothreadpin = params.get("change_iothreadpin", "no") == 'yes'

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    check_to_skip_case(params, test)

    if vm.is_alive():
        vm.destroy()

    original_vm_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_copy = original_vm_xml.copy()
    prepare_vm(original_vm_xml, params)

    host_cpus = cpu.online_cpus_count()
    cpu_max_index = int(host_cpus) - 1

    try:

        if start_vm:
            vm.start()
        logging.debug("After vm starts, vm xml is:"
                      "%s\n", vm_xml.VMXML.new_from_dumpxml(vm_name))
        logging.debug("Get default vcpupin/emulatorpin/iothreadpin values of the vm")
        vcpupin_result, emulatorpin_result, iothreadpin_result = get_current_values(vm_name)
        logging.debug("Check and compare default vcpupin/emulatorpin/iothreadpin values")
        compare_results(vcpupin_result, emulatorpin_result,
                        iothreadpin_result, params.get("iothreadid", '1'), test)
        if change_vcpupin:
            # Change vcpupin, then check vcpupin, and emulatorpin/iothreadpin
            # should not be effected.
            logging.debug("Now change vcpupin value to the guest")
            cpu_list = "0-%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
            virsh.vcpupin(vm_name, "0", cpu_list, None, debug=True, ignore_status=False)
            changed_vcpupin = {'0': cpu_list}
            check_vcpupin(vcpupin_result, changed_vcpupin, vm_name, test)
            check_emulatorpin(emulatorpin_result, None, vm_name, test)
            check_iothreadpin(iothreadpin_result, None, vm_name, test)

        if change_emulatorpin:
            # Change emulatorpin, then check emulatorpin, and vcpupin/iothreadpin
            # should not be effected
            logging.debug("Now change emulatorpin value to the guest")
            vcpupin_result, emulatorpin_result, iothreadpin_result = get_current_values(vm_name)
            cpu_list = "0,%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
            virsh.emulatorpin(vm_name, cpu_list, ignore_status=False, debug=True)
            changed_emulatorpin = {'*': cpu_list}
            check_emulatorpin(emulatorpin_result, changed_emulatorpin, vm_name, test)
            check_vcpupin(vcpupin_result, None, vm_name, test)
            check_iothreadpin(iothreadpin_result, None, vm_name, test)

        if change_iothreadpin:
            # Change iothreadpin, then check iothreadpin, and vcpupin/emulatorpin
            # should not be effected
            logging.debug("Now change iothreadpin value to the guest")
            vcpupin_result, emulatorpin_result, iothreadpin_result = get_current_values(vm_name)
            cpu_list = "%s" % (cpu_max_index - 1) if cpu_max_index > 1 else "0"
            iothread_id = params.get("iothread_id", "1")
            virsh.iothreadpin(vm_name, iothread_id, cpu_list,  ignore_status=False, debug=True)
            changed_iothreadpin = {iothread_id: cpu_list}
            check_iothreadpin(iothreadpin_result, changed_iothreadpin, vm_name, test)
            check_vcpupin(vcpupin_result, None, vm_name, test)
            check_emulatorpin(emulatorpin_result, None, vm_name, test)
    finally:
        vm_xml_copy.sync()
        if vm.is_alive():
            vm.destroy()
