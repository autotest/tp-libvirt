import os
import logging

from virttest import virsh
from virttest import libvirt_xml
from virttest.utils_test import libvirt


def reset_domain(vm, vm_state, maxvcpu, curvcpu, sockets,
                 cores, threads,
                 set_agent_channel, install_agent, start_agent):
    """
    Set domain vcpu number to 4 and current vcpu as 1

    :param vm: the vm object
    :param vm_state: the given vm state string "shut off" or "running"
    :param maxvcpu: maximumvcpu value
    :param curvcpu: currentvcpu value
    :param sockets: socketscount value
    :param cores: corescount value
    :param threads: threadcount value
    :param set_agent_channel: if to set agent channel on xml
    :param install_agent: if to try install agent in machine
    :param start_agent: if to start agent service in machine
    :return:
    """

    if vm.is_alive():
        vm.destroy()
    vm_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    if set_agent_channel:
        logging.debug("Attempting to set guest agent channel")
        vm_xml.set_agent_channel()
        vm_xml.sync()
    vm_xml.set_vm_vcpus(vm.name, maxvcpu, curvcpu, sockets, cores, threads)
    if not vm_state == "shut off":
        vm.start()
        if install_agent:
            vm.prepare_guest_agent(prepare_xml=False,
                                   channel=False, start=False)
        if start_agent:
            vm.set_state_guest_agent(start=True)


def del_topology(vm, vm_state):
    """
    Delete the topology definition from xml if present

    :param vm: vm object
    :param vm_state: the given vm state string "shut off" or "running"
    """
    vm_xml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    topology = vm_xml.get_cpu_topology()
    if topology:
        del vm_xml.cpu
        vm_xml.sync()
    if not vm_state == "shut off":
        vm.destroy()
        vm.start()
        vm.wait_for_login()


def chk_output_running(output, expect_out, options, test):
    """
    Check vcpucount when domain is running

    :param output: the output of vcpucount command
    :param expect_out: the list of expected result
    :parma options: the vcpucount command options string
    """
    if options == "":
        out = output.split('\n')
        for i in range(4):
            if int(out[i].split()[-1]) != expect_out[i]:
                test.fail("Output is not expected")
    elif "--config" in options:
        if "--active" in options:
            if int(output) != expect_out[2]:
                test.fail("Output is not expected")
        elif "--maximum" in options:
            if int(output) != expect_out[0]:
                test.fail("Output is not expected")
        elif "--current" in options:
            pass
        elif "--live" in options:
            pass
        elif options == "--config":
            pass
        else:
            test.fail("Options %s should failed" % options)
    elif "--live" or "--current" in options:
        if "--active" in options:
            if int(output) != expect_out[3]:
                test.fail("Output is not expected")
        elif "--maximum" in options:
            if int(output) != expect_out[1]:
                test.fail("Output is not expected")
        elif "--guest" in options:
            pass
        elif options == "--live" or options == "--current":
            pass
        else:
            test.fail("Options %s should failed" % options)
    elif options == "--active":
        pass
    elif options == "--guest":
        if int(output) != expect_out[4]:
            test.fail("Output is not expected")
    else:
        test.fail("Options %s should failed" % options)


def chk_output_shutoff(output, expect_out, options, test):
    """
    Check vcpucount when domain is shut off

    :param output: the output of vcpucount command
    :param expect_out: the list of expected result
    :param options: the vcpucount command options string
    """
    if options == "":
        out = output.split('\n')
        for i in range(2):
            if int(out[i].split()[-1]) != expect_out[i]:
                test.fail("Output is not expected")
    elif "--config" or "--current" in options:
        if "--active" in options:
            if int(output) != expect_out[1]:
                test.fail("Output is not expected")
        elif "--maximum" in options:
            if int(output) != expect_out[0]:
                test.fail("Output is not expected")
        elif options == "--config" or options == "--current":
            pass
        else:
            test.fail("Options %s should failed" % options)
    else:
        test.fail("Options %s should failed" % options)


def reset_env(vm_name, xml_file):
    virsh.destroy(vm_name)
    virsh.undefine(vm_name)
    virsh.define(xml_file)
    if os.path.exists(xml_file):
        os.remove(xml_file)


