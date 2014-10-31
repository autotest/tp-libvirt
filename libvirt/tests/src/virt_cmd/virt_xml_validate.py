import os

from autotest.client import os_dep, utils
from autotest.client.shared import error

from virttest import common, virsh, data_dir


def run(test, params, env):
    """
    Test for virt-xml-validate
    """
    # Get the full path of virt-xml-validate command.
    try:
        VIRT_XML_VALIDATE = os_dep.command("virt-xml-validate")
    except ValueError:
        raise error.TestNAError("Not find virt-xml-validate command on host.")

    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)
    schema = params.get("schema", "domain")
    output = params.get("output_file", "output")
    output_path = os.path.join(data_dir.get_tmp_dir(), output)

    if schema == "domain":
        virsh.dumpxml(vm_name, to_file=output_path)
    # TODO Add more case for other schema.

    cmd = "%s %s %s" % (VIRT_XML_VALIDATE, output_path, schema)
    cmd_result = utils.run(cmd, ignore_status=True)
    if cmd_result.exit_status:
        raise error.TestFail("virt-xml-validate command failed.\n"
                             "Detail: %s." % cmd_result)

    if cmd_result.stdout.count("fail"):
        raise error.TestFail("xml fails to validate\n"
                             "Detail: %s." % cmd_result)
