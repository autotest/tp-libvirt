import logging
import time

from virttest import error_context
from virttest import utils_test
from virttest import virsh
from virttest.libvirt_xml import vm_xml


@error_context.context_aware
def run(test, params, env):

    rcu_stall_traces = params.get("rcu_stall_traces", "no") == "yes"
    vms = env.get_all_vms()
    stress_time = int(params.get("stress_time", "150"))
    stress_type = params.get("stress_type", "stress_in_vms")
    check_snap_time = params.get("check_snap_time", "no") == "yes"

    for vm in vms:
        # setting on_crash value to "preserve" when guest crashes
        logging.debug("Setting on_crash to preserve in %s" % vm.name)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml.on_crash = "preserve"
        vmxml.sync()
        vm.start()

    if stress_type == "stress_in_vms":
        try:
            utils_test.load_stress(stress_type, params=params, vms=vms)
        except Exception as err:
            test.fail("Error running stress in vms: %s" % str(err))

    elif stress_type == "htxcmdline_in_vms":
        try:
            utils_test.load_htxstress_tool(stress_type, params=params, vms=vms)
        except Exception as err:
            test.fail("Error running htx stress in vms: %s" % str(err))

    def get_snapshot(vm, itr):

        logging.debug("Creating snapshot %d for guest %s" % ((itr+1), vm.name))
        try:
            snap_name = virsh.snapshot_create(vm.name)
            logging.debug(snap_name.stdout)

        except Exception as err:
            logging.debug("Couldn't take snapshot..!!\n"
                          "Hence removing the vm from further testing")
            testVMs.remove(vm)
            return

        return

    def check_domain_state(vm):

        if vm.state() != "running":
            logging.debug("Domain %s is in %s state. Hence removing" % (vm.name, vm.state()))
            testVMs.remove(vm)

    def check_rcu_traces(vm):

        #check for any call traces in guest dmesg along with RCU stall
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
                    retry_time += 1
                    if retry_times == 3:
                        logging.debug("Error in logging into %s" % vm.name)
                        if vm not in login_error_vms:
                            login_error_vms.append(vm)
                        return False

                    time.sleep(30)
                    stress_timer -= 30

            dmesg = session.cmd("dmesg")
            dmesg_level = session.cmd("dmesg | grep -i rcu_sched detected")
            if "Call Trace" in dmesg or len(dmesg_level) >= 1:
                logging.debug("Call traces found in %s" % vm.name)
                if vm not in failed_vms:
                    failed_vms.append(vm)
                found_trace = True
            session.close()
        except Exception as err:
            test.error("Error getting dmesg of %s due to %s" % (vm.name, str(err)))

        return found_trace

    logging.debug("Sleeping for %ds for stress completion" % stress_time)

    stress_timer = stress_time
    total_checks = int(stress_time / 60) + 1
    delta_time = int(stress_time % 60)
    testVMs = vms
    RCU_VMs = []

    for itr in range(total_checks):

        RCUs_found = False

        for vm in testVMs:

            check_domain_state(vm)

            if len(testVMs) == 0:
                break

            if rcu_stall_traces:
                logging.debug("Checking for RCU traces before taking snapshot")
                found_rcu = False
                found_rcu = check_rcu_traces()
                if found_rcu:
                    RCUs_found = True
                    logging.debug("RCU stall traces found on domain %s" % vm.name)
                    RCU_VMs.append(vm)
                    testVMs.remove(vm)
                    continue

            if check_snap_time:
                time_before_snap = time.time()

            get_snapshot(vm, itr)

            if rcu_stall_traces:
                logging.debug("Checking for RCU traces after taking snapshot")
                found_rcu = check_rcu_traces()
                if found_rcu:
                    RCUs_found = True
                    logging.debug("RCU stall traces found on domain %s" % vm.name)
                    RCU_VMs.append(vm)
                    testVMs.remove(vm)

            if check_snap_time:
                time_after_snap = time.time()
                snap_duration = time_after_snap - time_before_snap
                logging.debug("TIME TAKEN = %d" % snap_duration)

        if len(testVMs) == 0:
            test.fail("No guests left for testing")
            break

        if itr != total_checks - 1:
            time.sleep(60)
        if itr != 0:
            stress_timer -= 60

    if delta_time > 0 and stress_timer > 0 and len(testVMs) != 0:

        time.sleep(delta_time)
        for vm in testVMs:
            check_domain_state(vm)

            if len(testVMs) == 0:
                test.fail("No guests left for testing")
                break

            get_snapshot(vm, (total_checks))

    else:
        if len(testVMs) != 0:
            pass
        else:
            logging.debug("Test failed on all the guests"
                          "check log for details")

    logging.debug("code for unloading the stress to be added")

    for vm in vms:
        if vm.state() == "running":
            virsh.destroy(vm)

        for snap in range(len(virsh.snapshot_list(vm.name))):
            virsh.snapshot_delete(vm, "--current")

        virsh.undefine(vm.name)
