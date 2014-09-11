import re
import os
import logging
from autotest.client.shared import error
from virttest import remote
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest import libvirt_vm
from virttest.utils_test import libvirt
from xml.dom.minidom import parse


def remote_test(remote_ip, local_ip, remote_pwd, remote_prompt,
                vm_name, status_error_test):
    """
    Test remote case
    """
    err = ""
    status = 1
    status_error = status_error_test
    try:
        remote_uri = libvirt_vm.complete_uri(local_ip)
        session = remote.remote_login("ssh", remote_ip, "22",
                                      "root", remote_pwd, remote_prompt)
        session.cmd_output('LANG=C')
        command = "virsh -c %s setvcpus %s 1 --live" % (remote_uri, vm_name)
        if virsh.has_command_help_match("setvcpus", "--live") is None:
            raise error.TestNAError("The current libvirt doesn't support"
                                    " '--live' option for setvcpus")
        status, output = session.cmd_status_output(command, internal_timeout=5)
        session.close()
        if status != 0:
            err = output
    except error.CmdError:
        status = 1
        err = "remote test failed"
    return status, status_error, err


def get_xmldata(vm_name, xml_file, options):
    """
    Get some values out of the guests xml
    Returns:
        count => Number of vCPUs set for the guest
        current => If there is a 'current' value set
                   in the xml indicating the ability
                   to add vCPUs. If 'current' is not
                   set, then return 0 for this value.
        os_machine => Name of the <os> <type machine=''>
                      to be used to determine if we can
                      support hotplug
    """
    # Grab a dump of the guest - if we're using the --config,
    # then get an --inactive dump.
    extra_opts = ""
    if "--config" in options:
        extra_opts = "--inactive"
    vcpus_current = ""
    virsh.dumpxml(vm_name, extra=extra_opts, to_file=xml_file)
    dom = parse(xml_file)
    root = dom.documentElement
    # get the vcpu value
    vcpus_parent = root.getElementsByTagName("vcpu")
    vcpus_count = int(vcpus_parent[0].firstChild.data)
    for n in vcpus_parent:
        vcpus_current += n.getAttribute("current")
        if vcpus_current != "":
            vcpus_current = int(vcpus_current)
        else:
            vcpus_current = 0
    # get the machine type
    os_parent = root.getElementsByTagName("os")
    os_machine = ""
    for os_elem in os_parent:
        for node in os_elem.childNodes:
            if node.nodeName == "type":
                os_machine = node.getAttribute("machine")
    dom.unlink()
    return vcpus_count, vcpus_current, os_machine


