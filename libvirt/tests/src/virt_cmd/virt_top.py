import os

from autotest.client import os_dep, utils
from autotest.client.shared import error

from virttest import common, virsh, data_dir


def run(test, params, env):
    """
    Test for virt-top, it is a top like tool for virtual
    machine.
    """
    # Get the full path of virt-top command.
    try:
        VIRT_TOP = os_dep.command("virt-top")
    except ValueError:
        raise error.TestNAError("No virt-top command found.")

    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)
    output = params.get("output_file", "output")
    output_path = os.path.join(data_dir.get_tmp_dir(), output)

    id_result = virsh.domid(vm_name)
    if id_result.exit_status:
        raise error.TestNAError("Get domid failed.")
    domid = id_result.stdout.strip()

    cmd = "%s --stream 1>%s" % (VIRT_TOP, output_path)
    # Add a timeout command to end it automatically.
    cmd = "timeout 10 %s" % cmd
    cmd_result = utils.run(cmd, ignore_status=True)
    # Read and analyse the output of virt-top.
    success = False
    output_file = open(output_path)
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
        raise error.TestFail("Command virt-top exit successfully, but "
                             "domid is expected")
