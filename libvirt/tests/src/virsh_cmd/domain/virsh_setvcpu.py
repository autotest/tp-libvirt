import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest import cpu


def run(test, params, env):
    """
    Test command: virsh setvcpu.

    The command can change the number of virtual CPUs in the guest domain.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh setvcpu operation.
    3. Check in the following places
    vcpuinfo
    vcpupin
    vcpucount
    inside guest
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pre_vm_state = params.get("setvcpu_pre_vm_state")
    options = params.get("setvcpu_options")
    vm_ref = params.get("setvcpu_vm_ref", "name")
    current_vcpu = int(params.get("setvcpu_current", "2"))
    vcpu_list_format = params.get("setvcpu_list_format", "comma")
    iteration = int(params.get("setvcpu_iteration", 1))
    invalid_vcpulist = params.get("invalid_vcpulist", "")
    convert_err = "Can't convert {0} to integer type"
    unsupport_str = params.get("unsupport_str", "")
    try:
        current_vcpu = int(params.get("setvcpu_current", "1"))
    except ValueError:
        test.cancel(convert_err.format(current_vcpu))

    try:
        max_vcpu = int(params.get("setvcpu_max", "4"))
    except ValueError:
        test.cancel(convert_err.format(max_vcpu))

    extra_param = params.get("setvcpu_extra_param")
    status_error = params.get("status_error")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
    set_topology = "yes" == params.get("set_topology", "no")
    sockets = int(params.get("topology_sockets", '1'))
    cores = int(params.get("topology_cores", '4'))
    threads = int(params.get("topology_threads", '1'))
    start_vm_after_set = "yes" == params.get("start_vm_after_set", "no")
    iteration = int(params.get("hotplug_iteration", "1"))

    # Early death
    option_list = options.split(" ")
    for item in option_list:
        if virsh.has_command_help_match("setvcpu", item) is None:
            test.cancel("The current libvirt "
                        "version doesn't support "
                        "'%s' option" % item)

    # Calculate count options
    vcpu_list = []
    # Init expect vcpu count values
    exp_vcpu = {'max_config': max_vcpu, 'max_live': max_vcpu,
                'cur_config': current_vcpu, 'cur_live': current_vcpu,
                'guest_live': current_vcpu}

    def set_expected(vm, options, enabled=True):
        """
        Set the expected vcpu numbers

        :param vm: vm object
        :param options: setvcpu options
        :param enabled: True or False base on enable or disable
        """
        if enabled:
            if ("config" in options) or ("current" in options and vm.is_dead()):
                exp_vcpu['cur_config'] += threads
            elif ("live" in options) or ("current" in options and vm.is_alive()):
                exp_vcpu['cur_live'] += threads
                exp_vcpu['guest_live'] += threads
            else:
                # when none given it defaults to live
                exp_vcpu['cur_live'] += threads
                exp_vcpu['guest_live'] += threads
        else:
            if ("--config" in options) or ("--current" in options and vm.is_dead()):
                exp_vcpu['cur_config'] -= threads
            elif ("--live" in options) or ("--current" in options and vm.is_alive()):
                exp_vcpu['cur_live'] -= threads
                exp_vcpu['guest_live'] -= threads
            else:
                # when none given it defaults to live
                exp_vcpu['cur_live'] -= threads
                exp_vcpu['guest_live'] -= threads

    if threads > 1:
        start_vcpu = current_vcpu
    else:
        start_vcpu = current_vcpu + 1

    if 'hypen' in vcpu_list_format:
        for vcpu_start in range(start_vcpu, max_vcpu, threads):
            if int(threads) > 1:
                lst = "%s-%s" % (
                    str(vcpu_start), str(vcpu_start + threads - 1))
            else:
                lst = vcpu_start
            vcpu_list.append(lst)
    elif 'comma' in vcpu_list_format:
        for vcpu_start in range(start_vcpu, max_vcpu, threads):
            if int(threads) > 1:
                lst = ''
                for idx in range(vcpu_start, vcpu_start + threads):
                    lst += "%s," % idx
            else:
                lst = vcpu_start
            vcpu_list.append(lst.strip(','))
    else:
        pass

    # Early death
    if vm_ref == "remote" and (remote_ip.count("EXAMPLE.COM") or
                               local_ip.count("EXAMPLE.COM")):
        test.cancel("remote/local ip parameters not set.")

    # Save original configuration
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    # Run test
    try:
        if vm.is_alive():
            vm.destroy()

        # Set cpu topology
        if set_topology:
            vmxml.set_vm_vcpus(vm.name, max_vcpu, current_vcpu,
                               sockets=sockets, cores=cores, threads=threads,
                               add_topology=True)

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        logging.debug("Pre-test xml is %s", vmxml.xmltreefile)
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()

        domid = vm.get_id()  # only valid for running
        domuuid = vm.get_uuid()

        if pre_vm_state == "paused":
            vm.pause()
        elif pre_vm_state == "shut off" and vm.is_alive():
            vm.destroy()

        setvcpu_exit_status = 0
        setvcpu_exit_stderr = ''
        # TODO: Run remote test,for future use
        if vm_ref == "remote":
            pass
        # Run local test
        else:
            if vm_ref == "name":
                dom_option = vm_name
            elif vm_ref == "id":
                dom_option = domid
                if params.get("setvcpu_hex_id") is not None:
                    dom_option = hex(int(domid))
                elif params.get("setvcpu_invalid_id") is not None:
                    dom_option = params.get("setvcpu_invalid_id")
            elif vm_ref == "uuid":
                dom_option = domuuid
                if params.get("setvcpu_invalid_uuid") is not None:
                    dom_option = params.get("setvcpu_invalid_uuid")
            else:
                dom_option = vm_ref
            for itr in range(iteration):
                if extra_param:
                    vcpu_list_option = "%s %s" % (vcpu_list[itr], extra_param)
                elif invalid_vcpulist != "":
                    vcpu_list_option = invalid_vcpulist
                else:
                    vcpu_list_option = vcpu_list[itr]
                if 'enable' in options:
                    status = virsh.setvcpu(
                        dom_option, vcpu_list_option, options,
                        ignore_status=True, debug=True)
                    # Preserve the first failure
                    if status.exit_status != 0:
                        setvcpu_exit_status = status.exit_status
                    # Accumulate the error strings
                    setvcpu_exit_stderr += "itr-%d-enable: %s\n" % (itr,
                                                                    status.stderr.strip())
                    set_expected(vm, options, True)
                elif 'disable' in options:
                    # disable needs a hotpluggable cpus, lets make sure we have
                    if status_error != "yes":
                        options_enable = options.replace("disable", "enable")
                        virsh.setvcpu(dom_option, vcpu_list_option,
                                      options_enable, ignore_status=False,
                                      debug=True)
                        # Adjust the expected vcpus
                        set_expected(vm, options, True)
                    status = virsh.setvcpu(
                        dom_option, vcpu_list_option, options,
                        ignore_status=True, debug=True)
                    unsupport_str = cpu.vcpuhotunplug_unsupport_str()
                    if unsupport_str and (unsupport_str in status.stderr):
                        test.cancel("Vcpu hotunplug is not supported in this host:"
                                    "\n%s" % status.stderr)
                    # Preserve the first failure
                    if status.exit_status != 0:
                        setvcpu_exit_status = status.exit_status
                    # Accumulate the error strings
                    setvcpu_exit_stderr += "itr-%d-disable: %s\n" % (itr,
                                                                     status.stderr.strip())
                    # Adjust the expected vcpus
                    set_expected(vm, options, False)
                # Handle error cases
                else:
                    status = virsh.setvcpu(dom_option, vcpu_list_option,
                                           options, ignore_status=True,
                                           debug=True)
                    # Preserve the first failure
                    if status.exit_status != 0:
                        setvcpu_exit_status = status.exit_status
                    # Accumulate the error strings
                    setvcpu_exit_stderr += "itr-%d-error: %s\n" % (itr,
                                                                   status.stderr.strip())
            # Start VM after set vcpu
            if start_vm_after_set:
                if "--enable" in options:
                    if "--config" in options or "--current" in options:
                        exp_vcpu['cur_live'] = exp_vcpu['cur_config']
                if "--disable" in options:
                    if "--config" in options or "--current" in options:
                        exp_vcpu['cur_live'] = exp_vcpu['cur_config']
                if vm.is_alive():
                    logging.debug("VM already started")
                else:
                    result = virsh.start(vm_name, ignore_status=True,
                                         debug=True)
                    libvirt.check_exit_status(result)

            # Lets validate the result in positive cases
            if status_error != "yes":
                result = cpu.check_vcpu_value(vm, exp_vcpu,
                                              option=options)
    finally:
        if pre_vm_state == "paused":
            virsh.resume(vm_name, ignore_status=True)
        orig_config_xml.sync()

    # check status_error
    if status_error == "yes":
        if setvcpu_exit_status == 0:
            test.fail("Run successfully with wrong command!")
    else:
        if setvcpu_exit_status != 0:
            # Otherwise, it seems we have a real error
            test.fail("Run failed with right command"
                      " stderr=%s" % setvcpu_exit_stderr)
        else:
            if not result:
                test.fail("Test Failed")