def run(test, params, env):
    """
    Test command: virsh setvcpus.

    The command can change the number of virtual CPUs in the guest domain.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh setvcpus operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pre_vm_state = params.get("setvcpus_pre_vm_state")
    command = params.get("setvcpus_command", "setvcpus")
    options = params.get("setvcpus_options")
    vm_ref = params.get("setvcpus_vm_ref", "name")
    count = params.get("setvcpus_count", "")
    convert_err = "Can't convert {0} to integer type"
    try:
        count = int(count)
    except ValueError:
        # 'count' may not invalid number in negative tests
        logging.debug(convert_err.format(count))
    current_vcpu = int(params.get("setvcpus_current", "1"))
    try:
        current_vcpu = int(current_vcpu)
    except ValueError:
        raise error.TestError(convert_err.format(current_vcpu))
    max_vcpu = int(params.get("setvcpus_max", "4"))
    try:
        max_vcpu = int(max_vcpu)
    except ValueError:
        raise error.TestError(convert_err.format(max_vcpu))
    extra_param = params.get("setvcpus_extra_param")
    count_option = "%s %s" % (count, extra_param)
    status_error = params.get("status_error")
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", "")
    remote_prompt = params.get("remote_prompt", "#")
    tmpxml = os.path.join(test.tmpdir, 'tmp.xml')
    set_topology = "yes" == params.get("set_topology", "no")
    sockets = params.get("topology_sockets")
    cores = params.get("topology_cores")
    threads = params.get("topology_threads")
    start_vm_after_set = "yes" == params.get("start_vm_after_set", "no")
    start_vm_expect_fail = "yes" == params.get("start_vm_expect_fail", "no")
    remove_vm_feature = params.get("remove_vm_feature", "")

    # Early death
    if vm_ref == "remote" and (remote_ip.count("EXAMPLE.COM") or
                               local_ip.count("EXAMPLE.COM")):
        raise error.TestNAError("remote/local ip parameters not set.")

    # Save original configuration
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    # Normal processing of the test is to set the maximum vcpu count to 4,
    # and set the current vcpu count to 1, then adjust the 'count' value to
    # plug or unplug vcpus.
    #
    # This is generally fine when the guest is not running; however, the
    # hotswap functionality hasn't always worked very well and is under
    # going lots of change from using the hmp "cpu_set" command in 1.5
    # to a new qmp "cpu-add" added in 1.6 where the "cpu-set" command
    # seems to have been deprecated making things very messy.
    #
    # To further muddy the waters, the "cpu-add" functionality is supported
    # for specific machine type versions. For the purposes of this test that
    # would be "pc-i440fx-1.5" or "pc-q35-1.5" or later type machines (from
    # guest XML "<os> <type ... machine=''/type> </os>"). Depending on which
    # version of qemu/kvm was used to initially create/generate the XML for
    # the machine this could result in a newer qemu still using 1.4 or earlier
    # for the machine type.
    #

    try:
        if vm.is_alive():
            vm.destroy()

        # Set maximum vcpus, so we can run all kinds of normal tests without
        # encounter requested vcpus greater than max allowable vcpus error
        vmxml.set_vm_vcpus(vm_name, max_vcpu, current_vcpu)

        # Get the number of cpus, current value if set, and machine type
        orig_count, orig_current, mtype = get_xmldata(vm_name, tmpxml, options)
        logging.debug("Before run setvcpus: cpu_count=%d, cpu_current=%d,"
                      " mtype=%s", orig_count, orig_current, mtype)

        # Set cpu topology
        if set_topology:
            vmcpu_xml = vm_xml.VMCPUXML()
            vmcpu_xml['topology'] = {'sockets': sockets, 'cores': cores,
                                     'threads': threads}
            vmxml['cpu'] = vmcpu_xml
            vmxml.sync()

        # Remove vm features
        if remove_vm_feature:
            vmfeature_xml = vmxml['features']
            vmfeature_xml.remove_feature(remove_vm_feature)
            vmxml['features'] = vmfeature_xml
            vmxml.sync()

        # Restart, unless that's not our test
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()

        if orig_count == 1 and count == 1:
            logging.debug("Original vCPU count is 1, just checking if setvcpus "
                          "can still set current.")

        domid = vm.get_id()  # only valid for running
        domuuid = vm.get_uuid()

        if pre_vm_state == "paused":
            vm.pause()
        elif pre_vm_state == "shut off" and vm.is_alive():
            vm.destroy()

        # Run test
        if vm_ref == "remote":
            (setvcpu_exit_status, status_error,
             setvcpu_exit_stderr) = remote_test(remote_ip,
                                                local_ip,
                                                remote_pwd,
                                                remote_prompt,
                                                vm_name,
                                                status_error)
        else:
            if vm_ref == "name":
                dom_option = vm_name
            elif vm_ref == "id":
                dom_option = domid
                if params.get("setvcpus_hex_id") is not None:
                    dom_option = hex(int(domid))
                elif params.get("setvcpus_invalid_id") is not None:
                    dom_option = params.get("setvcpus_invalid_id")
            elif vm_ref == "uuid":
                dom_option = domuuid
                if params.get("setvcpus_invalid_uuid") is not None:
                    dom_option = params.get("setvcpus_invalid_uuid")
            else:
                dom_option = vm_ref

            option_list = options.split(" ")
            for item in option_list:
                if virsh.has_command_help_match(command, item) is None:
                    raise error.TestNAError("The current libvirt version"
                                            " doesn't support '%s' option"
                                            % item)
            status = virsh.setvcpus(dom_option, count_option, options,
                                    ignore_status=True, debug=True)
            setvcpu_exit_status = status.exit_status
            setvcpu_exit_stderr = status.stderr.strip()

            # Start VM after set vcpu
            if start_vm_after_set:
                if vm.is_alive():
                    logging.debug("VM already started")
                else:
                    result = virsh.start(vm_name, ignore_status=True,
                                         debug=True)
                    libvirt.check_exit_status(result, start_vm_expect_fail)

    finally:
        new_count, new_current, mtype = get_xmldata(vm_name, tmpxml, options)
        logging.debug("After run setvcpus: cpu_count=%d, cpu_current=%d,"
                      " mtype=%s", new_count, new_current, mtype)

        # Cleanup
        if pre_vm_state == "paused":
            virsh.resume(vm_name, ignore_status=True)
        orig_config_xml.sync()
        if os.path.exists(tmpxml):
            os.remove(tmpxml)

    # check status_error
    if status_error == "yes":
        if setvcpu_exit_status == 0:
            # RHEL7/Fedora has a bug(BZ#1000354) against qemu-kvm, so throw the
            # bug info here
            if remove_vm_feature:
                logging.error(
                    "You may encounter bug: "
                    "https://bugzilla.redhat.com/show_bug.cgi?id=1000354")
            raise error.TestFail("Run successfully with wrong command!")
    else:
        if setvcpu_exit_status != 0:
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
                         setvcpu_exit_stderr):
                raise error.TestNAError("guest <os> machine property '%s' "
                                        "may be too old to allow hotplug.",
                                        mtype)

            # A qemu older than 1.5 or an unplug for 1.6 will result in
            # the following failure.  In general, any time libvirt determines
            # it cannot support adding or removing a vCPU...
            if re.search("cannot change vcpu count of this domain",
                         setvcpu_exit_stderr):
                raise error.TestNAError("virsh setvcpu hotplug unsupported, "
                                        " mtype=%s" % mtype)

            # Otherwise, it seems we have a real error
            raise error.TestFail("Run failed with right command mtype=%s"
                                 " stderr=%s" % (mtype, setvcpu_exit_stderr))
        else:
            if "--maximum" in options:
                if new_count != count:
                    raise error.TestFail("Changing guest maximum vcpus failed"
                                         " while virsh command return 0")
            else:
                if new_current != count:
                    raise error.TestFail("Changing guest current vcpus failed"
                                         " while virsh command return 0")
