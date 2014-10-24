import re
import logging
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import capability_xml
from provider import libvirt_version


def run(test, params, env):
    """
    Test command: virsh dumpxml.

    1) Prepare parameters.
    2) Set options of virsh dumpxml.
    3) Prepare environment: vm_state, etc.
    4) Run dumpxml command.
    5) Recover environment.
    6) Check result.
    """
    def is_dumpxml_of_running_vm(dumpxml, domid):
        """
        To check whether the dumpxml is got during vm is running.
        (Verify the domid in dumpxml)

        :param dumpxml: the output of virsh dumpxml.
        :param domid: the id of vm
        """
        match_string = "<domain.*id='%s'/>" % domid
        if re.search(dumpxml, match_string):
            return True
        return False

    def custom_cpu(vm_name, cpu_match):
        """
        Custom guest cpu match/model/features for --update-cpu option.
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmcpu_xml = vm_xml.VMCPUXML()
        vmcpu_xml['match'] = cpu_match
        vmcpu_xml['model'] = "Penryn"
        vmcpu_xml['vendor'] = "Intel"
        vmcpu_xml.add_feature('xtpr', 'optional')
        vmcpu_xml.add_feature('tm2', 'disable')
        vmcpu_xml.add_feature('est', 'force')
        vmcpu_xml.add_feature('vmx', 'forbid')
        # Unsupport feature 'ia64'
        vmcpu_xml.add_feature('ia64', 'optional')
        vmcpu_xml.add_feature('vme', 'require')
        vmxml['cpu'] = vmcpu_xml
        vmxml.sync()

    def check_cpu(xml, cpu_match):
        """
        Check the dumpxml result for --update-cpu option

        Note, function custom_cpu() hard code these features and policy,
        so after run virsh dumpxml --update-cpu:
        1. For match='minimum', all host support features will added,
           and match='exact'
        2. policy='optional' features(support by host) will update to
           policy='require'
        3. policy='optional' features(unsupport by host) will update to
           policy='disable'
        4. Other policy='disable|force|forbid|require' with keep the
           original values
        """
        vmxml = vm_xml.VMXML()
        vmxml['xml'] = xml
        vmcpu_xml = vmxml['cpu']
        check_pass = True
        require_count = 0
        expect_require_features = 0
        cpu_feature_list = vmcpu_xml.get_feature_list()
        host_capa = capability_xml.CapabilityXML()
        for i in range(len(cpu_feature_list)):
            f_name = vmcpu_xml.get_feature_name(i)
            f_policy = vmcpu_xml.get_feature_policy(i)
            err_msg = "Policy of '%s' is not expected: %s" % (f_name, f_policy)
            expect_policy = "disable"
            if f_name in ["xtpr", "vme", "ia64"]:
                # Check if feature is support on the host
                if host_capa.check_feature_name(f_name):
                    expect_policy = "require"
                if f_policy != expect_policy:
                    logging.error(err_msg)
                    check_pass = False
            if f_name == "tm2":
                if f_policy != "disable":
                    logging.error(err_msg)
                    check_pass = False
            if f_name == "est":
                if f_policy != "force":
                    logging.error(err_msg)
                    check_pass = False
            if f_name == "vmx":
                if f_policy != "forbid":
                    logging.error(err_msg)
                    check_pass = False
            # Count expect require features
            if expect_policy == "require":
                expect_require_features += 1
            # Count actual require features
            if f_policy == "require":
                require_count += 1
        # Check
        if cpu_match == "minimum":
            expect_match = "exact"
            # For different host, the support require features are different,
            # so just check the actual require features greater than the
            # expect number
            if require_count < expect_require_features:
                logging.error("Find %d require features, but expect >=%s",
                              require_count, expect_require_features)
                check_pass = False
        else:
            expect_match = cpu_match
            if require_count != expect_require_features:
                logging.error("Find %d require features, but expect %s",
                              require_count, expect_require_features)
                check_pass = False
        match = vmcpu_xml['match']
        if match != expect_match:
            logging.error("CPU match '%s' is not expected", match)
            check_pass = False
        if vmcpu_xml['model'] != 'Penryn':
            logging.error("CPU model %s is not expected", vmcpu_xml['model'])
            check_pass = False
        if vmcpu_xml['vendor'] != "Intel":
            logging.error("CPU vendor %s is not expected", vmcpu_xml['vendor'])
            check_pass = False
        return check_pass

    # Prepare parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vm_ref = params.get("dumpxml_vm_ref", "domname")
    options_ref = params.get("dumpxml_options_ref", "")
    options_suffix = params.get("dumpxml_options_suffix", "")
    vm_state = params.get("dumpxml_vm_state", "running")
    security_pwd = params.get("dumpxml_security_pwd", "123456")
    status_error = params.get("status_error", "no")
    cpu_match = params.get("cpu_match", "minimum")
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if options_ref.count("update-cpu"):
        custom_cpu(vm_name, cpu_match)
    elif options_ref.count("security-info"):
        new_xml = backup_xml.copy()
        vm_xml.VMXML.add_security_info(new_xml, security_pwd)
    domuuid = vm.get_uuid()
    domid = vm.get_id()

    # acl polkit params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    + " libvirt version.")

    # Prepare vm state for test
    if vm_state == "shutoff" and vm.is_alive():
        vm.destroy()  # Confirm vm is shutoff

    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domid":
        vm_ref = domid
    elif vm_ref == "domuuid":
        vm_ref = domuuid
    elif vm_ref == "hex_id":
        if domid == "-":
            vm_ref = domid
        else:
            vm_ref = hex(int(domid))

    if options_suffix:
        options_ref = "%s %s" % (options_ref, options_suffix)

    # Run command
    logging.info("Command:virsh dumpxml %s", vm_ref)
    try:
        try:
            if params.get('setup_libvirt_polkit') == 'yes':
                cmd_result = virsh.dumpxml(vm_ref, extra=options_ref,
                                           uri=uri,
                                           unprivileged_user=unprivileged_user)
            else:
                cmd_result = virsh.dumpxml(vm_ref, extra=options_ref)
            output = cmd_result.stdout.strip()
            if cmd_result.exit_status:
                raise error.TestFail("dumpxml %s failed.\n"
                                     "Detail: %s.\n" % (vm_ref, cmd_result))
            status = 0
        except error.TestFail, detail:
            status = 1
            output = detail
        logging.debug("virsh dumpxml result:\n%s", output)

        # Recover vm state
        if vm_state == "paused":
            vm.resume()

        # Check result
        if status_error == "yes":
            if status == 0:
                raise error.TestFail("Run successfully with wrong command.")
        elif status_error == "no":
            if status:
                raise error.TestFail("Run failed with right command.")
            else:
                # validate dumpxml file
                # Since validate LibvirtXML functions are been working by
                # cevich, reserving it here. :)
                if options_ref.count("inactive"):
                    if is_dumpxml_of_running_vm(output, domid):
                        raise error.TestFail("Got dumpxml for active vm "
                                             "with --inactive option!")
                elif options_ref.count("update-cpu"):
                    if not check_cpu(output, cpu_match):
                        raise error.TestFail("update-cpu option check fail")
                elif options_ref.count("security-info"):
                    if not output.count("passwd='%s'" % security_pwd):
                        raise error.TestFail("No more cpu info outputed!")
                else:
                    if (vm_state == "shutoff"
                            and is_dumpxml_of_running_vm(output, domid)):
                        raise error.TestFail("Got dumpxml for active vm "
                                             "when vm is shutoff.")
    finally:
        backup_xml.sync()
