"""
Test cases for: virsh await

virsh await <domain> --condition <string> [--timeout <number>]

Supported conditions:
  domain-inactive        - wait until the domain is no longer running
  guest-agent-available  - wait until the QEMU Guest Agent is reachable

Exit codes observed on libvirt 12.3.0:
  0   condition was met (or already met on entry)
  1   bad arguments (missing --condition, unknown condition, unknown domain)
  2   event loop timed out before condition was met
"""

import logging as log

from virttest import virsh
from virttest import utils_misc
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)

# Minimum sensible timeout accepted by virsh await (0 is rejected as
# "out of range"; 1 is the smallest valid value).
AWAIT_TIMEOUT = 10
# Generous wait budget used when we expect the condition to be met quickly
# (e.g. VM is already shutoff).
ALREADY_MET_TIMEOUT = 5


def _await(vm_ref, condition, timeout=AWAIT_TIMEOUT, **dargs):
    """
    Thin wrapper so callers don't have to build the option string manually.

    :param vm_ref: domain name, id, or uuid
    :param condition: await condition string
    :param timeout: --timeout value passed to virsh await
    :param dargs: extra kwargs forwarded to virsh.command (ignore_status, debug…)
    :return: CmdResult
    """
    opts = "--condition %s --timeout %d" % (condition, timeout)
    return virsh.command("await %s %s" % (vm_ref, opts), **dargs)


