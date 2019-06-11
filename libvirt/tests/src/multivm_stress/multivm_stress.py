import logging

from virttest import utils_stress
from virttest import error_context
from virttest import utils_test


@error_context.context_aware
def run(test, params, env):
    """
    :param test:   kvm test object
    :param params: Dictionary with the test parameters
    :param env:    Dictionary with test environment.
    """

    guest_stress = params.get("guest_stress", "no") == "yes"
    host_stress = params.get("host_stress", "no") == "yes"
    stress_events = params.get("stress_events", "reboot")
    vms = env.get_all_vms()
    vms_uptime_init = {}
    if "reboot" not in stress_events:
        for vm in vms:
            vms_uptime_init[vm.name] = vm.uptime()
    stress_event = utils_stress.VMStressEvents(params, env)
    if guest_stress:
        try:
            utils_test.load_stress("stress_in_vms", params=params, vms=vms)
        except Exception as err:
            test.fail("Error running stress in vms: %s" % err)
    if host_stress:
        if params.get("host_stress_args", ""):
            params["stress_args"] = params.get("host_stress_args")
        try:
            utils_test.load_stress("stress_on_host", params=params)
        except Exception as err:
            test.fail("Error running stress in host: %s" % err)
    try:
        stress_event.run_threads()
    finally:
        stress_event.wait_for_threads()
        if guest_stress:
            utils_test.unload_stress("stress_in_vms", params=params, vms=vms)
        if host_stress:
            utils_test.unload_stress("stress_on_host", params=params)
        if "reboot" not in stress_events:
            fail = False
            for vm in vms:
                if vm.uptime() < vms_uptime_init[vm.name]:
                    logging.error("Unexpected reboot of VM: %s between test", vm.name)
                    fail = True
            if fail:
                test.fail("Unexpected VM reboot detected")
