import logging
import os
import uuid

import aexpect

from virttest import virsh


def run(test, params, env):
    """
    Test command: virsh desc.

    This command allows to show or modify description or title of a domain.
    1). For running domain, get/set description&title with options.
    2). For shut off domian, get/set description&title with options.
    3). For persistent/transient domain, get/set description&title with options.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    options = params.get("desc_option", "")
    persistent_vm = params.get("persistent_vm", "yes")
    domain = params.get("domain", "name")
    if domain == "UUID":
        vm_name = vm.get_uuid()
    elif domain == "invalid_domain":
        vm_name = "domain_" + str(uuid.uuid1())
    elif domain == "invalid_uuid":
        vm_name = uuid.uuid1()

    def run_cmd(name, options, desc_str, status_error):
        """
        Run virsh desc command

        :return: cmd output
        """
        if "--edit" not in options:
            cmd_result = virsh.desc(name, options, desc_str, ignore_status=True,
                                    debug=True)
            output = cmd_result.stdout.strip()
            err = cmd_result.stderr.strip()
            status = cmd_result.exit_status
        else:
            logging.debug("Setting domain desc \"%s\" by --edit", desc_str)
            session = aexpect.ShellSession("sudo -s")
            try:
                session.sendline("virsh -c %s desc %s --edit" %
                                 (vm.connect_uri, name))
                session.sendline("dgg")
                session.sendline("dG")
                session.sendline(":%s/^$/" + desc_str + "/")
                session.send('\x1b')
                session.send('ZZ')
                match, text = session.read_until_any_line_matches(
                    [r"Domain description updated successfully"],
                    timeout=10, internal_timeout=1)
                session.close()
                if match == -1:
                    status = 0
                    output = "Domain description updated successfully"
                else:
                    status = 1
                    err = "virsh desc --edit fails"
            except Exception:
                test.fail("Fail to create session.")
        if status_error == "no" and status:
            test.fail(err)
        elif status_error == "yes" and status == 0:
            test.fail("Expect fail, but run successfully.")
        return output

    def vm_state_switch():
        """
        Switch the vm state
        """
        if vm.is_dead():
            vm.start()
        if vm.is_alive():
            vm.destroy()

    def desc_check(name, desc_str, options):
        """
        Check the domain's description or title
        """
        ret = False
        state_switch = False
        if options.count("--config") and vm.is_persistent():
            state_switch = True
        # If both --live and --config are specified, the --config
        # option takes precedence on getting the current description
        # and both live configuration and config are updated while
        # setting the description.
        # This situation just happens vm is alive
        if options.count("--config") and options.count("--live"):
            # Just test options exclude --config (--live [--title])
            desc_check(name, desc_str, options.replace("--config", ""))
            # Just test options exclude --live (--config [--title])
            desc_check(name, desc_str, options.replace("--live", ""))
            ret = True
        else:
            if state_switch:
                vm_state_switch()
            # --new-desc and --edit option should not appear in check
            if options.count("--edit") or options.count("--new-desc"):
                output = run_cmd(name, "", "", "no")
            else:
                output = run_cmd(name, options, "", "no")
            if desc_str == output:
                logging.debug("Domain desc check successfully.")
                ret = True
            else:
                test.fail("Expect fail, but run successfully.")

        return ret

    def run_test():
        """
        Get/Set vm desc by running virsh desc command.
        """
        status_error = params.get("status_error", "no")
        desc_str = params.get("desc_str", "")
        # Test 1: get vm desc
        if "--edit" not in options:
            if "--new-desc" in options:
                run_cmd(vm_name, options, "", "yes")
            else:
                run_cmd(vm_name, options, "", status_error)
        # Test 2: set vm desc
        if options.count("--live") and vm.state() == "shut off":
            status_error = "yes"
        if len(desc_str) == 0 and status_error == "no":
            desc_str = "New Description/title for the %s vm" % vm.state()
            logging.debug("Use the default desc message: %s", desc_str)
        run_cmd(vm_name, options, desc_str, status_error)
        if status_error == "no":
            desc_check(vm_name, desc_str, options)

    # Prepare transient/persistent vm
    original_xml = vm.backup_xml()
    if persistent_vm == "no" and vm.is_persistent():
        vm.undefine()
    elif persistent_vm == "yes" and not vm.is_persistent():
        vm.define(original_xml)
    try:
        if vm.is_dead():
            vm.start()
        if domain == "ID":
            vm_name = vm.get_id()
        run_test()
        # Recvoer the vm and shutoff it
        if persistent_vm == "yes" and domain != "ID":
            vm.define(original_xml)
            vm.destroy()
            run_test()
    finally:
        vm.destroy(False)
        virsh.define(original_xml)
        os.remove(original_xml)
