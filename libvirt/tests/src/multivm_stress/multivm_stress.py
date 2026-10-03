import logging as log
import time

from virttest import utils_stress
from virttest import error_context
from virttest import utils_test
from virttest import virsh
from virttest.libvirt_xml import vm_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


@error_context.context_aware
def run(test, params, env):
    """
    :param test:   kvm test object
    :param params: Dictionary with the test parameters
    :param env:    Dictionary with test environment.
    """

    guest_stress = params.get("guest_stress", "no") == "yes"
    host_stress = params.get("host_stress", "no") == "yes"
    stress_events = params.get("stress_events", "")
    stress_time = params.get("stress_time", "30")
    debug_dir = params.get("debug_dir", "/home/")
    dump_options = params.get("dump_options", "--memory-only --bypass-cache")
    vms = env.get_all_vms()
    vms_uptime_init = {}

    if "reboot" not in stress_events:
        for vm in vms:
            vms_uptime_init[vm.name] = vm.uptime()

    if guest_stress:
        # change the on_crash value to "preserve" when guest crashes
        for vm in vms:
            logging.debug("Setting on_crash to preserve in %s" % vm.name)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml.on_crash = "preserve"
            vmxml.sync()
            vm.start()

        try:
            utils_test.load_stress("stress_in_vms", params=params, vms=vms)
        except Exception as err:
            test.fail("Error running stress in vms: %s" % str(err))

    if host_stress:
        if params.get("host_stress_args", ""):
            params["stress_args"] = params.get("host_stress_args")
        try:
            utils_test.load_stress("stress_on_host", params=params)
        except Exception as err:
            test.fail("Error running stress in host: %s" % str(err))

    stress_timer = int(stress_time)
    fail = False
    found_traces = False
    failed_vms = []
    login_error_vms = []
    unexpected_reboot_vms = []
    error_message = ""

    if guest_stress:
        # check for any call traces in guest dmesg while stress is running
        def check_call_traces(vm):
            nonlocal stress_timer
            found_trace = False
            try:
                retry_login = True
                retry_times = 0
                while retry_login:
                    try:
                        retry_login = False
                        session = vm.wait_for_login(timeout=100)
                        if vm in login_error_vms:
                            login_error_vms.remove(vm)

                    except Exception:
                        stress_timer -= 150
                        if vm in login_error_vms:
                            return False

                        retry_login = True
                        retry_times += 1
                        if retry_times == 3:
                            logging.debug("Error in logging into %s" % vm.name)
                            if vm not in login_error_vms:
                                login_error_vms.append(vm)
                            return False

                        time.sleep(30)
                        stress_timer -= 30

                dmesg = session.cmd("dmesg")
                dmesg_level = session.cmd("dmesg -l emerg,alert,crit")
                if "Call Trace" in dmesg or len(dmesg_level) >= 1:
                    logging.debug("Call trace found in %s" % vm.name)
                    if vm not in failed_vms:
                        failed_vms.append(vm)
                    found_trace = True
                session.close()

            except Exception as err:
                test.error("Error getting dmesg of %s due to %s" % (vm.name, str(err)))
            return found_trace

        # run stress for stress_time seconds
        logging.debug("Sleeping for %s seconds waiting for stress completion" % stress_time)
        stress_time = int(stress_time)

        # check domstate of vms after stress_time
        if stress_time < 600:
            time.sleep(stress_time)
            for vm in vms:
                if vm.state() != "running":
                    logging.debug("%s state is %s" % (vm.name, vm.state()))
                    failed_vms.append(vm)
                    fail = True
                else:
                    found_traces = check_call_traces(vm)
                    if found_traces:
                        fail = True
                    time.sleep(2)

        # check domstate of vms for every 5 minutes during stress_time
        else:
            all_failed = False
            number_of_checks = int(stress_time / 600)
            delta_time = int(stress_time % 600)
            for itr in range(number_of_checks):
                if len(failed_vms) == len(vms) or len(login_error_vms) == len(vms):
                    all_failed = True
                    break
                if stress_timer <= 0:
                    break
                time.sleep(600)
                for vm in vms:
                    if vm.state() != "running":
                        logging.debug("%s state is %s" % (vm.name, vm.state()))
                        if vm not in failed_vms:
                            failed_vms.append(vm)
                        fail = True
                    else:
                        found_traces = check_call_traces(vm)
                        if found_traces:
                            fail = True
                        time.sleep(3)
                        stress_timer -= 3

            if delta_time > 0 and stress_timer > 0 and not all_failed:
                time.sleep(delta_time)
                for vm in vms:
                    if vm.state() != "running":
                        logging.debug("%s state is %s" % (vm.name, vm.state()))
                        if vm not in failed_vms:
                            failed_vms.append(vm)
                        fail = True
                    else:
                        found_traces = check_call_traces(vm)
                        if found_traces:
                            fail = True
                        time.sleep(3)
                        stress_timer -= 3

        # virsh dump the failed vms into debug_dir
        if fail:
            for vm in failed_vms:
                if vm.state() != "shut off":
                    logging.debug("Dumping %s to debug_dir %s" % (vm.name, debug_dir))
                    virsh.dump(vm.name, debug_dir+vm.name+"-core", dump_options, ignore_status=False, debug=True)
                    logging.debug("Successfully dumped %s as %s-core" % (vm.name, vm.name))
                else:
                    logging.debug("Cannot dump %s as it is in shut off state" % vm.name)
            failed_vms_string = ", ".join(vm.name for vm in failed_vms)
            error_message = "Failure in " + failed_vms_string + " while running stress. "

        if login_error_vms:
            login_error_vms_string = ", ".join(vm.name for vm in login_error_vms)
            error_message += "Login error in " + login_error_vms_string + " while running stress. "

        if len(failed_vms) == len(vms) or len(login_error_vms) == len(vms):
            error_message += "All vms in unstable state while running stress. Couldn't run STRESS EVENTS"
            test.fail(error_message)

    # run STRESS EVENTS in the remaining stable guests
    if len(failed_vms) < len(vms) and len(login_error_vms) < len(vms):
        for vm in failed_vms:
            if vm in vms:
                vms.remove(vm)
        for vm in login_error_vms:
            if vm in vms:
                vms.remove(vm)

        if len(vms) == 0:
            error_message += "All vms in unstable state while running stress. Couldn't run STRESS EVENTS"
            test.fail(error_message)

        new_vms = ", ".join(vm.name for vm in vms)
        try:
            if stress_events != "":
                logging.debug("Running stress_events in %s" % new_vms)
                stress_event = utils_stress.VMStressEvents(params, env, vms)
                stress_event.run_threads()
                stress_event.wait_for_threads()

            if guest_stress:
                utils_test.unload_stress("stress_in_vms", params=params, vms=vms)

            if host_stress:
                utils_test.unload_stress("stress_on_host", params=params)

            if "reboot" not in stress_events:
                for vm in vms:
                    if vm.uptime() < vms_uptime_init[vm.name]:
                        logging.debug("Unexpected reboot of VM: %s between test", vm.name)
                        unexpected_reboot_vms.append(vm)
                unexpected_reboot_vms_string = ", ".join(vm.name for vm in unexpected_reboot_vms)
                if unexpected_reboot_vms:
                    error_message += "Unexpected reboot of guest(s) " + unexpected_reboot_vms_string + ". "

        except Exception as err:
            error_message += "Failure running STRESS EVENTS in " + new_vms + " due to" + str(err)

    # check the test status
    if error_message:
        test.fail(error_message)
