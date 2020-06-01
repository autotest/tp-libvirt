import os
import re
import logging
import platform
import time

from avocado.utils import cpu as cpu_util

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import cpu
from virttest import utils_libvirtd
from virttest import utils_test
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML

vm_uptime_init = 0


def run(test, params, env):
    """
    Domain CPU management testing.

    1. Prepare a domain for testing, install qemu-guest-ga if needed.
    2. Checking for vcpu numbers in vcpucount, vcpuinfo, domain xml,
       vcpupin and inside domain.
    3. Plug vcpu for the domain.
    4. Repeat step 2 to check again.
    5. Control domain(save, managedsave, s3, s4, etc.).
    6. Repeat step 2 to check again.
    7. Recover domain(restore, wakeup, etc.).
    8. Repeat step 2 to check again.
    9. Unplug vcpu for the domain.
    10. Repeat step 2 to check again.
    11. Repeat step 5 to control domain(As BZ#1088216 not fix, skip
        save/managedsave related actions).
    12. Repeat step 2 to check again.
    13. Repeat step 7 to recover domain.
    14. Repeat step 2 to check again.
    15. Recover test environment.
    """

    def manipulate_domain(vm_name, vm_operation, recover=False):
        """
        Operate domain to given state or recover it.

        :params vm_name: Name of the VM domain
        :params vm_operation: Operation to be performed on VM domain
                              like save, managedsave, suspend
        :params recover: flag to inform whether to set or reset
                         vm_operation
        """
        global vm_uptime_init
        save_file = os.path.join(data_dir.get_tmp_dir(), vm_name + ".save")
        if not recover:
            if vm_operation == "save":
                save_option = ""
                result = virsh.save(vm_name, save_file, save_option,
                                    ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif vm_operation == "managedsave":
                managedsave_option = ""
                result = virsh.managedsave(vm_name, managedsave_option,
                                           ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif vm_operation == "s3":
                suspend_target = "mem"
                result = virsh.dompmsuspend(vm_name, suspend_target,
                                            ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif vm_operation == "s4":
                suspend_target = "disk"
                result = virsh.dompmsuspend(vm_name, suspend_target,
                                            ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                # Wait domain state change: 'in shutdown' -> 'shut off'
                utils_misc.wait_for(lambda: virsh.is_dead(vm_name), 5)
            elif vm_operation == "suspend":
                result = virsh.suspend(vm_name, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif vm_operation == "reboot":
                vm.reboot()
                vm_uptime_init = vm.uptime()
            else:
                logging.debug("No operation for the domain")

        else:
            if vm_operation == "save":
                if os.path.exists(save_file):
                    result = virsh.restore(save_file, ignore_status=True,
                                           debug=True)
                    libvirt.check_exit_status(result)
                    os.remove(save_file)
                else:
                    test.error("No save file for domain restore")
            elif vm_operation in ["managedsave", "s4"]:
                result = virsh.start(vm_name, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif vm_operation == "s3":
                suspend_target = "mem"
                result = virsh.dompmwakeup(vm_name, ignore_status=True,
                                           debug=True)
                libvirt.check_exit_status(result)
            elif vm_operation == "suspend":
                result = virsh.resume(vm_name, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
            elif vm_operation == "reboot":
                pass
            else:
                logging.debug("No need recover the domain")

    def online_new_vcpu(vm, vcpu_plug_num):
        """
        For Fedora/RHEL7 guests, udev can not online hot-added CPUs
        automatically, (refer to BZ#968811 for details) so enable them manually.

        :params vm: VM object
        :params vcpu_plug_num: Hotplugged vcpu count
        """
        cpu_is_online = []
        session = vm.wait_for_login()
        for i in range(1, int(vcpu_plug_num)):
            cpu_is_online.append(False)
            cpu = "/sys/devices/system/cpu/cpu%s/online" % i
            cmd_s, cmd_o = session.cmd_status_output("cat %s" % cpu)
            logging.debug("cmd exist status: %s, cmd output %s", cmd_s, cmd_o)
            if cmd_s != 0:
                logging.error("Can not find cpu %s in domain", i)
            else:
                if cmd_o.strip() == "0":
                    if session.cmd_status("echo 1 > %s" % cpu) == 0:
                        cpu_is_online[i-1] = True
                    else:
                        logging.error("Fail to enable cpu %s online", i)
                else:
                    cpu_is_online[i-1] = True
        session.close()
        return False not in cpu_is_online

    def check_setvcpus_result(cmd_result, expect_error):
        """
        Check command result.

        For setvcpus, pass unsupported commands(plug or unplug vcpus) by
        checking command stderr.

        :params cmd_result: Command result
        :params expect_error: Whether to expect error True or False
        """
        if cmd_result.exit_status != 0:
            if expect_error:
                logging.debug("Expect fail: %s", cmd_result.stderr)
                return
            # setvcpu/hotplug is only available as of qemu 1.5 and it's still
            # evolving. In general the addition of vcpu's may use the QMP
            # "cpu_set" (qemu 1.5) or "cpu-add" (qemu 1.6 and later) commands.
            # The removal of vcpu's may work in qemu 1.5 due to how cpu_set
            # can set vcpus online or offline; however, there doesn't appear
            # to be a complementary cpu-del feature yet, so we can add, but
            # not delete in 1.6.

            # A 1.6 qemu will not allow the cpu-add command to be run on
            # a configuration using <os> machine property 1.4 or earlier.
            # That is the XML <os> element with the <type> property having
            # an attribute 'machine' which is a tuple of 3 elements separated
            # by a dash, such as "pc-i440fx-1.5" or "pc-q35-1.5".
            if re.search("unable to execute QEMU command 'cpu-add'",
                         cmd_result.stderr):
                test.cancel("guest <os> machine property may be too"
                            "  old to allow hotplug")

            # A qemu older than 1.5 or an unplug for 1.6 will result in
            # the following failure.  In general, any time libvirt determines
            # it cannot support adding or removing a vCPU...
            if re.search("cannot change vcpu count of this domain",
                         cmd_result.stderr):
                test.cancel("Unsupport virsh setvcpu hotplug")

            # Maybe QEMU doesn't support unplug vcpu
            if re.search("Operation not supported: qemu didn't unplug the vCPUs",
                         cmd_result.stderr):
                test.cancel("Your qemu unsupport unplug vcpu")

            # Qemu guest agent version could be too low
            if re.search("The command guest-get-vcpus has not been found",
                         cmd_result.stderr):
                err_msg = "Your agent version is too low: %s" % cmd_result.stderr
                logging.warning(err_msg)
                test.cancel(err_msg)

            # Attempting to enable more vCPUs in the guest than is currently
            # enabled in the guest but less than the maximum count for the VM
            if re.search("requested vcpu count is greater than the count of "
                         "enabled vcpus in the domain",
                         cmd_result.stderr):
                logging.debug("Expect fail: %s", cmd_result.stderr)
                return

            # Otherwise, it seems we have a real error
            test.fail("Run failed with right command: %s"
                      % cmd_result.stderr)
        else:
            if expect_error:
                test.fail("Expect fail but run successfully")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    global vm_uptime_init
    vm_operation = params.get("vm_operation", "null")
    vcpu_max_num = int(params.get("vcpu_max_num"))
    vcpu_current_num = int(params.get("vcpu_current_num"))
    vcpu_plug = "yes" == params.get("vcpu_plug", "no")
    vcpu_plug_num = int(params.get("vcpu_plug_num"))
    vcpu_unplug = "yes" == params.get("vcpu_unplug", "no")
    vcpu_unplug_num = int(params.get("vcpu_unplug_num"))
    vcpu_max_timeout = int(params.get("vcpu_max_timeout", "480"))
    setvcpu_option = params.get("setvcpu_option", "")
    agent_channel = "yes" == params.get("agent_channel", "yes")
    install_qemuga = "yes" == params.get("install_qemuga", "no")
    start_qemuga = "yes" == params.get("start_qemuga", "no")
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    setvcpu_readonly = "yes" == params.get("setvcpu_readonly", "no")
    status_error = "yes" == params.get("status_error", "no")
    pin_before_plug = "yes" == params.get("pin_before_plug", "no")
    pin_after_plug = "yes" == params.get("pin_after_plug", "no")
    pin_before_unplug = "yes" == params.get("pin_before_unplug", "no")
    pin_after_unplug = "yes" == params.get("pin_after_unplug", "no")
    pin_vcpu = params.get("pin_vcpu")
    pin_cpu_list = params.get("pin_cpu_list", "x")
    check_after_plug_fail = "yes" == params.get("check_after_plug_fail", "no")
    with_stress = "yes" == params.get("run_stress", "no")
    iterations = int(params.get("test_itr", 1))
    topology_correction = "yes" == params.get("topology_correction", "no")
    # Init expect vcpu count values
    expect_vcpu_num = {'max_config': vcpu_max_num, 'max_live': vcpu_max_num,
                       'cur_config': vcpu_current_num,
                       'cur_live': vcpu_current_num,
                       'guest_live': vcpu_current_num}
    if check_after_plug_fail:
        expect_vcpu_num_bk = expect_vcpu_num.copy()
    # Init expect vcpu pin values
    expect_vcpupin = {}
    result_failed = 0

    # Init cpu-list for vcpupin
    host_cpu_count = os.sysconf('SC_NPROCESSORS_CONF')
    if (int(host_cpu_count) < 2) and (not pin_cpu_list == "x"):
        test.cancel("We need more cpus on host in this case for the cpu-list"
                    "=%s. But current number of cpu on host is %s."
                    % (pin_cpu_list, host_cpu_count))

    cpus_list = cpu_util.cpu_online_list()
    logging.debug("Active cpus in host are %s", cpus_list)

    cpu_seq_str = ""
    for i in range(len(cpus_list) - 1):
        if int(cpus_list[i]) + 1 == int(cpus_list[i + 1]):
            cpu_seq_str = "%s-%s" % (cpus_list[i], cpus_list[i + 1])
            break

    if pin_cpu_list == "x":
        pin_cpu_list = cpus_list[-1]
    if pin_cpu_list == "x-y":
        if cpu_seq_str:
            pin_cpu_list = cpu_seq_str
        else:
            pin_cpu_list = "%s-%s" % (cpus_list[0], cpus_list[0])
    elif pin_cpu_list == "x,y":
        pin_cpu_list = "%s,%s" % (cpus_list[0], cpus_list[1])
    elif pin_cpu_list == "x-y,^z":
        if cpu_seq_str:
            pin_cpu_list = cpu_seq_str + ",^%s" % cpu_seq_str.split('-')[1]
        else:
            pin_cpu_list = "%s,%s,^%s" % (cpus_list[0], cpus_list[1],
                                          cpus_list[0])
    else:
        # Just use the value get from cfg
        pass

    need_mkswap = False
    # Back up domain XML
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    try:
        # Customize domain vcpu number
        if vm.is_alive():
            vm.destroy()
        if agent_channel:
            vmxml.set_agent_channel()
        else:
            vmxml.remove_agent_channels()
        vmxml.sync()

        vmxml.set_vm_vcpus(vm_name, vcpu_max_num, vcpu_current_num,
                           topology_correction=topology_correction)
        # Do not apply S3/S4 on power
        cpu_arch = platform.machine()
        if cpu_arch in ('x86_64', 'i386', 'i686'):
            vmxml.set_pm_suspend(vm_name, "yes", "yes")
        vm.start()
        vm_uptime_init = vm.uptime()
        if with_stress:
            testlist = utils_test.get_avocadotestlist(params)
            bt = utils_test.run_avocado_bg(vm, params, test, testlist)
            if not bt:
                test.cancel("guest stress failed to start")
        # Create swap partition/file if nessesary
        if vm_operation == "s4":
            need_mkswap = not vm.has_swap()
        if need_mkswap:
            logging.debug("Creating swap partition")
            vm.create_swap_partition()

        # Prepare qemu guest agent
        if install_qemuga:
            vm.prepare_guest_agent(prepare_xml=False, start=start_qemuga)
            vm.setenforce(0)
        else:
            # Remove qemu-guest-agent for negative test
            vm.remove_package('qemu-guest-agent')

        # Run test
        for _ in range(iterations):
            if not cpu.check_vcpu_value(vm, expect_vcpu_num):
                logging.error("Expected vcpu check failed")
                result_failed += 1
            # plug vcpu
            if vcpu_plug:
                # Pin vcpu
                if pin_before_plug:
                    result = virsh.vcpupin(vm_name, pin_vcpu, pin_cpu_list,
                                           ignore_status=True, debug=True)
                    libvirt.check_exit_status(result)
                    expect_vcpupin = {pin_vcpu: pin_cpu_list}

                result = virsh.setvcpus(vm_name, vcpu_plug_num, setvcpu_option,
                                        readonly=setvcpu_readonly,
                                        ignore_status=True, debug=True)
                check_setvcpus_result(result, status_error)

                if setvcpu_option == "--config":
                    expect_vcpu_num['cur_config'] = vcpu_plug_num
                elif setvcpu_option == "--guest":
                    # vcpuset '--guest' only affect vcpu number in guest
                    expect_vcpu_num['guest_live'] = vcpu_plug_num
                else:
                    expect_vcpu_num['cur_live'] = vcpu_plug_num
                    expect_vcpu_num['guest_live'] = vcpu_plug_num
                    if not status_error:
                        if not utils_misc.wait_for(lambda: cpu.check_if_vm_vcpu_match(vcpu_plug_num, vm),
                                                   vcpu_max_timeout, text="wait for vcpu online") or not online_new_vcpu(vm, vcpu_plug_num):
                            test.fail("Fail to enable new added cpu")

                # Pin vcpu
                if pin_after_plug:
                    result = virsh.vcpupin(vm_name, pin_vcpu, pin_cpu_list,
                                           ignore_status=True, debug=True)
                    libvirt.check_exit_status(result)
                    expect_vcpupin = {pin_vcpu: pin_cpu_list}

                if status_error and check_after_plug_fail:
                    if not cpu.check_vcpu_value(vm, expect_vcpu_num_bk, {}, setvcpu_option):
                        logging.error("Expected vcpu check failed")
                        result_failed += 1

                if not status_error:
                    if restart_libvirtd:
                        utils_libvirtd.libvirtd_restart()

                    # Check vcpu number and related commands
                    if not cpu.check_vcpu_value(vm, expect_vcpu_num, expect_vcpupin, setvcpu_option):
                        logging.error("Expected vcpu check failed")
                        result_failed += 1

                    # Control domain
                    manipulate_domain(vm_name, vm_operation)

                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        if not cpu.check_vcpu_value(vm, expect_vcpu_num, expect_vcpupin, setvcpu_option):
                            logging.error("Expected vcpu check failed")
                            result_failed += 1

                    # Recover domain
                    manipulate_domain(vm_name, vm_operation, recover=True)

                    # Resume domain from S4 status may takes long time(QEMU bug),
                    # here we wait for 10 mins then skip the remaining part of
                    # tests if domain not resume successfully
                    try:
                        vm.wait_for_login(timeout=600)
                    except Exception as e:
                        test.cancel("Skip remaining test steps as domain"
                                    " not resume in 10 mins: %s" % e)
                    # For hotplug/unplug vcpu without '--config flag, after
                    # suspend domain to disk(shut off) and re-start it, the
                    # current live vcpu number will recover to orinial value
                    if vm_operation == 's4':
                        if setvcpu_option.count("--config"):
                            expect_vcpu_num['cur_live'] = vcpu_plug_num
                            expect_vcpu_num['guest_live'] = vcpu_plug_num
                        elif setvcpu_option.count("--guest"):
                            expect_vcpu_num['guest_live'] = vcpu_plug_num
                        else:
                            expect_vcpu_num['cur_live'] = vcpu_current_num
                            expect_vcpu_num['guest_live'] = vcpu_current_num
                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        if not cpu.check_vcpu_value(vm, expect_vcpu_num, expect_vcpupin, setvcpu_option):
                            logging.error("Expected vcpu check failed")
                            result_failed += 1

            # Unplug vcpu
            # Since QEMU 2.2.0, by default all current vcpus are non-hotpluggable
            # when VM started , and it required that vcpu 0(id=1) is always
            # present and non-hotpluggable, which means we can't hotunplug these
            # vcpus directly. So we can either hotplug more vcpus before we do
            # hotunplug, or modify the 'hotpluggable' attribute to 'yes' of the
            # vcpus except vcpu 0, to make sure libvirt can find appropriate
            # hotpluggable vcpus to reach the desired target vcpu count. For
            # simple prepare step, here we choose to hotplug more vcpus.
            if vcpu_unplug:
                if setvcpu_option == "--live":
                    logging.info("Hotplug vcpu to the maximum count to make"
                                 "sure all these new plugged vcpus are "
                                 "hotunpluggable")
                    result = virsh.setvcpus(vm_name, vcpu_max_num, '--live',
                                            debug=True)
                    libvirt.check_exit_status(result)
                # Pin vcpu
                if pin_before_unplug:
                    result = virsh.vcpupin(vm_name, pin_vcpu, pin_cpu_list,
                                           ignore_status=True, debug=True)
                    libvirt.check_exit_status(result)
                    # As the vcpu will unplug later, so set
                    # expect_vcpupin to empty
                    expect_vcpupin = {}

                # Operation of setvcpus is asynchronization, even if it return,
                # may not mean it is complete, a poll checking of guest vcpu numbers
                # need to be executed.
                # So for case of unpluging vcpus from max vcpu number to 1, when
                # setvcpus return, need continue to obverse if vcpu number is
                # continually to be unplugged to 1 gradually.
                result = virsh.setvcpus(vm_name, vcpu_unplug_num,
                                        setvcpu_option,
                                        readonly=setvcpu_readonly,
                                        ignore_status=True, debug=True)
                unsupport_str = cpu.vcpuhotunplug_unsupport_str()
                if unsupport_str and (unsupport_str in result.stderr):
                    test.cancel("Vcpu hotunplug is not supported in this host:"
                                "\n%s" % result.stderr)
                try:
                    session = vm.wait_for_login()
                    cmd = "lscpu | grep \"^CPU(s):\""
                    operation = "setvcpus"
                    prev_output = -1
                    while True:
                        ret, output = session.cmd_status_output(cmd)
                        if ret:
                            test.error("Run lscpu failed, output: %s" % output)
                        output = output.split(":")[-1].strip()

                        if int(prev_output) == int(output):
                            break
                        prev_output = output
                        time.sleep(5)
                    logging.debug("CPUs available from inside guest after %s - %s",
                                  operation, output)
                    if int(output) != vcpu_unplug_num:
                        test.fail("CPU %s failed as cpus are not "
                                  "reflected from inside guest" % operation)
                finally:
                    if session:
                        session.close()

                check_setvcpus_result(result, status_error)
                if setvcpu_option == "--config":
                    expect_vcpu_num['cur_config'] = vcpu_unplug_num
                elif setvcpu_option == "--guest":
                    # vcpuset '--guest' only affect vcpu number in guest
                    expect_vcpu_num['guest_live'] = vcpu_unplug_num
                else:
                    expect_vcpu_num['cur_live'] = vcpu_unplug_num
                    expect_vcpu_num['guest_live'] = vcpu_unplug_num

                # Pin vcpu
                if pin_after_unplug:
                    result = virsh.vcpupin(vm_name, pin_vcpu, pin_cpu_list,
                                           ignore_status=True, debug=True)
                    libvirt.check_exit_status(result)
                    expect_vcpupin = {pin_vcpu: pin_cpu_list}

                if not status_error:
                    if restart_libvirtd:
                        utils_libvirtd.libvirtd_restart()

                    # Check vcpu number and related commands
                    if not cpu.check_vcpu_value(vm, expect_vcpu_num, expect_vcpupin, setvcpu_option):
                        logging.error("Expected vcpu check failed")
                        result_failed += 1

                    # Control domain
                    manipulate_domain(vm_name, vm_operation)

                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        if not cpu.check_vcpu_value(vm, expect_vcpu_num, expect_vcpupin, setvcpu_option):
                            logging.error("Expected vcpu check failed")
                            result_failed += 1

                    # Recover domain
                    manipulate_domain(vm_name, vm_operation, recover=True)

                    # Resume domain from S4 status may takes long time
                    # (QEMU bug), here we wait for 10 mins then skip the
                    # remaining part of tests if domain not resume successfully
                    try:
                        vm.wait_for_login(timeout=600)
                    except Exception as e:
                        test.cancel("Skip remaining test steps as domain"
                                    " not resume in 10 mins: %s" % e)
                    # For hotplug/unplug vcpu without '--config flag, after
                    # suspend domain to disk(shut off) and re-start it, the
                    # current live vcpu number will recover to orinial value
                    if vm_operation == 's4':
                        if setvcpu_option.count("--config"):
                            expect_vcpu_num['cur_live'] = vcpu_unplug_num
                            expect_vcpu_num['guest_live'] = vcpu_unplug_num
                        elif setvcpu_option.count("--guest"):
                            expect_vcpu_num['guest_live'] = vcpu_unplug_num
                        else:
                            expect_vcpu_num['cur_live'] = vcpu_current_num
                            expect_vcpu_num['guest_live'] = vcpu_current_num
                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        if not cpu.check_vcpu_value(vm, expect_vcpu_num, expect_vcpupin, setvcpu_option):
                            logging.error("Expected vcpu check failed")
                            result_failed += 1
        if vm.uptime() < vm_uptime_init:
            test.fail("Unexpected VM reboot detected in between test")
    # Recover env
    finally:
        if need_mkswap:
            vm.cleanup_swap()
        if with_stress:
            if "bt" in locals() and bt:
                bt.join()
        vm.destroy()
        backup_xml.sync()

    if not status_error:
        if result_failed > 0:
            test.fail("Test Failed")
