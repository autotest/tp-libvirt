import os
import time
import shutil
import subprocess

from avocado.utils import process
from glob import glob


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
    case = params.get('case', '')

    if extra_option:
        swtpm_socket_cmd += " " + extra_option
    if tpm_state_dir and not os.path.exists(tpm_state_dir):
        os.mkdir(tpm_state_dir)
    p1 = None

    try:
        if co_cmd:
            co_output = process.run(co_cmd, shell=True, ignore_status=False).stdout_text
            if unexpected_error in co_output:
                test.fail(co_output)
        else:
            process.run(swtpm_setup_cmd, shell=True, ignore_status=False)
            if case == 'print_caps':
                process.run(swtpm_socket_cmd, shell=True, ignore_status=False)
            else:
                p1 = subprocess.Popen(swtpm_socket_cmd, shell=True, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
            if qemu_cmd and p1.poll() is None:
                p2 = subprocess.Popen(qemu_cmd, shell=True, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
                time.sleep(3)
                if p2.poll() is None:
                    p2.kill()
                if p1.poll() is None and '--terminate' in swtpm_socket_cmd:
                    test.fail("swtpm process should exit with '--terminate' when qemu is killed.")
                elif p1.poll() is not None and '--terminate' not in swtpm_socket_cmd:
                    test.fail("swtpm process should not exit without '--terminate' when qemu is killed.")

    finally:
        if p1.poll() is None:
            p1.kill()
        if os.path.exists(tpm_state_dir):
            os.remove(tpm_state_dir)
        for folder in glob("/var/vtpm*"):
            shutil.rmtree(folder)
