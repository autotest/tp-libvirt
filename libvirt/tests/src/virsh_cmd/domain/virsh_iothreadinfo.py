import logging

from avocado.core import exceptions

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh iothreadinfo.

    The command can get the number of iothread.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh iothreadadd operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pre_vm_state = params.get("iothread_pre_vm_state")
    command = params.get("iothread_command", "iothread")
    options = params.get("iothread_options")
    vm_ref = params.get("iothread_vm_ref", "name")
    iothreads = params.get("iothreads", 4)
    iothread_id = params.get("iothread_id", "6")
    cpuset = params.get("cpuset", "1")
    status_error = "yes" == params.get("status_error")
    iothreadids = params.get("iothreadids")
    iothreadpins = params.get("iothreadpins")
    try:
        iothreads = int(iothreads)
    except ValueError:
        # 'iothreads' may not invalid number in negative tests
        logging.debug("Can't convert %s to integer type", iothreads)

    # Save original configuration
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        if vm.is_alive():
            vm.destroy()

        option_list = options.split(" ")
        for item in option_list:
            if virsh.has_command_help_match(command, item) is None:
                raise exceptions.TestSkipError("The current libvirt version"
                                               " doesn't support '%s' option"
                                               % item)
        # Set iothreads first
        if iothreadids:
            ids_xml = vm_xml.VMIothreadidsXML()
            ids_xml.iothread = iothreadids.split()
            vmxml.iothreadids = ids_xml
        if iothreadpins:
            cputune_xml = vm_xml.VMCPUTuneXML()
            io_pins = []
            for pins in iothreadpins.split():
                thread, cpu = pins.split(':')
                io_pins.append({"iothread": thread,
                                "cpuset": cpu})
            cputune_xml.iothreadpins = io_pins
            vmxml.cputune = cputune_xml
        vmxml.iothreads = iothreads
        logging.debug("Pre-test xml is %s", vmxml)
        vmxml.sync()

        # Restart, unless that's not our test
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()

        domid = vm.get_id()  # only valid for running
        domuuid = vm.get_uuid()

        if pre_vm_state == "shut off" and vm.is_alive():
            vm.destroy()

        # Run test
        if vm_ref == "name":
            dom_option = vm_name
        elif vm_ref == "id":
            dom_option = domid
        elif vm_ref == "uuid":
            dom_option = domuuid
        else:
            dom_option = vm_ref

        ret = virsh.iothreadinfo(dom_option, options,
                                 ignore_status=True, debug=True)
        libvirt.check_exit_status(ret, status_error)

        if not status_error:
            # Check iothreadinfo by virsh command
            iothread_info = libvirt.get_iothreadsinfo(dom_option, options)
            logging.debug("iothreadinfo: %s", iothread_info)
            for x in iothreadids.split():
                if x not in iothread_info:
                    raise exceptions.TestFail("Failed to get iothreadinfo %s", x)

    finally:
        # Cleanup
        if vm.is_alive():
            vm.destroy()
        orig_config_xml.sync()
