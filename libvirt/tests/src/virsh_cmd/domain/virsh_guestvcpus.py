import logging as log

from avocado.utils import cpu

from virttest import virsh
from virttest import cpu as cpuutil
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh guestvcpus

    The command query or modify state of vcpu in the vm
    1. Prepare test environment, start vm with guest agent
    2. Perform virsh guestvcpus query/enable/disable operation
    3. Check the vcpu number by virsh command via guest agent
    4. Check the vcpu number within the guest
    5. In combine tests, repeat steps 2-4
    6. Recover test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vcpus_num = int(params.get("vcpus_num", "20"))
    vcpus_placement = params.get("vcpus_placement", "static")
    max_test_combine = params.get("max_test_combine", "")
    option = params.get("option", "")
    combine = params.get("combine", "")
    status_error = params.get("status_error", "no")
    vcpus_list = ""

    # Back up domain XML
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_bakup = vmxml.copy()

    # Max test: set vcpus_num to the host online cpu number
    if max_test_combine == "yes":
        vcpus_num = cpu.online_count()
        logging.debug("Host online CPU number: %s", str(vcpus_num))

    try:
        # Modify vm with static vcpus
        if vm.is_alive():
            vm.destroy()
        vmxml.placement = vcpus_placement
        vmxml.set_vm_vcpus(vm_name, vcpus_num, vcpus_num, topology_correction=True)
        logging.debug("Define guest with '%s' vcpus", str(vcpus_num))

        # Start guest agent in vm
        vm.prepare_guest_agent()

        # Normal test: disable/enable guest vcpus
        if option and status_error == "no":
            for vcpu in range(1, vcpus_num):
                virsh.guestvcpus(vm_name, str(vcpu), option, debug=True)
            check_cpu_count(test, params, env, vcpus_num, option)

        # Combine: --disable 1-max then --enable
        if (max_test_combine == "yes" or combine == "yes") and status_error == "no":
            vcpus_list = '1' + '-' + str(vcpus_num - 1)
            option = "--disable"
            virsh.guestvcpus(vm_name, vcpus_list, option, debug=True)
            check_cpu_count(test, params, env, vcpus_num, option)

            # Max test: --enable 1-max (no change to vcpus_list)
            # Normal test: --enable 1
            if combine == "yes":
                vcpus_list = '1'

            option = "--enable"
            virsh.guestvcpus(vm_name, vcpus_list, option, debug=True)
            check_cpu_count(test, params, env, vcpus_num, option)

    finally:
        # Recover VM
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_bakup.sync()


def check_cpu_count(test, params, env, vcpus_num, option=""):
    """
    Makes any changes necessary for the error test and then
    runs the vcpu checks specified in steps 3 and 4 of run()

    3. Check the vcpu number by virsh command via guest agent
    4. Check the vcpu number within the guest
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    combine = params.get("combine", "")
    invalid_domain = params.get("invalid_domain", "")
    domain_name = params.get("domain_name", "")
    invalid_cpulist = params.get("invalid_cpulist", "")
    status_error = params.get("status_error", "no")
    error_msg = eval(params.get('error_msg', '[]'))
    vcpus_list = ""
    offline_vcpus = ""

    # Error test: invalid_domain
    if invalid_domain == "yes":
        vm_name = domain_name
    # Error test: invalid_cpulist
    if invalid_cpulist == "yes":
        if option == "--enable":
            vcpus_list = str(vcpus_num)
        else:
            vcpus_list = '0' + '-' + str(vcpus_num - 1)
        ret = virsh.guestvcpus(vm_name, vcpus_list, option)
    else:
        # Query guest vcpus
        ret = virsh.guestvcpus(vm_name)
        output = ret.stdout.strip()

    # Check test results
    if status_error == "yes":
        libvirt.check_result(ret, error_msg)
    else:
        # Check the test result of query
        ret_output = dict([item.strip() for item in line.split(":")]
                          for line in output.split("\n"))
        if combine == "yes" and option == "--enable":
            online_vcpus = '0-1'
        elif option == "--disable":
            online_vcpus = '0'
        else:
            # either normal --enable test or max test on the --enable step
            online_vcpus = '0' + '-' + str(vcpus_num - 1)

        if ret_output["online"] != online_vcpus:
            test.fail("Expected online vcpus to be %s, "
                      "but found %s." % (online_vcpus, ret_output["online"]))

        # Check the vcpu number within the guest
        session = vm.wait_for_login()
        vm_cpu_info = cpuutil.get_cpu_info(session)
        session.close()

        if combine == "yes" and option == "--enable":
            online_vcpus = '0,1'
        elif option == "--disable":
            online_vcpus = '0'
            offline_vcpus = '1' + '-' + str(vcpus_num - 1)
        else:
            # either normal --enable test or max test on the --enable step
            online_vcpus = '0' + '-' + str(vcpus_num - 1)

        if offline_vcpus:
            if (vm_cpu_info["Off-line CPU(s) list"] != offline_vcpus or
                    vm_cpu_info["On-line CPU(s) list"] != online_vcpus):
                test.fail("CPUs in vm is different from"
                          " the `virsh guestvcpus %s` command." % option)
        elif vm_cpu_info["On-line CPU(s) list"] != online_vcpus:
            test.fail("On-line CPUs in vm is different"
                      " from the `virsh guestvcpus %s` command." % option)
        logging.debug("lscpu in vm '%s' is: \n '%s'",
                      vm_name, vm_cpu_info)
