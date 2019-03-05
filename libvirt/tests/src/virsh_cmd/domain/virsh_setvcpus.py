import re
import os
import logging

from virttest import ssh_key
from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest import utils_hotplug


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
    status_error = (params.get("status_error", "no") == "yes")
    convert_err = "Can't convert {0} to integer type"
    try:
        current_vcpu = int(params.get("setvcpus_current", "1"))
    except ValueError:
        test.error(convert_err.format(current_vcpu))
    try:
        max_vcpu = int(params.get("setvcpus_max", "4"))
    except ValueError:
        test.error(convert_err.format(max_vcpu))
    try:
        count = params.get("setvcpus_count", "")
        if count:
            count = eval(count)
        count = int(count)
    except ValueError:
        # 'count' may not invalid number in negative tests
        logging.debug(convert_err.format(count))

    extra_param = params.get("setvcpus_extra_param")
    count_option = "%s %s" % (count, extra_param)
    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", "")
    remote_user = params.get("remote_user", "root")
    remote_uri = params.get("remote_uri")
    tmpxml = os.path.join(data_dir.get_tmp_dir(), 'tmp.xml')
    topology_correction = "yes" == params.get("topology_correction", "yes")
    result = True

    # Early death 1.1
    if remote_uri:
        if remote_ip.count("EXAMPLE.COM"):
            test.cancel("remote ip parameters not set.")
        ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)

    # Early death 1.2
    option_list = options.split(" ")
    for item in option_list:
        if virsh.has_command_help_match(command, item) is None:
            test.cancel("The current libvirt version"
                        " doesn't support '%s' option" % item)

    # Init expect vcpu count values
    exp_vcpu = {'max_config': max_vcpu, 'max_live': max_vcpu,
                'cur_config': current_vcpu, 'cur_live': current_vcpu,
                'guest_live': current_vcpu}

    def set_expected(vm, options):
        """
        Set the expected vcpu numbers

        :param vm: vm object
        :param options: setvcpus options
        """
        if ("config" in options) or ("current" in options and vm.is_dead()):
            if "maximum" in options:
                exp_vcpu["max_config"] = count
            else:
                exp_vcpu['cur_config'] = count
        if ("live" in options) or ("current" in options and vm.is_alive()):
            exp_vcpu['cur_live'] = count
            exp_vcpu['guest_live'] = count
        if options == '':
            # when none given it defaults to live
            exp_vcpu['cur_live'] = count
            exp_vcpu['guest_live'] = count

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
        # Set maximum vcpus, so we can run all kinds of normal tests without
        # encounter requested vcpus greater than max allowable vcpus error
        topology = vmxml.get_cpu_topology()
        if topology and ("config" and "maximum" in options) and not status_error:
            # https://bugzilla.redhat.com/show_bug.cgi?id=1426220
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            del vmxml.cpu
            vmxml.sync()
        vmxml.set_vm_vcpus(vm_name, max_vcpu, current_vcpu,
                           topology_correction=topology_correction)

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        logging.debug("Pre-test xml is %s", vmxml.xmltreefile)

        # Get the number of cpus, current value if set, and machine type
        cpu_xml_data = utils_hotplug.get_cpu_xmldata(vm, options)
        logging.debug("Before run setvcpus: cpu_count=%d, cpu_current=%d,"
                      " mtype=%s", cpu_xml_data['vcpu'],
                      cpu_xml_data['current_vcpu'], cpu_xml_data['mtype'])

        # Restart, unless that's not our test
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()

        if cpu_xml_data['vcpu'] == 1 and count == 1:
            logging.debug("Original vCPU count is 1, just checking if setvcpus "
                          "can still set current.")

        domid = vm.get_id()  # only valid for running
        domuuid = vm.get_uuid()

        if pre_vm_state == "paused":
            vm.pause()
        elif pre_vm_state == "shut off" and vm.is_alive():
            vm.destroy()

        # Run test
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

        if remote_uri:
            status = virsh.setvcpus(dom_option, "1", "--config",
                                    ignore_status=True, debug=True, uri=remote_uri)
        else:
            status = virsh.setvcpus(dom_option, count_option, options,
                                    ignore_status=True, debug=True)
            if not status_error:
                set_expected(vm, options)
                result = utils_hotplug.check_vcpu_value(vm, exp_vcpu,
                                                        option=options)
        setvcpu_exit_status = status.exit_status
        setvcpu_exit_stderr = status.stderr.strip()

    finally:
        cpu_xml_data = utils_hotplug.get_cpu_xmldata(vm, options)
        logging.debug("After run setvcpus: cpu_count=%d, cpu_current=%d,"
                      " mtype=%s", cpu_xml_data['vcpu'],
                      cpu_xml_data['current_vcpu'], cpu_xml_data['mtype'])

        # Cleanup
        if pre_vm_state == "paused":
            virsh.resume(vm_name, ignore_status=True)
        orig_config_xml.sync()
        if os.path.exists(tmpxml):
            os.remove(tmpxml)

    # check status_error
    if status_error:
        if setvcpu_exit_status == 0:
            test.fail("Run successfully with wrong command!")
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
                test.cancel("guest <os> machine property '%s' "
                            "may be too old to allow hotplug." % cpu_xml_data['mtype'])

            # A qemu older than 1.5 or an unplug for 1.6 will result in
            # the following failure.  In general, any time libvirt determines
            # it cannot support adding or removing a vCPU...
            if re.search("cannot change vcpu count of this domain",
                         setvcpu_exit_stderr):
                test.cancel("virsh setvcpu hotplug unsupported, "
                            " mtype=%s" % cpu_xml_data['mtype'])

            # Otherwise, it seems we have a real error
            test.fail("Run failed with right command mtype=%s"
                      " stderr=%s" % (cpu_xml_data['mtype'], setvcpu_exit_stderr))
        else:
            if not result:
                test.fail("Test Failed")
