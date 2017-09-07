import os
import re
import logging

from autotest.client import utils
from autotest.client.shared import error

from avocado.utils import cpu as cpu_util

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import utils_libvirtd
from virttest import utils_test
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError


def check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin, setvcpu_option=""):
    """
    Check domain vcpu, including vcpucount, vcpuinfo, vcpupin, vcpu number and
    cputune in domain xml, vcpu number inside the domain, and cpu-stats.

    :param vm: VM object
    :param expect_vcpu_num: A list of expect vcpu number:
        expect_vcpu_num[0] = maximum config vcpu nubmer
        expect_vcpu_num[1] = maximum live vcpu number
        expect_vcpu_num[2] = current config vcpu number
        expect_vcpu_num[3] = current live vcpu number
        expect_vcpu_num[4] = vcpu number inside the domain
    :param expect_vcpupin: A Dict of expect vcpu affinity
    :param setvcpu_option: Option for virsh setvcpus command
    """
    logging.debug("Expect vcpu number: %s", expect_vcpu_num)
    # Check virsh vcpucount output
    vcpucount_option = ""
    if setvcpu_option == "--guest" and vm.state() == "running":
        vcpucount_option = "--guest"
    result = virsh.vcpucount(vm.name, vcpucount_option, ignore_status=True,
                             debug=True)
    libvirt.check_exit_status(result)
    output = result.stdout.strip()
    if vcpucount_option == "--guest":
        if output != expect_vcpu_num[-1]:
            raise error.TestFail("Virsh vcpucount output is unexpected")
    else:
        elems = len(output.splitlines())
        for i in range(elems):
            # If domain is not alive, vcpucpunt output is:
            # #virsh vcpucount test
            #  maximum      config         4
            #  current      config         1
            # Which are correspond to expect_vcpu_num[0] and expect_vcpu[2]
            if vm.is_alive():
                j = i
            else:
                j = i + i
            try:
                if output.splitlines()[i].split()[-1] != expect_vcpu_num[j]:
                    raise error.TestFail("Virsh vcpucount output is unexpected")
            except IndexError, detail:
                raise error.TestFail(detail)
    logging.debug("Command vcpucount check pass")

    # Check virsh vcpuinfo output, (1) count vcpu number, if domain is
    # alive, vcpu number(current) correspond to expect_vcpu_num[3],
    # otherwise, it correspond to expect_vcpu_num[2]; (2) get cpus affinity,
    # and check them in virsh vcpupin command
    if vm.is_alive():
        i = 3
    else:
        i = 2
    result = virsh.vcpuinfo(vm.name, ignore_status=True, debug=True)
    libvirt.check_exit_status(result)
    output = result.stdout.strip()
    vcpuinfo_num = len(output.split("\n\n"))
    logging.debug("Get %s vcpus in virsh vcpuinfo output", vcpuinfo_num)
    if vcpuinfo_num != int(expect_vcpu_num[i]):
        raise error.TestFail("Vcpu number in virsh vcpuinfo is unexpected")
    vcpuinfo_affinity = re.findall('CPU Affinity: +([-y]+)', output)
    logging.debug("Command vcpuinfo check pass")

    # Check vcpu number in domain XML, if setvcpu with '--config' option,
    # or domain is dead, vcpu number correspond to expect_vcpu_num[2],
    # otherwise, it correspond to expect_vcpu_num[3]
    dumpxml_option = ""
    if setvcpu_option == "--config" or vm.is_dead():
        dumpxml_option = "--inactive"
        i = 2
    else:
        i = 3
    vmxml = VMXML()
    vmxml['xml'] = virsh.dumpxml(vm.name, dumpxml_option).stdout.strip()
    try:
        if vmxml['vcpu'] != int(expect_vcpu_num[0]):
            raise error.TestFail("Max vcpu number %s in domain XML is not"
                                 " expected" % vmxml['vcpu'])
        if vmxml['current_vcpu'] != expect_vcpu_num[i]:
            raise error.TestFail("Current vcpu number %s in domain XML is"
                                 " not expected" % vmxml['current_vcpu'])
    except (ValueError, IndexError), detail:
        raise error.TestFail(detail)
    logging.debug("Vcpu number in domain xml check pass")

    # check cpu affinity got from vcpupin command output, and vcpupin command
    # output, and vcpupin info(cputune element) in domain xml
    result = virsh.vcpupin(vm.name, ignore_status=True, debug=True)
    libvirt.check_exit_status(result)
    vcpupin_output = result.stdout.strip().splitlines()[2:]
    if expect_vcpupin:
        host_cpu_count = os.sysconf('SC_NPROCESSORS_CONF')
        xml_affinity_list = []
        xml_affinity = {}
        try:
            xml_affinity_list = vmxml['cputune'].vcpupins
        except LibvirtXMLNotFoundError:
            logging.debug("No <cputune> element find in domain xml")
        # Store xml_affinity_list to a dict
        for vcpu in xml_affinity_list:
            xml_affinity[vcpu['vcpu']] = "".join(
                libvirt.cpus_string_to_affinity_list(vcpu['cpuset'],
                                                     host_cpu_count))
        # Check
        for vcpu in expect_vcpupin.keys():
            if int(vcpu) not in range(len(vcpuinfo_affinity)):
                logging.error('Expect vcpu %s not exist', vcpu)
                continue

            expect_affinity = "".join(libvirt.cpus_string_to_affinity_list(
                expect_vcpupin[vcpu], host_cpu_count))
            logging.debug("Expect affinity of vcpu %s is: %s", vcpu,
                          expect_affinity)
            # Vcpuinfo
            logging.debug("Virsh vcpuinfo shows affinity of vcpu %s is: %s",
                          vcpu, vcpuinfo_affinity[int(vcpu)])
            if vcpuinfo_affinity[int(vcpu)] != expect_affinity:
                raise error.TestFail("CPU affinity in virsh vcpuinfo output"
                                     " is unexpected")
            # Vcpupin
            vcpupin_affinity = vcpupin_output[int(vcpu)].split(":")[1].strip()
            vcpupin_affinity = "".join(libvirt.cpus_string_to_affinity_list(
                vcpupin_affinity, host_cpu_count))
            logging.debug("Virsh vcpupin shows affinity of vcpu %s is: %s",
                          vcpu, vcpupin_affinity)
            if vcpupin_affinity != expect_affinity:
                raise error.TestFail("Virsh vcpupin output is unexpected")
            # Domain xml
            if xml_affinity:
                logging.debug("Domain xml shows affinity of vcpu %s is: %s",
                              vcpu, xml_affinity[vcpu])
                if xml_affinity[vcpu] != expect_affinity:
                    raise error.TestFail("Affinity in domain XML is unexpected")
    logging.debug("Vcpupin info check pass")

    # Check vcpu number inside the domian
    if vm.state() == "running":
        session = vm.wait_for_login()
        cmd = "cat /proc/cpuinfo | grep processor | wc -l"
        try:
            output = session.cmd_output(cmd, timeout=10).strip()
        finally:
            session.close()
        if output != expect_vcpu_num[-1]:
            raise error.TestFail("Find %s CPUs in domain but expect %s"
                                 % (output, expect_vcpu_num[-1]))
        else:
            logging.debug("Find %s CPUs in domian as expected", output)

        # Check cpu-stats command, only for running domian
        result = virsh.cpu_stats(vm.name, "", ignore_status=True, debug=True)
        libvirt.check_exit_status(result)


