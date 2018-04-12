import logging
import aexpect

from avocado.utils import process
from avocado.core import exceptions

from virttest import virt_admin
from virttest import element_tree


def sh_escape(sh_str):
    """
    Return escaped shell string in virt-admin  interactive mode.

    :param sh_str: the string need process with escape
    :return: the escaped string
    """
    if not sh_str:
        return ''
    else:
        if '\'' in sh_str:
            escaped_str = "'" + sh_str.replace("'", "'\\''") + "'"
        else:
            escaped_str = sh_str
        logging.debug("The escaped shell string is: %s", escaped_str)
        return escaped_str


def check_echo_shell(escaped_str, result):
    """
    Run echo with escaped shell string, return True if output
    match with virt-admin result, else return False.

    :param escaped_str: escaped shell string
    :param result: virt-admin echo output with the escaped string
    :return: True or False due to match of the output
    """
    cmd = "echo %s" % escaped_str
    cmd_result = process.run(cmd, ignore_status=True, shell=True)
    output = cmd_result.stdout_text.strip()
    logging.debug("Shell echo result is: %s", output)
    return (output == result)


def run(test, params, env):
    """
    Test virt-admin itself group commands, which exclude connect and help,
    in interactive mode.
    Virsh itself (help keyword 'virt-admin'):
       cd                             change the current directory
       echo                           echo arguments
       exit                           quit this interactive terminal
       pwd                            print the current directory
       quit                           quit this interactive terminal
    """
    # Get parameters for test
    cd_option = params.get("cd_option", "/")
    invalid_cmd = params.get("invalid_cmd", " ")
    cd_extra = params.get("cd_extra", "")
    pwd_extra = params.get("pwd_extra", "")
    echo_extra = params.get("echo_extra", "")
    exit_cmd = params.get("exit_cmd", "exit")
    echo_option = params.get("echo_option", "")
    echo_str = params.get("echo_str", "xyz")
    invalid_status_error = params.get("invalid_status_error", "no")
    cd_status_error = params.get("cd_status_error", "no")
    pwd_status_error = params.get("pwd_status_error", "no")
    echo_status_error = params.get("echo_status_error", "no")

    # Run virtadmin command in interactive mode
    vp = virt_admin.VirtadminPersistent()

    # Run invalid command
    result = vp.command(invalid_cmd, ignore_status=True, debug=True)
    status = result.exit_status
    if invalid_status_error == "yes":
        if status == 0:
            raise exceptions.TestFail("Run successful with wrong command!")
        else:
            logging.info("Run command failed as expected.")
    else:
        if status != 0:
            raise exceptions.TestFail("Run failed with right command!\n%s", result)

    # Run cd command
    result = vp.cd(cd_option, cd_extra, ignore_status=True, debug=True)
    cd_status = result.exit_status
    if cd_status_error == "yes":
        if cd_status == 0:
            raise exceptions.TestFail("Run successful with wrong command!")
        else:
            logging.info("Run command failed as expected.")
    else:
        if cd_status != 0:
            raise exceptions.TestFail("Run failed with right command!\n%s", result)

    # Run pwd command
    result = vp.pwd(pwd_extra, ignore_status=True, debug=True)
    status = result.exit_status
    output = result.stdout.strip()
    if pwd_status_error == "yes":
        if status == 0:
            raise exceptions.TestFail("Run successful with wrong command!")
        else:
            logging.info("Run command failed as expected.")
    else:
        if status != 0:
            raise exceptions.TestFail("Run failed with right command!\n%s", result)
        elif cd_option and cd_status == 0:
            if output != cd_option:
                raise exceptions.TestFail("The pwd is not right with set!")

    # Run echo command
    options = "%s %s" % (echo_option, echo_extra)
    result = vp.echo(echo_str, options, ignore_status=True, debug=True)
    status = result.exit_status
    output = result.stdout.strip()
    if echo_status_error == "yes":
        if status == 0:
            raise exceptions.TestFail("Run successful with wrong command!")
        else:
            logging.info("Run command failed as expected.")
    else:
        if status != 0:
            raise exceptions.TestFail("Run failed with right command!\n%s", result)
        elif "--xml" in echo_option:
            escape_out = element_tree._escape_attrib(echo_str)
            if escape_out != output:
                raise exceptions.TestFail("%s did not match with expected output %s"
                                          % (output, escape_out))
        else:
            escaped_str = sh_escape(echo_str)
            if not check_echo_shell(escaped_str, output):
                raise exceptions.TestFail("Command output is not expected.")

    # Run exit command and close the session
    try:
        if 'exit' in exit_cmd:
            vp.exit(ignore_status=True, debug=True)
        elif 'quit' in exit_cmd:
            vp.quit(ignore_status=True, debug=True)
    except aexpect.ShellProcessTerminatedError:
        logging.debug("Exit virt-admin session successfully.")
