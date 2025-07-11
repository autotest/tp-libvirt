import os
import time
import shutil
import subprocess
import logging as log

from avocado.utils import process
from glob import glob

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_process_exist(process_cmd, check_cmd):
    """
    Check if a cmd process exists in system processes.

    :param process_cmd: the process to check
    :param check_cmd: the cmd for how to check process
    :return: boolean value for exist or not
    """
    p_list = process.run(check_cmd, shell=True, ignore_status=True).stdout_text
    ret = True if process_cmd in p_list else False
    return ret


def run(test, params, env):
    """
    Test the swtpm_setup or 'swtpm_socket' cmds
    """
    swtpm_setup_cmd = params.get("swtpm_setup_cmd")
    swtpm_socket_cmd = params.get("swtpm_socket_cmd", "")
    qemu_cmd = params.get("qemu_cmd", "")
    extra_option = params.get("extra_option", "")
    co_cmd = params.get("co_cmd", "")
    unexpected_error = params.get("unexpected_error", "")
    tpm_state_dir = params.get("tpm_state_dir", "")
    p2 = None

    if extra_option:
        swtpm_socket_cmd += " " + extra_option
    if tpm_state_dir and not os.path.exists(tpm_state_dir):
        os.mkdir(tpm_state_dir)

    swtpm_check_cmd = "ps aux|grep 'swtpm socket'\
                      |grep -v avocado-runner-avocado-vt|grep -v grep"

    try:
        if co_cmd:
            logging.debug("Running '%s'", co_cmd)
            p_out = subprocess.Popen(co_cmd, shell=True,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE).stdout.read()
            logging.debug("The cocurrent swtpm_setup output \
                    including 'read' is:\n%s", p_out)
            if unexpected_error in str(p_out):
                test.fail(p_out)
        else:
            process.run(swtpm_setup_cmd, shell=True, ignore_status=False)
            process.run(swtpm_socket_cmd, shell=True, ignore_status=False)
            qemu_check_cmd = "ps aux|grep '/usr/libexec/qemu-kvm -tpmdev' \
                             |grep -v avocado-runner-avocado-v|grep -v grep"
            if qemu_cmd and check_process_exist(swtpm_socket_cmd,
                                                swtpm_check_cmd):
                p2 = subprocess.Popen(qemu_cmd, shell=True,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
                logging.debug("Running '%s'", qemu_cmd)
                time.sleep(3)
                check_process_exist(qemu_cmd, qemu_check_cmd)
                if p2.poll() is None:
                    logging.debug('Killing qemu cmd...')
                    p2.kill()
                    check_process_exist(qemu_cmd, qemu_check_cmd)
                swtpm_socket_exists = check_process_exist(swtpm_socket_cmd,
                                                          swtpm_check_cmd)
                if swtpm_socket_exists and '--terminate' in swtpm_socket_cmd:
                    test.fail("swtpm process should exit with '--terminate'\
                              when qemu is killed.")
                elif not swtpm_socket_exists and \
                        '--terminate' not in swtpm_socket_cmd:
                    test.fail("swtpm process should not exit\
                              without '--terminate' when qemu is killed.")

    finally:
        logging.debug('Cleaning env...')
        if check_process_exist(swtpm_socket_cmd, swtpm_check_cmd):
            process.run("pkill -9 swtpm", shell=True, ignore_status=True)
        if p2 and p2.poll() is None:
            p2.kill()
        if os.path.exists(tpm_state_dir):
            shutil.rmtree(tpm_state_dir)
        for folder in glob("/tmp/vtpm*"):
            shutil.rmtree(folder)
        process.run("rm -rf /var/lib/swtpm-localca/*", shell=True, ignore_status=True)
