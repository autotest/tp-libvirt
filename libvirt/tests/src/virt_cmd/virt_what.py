
def run(test, params, env):
    """
    Test for virt-what, it should be executed in guest
    and tell user which typervisor it is using.

    (1). Login guest & execute virt-waht command.
    (2). Check the result, lxc or kvm.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    expect_output = params.get("fact")

    if not vm.is_alive():
        vm.start()
    session = vm.wait_for_login()

    # Check whether the command of virt-what is available
    # on guest.
    status = session.cmd_status("which virt-what")
    if status:
        # Skip if virt-what is not available on guest.
        test.cancel("No virt-what command in guest.")

    # Execute virt-what on guest.
    output = session.cmd_output("virt-what").strip()

    if not output == expect_output:
        test.fail("Output of virt-what in guest is <%s>,"
                  "but we expect <%s>.\n" % (output, expect_output))
