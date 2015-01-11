import time
import logging
from autotest.client.shared import error
from virttest import libvirt_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test: vcpu hotplug.

    The command can change the number of virtual CPUs in the guest domain.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh setvcpus operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    test_times = int(params.get("setvcpus_test_times"))
    min_count = int(params.get("setvcpus_min_count", "1"))
    max_count = int(params.get("setvcpus_max_count", "2"))
    stress_type = params.get("stress_type", "")
    add_cmd = params.get("add_cmd", "setvcpus")
    del_cmd = params.get("del_cmd", "setvcpus")
    iozone_path = params.get("iozone_path", "")
    test_set_max = 2

    # Save original configuration
    orig_config_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if min_count > test_set_max or max_count > test_set_max:
        raise error.TestFail("Min(%d) or Max(%d) > test set max(%d)" %
                                 (min_count, max_count, test_set_max))

    # Set current to be min vcpu before test
    libvirt_xml.VMXML.set_vm_vcpus(vm_name, test_set_max, min_count)
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    try:
        if stress_type in ["memory", "cpu"]:
            if session.cmd_status("stress -h"):
                raise error.TestNAError("'stress' command has"
                                        "not been installed!")
        elif stress_type == "io":
            if session.cmd_status("ls %s/iozone" % iozone_path):
                raise error.TestNAError("Cannot find 'iozone' tools in guest!")
        if stress_type == "memory":
            # memory stress action
            session.cmd_output("stress -m 1 --vm-bytes 1G  &")
        elif stress_type == "cpu":
            session.cmd_output("stress -c 1 &")
            # cpu stress action
        elif stress_type == "io":
            session.cmd_output("%s/iozone -a -n 512m -g 4g"
                        " -i 0 -i 1 -i 5 -f /mnt/iozone -Rb"
                        " %s/iozone.xls &" % (iozone_path, iozone_path))
            # io stress action
        else:
            # No stress test
            pass
        time.sleep(2)
    except error.TestNAError, e:
        session.close()
        orig_config_xml.sync()
        raise error.TestNAError(str(e))
    except Exception, e:
        session.close()
        orig_config_xml.sync()
        raise error.TestFail(str(e))
    try:
        # Clear dmesg before set vcpu
        session.cmd("dmesg -c")
        for i in range(test_times):
            logging.info("This is the %d time(s) to test" % i)
            # 1. Add vcpu
            add_result = libvirt.hotplug_domain_vcpu(vm_name, max_count,
                                                     add_cmd)
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
            if not domain_add_dmesg.count("CPU %d got hotplugged"
                                          % (max_count - 1)):
                raise error.TestFail("Cannot find hotplug info in dmesg: %s"
                                     % domain_add_dmesg)
            # 1.3 check cpu related file
            online_cmd = "cat /sys/devices/system/cpu/cpu%d/online"\
                         % (max_count - 1)
            st, ot = session.cmd_status_output(online_cmd)
            if st:
                raise error.TestFail("Cannot find Cpu%d related file after"
                                     " hotplug" % (max_count - 1))
            # 1.4 check online
            if not ot.strip().count("1"):
                raise error.TestFail("Cpu%d is not online after hotplug"
                                     % (max_count - 1))
            # 1.5 check online interrupts info
            inter_on_output = session.cmd_output("cat /proc/interrupts")
            if not inter_on_output.count("CPU%d" % (int(max_count) - 1)):
                raise error.TestFail("Cpu%d can not be found in "
                                     "/proc/interrupts when it's online"
                                     % (int(max_count) - 1))
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
                raise error.TestFail("Cpu%d can be found in /proc/interrupts"
                                     " when it's offline"
                                     % (int(max_count) - 1))
            # 2. Del vcpu
            del_result = libvirt.hotplug_domain_vcpu(vm_name, min_count,
                                                     del_cmd, "unhotplug")
            del_status = del_result.exit_status
            if del_status:
                if del_result.stderr.count("support"):
                    raise error.TestNAError("No need to test any more:\n %s"
                                            % del_result.stderr.strip())
                raise error.TestFail("Test fail for:\n %s"
                                     % del_result.stderr.strip())
            domain_del_dmesg = session.cmd_output("dmesg -c")
            if not domain_del_dmesg.count("CPU %d is now offline"
                                          % (max_count - 1)):
                raise error.TestFail("Cannot find unhotplug info in dmesg: %s"
                                     % domain_del_dmesg)
    finally:
        session.close()
        # Cleanup
        orig_config_xml.sync()
