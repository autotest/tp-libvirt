import aexpect
import os
import logging as log

from avocado.core import exceptions
from avocado.utils import process

from virttest import data_dir
from virttest import remote
from virttest import utils_misc
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def local_access(params, test):
    """
    Connect local libvirt daemon as a non-root user.

    :param params: dict of test parameters
    :param test: Avocado-VT test instance
    """
    virsh_uri = params.get("virsh_uri")
    auth_user = params.get("auth_user", "root")
    auth_pwd = params.get("auth_pwd")
    virsh_cmd = params.get("virsh_cmd", "list")
    read_only = params.get("read_only", "")
    vm_name = params.get("main_vm", "")
    extra_env = params.get("extra_env", "")
    su_user = params.get("su_user", "")
    message = params.get("message", "")
    virsh_patterns = params.get("patterns_virsh_cmd", r".*Id\s*Name\s*State\s*.*")
    patterns_extra_dict = params.get("patterns_extra_dict", None)
    log_level = params.get("log_level", "LIBVIRT_DEBUG=3")
    status_error = params.get("status_error", "yes")
    ret, output = libvirt.connect_libvirtd(virsh_uri, read_only, virsh_cmd, auth_user,
                                           auth_pwd, vm_name, status_error, extra_env,
                                           log_level, su_user, virsh_patterns,
                                           patterns_extra_dict)
    if message not in output:
        test.fail("Expected message: {} not found in the output".format(message))
    else:
        logging.info("The test has passed.")


def ssh_access(params, test):
    """
    Connect local libvirt daemon over SSH as a non-root user

    :param params: dict of test parameters
    :param test: Avocado-VT test instance
    """
    non_root_name = params.get("su_user")
    non_root_pass = params.get("su_user_pass", "toor")
    auth_pwd = params.get("auth_pwd")
    virsh_uri = params.get("virsh_uri")
    ssh_params = params.get("ssh_params", "")
    message = params.get("message", "")
    session = remote.remote_login("ssh", "localhost", "22", non_root_name,
                                  non_root_pass, r"[\#\$]\s*$",
                                  extra_cmdline=ssh_params)
    try:
        command = ("virsh -c {}".format(virsh_uri))
        session.sendline(command)
        match, output = session.read_until_output_matches([r".*[Pp]assword.*", ],
                                                          timeout=10.0,
                                                          print_func=logging.debug)
        logging.debug(output)
        if message not in output:
            test.fail("Expected message: {} not found in the output".
                      format(message))
        else:
            logging.info("The test has passed.")
        session.sendline(auth_pwd)
        session.read_until_output_matches(["#", ],
                                          timeout=10.0,
                                          print_func=logging.debug)
        session.sendline("quit")
    except Exception as e:
        test.error("The virsh connection over SSH was unsuccessful with "
                   "non-root user due to:{}".format(e))
    finally:
        session.close()
        logging.debug("The SSH session has been closed.")


def create_non_root_user(params, test):
    """
    Create a non-root user with a valid password on the local machine

    :param params: dict of test parameters
    :param test: Avocado-VT test instance
    """
    non_root_name = params.get("su_user")
    non_root_pass = params.get("su_user_pass", "toor")
    try:
        # Check if user exists
        ret = process.run("id -u {}".format(non_root_name), ignore_status=True)
        # User does not exists on system, we can add him
        if ret.exit_status:
            # Add a non-root user
            ret = process.run("useradd {}".format(non_root_name))
            if ret.exit_status:
                test.error("Creation of a non-root: {} user has failed.".
                           format(non_root_name))
            else:
                # Create a password for a new non-root user
                ret = process.run('echo {}:{} | chpasswd'.
                                  format(non_root_name, non_root_pass),
                                  shell=True)
                if ret.exit_status:
                    test.error("Cannot create a password:{} for non-root "
                               "user:{}.".format(non_root_pass, non_root_name))
        else:
            test.error("The user {} already exists on the system and therefore "
                       "it is not safe to continue.".format(non_root_name))
    except (exceptions.TestFail, exceptions.TestCancel):
        raise
    except Exception as e:
        test.error("Unexpected error: {}".format(e))


def get_tailed_log_file():
    """
    Tail output appended data as the file /var/log/messages grows

    :returns: the appended data tailed from /var/log/messages
    """
    tailed_log_file = os.path.join(data_dir.get_tmp_dir(), 'tail_log')
    tailed_messages = aexpect.Tail(command='tail -f /var/log/messages',
                                   output_func=utils_misc.log_line,
                                   output_params=(tailed_log_file))
    return tailed_log_file


def run(test, params, env):
    """
    Test virsh polkit for non-root user
    """
    ssh_connection = params.get("ssh_connection", "no") == "yes"
    non_root_name = params.get("su_user")
    status_error = "yes" == params.get("status_error", "no")

    create_non_root_user(params, test)
    tailed_log_file = get_tailed_log_file()
    try:
        if ssh_connection:
            ssh_access(params, test)
        else:
            local_access(params, test)
        if not status_error:
            libvirt.check_logfile('error :', tailed_log_file, str_in_log=False)
    except (exceptions.TestFail, exceptions.TestCancel):
        raise
    except Exception as e:
        test.error("Unexpected error: {}".format(e))
    finally:
        res = utils_misc.wait_for(lambda: not process.run("userdel {}".
                                                          format(non_root_name),
                                                          ignore_status=True).exit_status, 50)
        if not res:
            test.error("Cannot delete non-root user: {}.".format(non_root_name))
