import re

from virttest import virsh
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test the command virsh freecell

    (1) Call virsh freecell
    (2) Call virsh freecell --all
    (3) Call virsh freecell with a numeric argument
    (4) Call virsh freecell xyz
    (5) Call virsh freecell with libvirtd service stop
    """

    args = params.get("virsh_freecell_args")
    option = params.get("virsh_freecell_opts")

    # Prepare libvirtd service
    check_libvirtd = "libvirtd" in params
    if check_libvirtd:
        libvirtd = params.get("libvirtd")
        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

    # Run test case
    cmd_result = virsh.freecell(cellno=args, options=option)
    output = cmd_result.stdout.strip()
    status = cmd_result.exit_status

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check the output
    if virsh.has_help_command('numatune'):
        OLD_LIBVIRT = False
    else:
        OLD_LIBVIRT = True
        if option == '--all':
            test.cancel("Older libvirt virsh freecell "
                        "doesn't support --all option")

    def output_check(freecell_output):
        if not re.search("ki?B", freecell_output, re.IGNORECASE):
            test.fail(
                "virsh freecell output invalid: " + freecell_output)

    # Check status_error
    status_error = params.get("status_error")
    if status_error == "yes":
        if status == 0:
            if libvirtd == "off":
                test.fail("Command 'virsh freecell' succeeded "
                          "with libvirtd service stopped, incorrect")
            else:
                # newer libvirt
                if not OLD_LIBVIRT:
                    test.fail("Command 'virsh freecell %s' succeeded"
                              "(incorrect command)" % option)
                else:  # older libvirt
                    test.cancel('Older libvirt virsh freecell '
                                'incorrectly processes extranious'
                                'command-line options')
    elif status_error == "no":
        output_check(output)
        if status != 0:
            test.fail("Command 'virsh freecell %s' failed "
                      "(correct command)" % option)
