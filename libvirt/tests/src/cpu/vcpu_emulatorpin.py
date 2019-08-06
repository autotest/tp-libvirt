import re
import logging
import random

from avocado.utils import cpu

from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest import virsh
from virttest.utils_test.libvirt import cpus_parser


def run(test, params, env):
    """
    Start VM with emulatorpin and vcpu XML and check/modify emulatorpin info

    The test scenarios are as follows:

    Positive XML [*(positive modify; negative modify)]* [*(live modify; config modify)] scenarios:
    1) Configure VM
       1.1) Configure VM "without*with emulatorpin"
       1.2) Configure VM "with vcpu [placement:auto;static;none] * [cpuset:none:existent]"
    2) Start VM (successfully)
    3) Check emulatorpin info
    4) modify emulatorpin info
       4.1) Live modify emulatorpin info in a legal way
       4.2) Live modify emulatorpin info in an illegal way
       4.3) Config modify emulatorpin info in a legal way
       4.4) Config modify emulatorpin info in an illegal way
    5) Check emulatorpin info

    Negative XML [*(positive modify; negative modify)]* [*(live modify; config modify)] scenarios:
    1) Configure VM
       1.1) Configure VM "with illegal emulatorpin"
       1.2) Configure VM "with vcpu [placement:auto;static;none] * [cpuset:none:existent]"
    2) Start VM (failed)

    """

    def generate_emulatorpin_xml(emulatorpin_cpuset):
        """
        Func to generate emulatorpin XML according to value of emulatorpin_cpuset
        """
        cputune = vm_xml.VMCPUTuneXML()
        cputune.emulatorpin = emulatorpin_cpuset
        vmxml.cputune = cputune
        logging.debug(vmxml.cputune)

    def get_expected_emulatorpin_cpuset_s1(emulatorpin_cpuset):
        """
        Func to translate the right format of expected cpuset; "X,Y" ( Only 2 CPUS )
        """
        tmp = emulatorpin_cpuset.split(",")
        for i in range(len(tmp)):
            tmp[i] = int(tmp[i])
        expected_emulatorpin_cpuset = sorted(tmp)
        logging.debug("The expected emulatorpin cpuset info should be: %s", expected_emulatorpin_cpuset)
        return expected_emulatorpin_cpuset

    def get_emulatorpin_cpuset_from_cmd_live_s1():
        """
        Func to check actual emulator cpuset through virsh cmd --live; "X,Y" or "X-Y" ( Only 2 CPUS )
        """
        cmd_result = virsh.emulatorpin(vm_name, options="live", debug=True)
        cmd_result_cpulist = cmd_result.stdout.strip().split(": ")[2]

        if len(re.compile(r'\-', re.VERBOSE).findall(cmd_result_cpulist)) == 1:
            tmp = cmd_result_cpulist.split(re.compile(r'\-', re.VERBOSE).findall(cmd_result_cpulist)[0])
        if len(re.compile(r'\,', re.VERBOSE).findall(cmd_result_cpulist)) == 1:
            tmp = cmd_result_cpulist.split(re.compile(r'\,', re.VERBOSE).findall(cmd_result_cpulist)[0])

        for i in range(len(tmp)):
            tmp[i] = int(tmp[i])

        actual_emulatorpin_cpuset_cmd = sorted(tmp)
        logging.debug("The emulatorpin cpuset info in virsh emulatorpin is %s", actual_emulatorpin_cpuset_cmd)
        return actual_emulatorpin_cpuset_cmd

    def get_emulatorpin_cpuset_from_dumpxml_active_s1():
        """
        Func to check actual emulator cpuset through dumxml active VM; "X,Y" or "X-Y" ( Only 2 CPUS )
        """
        actual_emulatorpin_xml = vm_xml.VMXML().new_from_dumpxml(vm_name).cputune.emulatorpin
        actual_emulatorpin_cpuset_xml = sorted(cpus_parser(actual_emulatorpin_xml))
        logging.debug("The emulatorpin cpuset info in virsh dumpxml is %s", actual_emulatorpin_cpuset_xml)
        return actual_emulatorpin_cpuset_xml

    # Run test case and obtain paras from cfg file
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vcpu_placement = params.get("vcpu_placement_op", "")
    vcpu_cpuset_op = params.get("vcpu_cpuset_op", "")
    emulatorpin_cpuset_op = params.get("emulatorpin_cpuset_op", "")
    start_err = params.get("start_err", "")
    modify_err = params.get("modify_err", "")
    modify_emulatorpin_op = params.get("modify_emulatorpin_op", "")

    # Backup original vm
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    # Delate CPUtune/Numatune related XML
    try:
        del vmxml.numa_memory
        del vmxml.numa_memnode
        del vmxml.cputune
    except Exception:
        pass
    vmxml.sync()

    # Obtain host CPUs count
    host_cpus = cpu.online_cpus_count()
    cpu_max = int(host_cpus) - 1
    logging.debug("There are %s CPUs on physical host", host_cpus)
    # As 1 manual case needed to be ran on physical host with cpus number is multiple of 8
    if cpu_max < 7:
        test.cancel("Only %s cpus on host is not enough." % host_cpus)

    # Configure vcpu_placement for VM dumpxml
    logging.debug("vcpu_placement: %s", vcpu_placement)
    vmxml.placement = vcpu_placement

    # Configure vcpu_cpuset for VM dumpxml
    vcpu_cpuset = ""
    if vcpu_cpuset_op is "none":
        pass
    if vcpu_cpuset_op == "yes":
        vcpu_cpuset = "0-%s" % (cpu_max - 1)
        vmxml.cpuset = vcpu_cpuset
    logging.debug("vcpu_cpuset is: %s", vcpu_cpuset)

    # Configure emulatorpin_cpuset for VM dumpxml
    emulatorpin_cpuset = ""
    if emulatorpin_cpuset_op == "none":
        pass
    if emulatorpin_cpuset_op == "legal":
        emulatorpin_cpuset = ','.join(random.sample(list(map(str, cpu.cpu_online_list())), 2))
        logging.debug("emulatorpin_cpuset value is : %s", emulatorpin_cpuset)
        generate_emulatorpin_xml(emulatorpin_cpuset)
    if emulatorpin_cpuset_op == "illegal":
        emulatorpin_cpuset = str(cpu_max + 1)
        logging.debug("emulatorpin_cpuset value is : %s", emulatorpin_cpuset)
        generate_emulatorpin_xml(emulatorpin_cpuset)

    vmxml.sync()
    logging.debug(virsh.dumpxml(vm_name))

    try:
        # Define VM
        result_define = virsh.define(vmxml.xml, debug=True)
        libvirt.check_exit_status(result_define)

        # Test negative scenarios with illegal emulatorpincpuset
        if start_err == "yes":
            # Start VM will fail
            result_start = virsh.start(vm_name, debug=True)
            libvirt.check_result(result_start, "Failed to start")

        # Test positive scenarios with legal/none emulatorpincpuset
        else:
            # Start VM will succeed
            result_start = virsh.start(vm_name, debug=True)
            libvirt.check_exit_status(result_start)
            session = vm.wait_for_login(timeout=180)

            # Check whether emulatorpin info is expected when emulatorpin_cpuset_legal
            # In this condition; emulatorpin_cpuset will be decided by <emulatorpin cpuset="">
            # Expected_emulatorpin should be "X,Y" format
            if emulatorpin_cpuset_op == "legal":
                if get_expected_emulatorpin_cpuset_s1(emulatorpin_cpuset) == get_emulatorpin_cpuset_from_cmd_live_s1() == get_emulatorpin_cpuset_from_dumpxml_active_s1():
                    logging.debug("The emulatorpin cpuset info in virsh emulatorpin cmd and in active vm dumpxml is expected.")
                else:
                    test.fail("The emulatorpin cpuset info is not right.")

            # Check whether emulatorpin info is expected when emulatorpin_cpuset_none and vcpu_placement_static and vcpu_cpuset_yes
            # In this condition, emulatorpin_cpuset will be decided by <vcpu cpuset="">
            # Expected_emulatorpin should be "0-(cpu_max-1)"
            if emulatorpin_cpuset_op == "none" and vcpu_placement == "static" and vcpu_cpuset_op == "yes":
                logging.debug("The expected emulatorpin cpuset info should be: %s", vcpu_cpuset)
                cmd_result = virsh.emulatorpin(vm_name, options="live", debug=True)
                actual_emulatorpin_cpuset_cmd = cmd_result.stdout.strip().split(": ")[2]
                logging.debug("The emulatorpin cpuset info in virsh emulatorpin is %s", actual_emulatorpin_cpuset_cmd)
                if vcpu_cpuset == actual_emulatorpin_cpuset_cmd:
                    logging.debug("The emulatorpin cpuset info in virsh emulatorpin cmd is expected.")
                else:
                    test.fail("The emulatorpin cpuset info is not right.")

            # Check whether emulatorpin info is expected when emulatorpin_cpuset_none and vcpu_placement_static and vcpu_cpuset_none
            # In this condition, emulatorpin_cpuset will be decided by "all host cpus"
            # Expected_emulatorpin should be "0-(cpu_max)"
            if emulatorpin_cpuset_op == "none" and vcpu_placement == "static" and vcpu_cpuset_op == "none":
                expected_emulatorpin_cpuset = "0-%s" % (cpu_max)
                logging.debug("The expected emulatorpin cpuset info should be: %s", expected_emulatorpin_cpuset)
                cmd_result = virsh.emulatorpin(vm_name, options="live", debug=True)
                actual_emulatorpin_cpuset_cmd = cmd_result.stdout.strip().split(": ")[2]
                logging.debug("The emulatorpin cpuset info in virsh emulatorpin is %s", actual_emulatorpin_cpuset_cmd)
                if expected_emulatorpin_cpuset == actual_emulatorpin_cpuset_cmd:
                    logging.debug("The emulatorpin cpuset info in virsh emulatorpin cmd is expected.")
                else:
                    test.fail("The emulatorpin cpuset info is not right.")

            # Check whether emulatorpin info is expected when emulatorpin_cpuset_none and vcpu_placement_auto
            # In this condition, emulatorpin will be decided by "numad"
            # Expected_emulatorpin should be the sub of "0-(cpu_max)" but not sure the exact value
            if emulatorpin_cpuset_op == "none" and vcpu_placement == "auto":
                cmd_result = virsh.emulatorpin(vm_name, options="live", debug=True)
                actual_emulatorpin_cpuset_cmd = cmd_result.stdout.strip().split(": ")[2]
                logging.debug("The emulatorpin cpuset info in virsh emulatorpin is %s", actual_emulatorpin_cpuset_cmd)
                if actual_emulatorpin_cpuset_cmd is not None:
                    logging.debug("The emulatorpin cpuset info in virsh emulatorpin is not none.")
                else:
                    test.fail("The emulatorpin cpuset info is not right.")

            # Test negative scenarios of modifying emulatorpin
            if modify_err == "yes":
                # Modify emulatorpin by virsh cmd with --live and cpulist="cpu_max + 1"
                if modify_emulatorpin_op == "live_illegal":
                    logging.debug("Modifying emulatorpin with --live and illegal cpulist = cpu_max + 1")
                    result_modify = virsh.emulatorpin(vm_name, cpulist=str(cpu_max + 1), options='live', debug=True)
                    libvirt.check_result(result_modify, "exceed the maxcpu")

                # Modify emulatorpin by virsh cmd with --config and cpulist="cpu_max + 1"
                else:
                    logging.debug("Modifying emulatorpin with --config and illegal cpulist = cpu_max + 1")
                    result_modify = virsh.emulatorpin(vm_name, cpulist=str(cpu_max + 1), options='config', debug=True)
                    libvirt.check_result(result_modify, "exceed the maxcpu")

            # Test positive scenarios of modifying emulatorpin
            else:
                # Modify emulatorpin by virsh cmd with --live and cpuset="0"
                if modify_emulatorpin_op == "live_legal":
                    logging.debug("Modifying emulatorpin with --live and legal cpulist = 0")
                    result_modify = virsh.emulatorpin(vm_name, cpulist="0", options='live', debug=True)
                    result_check = virsh.emulatorpin(vm_name, options='live', debug=True)
                    cmd_result_cpulist = result_check.stdout.strip().split(": ")[2]
                    logging.debug("The emulatorpin cpulist in virsh emulatorpin is: %s", cmd_result_cpulist)
                    xml_check = cpus_parser(vm_xml.VMXML().new_from_dumpxml(vm_name).cputune.emulatorpin)
                    xml_result_cpulist = str(xml_check[0])
                    logging.debug("The emulatorpin cpulist in virsh dumpxml is: %s", xml_result_cpulist)
                    if cmd_result_cpulist == xml_result_cpulist == "0":
                        logging.debug("Modifying emulatorpin cpulist succeed.")
                    else:
                        test.fail("The emulatorpin cpuset info is not right after modifying.")

                # Modify emulatorpin by virsh cmd with --config and cpuset="0"
                else:
                    logging.debug("Modifying emulatorpin with --config and legal cpulist = 0")
                    result_modify = virsh.emulatorpin(vm_name, cpulist="0", options='config', debug=True)
                    result_check = virsh.emulatorpin(vm_name, options='config', debug=True)
                    cmd_result_cpulist = result_check.stdout.strip().split(": ")[2]
                    logging.debug("The emulatorpin cpulist in virsh emulatorpin --config is: %s", cmd_result_cpulist)
                    xml_check = cpus_parser(vm_xml.VMXML().new_from_dumpxml(vm_name, "--inactive").cputune.emulatorpin)
                    xml_result_cpulist = str(xml_check[0])
                    logging.debug("The emulatorpin cpulist in virsh dumpxml --inactive is: %s", xml_result_cpulist)
                    if cmd_result_cpulist == xml_result_cpulist == "0":
                        logging.debug("Modifying emulatorpin cpulist with --config succeed.")
                    else:
                        test.fail("The emulatorpin cpuset info is not right after modifying --config.")

    # Recover VM
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