def manipulate_domain(vm_name, vm_operation, recover=False):
    """
    Operate domain to given state or recover it.
    """
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
        else:
            logging.debug("No operation for the domain")

    else:
        if vm_operation == "save":
            if os.path.exists(save_file):
                result = virsh.restore(save_file, ignore_status=True, debug=True)
                libvirt.check_exit_status(result)
                os.remove(save_file)
            else:
                raise error.TestError("No save file for domain restore")
        elif vm_operation in ["managedsave", "s4"]:
            result = virsh.start(vm_name, ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        elif vm_operation == "s3":
            suspend_target = "mem"
            result = virsh.dompmwakeup(vm_name, ignore_status=True, debug=True)
            libvirt.check_exit_status(result)
        else:
            logging.debug("No need recover the domain")


def online_new_vcpu(vm, vcpu_plug_num):
    """
    For Fedora/RHEL7 guests, udev can not online hot-added CPUs automatically,
    (refer to BZ#968811 for details), so enable them manually.
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
            raise error.TestNAError("guest <os> machine property may be too"
                                    "  old to allow hotplug")

        # A qemu older than 1.5 or an unplug for 1.6 will result in
        # the following failure.  In general, any time libvirt determines
        # it cannot support adding or removing a vCPU...
        if re.search("cannot change vcpu count of this domain",
                     cmd_result.stderr):
            raise error.TestNAError("Unsupport virsh setvcpu hotplug")

        # Maybe QEMU doesn't support unplug vcpu
        if re.search("Operation not supported: qemu didn't unplug the vCPUs",
                     cmd_result.stderr):
            raise error.TestNAError("Your qemu unsupport unplug vcpu")

        # Qemu guest agent version could be too low
        if re.search("The command guest-get-vcpus has not been found",
                     cmd_result.stderr):
            err_msg = "Your agent version is too low: %s" % cmd_result.stderr
            logging.warning(err_msg)
            raise error.TestNAError(err_msg)

        # Attempting to enable more vCPUs in the guest than is currently
        # enabled in the guest but less than the maximum count for the VM
        if re.search("requested vcpu count is greater than the count of "
                     "enabled vcpus in the domain",
                     cmd_result.stderr):
            logging.debug("Expect fail: %s", cmd_result.stderr)
            return

        # Otherwise, it seems we have a real error
        raise error.TestFail("Run failed with right command: %s"
                             % cmd_result.stderr)
    else:
        if expect_error:
            raise error.TestFail("Expect fail but run successfully")


def run(test, params, env):
    """
    Domain CPU management testing.

    1. Prepare a domain for testing, install qemu-guest-ga if needed.
    2. Plug vcpu for the domain.
    3. Checking:
      3.1. Virsh vcpucount.
      3.2. Virsh vcpuinfo.
      3.3. Current vcpu number in domain xml.
      3.4. Virsh vcpupin and vcpupin in domain xml.
      3.5. The vcpu number in domain.
      3.6. Virsh cpu-stats.
    4. Repeat step 3 to check again.
    5. Control domain(save, managedsave, s3, s4, migrate, etc.).
    6. Repeat step 3 to check again.
    7. Recover domain(restore, wakeup, etc.).
    8. Repeat step 3 to check again.
    9. Unplug vcpu for the domain.
    10. Repeat step 3 to check again.
    11. Repeat step 5 to control domain(As BZ#1088216 not fix, skip
        save/managedsave/migrate related actions).
    12. Repeat step 3 to check again.
    13. Repeat step 7 to recover domain.
    14. Repeat step 3 to check again.
    15. Recover test environment.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm_operation = params.get("vm_operation", "null")
    vcpu_max_num = params.get("vcpu_max_num")
    vcpu_current_num = params.get("vcpu_current_num")
    vcpu_plug = "yes" == params.get("vcpu_plug", "no")
    vcpu_plug_num = params.get("vcpu_plug_num")
    vcpu_unplug = "yes" == params.get("vcpu_unplug", "no")
    vcpu_unplug_num = params.get("vcpu_unplug_num")
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
    sockets = int(params.get("sockets", "0"))
    cores = int(params.get("cores", "0"))
    threads = int(params.get("threads", "0"))
    with_stress = "yes" == params.get("run_stress", "no")
    iterations = int(params.get("test_itr", 1))
    # Init expect vcpu count values
    expect_vcpu_num = [vcpu_max_num, vcpu_max_num, vcpu_current_num,
                       vcpu_current_num, vcpu_current_num]
    if check_after_plug_fail:
        expect_vcpu_num_bk = list(expect_vcpu_num)
    # Init expect vcpu pin values
    expect_vcpupin = {}

    # Init cpu-list for vcpupin
    host_cpu_count = os.sysconf('SC_NPROCESSORS_CONF')
    if (int(host_cpu_count) < 2) and (not pin_cpu_list == "x"):
        raise error.TestNAError("We need more cpus on host in this case for"
                                " the cpu-list=%s. But current number of cpu"
                                " on host is %s."
                                % (pin_cpu_list, host_cpu_count))

    cpus_list = utils.cpu_online_map()
    logging.info("Active cpus in host are %s", cpus_list)

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

        topology = vmxml.get_cpu_topology()
        if all([topology, sockets, cores, threads]):
            vmxml.set_vm_vcpus(vm_name, int(vcpu_max_num),
                               int(vcpu_current_num), sockets, cores, threads)
        else:
            vmxml.set_vm_vcpus(vm_name, int(vcpu_max_num),
                               int(vcpu_current_num))
        # Do not apply S3/S4 on power
        if 'power' not in cpu_util.get_cpu_arch():
            vmxml.set_pm_suspend(vm_name, "yes", "yes")
        vm.start()
        if with_stress:
            bt = utils_test.run_avocado_bg(vm, params, test)
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
            check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin)
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
                    expect_vcpu_num[2] = vcpu_plug_num
                elif setvcpu_option == "--guest":
                    # vcpuset '--guest' only affect vcpu number in guest
                    expect_vcpu_num[4] = vcpu_plug_num
                else:
                    expect_vcpu_num[3] = vcpu_plug_num
                    expect_vcpu_num[4] = vcpu_plug_num
                    if not status_error:
                        if not online_new_vcpu(vm, vcpu_plug_num):
                            raise error.TestFail("Fail to enable new added cpu")

                # Pin vcpu
                if pin_after_plug:
                    result = virsh.vcpupin(vm_name, pin_vcpu, pin_cpu_list,
                                           ignore_status=True, debug=True)
                    libvirt.check_exit_status(result)
                    expect_vcpupin = {pin_vcpu: pin_cpu_list}

                if status_error and check_after_plug_fail:
                    check_vcpu_number(vm, expect_vcpu_num_bk, {}, setvcpu_option)

                if not status_error:
                    if restart_libvirtd:
                        utils_libvirtd.libvirtd_restart()

                    # Check vcpu number and related commands
                    check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin,
                                      setvcpu_option)

                    # Control domain
                    manipulate_domain(vm_name, vm_operation)

                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin,
                                          setvcpu_option)

                    # Recover domain
                    manipulate_domain(vm_name, vm_operation, recover=True)

                    # Resume domain from S4 status may takes long time(QEMU bug),
                    # here we wait for 10 mins then skip the remaining part of
                    # tests if domain not resume successfully
                    try:
                        vm.wait_for_login(timeout=600)
                    except Exception, e:
                        raise error.TestWarn("Skip remaining test steps as domain"
                                             " not resume in 10 mins: %s" % e)
                    # For hotplug/unplug vcpu without '--config flag, after
                    # suspend domain to disk(shut off) and re-start it, the
                    # current live vcpu number will recover to orinial value
                    if vm_operation == 's4':
                        if setvcpu_option.count("--config"):
                            expect_vcpu_num[3] = vcpu_plug_num
                            expect_vcpu_num[4] = vcpu_plug_num
                        elif setvcpu_option.count("--guest"):
                            expect_vcpu_num[4] = vcpu_plug_num
                        else:
                            expect_vcpu_num[3] = vcpu_current_num
                            expect_vcpu_num[4] = vcpu_current_num
                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin,
                                          setvcpu_option)

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
                    logging.info("Hotplug vcpu to the maximum count to make sure"
                                 " all these new plugged vcpus are hotunpluggable")
                    result = virsh.setvcpus(vm_name, vcpu_max_num, '--live',
                                            debug=True)
                    libvirt.check_exit_status(result)
                # Pin vcpu
                if pin_before_unplug:
                    result = virsh.vcpupin(vm_name, pin_vcpu, pin_cpu_list,
                                           ignore_status=True, debug=True)
                    libvirt.check_exit_status(result)
                    # As the vcpu will unplug later, so set expect_vcpupin to empty
                    expect_vcpupin = {}

                result = virsh.setvcpus(vm_name, vcpu_unplug_num, setvcpu_option,
                                        readonly=setvcpu_readonly,
                                        ignore_status=True, debug=True)

                try:
                    check_setvcpus_result(result, status_error)
                except error.TestNAError:
                    raise error.TestWarn("Skip unplug vcpu as it is not supported")

                if setvcpu_option == "--config":
                    expect_vcpu_num[2] = vcpu_unplug_num
                elif setvcpu_option == "--guest":
                    # vcpuset '--guest' only affect vcpu number in guest
                    expect_vcpu_num[4] = vcpu_unplug_num
                else:
                    expect_vcpu_num[3] = vcpu_unplug_num
                    expect_vcpu_num[4] = vcpu_unplug_num

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
                    check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin,
                                      setvcpu_option)

                    # Control domain
                    manipulate_domain(vm_name, vm_operation)

                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin,
                                          setvcpu_option)

                    # Recover domain
                    manipulate_domain(vm_name, vm_operation, recover=True)

                    # Resume domain from S4 status may takes long time(QEMU bug),
                    # here we wait for 10 mins then skip the remaining part of
                    # tests if domain not resume successfully
                    try:
                        vm.wait_for_login(timeout=600)
                    except Exception, e:
                        raise error.TestWarn("Skip remaining test steps as domain"
                                             " not resume in 10 mins: %s" % e)
                    # For hotplug/unplug vcpu without '--config flag, after
                    # suspend domain to disk(shut off) and re-start it, the
                    # current live vcpu number will recover to orinial value
                    if vm_operation == 's4':
                        if setvcpu_option.count("--config"):
                            expect_vcpu_num[3] = vcpu_unplug_num
                            expect_vcpu_num[4] = vcpu_unplug_num
                        elif setvcpu_option.count("--guest"):
                            expect_vcpu_num[4] = vcpu_unplug_num
                        else:
                            expect_vcpu_num[3] = vcpu_current_num
                            expect_vcpu_num[4] = vcpu_current_num
                    if vm_operation != "null":
                        # Check vcpu number and related commands
                        check_vcpu_number(vm, expect_vcpu_num, expect_vcpupin,
                                          setvcpu_option)
    # Recover env
    finally:
        if need_mkswap:
            vm.cleanup_swap()
        if with_stress:
            bt.join(ignore_status=True)
        vm.destroy()
        backup_xml.sync()
