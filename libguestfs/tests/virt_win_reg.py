import os
import logging

from avocado.utils import path
from avocado.utils import process

from virttest import data_dir


def run(test, params, env):
    r"""
    Test for virt-win-reg.

    (1).Get parameters from params.
    (2).Build the full command of virt-win-reg.
    (3).Login vm to get a session.
    (4).Prepare for test.
    (5).Run virt-win-reg command.
            Command virt-win-reg is used to export and merge Windows Registry
        entries from a Windows guest. We can do add/remove/modify/query with
        it.

        Example:
        * add:
            Make sure there is no value named AddTest in
            [HKLM\SYSTEM\ControlSet001\Control\ComputerName\ComputerName]
            # cat reg_file.reg
            [HKLM\SYSTEM\ControlSet001\Control\ComputerName\ComputerName]
            "AddTest" = "VIRTTEST"
            # virt-win-reg Guestname/disk --merge reg_file.reg
        * remove:
            # cat reg_file.reg
            [HKLM\SYSTEM\ControlSet001\Control\ComputerName\ComputerName]
            "ComputerName" = -
            # virt-win-reg Guestname/disk --merge reg_file.reg
        * modify:
            # cat reg_file.reg
            [HKLM\SYSTEM\ControlSet001\Control\ComputerName\ComputerName]
            "ComputerName" = "VIRTTEST_v2"
            # virt-win-reg Guestname/disk --merge reg_file.reg
        * query:
            # virt-win-reg domname
              'HKLM\SYSTEM\ControlSet001\Control\ComputerName\ComputerName'
              ComputerName

    (6).Verify the result.
    (7).Clean up.
    """
    try:
        virt_win_reg_exec = path.command("virt-win-reg")
    except ValueError:
        test.cancel("Not find virt-win-reg command.")
    # Get parameters.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Get parameters for remote.
    remote_yes = (params.get("virt_win_reg_remote", "no") == "yes")
    remote_uri = params.get("virt_win_reg_remote_uri", "ENTER.YOUR.REMOTE")
    if remote_yes and remote_uri.count("ENTER"):
        test.cancel("Remote Test is skipped.")

    # Get parameters about reg value.
    computer_name = params.get("virt_win_reg_computer_name")
    computer_name_v2 = params.get("virt_win_reg_computer_name_v2")
    key_path = params.get("virt_win_reg_key_path")
    value_name = params.get("virt_win_reg_value_name")

    # Get vm_ref.
    vm_ref = params.get("virt_win_reg_vm_ref")
    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "image_name":
        disks = vm.get_disk_devices()
        vm_ref = list(disks.values())[0]['source']

    # Get information about operations.
    operation = params.get("virt_win_reg_operation")
    virt_win_reg_cmd = params.get("virt_win_reg_cmd")
    prepare_reg_cmd = params.get("prepare_reg_cmd")
    verify_reg_cmd = params.get("verify_reg_cmd")
    if not (virt_win_reg_cmd and prepare_reg_cmd and verify_reg_cmd):
        test.cancel("Missing command for virt_win_reg or"
                    " cmd in guest to check result.")

    # Build a command.
    command = virt_win_reg_exec
    if remote_yes:
        command += " -c %s" % remote_uri
        command += " %s" % vm_name
    else:
        command += " %s" % vm_ref
    command += " %s" % virt_win_reg_cmd

    reg_file = None
    if not operation == "query":
        # Prepare a file for virt-win-reg --merge
        lines = []
        lines.append("[%s]\n" % key_path)
        if operation == "add":
            lines.append("\"%s\"=\"%s\"" % (value_name, computer_name))
        elif operation == "remove":
            lines.append("\"%s\"=-" % (value_name))
        elif operation == "modify":
            lines.append("\"%s\"=\"%s\"" % (value_name, computer_name_v2))
        with open(os.path.join(data_dir.get_tmp_dir(), "merge.reg"), "w") as reg_file:
            reg_file.writelines(lines)

        command += " %s" % reg_file.name

    session = vm.wait_for_login()

    try:
        status, output = session.cmd_status_output(prepare_reg_cmd)
        if status:
            logging.debug("Preparation is already done.")

        vm.destroy()
        result = process.run(command, ignore_status=True, shell=True)
        if result.exit_status:
            test.fail(result)

        output_virt_win_reg = result.stdout_text.strip()

        if not vm.is_alive():
            vm.start()
            session = vm.wait_for_login()
        status, output = session.cmd_status_output(verify_reg_cmd)
        if operation == "query":
            output_in_guest = output.split()[-1].strip()
            if not output_in_guest == output_virt_win_reg:
                test.fail("Informations are not equal from "
                          "virt_win_reg and from cmd in guest.\n"
                          "virt_win_reg: %s\n"
                          "cmd_in_guest: %s" %
                          (output_virt_win_reg, output_in_guest))
        elif operation == "remove":
            if not status:
                test.fail("Get the value of computer %s in remove"
                          " test.\nSo it means the virt-win-reg"
                          "to remove it failed." % output)
        elif operation == "modify":
            output_in_guest = output.split()[-1].strip()
            if not output_in_guest == computer_name_v2:
                test.fail("Modify test failed. The value of"
                          "computer after virt-win-reg is %s."
                          "But our expected value is %s." %
                          (output_in_guest, computer_name_v2))
        elif operation == "add":
            if status:
                test.fail("Add test failed. Get the computer_name"
                          "failed after virt-win-reg command."
                          "Detail: %s." % output)
    finally:
        # Clean up.
        session.close()
        # remove temp file.
        if reg_file and os.path.exists(reg_file.name):
            os.remove(reg_file.name)
