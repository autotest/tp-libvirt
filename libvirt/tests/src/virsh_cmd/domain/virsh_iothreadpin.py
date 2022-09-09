import logging as log

from avocado.core import exceptions
from avocado.utils import cpu
from avocado.utils import process


from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def get_iothreadpins(vm_name, options):
    """
    Get some iothreadpins info from the guests xml
    Returns:
        The iothreadpins
    """
    if "--config" in options:
        xml_info = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    else:
        xml_info = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug("domxml: %s", xml_info)
    try:
        return xml_info.cputune.iothreadpins
    except xcepts.LibvirtXMLNotFoundError:
        return None


def setup_vmxml_before_start(vmxml, params):
    """
    Configure vm xml using given parameters

    :param vmxml: vm xml
    :param params: dict for the test
    """
    iothreads = params.get("iothreads")
    iothreadids = params.get("iothreadids")
    iothreadpins = params.get("iothreadpins")

    if iothreadids:
        ids_xml = vm_xml.VMIothreadidsXML()
        ids_xml.iothread = [{'id': id} for id in iothreadids.split()]
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
    if iothreads:
        vmxml.iothreads = int(iothreads)
    logging.debug("Pre-test xml is %s", vmxml)
    vmxml.sync()


def get_dom_option(vm, vm_ref):
    """
    Get the domain option for iothreadpin

    :param vm: vm object
    :param vm_ref:  vm reference
    :return: str, vm name or domain id or domain uuid or others
    """
    domid = vm.get_id()  # only valid for running
    domuuid = vm.get_uuid()

    if vm_ref == "name":
        dom_option = vm.name
    elif vm_ref == "id":
        dom_option = domid
    elif vm_ref == "uuid":
        dom_option = domuuid
    else:
        dom_option = vm_ref

    return dom_option


def process_cpuset(params, test):
    """
    Process cpuset value for some specific tests

    :param params: dict for testing
    :param test: test object
    :return: int, cpuset to be used for iothreadpin
    """
    cpuset = params.get("cpuset")
    disallowed_cpuset = params.get('disallowed_cpuset', 'no') == 'yes'

    if disallowed_cpuset:
        # Set cpuset to the first cpu id just for testing
        cpuset_cpus_path = '/sys/fs/cgroup/machine.slice/cpuset.cpus'
        logging.debug("Set allowed cpuset to %s", cpuset_cpus_path)
        online_cpu_list = cpu.online_list()
        if cpu.online_count() == 1:
            test.cancel("At least 2 online cpus are needed for this test case.")

        cmd = "echo %d > %s" % (online_cpu_list[0], cpuset_cpus_path)
        process.run(cmd, ignore_status=False, shell=True)
        cpuset = online_cpu_list[1]

    return cpuset


def verify_test_disallowed_cpuset(vm_name, options, test):
    """
    Verify the test for disallowed cpuset case and iothreadpin info should
    not exist

    :param vm_name: vm name
    :param options: iothreadpin options
    :param test: test object
    :raises: test.fail if iothreadpin info still exists in dumpxml
    """

    def _check_iothreadpins():
        iothreadpins = get_iothreadpins(vm_name, options)
        if iothreadpins:
            test.fail("iothreadpin info '%s' in guest xml is not expected" % iothreadpins)
        else:
            logging.debug("iothreadpins info does not exist as expected")

    _check_iothreadpins()
    virsh_dargs = {"debug": True, "ignore_status": False}
    virsh.managedsave(vm_name, **virsh_dargs)
    virsh.start(vm_name, **virsh_dargs)
    _check_iothreadpins()


def run(test, params, env):
    """
    Test command: virsh iothread.

    The command can change the number of iothread.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh iothreadadd operation.
    3.Recover test environment.
    4.Confirm the test result.
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pre_vm_state = params.get("iothread_pre_vm_state")
    command = params.get("iothread_command", "iothread")
    options = params.get("iothread_options")
    status_error = "yes" == params.get("status_error")
    add_iothread_id = params.get("add_iothread_id")
    iothread_id = params.get("iothread_id")
    disallowed_cpuset = params.get("disallowed_cpuset")
    error_msg = params.get("error_msg")
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

        setup_vmxml_before_start(vmxml, params)
        # Restart, unless that's not our test
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()

        dom_option = get_dom_option(vm, params.get("iothread_vm_ref"))

        if pre_vm_state == "shut off" and vm.is_alive():
            vm.destroy()

        virsh_dargs = {"debug": True}
        if "yes" == params.get("readonly", "no"):
            virsh_dargs.update({"readonly": True})

        cpuset = process_cpuset(params, test)
        if add_iothread_id:
            iothread_id = add_iothread_id
            virsh.iothreadadd(dom_option, add_iothread_id, debug=True, ignore_status=False)
        ret = virsh.iothreadpin(dom_option, iothread_id, cpuset,
                                options, **virsh_dargs)
        if error_msg:
            libvirt.check_result(ret, expected_fails=error_msg)
        else:
            libvirt.check_exit_status(ret, status_error)
        if disallowed_cpuset:
            verify_test_disallowed_cpuset(vm_name, options, test)

        if not status_error:
            # Check domainxml
            iothread_info = get_iothreadpins(vm_name, options)
            logging.debug("iothreadinfo: %s", iothread_info)
            for info in iothread_info:
                if info["iothread"] == iothread_id and info["cpuset"] == cpuset:
                    # Find the iothreadpins in domain xml
                    break
                elif iothread_info.index(info) == (len(iothread_info) - 1):
                    # Can not find the iothreadpins at last
                    raise exceptions.TestFail("Failed to add iothread %s in domain xml",
                                              iothread_id)

            # Check iothreadinfo by virsh command
            iothread_info = libvirt.get_iothreadsinfo(dom_option, options)
            logging.debug("iothreadinfo: %s", iothread_info)
            if (iothread_id not in iothread_info or
                    iothread_info[iothread_id] != cpuset):
                raise exceptions.TestFail("Failed to add iothreadpins %s", iothread_id)

    finally:
        # Cleanup
        if vm.is_alive():
            vm.destroy()
        orig_config_xml.sync()
