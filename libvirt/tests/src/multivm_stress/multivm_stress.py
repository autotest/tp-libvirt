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
    vms = env.get_all_vms()
    stress_event = utils_stress.VMStressEvents(params, env)
    if guest_stress:
        try:
            utils_test.load_stress("stress_in_vms", params=params, vms=vms)
        except Exception as err:
            test.fail("Error running stress in vms: %s" % err)
    try:
        stress_event.run_threads()
    finally:
        stress_event.wait_for_threads()
