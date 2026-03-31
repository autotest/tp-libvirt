import ast

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base


def get_bin_starts(params, key="bin_list"):
    """
    Extract bin start values from bin list parameter

    :param params: dict wrapped with params
    :param key: parameter key name
    :return: list of bin start values as strings
    """
    bin_list = ast.literal_eval(params.get(key, "[]"))
    return [str(bin_item['start']) for bin_item in bin_list]


def prepare_disk(test, vm, params):
    """
    Prepare disk with latency histogram statistics

    :param test: test object
    :param vm: VM object
    :param params: dict wrapped with params
    :return: disk object, disk_base object and new image path
    """
    disk_type = params.get("disk_type", "file")
    disk_dict = ast.literal_eval(params.get("disk_dict", "{}"))
    disk_base_obj = disk_base.DiskBase(test, vm, params)
    disk_obj, new_image_path = disk_base_obj.prepare_disk_obj(disk_type, disk_dict)
    return disk_obj, disk_base_obj, new_image_path


def check_domstats_latency(test, vm_name, expected_types=None, expected_bin_starts=None):
    """
    Check domstats output for latency histogram data

    :param test: test object
    :param vm_name: VM name
    :param expected_types: comma-separated expected histogram types, None to verify no latency data
    :param expected_bin_starts: list of expected bin start values (e.g., ['0', '1000', '100000'])
    """
    def validate_bin_starts(test, latency_lines, types_list, expected_bin_starts):
        """
        Validate bin start values for each histogram type

        :param test: test object
        :param latency_lines: list of latency histogram lines from domstats output
        :param types_list: list of histogram types to validate
        :param expected_bin_starts: list of expected bin start values
        """
        for expected_type in types_list:
            for idx, expected_start in enumerate(expected_bin_starts):
                bin_prefix = f'latency_histogram.{expected_type}.bin.{idx}.start='
                actual_start = None
                for line in latency_lines:
                    if bin_prefix in line:
                        parts = line.split(bin_prefix)
                        if len(parts) >= 2:
                            actual_start = parts[1].strip()
                            break
                if actual_start is None:
                    test.fail(f"Bin {idx} start value not found for type '{expected_type}'")
                if actual_start != expected_start:
                    test.fail(f"Wrong bin.{idx}.start for type '{expected_type}': "
                              f"expected '{expected_start}', got '{actual_start}'")

    result = virsh.domstats(vm_name, "--block", debug=True)
    libvirt.check_exit_status(result)
    output = result.stdout_text.strip()
    latency_lines = [line for line in output.split('\n') if 'latency_histogram' in line]

    if expected_types is None:
        if latency_lines:
            test.fail("Found unexpected latency histogram data.")
        test.log.debug("Verified no latency histogram data in domstats output.")
    else:
        if not latency_lines:
            test.fail("No latency histogram data found in domstats output.")
        types_list = [t.strip() for t in expected_types.split(',')]
        for expected_type in types_list:
            pattern = f'latency_histogram.{expected_type}.bin'
            matching_lines = [line for line in latency_lines if pattern in line]
            if not matching_lines:
                test.fail(f"Expected latency histogram type '{expected_type}' not found in domstats output.")
            test.log.debug(f"Found latency histogram data for type '{expected_type}'")

        if expected_bin_starts:
            validate_bin_starts(test, latency_lines, types_list, expected_bin_starts)