def run(test, params, env):
    """
    Test command: virsh await.

    Test strategy
    =============
    The test is parameterised through the Cartesian config.  The key axes are:

    condition (await_condition):
        domain-inactive        – exercised via start → shutdown → await
        guest-agent-available  – exercised on a running VM that has/lacks a
                                 QEMU Guest Agent channel

    vm_ref type (await_vm_ref):
        name, id, uuid, hex_id, invalid, empty

    error scenarios (status_error = yes):
        missing_condition     – omit --condition entirely
        unsupported_condition – pass an unknown condition string
        invalid_domain        – reference a non-existent domain
        timeout_expire        – await a running VM for domain-inactive with a
                                short timeout so it expires (exit 2)
        already_shutoff       – await domain-inactive on a VM that is already
                                shut off; expect immediate success (exit 0)

    Steps
    -----
    1. Back up the VM XML.
    2. Bring the VM to the required pre-condition state.
    3. Optionally trigger the event (e.g. issue virsh shutdown).
    4. Run virsh await and capture the result.
    5. Check exit status and, for success paths, verify the VM reached the
       expected state.
    6. Restore the VM to its original state.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    await_condition = params.get("await_condition", "domain-inactive")
    await_vm_ref = params.get("await_vm_ref", "name")
    await_pre_state = params.get("await_pre_state", "running")
    await_trigger = params.get("await_trigger", "none")
    await_timeout = int(params.get("await_timeout", str(AWAIT_TIMEOUT)))
    omit_condition = "yes" == params.get("await_omit_condition", "no")
    status_error = "yes" == params.get("status_error", "no")
    expect_exit = int(params.get("await_expect_exit", "0"))
    need_agent = "yes" == params.get("await_need_agent", "no")

    # Libvirt acl test related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get("unprivileged_user")
    if unprivileged_user:
        if unprivileged_user.count("EXAMPLE"):
            unprivileged_user = "testacl"

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get("setup_libvirt_polkit") == "yes":
            test.cancel("API acl test not supported in current libvirt version.")

    # virsh await first appeared in libvirt 10.0; cancel gracefully on older builds.
    if not libvirt_version.version_compare(10, 0, 0):
        test.cancel("virsh await requires libvirt >= 10.0.0")

    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # ------------------------------------------------------------------ #
        # 1. Prepare VM state
        # ------------------------------------------------------------------ #
        if await_pre_state == "running":
            if not vm.is_alive():
                vm.start()
                vm.wait_for_login().close()
        elif await_pre_state == "shutoff":
            if vm.is_alive():
                vm.destroy()
                vm.wait_for_shutdown()

        if need_agent:
            vm.prepare_guest_agent(channel=True, start=True)
        elif await_condition == "guest-agent-available":
            # Explicitly remove the agent channel and stop the service so the
            # negative paths are not silently satisfied by a pre-installed agent
            # in the base image.
            vm.prepare_guest_agent(channel=False, start=False)

        # ------------------------------------------------------------------ #
        # 2. Resolve vm_ref
        # ------------------------------------------------------------------ #
        domid = vm.get_id() if vm.is_alive() else ""
        domuuid = vm.get_uuid()

        if await_vm_ref == "name":
            vm_ref = vm_name
        elif await_vm_ref == "id":
            vm_ref = domid
        elif await_vm_ref == "uuid":
            vm_ref = domuuid
        elif await_vm_ref == "hex_id":
            if not domid:
                test.error("await_vm_ref=hex_id requires a running domain "
                           "but domid is empty (VM is not running).")
            vm_ref = hex(int(domid))
        elif await_vm_ref == "invalid_domain":
            vm_ref = "totally_nonexistent_domain_99999"
        elif await_vm_ref == "empty":
            vm_ref = ""
        else:
            vm_ref = vm_name

        # ------------------------------------------------------------------ #
        # 3. Optionally trigger the awaited event in a background thread so
        #    virsh await can observe it while waiting.
        # ------------------------------------------------------------------ #
        if await_trigger == "shutdown":
            # Issue a graceful shutdown; the await call below will observe it.
            virsh.shutdown(vm_name, ignore_status=True, debug=True)
        elif await_trigger == "destroy":
            # Force-stop the VM; await must observe the immediate transition
            # to inactive (distinct from already_shutoff: VM is running first).
            virsh.destroy(vm_name, ignore_status=True, debug=True)

        # ------------------------------------------------------------------ #
        # 4. Build and run the virsh await command
        # ------------------------------------------------------------------ #
        if omit_condition:
            # Test: missing required --condition flag
            result = virsh.command(
                "await %s" % vm_ref,
                ignore_status=True, debug=True,
                unprivileged_user=unprivileged_user,
                uri=uri,
            )
        else:
            result = _await(
                vm_ref, await_condition,
                timeout=await_timeout,
                ignore_status=True, debug=True,
                unprivileged_user=unprivileged_user,
                uri=uri,
            )

        logging.debug("virsh await result: %s", result)

        # ------------------------------------------------------------------ #
        # 5. Validate result
        # ------------------------------------------------------------------ #
        if status_error:
            # Error path: we expect a non-zero exit
            if result.exit_status == 0:
                test.fail(
                    "Expected virsh await to fail but it succeeded.\n"
                    "stdout: %s\nstderr: %s" % (result.stdout_text, result.stderr_text)
                )
            if expect_exit and result.exit_status != expect_exit:
                test.fail(
                    "Expected exit code %d, got %d.\nstderr: %s"
                    % (expect_exit, result.exit_status, result.stderr_text)
                )
        else:
            # Success path: exit code must be 0
            if result.exit_status != 0:
                test.fail(
                    "virsh await failed unexpectedly (exit %d).\n"
                    "stdout: %s\nstderr: %s"
                    % (result.exit_status, result.stdout_text, result.stderr_text)
                )

            # For domain-inactive: confirm the VM is no longer running.
            if await_condition == "domain-inactive":
                if not utils_misc.wait_for(lambda: not vm.is_alive(), 5, step=1):
                    test.fail(
                        "virsh await returned 0 for domain-inactive but "
                        "VM '%s' is still running." % vm_name
                    )

            # For guest-agent-available: confirm the agent is reachable.
            if await_condition == "guest-agent-available":
                agent_result = virsh.qemu_agent_command(
                    vm_name, '{"execute":"guest-ping"}',
                    ignore_status=True, debug=True,
                )
                if agent_result.exit_status != 0:
                    test.fail(
                        "virsh await returned 0 for guest-agent-available "
                        "but guest agent is not responding."
                    )

    finally:
        # ------------------------------------------------------------------ #
        # 6. Restore environment
        # ------------------------------------------------------------------ #
        if vm.is_alive():
            vm.destroy()
        xml_backup.sync()
