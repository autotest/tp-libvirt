import logging
import re

from autotest.client.shared import error

from virttest import libvirt_xml
from virttest import libvirt_vm
from virttest import utils_test
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test: vcpu hotplug.

    The command can change the number of virtual CPUs for VM.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh setvcpus operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    min_count = int(params.get("setvcpus_min_count", "1"))
    max_count = int(params.get("setvcpus_max_count", "2"))
    test_times = int(params.get("setvcpus_test_times", "1"))
    stress_type = params.get("stress_type", "")
    stress_param = params.get("stress_param", "")
    add_by_virsh = ("yes" == params.get("add_by_virsh"))
    del_by_virsh = ("yes" == params.get("del_by_virsh"))
    test_set_max = max_count * 2

    # Save original configuration
    orig_config_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Set min/max of vcpu
    libvirt_xml.VMXML.set_vm_vcpus(vm_name, test_set_max, min_count)

    # prepare VM instance
    vm = libvirt_vm.VM(vm_name, params, test.bindir, env.get("address_cache"))

    # prepare guest-agent service
    vm.prepare_guest_agent()

    # Increase the workload
    load_vms = []
    if stress_type in ['cpu', 'memory', 'io']:
        params["stress_args"] = stress_param
    load_vms.append(vm)
    if stress_type in ['cpu', 'memory']:
        utils_test.load_stress("stress_in_vms", load_vms, params)
    else:
        utils_test.load_stress("iozone_in_vms", load_vms, params)

    session = vm.wait_for_login()
    try:
        # Clear dmesg before set vcpu
        session.cmd("dmesg -c")
        for i in range(test_times):
            # 1. Add vcpu
            add_result = libvirt.hotplug_domain_vcpu(vm_name,
                                                     max_count,
                                                     add_by_virsh)
            add_status = add_result.exit_status
            # 1.1 check add status
            if add_status:
                if add_result.stderr.count("support"):
                    raise error.TestNAError("No need to test any more:\n %s"
                                            % add_result.stderr.strip())
                raise error.TestFail("Test failed for:\n %s"
                                     % add_result.stderr.strip())
            # 1.2 check dmesg
            domain_add_dmesg = session.cmd_output("dmesg -c")
            dmesg1 = "CPU%d has been hot-added" % (max_count - 1)
            dmesg2 = "CPU %d got hotplugged" % (max_count - 1)
            if (not domain_add_dmesg.count(dmesg1) and
                    not domain_add_dmesg.count(dmesg2)):
                raise error.TestFail("Cannot find hotplug info in dmesg: %s"
                                     % domain_add_dmesg)
            # 1.3 check cpu related file
            online_cmd = "cat /sys/devices/system/cpu/cpu%d/online" \
                         % (max_count - 1)
            st, ot = session.cmd_status_output(online_cmd)
            if st:
                raise error.TestFail("Cannot find CPU%d after hotplug"
                                     % (max_count - 1))
            # 1.4 check online
            if not ot.strip().count("1"):
                raise error.TestFail("CPU%d is not online after hotplug: %s"
                                     % ((max_count - 1), ot))
            # 1.5 check online interrupts info
            inter_on_output = session.cmd_output("cat /proc/interrupts")
            if not inter_on_output.count("CPU%d" % (int(max_count) - 1)):
                raise error.TestFail("CPU%d can not be found in "
                                     "/proc/interrupts when it's online:%s"
                                     % ((int(max_count) - 1), inter_on_output))
            # 1.6 offline vcpu
            off_st = session.cmd_status("echo 0 > "
                                        "/sys/devices/system/cpu/cpu%d/online"
                                        % (max_count - 1))
            if off_st:
                raise error.TestFail("Set cpu%d offline failed!"
                                     % (max_count - 1))
            # 1.7 check offline interrupts info
            inter_off_output = session.cmd_output("cat /proc/interrupts")
            if inter_off_output.count("CPU%d" % (int(max_count) - 1)):
                raise error.TestFail("CPU%d can be found in /proc/interrupts"
                                     " when it's offline"
                                     % (int(max_count) - 1))
            # 2. Del vcpu
            del_result = libvirt.hotplug_domain_vcpu(vm_name,
                                                     min_count,
                                                     del_by_virsh,
                                                     hotplug=False)
            del_status = del_result.exit_status
            if del_status:
                logging.info("del_result: %s" % del_result.stderr.strip())
                # A qemu older than 1.5 or an unplug for 1.6 will result in
                # the following failure.
                # TODO: when CPU-hotplug feature becomes stable and strong,
                #       remove these codes used to handle kinds of exceptions
                if re.search("The command cpu-del has not been found",
                             del_result.stderr):
                    raise error.TestNAError("unhotplug failed")
                if re.search("cannot change vcpu count", del_result.stderr):
                    raise error.TestNAError("unhotplug failed")
                if re.search("got wrong number of vCPU pids from QEMU monitor",
                             del_result.stderr):
                    raise error.TestNAError("unhotplug failed")
                # process all tips that contains keyword 'support'
                # for example, "unsupported"/"hasn't been support" and so on
                if re.search("support", del_result.stderr):
                    raise error.TestNAError("unhotplug failed")

                # besides above, regard it failed
                raise error.TestFail("Test fail for:\n %s"
                                     % del_result.stderr.strip())
            domain_del_dmesg = session.cmd_output("dmesg -c")
            if not domain_del_dmesg.count("CPU %d is now offline"
                                          % (max_count - 1)):
                raise error.TestFail("Cannot find hot-unplug info in dmesg: %s"
                                     % domain_del_dmesg)
    except error.TestNAError:
        # So far, QEMU doesn't support unplug vcpu,
        # unplug operation will encounter kind of errors.
        pass
    finally:
        session.close()
        # Cleanup
        orig_config_xml.sync()
