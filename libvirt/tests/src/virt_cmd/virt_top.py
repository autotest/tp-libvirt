import os
import logging

from avocado.utils import path
from avocado.utils import process
from avocado.core import exceptions
from avocado.utils import software_manager

from virttest import virsh
from virttest import data_dir


def run(test, params, env):
    """
    Test for virt-top, it is a top like tool for virtual
    machine.
    """
    # Install virt-top package if missing.
    software_mgr = software_manager.SoftwareManager()
    if not software_mgr.check_installed('virt-top'):
        logging.info('Installing virt-top package:')
        software_mgr.install('virt-top')

    # Get the full path of virt-top command.
    try:
        VIRT_TOP = path.find_command("virt-top")
    except path.CmdNotFoundError as info:
        raise exceptions.TestSkipError("No virt-top command found - %s" % info)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    output = params.get("output_file", "output")
    output_path = os.path.join(data_dir.get_tmp_dir(), output)
    status_error = ("yes" == params.get("status_error", "no"))
    options = params.get("options", "")

    id_result = virsh.domid(vm_name)
    if id_result.exit_status:
        raise exceptions.TestError("Get domid failed.")
    domid = id_result.stdout.strip()

    if "--stream" in options:
        cmd = "%s %s 1>%s" % (VIRT_TOP, options, output_path)
    else:
        cmd = "%s %s" % (VIRT_TOP, options)
    # Add a timeout command to end it automatically.
    cmd = "timeout 10 %s" % cmd
    cmd_result = process.run(cmd, ignore_status=True, shell=True)

    if not status_error:
        # Read and analyse the output of virt-top.
        success = False
        with open(output_path) as output_file:
            lines = output_file.readlines()
        for line in lines:
            if line.count(vm_name):
                sub_string = line.split()
                if domid == sub_string[0].strip():
                    success = True
                    break
                else:
                    continue
            else:
                continue
        if not success:
            raise exceptions.TestFail("Command virt-top exit successfully, but"
                                      "domid is expected")
    else:
        if cmd_result.exit_status != 2:
            raise exceptions.TestFail("Command virt-top exit successfully with"
                                      "invalid option:%s" % cmd_result.stdout_text)