def run(test, params, env):
    """
    Test the command virsh vcpucount

    (1) Iterate perform setvcpus operation with four valid options.
    (2) Iterate call virsh vcpucount with given options.
    (3) Check whether the virsh vcpucount works as expected.
    (4) Recover test environment.

    The test works for domain state as "shut off" or "running", it check
    vcpucount result after vcpu hotplug using setvcpus.

    For setvcpus, include four valid options:
      --config
      --config --maximum
      --live
      --guest

    For vcpucount options, restrict up to 2 options together, upstream libvirt
    support more options combinations now (e.g. 3 options together or single
    --maximum option), for backward support, only following options are
    checked:
      None
      --config --active
      --config --maximum
      --live --active
      --live --maximum
      --current --active
      --current --maximum
      --guest
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    xml_file = params.get("vcpucount_xml_file", "vm.xml")
    virsh.dumpxml(vm_name, extra="--inactive", to_file=xml_file)
    pre_vm_state = params.get("vcpucount_pre_vm_state")
    options = params.get("vcpucount_options")
    status_error = "yes" == params.get("status_error", "no")
    maxvcpu = int(params.get("vcpucount_maxvcpu", "4"))
    curvcpu = int(params.get("vcpucount_current", "1"))
    sockets = int(params.get("sockets", "1"))
    cores = int(params.get("cores", "4"))
    threads = int(params.get("threads", "1"))
    expect_msg = params.get("vcpucount_err_msg")
    livevcpu = curvcpu + threads
    set_option = ["--config", "--config --maximum", "--live", "--guest"]

    # Early death
    # 1.1 More than two options not supported
    if len(options.split()) > 2:
        test.cancel("Options exceeds 2 is not supported")

    # 1.2 Check for all options
    option_list = options.split(" ")
    if not status_error:
        for item in option_list:
            if virsh.has_command_help_match("vcpucount", item) is None:
                test.cancel("The current libvirt version doesn't support "
                            "'%s' option" % item)
    # 1.3 Check for vcpu values
    if (sockets and cores and threads):
        if int(maxvcpu) != int(sockets) * int(cores) * int(threads):
            test.cancel("Invalid topology definition, VM will not start")

    try:
        # Prepare domain
        set_agent_channel = "--guest" in options
        install_guest_agent = set_agent_channel
        start_guest_agent = set_agent_channel
        reset_domain(vm, pre_vm_state, maxvcpu, curvcpu,
                     sockets, cores, threads,
                     set_agent_channel, install_guest_agent, start_guest_agent)
        install_guest_agent = False

        # Perform guest vcpu hotplug
        for idx in range(len(set_option)):
            # Remove topology for maximum config
            # https://bugzilla.redhat.com/show_bug.cgi?id=1426220
            if idx == 1:
                del_topology(vm, pre_vm_state)
            # Hotplug domain vcpu
            result = virsh.setvcpus(vm_name, livevcpu, set_option[idx],
                                    ignore_status=True, debug=True)
            setvcpus_status = result.exit_status

            # Call virsh vcpucount with option
            result = virsh.vcpucount(vm_name, options, ignore_status=True,
                                     debug=True)
            output = result.stdout.strip()
            vcpucount_status = result.exit_status

            if "--guest" in options:
                if result.stderr.count("doesn't support option") or \
                   result.stderr.count("command guest-get-vcpus"
                                       " has not been found"):
                    test.fail("Option %s is not supported" % options)

            # Reset domain
            reset_domain(vm, pre_vm_state, maxvcpu, curvcpu,
                         sockets, cores, threads,
                         set_agent_channel, install_guest_agent,
                         start_guest_agent)

            # Check result
            if status_error:
                if vcpucount_status == 0:
                    test.fail("Run successfully with wrong command!")
                else:
                    logging.info("Run failed as expected")
                    if expect_msg:
                        libvirt.check_result(result, expect_msg.split(';'))
            else:
                if vcpucount_status != 0:
                    test.fail("Run command failed with options %s" %
                              options)
                elif setvcpus_status == 0:
                    if pre_vm_state == "shut off":
                        if idx == 0:
                            expect_out = [maxvcpu, livevcpu]
                            chk_output_shutoff(output, expect_out,
                                               options, test)
                        elif idx == 1:
                            expect_out = [livevcpu, curvcpu]
                            chk_output_shutoff(output, expect_out,
                                               options, test)
                        else:
                            test.fail("setvcpus should failed")
                    else:
                        if idx == 0:
                            expect_out = [maxvcpu, maxvcpu, livevcpu,
                                          curvcpu, curvcpu]
                            chk_output_running(output, expect_out,
                                               options, test)
                        elif idx == 1:
                            expect_out = [livevcpu, maxvcpu, curvcpu,
                                          curvcpu, curvcpu]
                            chk_output_running(output, expect_out,
                                               options, test)
                        elif idx == 2:
                            expect_out = [maxvcpu, maxvcpu, curvcpu,
                                          livevcpu, livevcpu]
                            chk_output_running(output, expect_out,
                                               options, test)
                        else:
                            expect_out = [maxvcpu, maxvcpu, curvcpu,
                                          curvcpu, livevcpu]
                            chk_output_running(output, expect_out,
                                               options, test)
                else:
                    if pre_vm_state == "shut off":
                        expect_out = [maxvcpu, curvcpu]
                        chk_output_shutoff(output, expect_out,
                                           options, test)
                    else:
                        expect_out = [
                            maxvcpu, maxvcpu, curvcpu, curvcpu, curvcpu]
                        chk_output_running(output, expect_out,
                                           options, test)
    finally:
        # Recover env
        reset_env(vm_name, xml_file)
