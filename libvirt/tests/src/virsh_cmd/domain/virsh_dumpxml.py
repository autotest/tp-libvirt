import re
import logging
import platform

from virttest import virsh
from virttest import cpu
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml import domcapability_xml
from virttest.utils_test import libvirt as utlv

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

    def get_cpu_model_policies(arch):
        """
        Get model and policies to be set

        :param arch: architecture, e.g. x86_64
        :return model, policies: cpu model and features with their policies
        """
        if arch == "s390x":
            return "z13.2-base", {"msa1": "require",
                                  "msa2": "force",
                                  "edat": "disable",
                                  "vmx": "forbid"}
        else:
            return "Penryn", {"xtpr": "optional",
                              "tm2": "disable",
                              "est": "force",
                              "vmx": "forbid",
                              # Unsupported feature 'ia64'
                              "ia64": "optional",
                              "vme": "optional"}

    def custom_cpu(vm_name, cpu_match, model, policies):
        """
        Custom guest cpu match/model/features for --update-cpu option.
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmcpu_xml = vm_xml.VMCPUXML()
        vmcpu_xml['match'] = cpu_match
        vmcpu_xml['model'] = model
        for feature in policies:
            vmcpu_xml.add_feature(feature, policies[feature])
        vmxml['cpu'] = vmcpu_xml
        logging.debug('Custom VM CPU: %s', vmcpu_xml.xmltreefile)
        vmxml.sync()

    def get_cpu_features():
        """
        Get all supported CPU features

        :return: list of feature string
        """
        features = []
        dom_capa = domcapability_xml.DomCapabilityXML()
        modelname = dom_capa.get_hostmodel_name()
        for item in dom_capa.get_additional_feature_list('host-model'):
            for key, value in item.items():
                if value == 'require':
                    features.append(key)
        return list(set(features) | set(cpu.get_model_features(modelname)))

    def get_expected_cpu_model_policies(arch, model, policies):
        """
        Get expected cpu model and feature policies after setting

        :param arch: architecture, e.g. x86_64
        :param model: previously set cpu model
        :param policies: previously set features and their policies
        :return e_model, e_policies: expected values after setting
        """
        if arch == "s390x":
            return model, policies
        else:
            return model, {"xtpr": "require",
                           "tm2": "disable",
                           "est": "force",
                           "vmx": "forbid",
                           # Unsupported feature 'ia64'
                           "ia64": "require",
                           "vme": "require"}

    def check_cpu(xml, cpu_match, e_model, e_policies):
        """
        Check the dumpxml result for --update-cpu option

        Note, function custom_cpu() hard code these features and policy,
        so after run virsh dumpxml --update-cpu:
        1. For match='minimum', all host support features will added,
           and match change to 'exact'. Since libvirt-3.0, cpu update is
           reworked, and the custom CPU with minimum match is converted
           similarly to host-model.
        2. policy='optional' features(support by host) will update to
           policy='require'
        3. policy='optional' features(unsupport by host) will update to
           policy='disable'
        4. Other policy='disable|force|forbid|require' with keep the
           original values

        :param xml: VM xml
        :param cpu_match: match mode
        :param e_model: expected model name
        :param e_policies: expected policies for features
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
                # Since libvirt3.9, libvirt query qemu/kvm to get one feature support or not
                if libvirt_version.version_compare(3, 9, 0):
                    if f_name in get_cpu_features():
                        expect_policy = "require"
                else:
                    if host_capa.check_feature_name(f_name):
                        expect_policy = "require"
            else:
                expect_policy = e_policies[f_name]
            if f_policy != e_policies[f_name]:
                logging.error(err_msg)
                check_pass = False
            # Count expect require features
            if expect_policy == "require":
                expect_require_features += 1
            # Count actual require features
            if f_policy == "require":
                require_count += 1

        # Check optional feature is changed to require/disable
        expect_model = e_model

        if cpu_match == "minimum":
            # libvirt commit 3b6be3c0 change the behavior of update-cpu
            # Check model is changed to host cpu-model given in domcapabilities
            if libvirt_version.version_compare(3, 0, 0):
                expect_model = host_capa.model
            expect_match = "exact"
            # For different host, the support require features are different,
            # so just check the actual require features greater than the
            # expect number
            if require_count < expect_require_features:
                logging.error("Found %d require features, but expect >=%s",
                              require_count, expect_require_features)
                check_pass = False
        else:
            expect_match = cpu_match
            if require_count != expect_require_features:
                logging.error("Found %d require features, but expect %s",
                              require_count, expect_require_features)
                check_pass = False

        logging.debug("Expect 'match' value is: %s", expect_match)
        match = vmcpu_xml['match']
        if match != expect_match:
            logging.error("CPU match '%s' is not expected", match)
            check_pass = False
        logging.debug("Expect 'model' value is: %s", expect_model)
        if vmcpu_xml['model'] != expect_model:
            logging.error("CPU model %s is not expected", vmcpu_xml['model'])
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
    status_error = "yes" == params.get("status_error", "no")
    cpu_match = params.get("cpu_match", "minimum")
    model, policies = None, None

    arch = platform.machine()
    if arch == 's390x' and cpu_match == 'minimum':
        test.cancel("Minimum mode not supported on s390x")

    # acl polkit params
    setup_libvirt_polkit = "yes" == params.get('setup_libvirt_polkit')
    if not libvirt_version.version_compare(1, 1, 1):
        if setup_libvirt_polkit:
            test.cancel("API acl test not supported in current libvirt version")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user and setup_libvirt_polkit:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if options_ref.count("update-cpu"):
        model, policies = get_cpu_model_policies(arch)
        custom_cpu(vm_name, cpu_match, model, policies)
    elif options_ref.count("security-info"):
        new_xml = backup_xml.copy()
        try:
            vm_xml.VMXML.add_security_info(new_xml, security_pwd)
        except Exception as info:
            test.cancel(info)
    domuuid = vm.get_uuid()
    domid = vm.get_id()

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
    try:
        cmd_result = virsh.dumpxml(vm_ref, extra=options_ref,
                                   uri=uri,
                                   unprivileged_user=unprivileged_user,
                                   debug=True)
        utlv.check_exit_status(cmd_result, status_error)
        output = cmd_result.stdout.strip()

        # Check result
        if not status_error:
            if (options_ref.count("inactive") and
                    is_dumpxml_of_running_vm(output, domid)):
                test.fail("Found domain id in XML when run virsh dumpxml"
                          " with --inactive option")
            elif options_ref.count("update-cpu"):
                e_model, e_policies = get_expected_cpu_model_policies(arch,
                                                                      model,
                                                                      policies)
                if not check_cpu(output, cpu_match, e_model, e_policies):
                    test.fail("update-cpu option check failed")
            elif options_ref.count("security-info"):
                if not output.count("passwd='%s'" % security_pwd):
                    test.fail("No security info found")
            else:
                if (vm_state == "shutoff" and
                        is_dumpxml_of_running_vm(output, domid)):
                    test.fail("Found domain id in XML when run virsh dumpxml"
                              " for a shutoff VM")
    finally:
        backup_xml.sync()