def run_test_start_guest(test, params, env):
    """
    Test starting guest with disk containing latency histogram statistics

    :param test: test object
    :param params: dict wrapped with params
    :param env: environment instance
    :return: disk_base object for cleanup
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    expected_types = params.get("expected_histogram_types")
    expected_bin_starts = get_bin_starts(params, "bin_list")

    disk_obj, disk_base_obj, _ = prepare_disk(test, vm, params)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml.add_device(disk_obj)
    vmxml.sync()

    test.log.info("TEST_STEP1: Start the guest with latency histogram disk")
    vm.start()
    vm.wait_for_login().close()

    test.log.info("TEST_STEP2: Check domstats output for latency histogram data")
    check_domstats_latency(test, vm_name, expected_types, expected_bin_starts)

    return disk_base_obj


def run_test_hotplug_unplug(test, params, env):
    """
    Test hotplug and unplug disk with latency histogram statistics

    :param test: test object
    :param params: dict wrapped with params
    :param env: environment instance
    :return: disk_base object for cleanup
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device_target = params.get("disk_target", "vdb")
    expected_types = params.get("expected_histogram_types")
    expected_bin_starts = get_bin_starts(params, "bin_list")

    test.log.info("TEST_STEP1: Start VM without latency histogram disk")
    vm.start()
    vm.wait_for_login().close()

    test.log.info("TEST_STEP2: Prepare and hotplug disk with latency histogram")
    disk_obj, disk_base_obj, _ = prepare_disk(test, vm, params)
    virsh.attach_device(vm_name, disk_obj.xml, debug=True, ignore_status=False)

    test.log.info("TEST_STEP3: Check disk is in domain XML")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disks = vmxml.devices.by_device_tag("disk")
    found_disk = False
    for disk_dev in disks:
        if disk_dev.target.get('dev') == device_target:
            found_disk = True
            test.log.debug("Found attached disk in XML")
            break

    if not found_disk:
        test.fail(f"Disk {device_target} not found in domain XML after attach")

    test.log.info("TEST_STEP4: Check domstats output for latency histogram data")
    check_domstats_latency(test, vm_name, expected_types, expected_bin_starts)

    test.log.info("TEST_STEP5: Hot-unplug the disk and check no latency histogram data.")
    virsh.detach_device(vm_name, disk_obj.xml, debug=True, ignore_status=False)
    check_domstats_latency(test, vm_name, expected_types=None)

    return disk_base_obj


def run_test_update_disk(test, params, env):
    """
    Test updating disk with latency histogram statistics using virsh update-device

    :param test: test object
    :param params: dict wrapped with params
    :param env: environment instance
    :return: disk_base object for cleanup
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    expected_types_initial = params.get("expected_histogram_types_initial")
    expected_types_updated = params.get("expected_histogram_types_updated")
    expected_bin_starts = get_bin_starts(params, "bin_list")
    updated_bin_starts = get_bin_starts(params, "updated_bin_list")

    test.log.info("TEST_STEP1: Start the guest with initial latency histogram disk")
    disk_obj, disk_base_obj, new_image_path = prepare_disk(test, vm, params)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml.add_device(disk_obj)
    vmxml.sync()
    vm.start()
    vm.wait_for_login().close()

    test.log.info("TEST_STEP2: Check domstats output for initial latency histogram data")
    check_domstats_latency(test, vm_name, expected_types_initial, expected_bin_starts)

    test.log.info("TEST_STEP3: Prepare updated disk XML with new latency histogram types")
    disk_type = params.get("disk_type", "file")
    disk_dict_updated = ast.literal_eval(params.get("disk_dict", "{}"))
    driver_statistics_updated = ast.literal_eval(params.get("driver_statistics_updated", "{}"))
    disk_dict_updated["driver_statistics"] = driver_statistics_updated
    disk_obj_updated, _ = disk_base_obj.prepare_disk_obj(disk_type, disk_dict_updated, new_image_path)

    test.log.info("TEST_STEP4: Update the disk using virsh update-device")
    result = virsh.update_device(vm_name, disk_obj_updated.xml, debug=True)
    libvirt.check_exit_status(result)

    test.log.info("TEST_STEP5: Check domstats output for updated latency histogram data")
    check_domstats_latency(test, vm_name, expected_types_updated, updated_bin_starts)

    return disk_base_obj


def tear_down_test(test, params, env, vmxml_backup, disk_base_obj):
    """
    Cleanup and verify test resources are properly torn down

    :param test: test object
    :param params: dict wrapped with params
    :param env: environment instance
    :param vmxml_backup: backup VMXML object
    :param disk_base_obj: disk_base object for cleanup
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type", "file")

    test.log.info("CLEANUP: Starting teardown and cleanup verification")
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup.sync()

    if disk_base_obj:
        disk_base_obj.cleanup_disk_preparation(disk_type)


def run(test, params, env):
    """
    Test disk latency histogram statistics

    :param test: test object
    :param params: dict wrapped with params
    :param env: environment instance
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type", "file")
    test_scenario = params.get("test_scenario")

    TEST_SCENARIOS = {
        'start_guest': run_test_start_guest,
        'hotplug_unplug': run_test_hotplug_unplug,
        'update_disk': run_test_update_disk
    }
    run_test_case = TEST_SCENARIOS.get(test_scenario)
    if not run_test_case:
        test.error(f"Unknown test scenario: {test_scenario}.")

    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    disk_base_obj = None

    try:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        disk_base_obj = run_test_case(test, params, env)
    finally:
        tear_down_test(test, params, env, vmxml_backup, disk_base_obj)
