import logging

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_misc import cmd_status_output

LOG = logging.getLogger('avocado.' + __name__)


def virt_install(vm_name, install_tree, extra_args):
    """
    Runs virt-install with specific kernel command line
    It returns immediately, reporting the status

    :param vm_name: guest name
    :param install_tree: the installation tree url
    :param extra_args: extra arguments for kernel command line
    """
    cmd = ("virt-install --name %s"
           " --disk none"
           " --vcpus 2 --memory 2048"
           " --nographics --noautoconsole"
           " --location %s --extra-args '%s'" %
           (vm_name, install_tree, extra_args))
    status, out = cmd_status_output(cmd, shell=True, verbose=True)
    LOG.debug("Command output: %s" % out)
    return status


def run(test, params, env):
    """
    Confirm that the 896 byte restriction for kernel
    command lines has been lifted for newer kernels
    and older kernels are handled correctly.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)

    location = params.get("location")
    expected_status = params.get("expected_status")
    extra_args = "a"*1000
    LOG.debug("Command line will have at least %s bytes length." % len(bytes(extra_args, 'utf-8')))

    try:

        vm.undefine()
        status = virt_install(vm_name, location, extra_args)

        if status != expected_status:
            test.fail("The installation didn't exit as expected."
                      " Expected: %s, actual: %s" % (expected_status, status))

    finally:
        vmxml.sync()
