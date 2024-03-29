import logging as log
from xml.dom.minidom import parseString

from avocado.utils import process

from virttest import virsh
from virttest import utils_libvirtd
from virttest import libvirt_version

logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh domifstat.

    The command can get network interface stats for a running domain.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh domifstat operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def get_interface(guest_name):
        """
        Get interface device of VM.

        :param guest_name: VM's name.
        :return: interface device of VM.
        """
        interface = ""
        domxml = process.run("virsh dumpxml %s" % guest_name, shell=True).stdout_text
        dom = parseString(domxml)
        root = dom.documentElement
        array = root.getElementsByTagName("interface")
        for element in array:
            if element.getAttribute("type") == "bridge" or \
               element.getAttribute("type") == "network":
                interface = "vnet0"
                nodelist = element.childNodes
                for node in nodelist:
                    if node.nodeName == "target":
                        interface = node.getAttribute("dev")
                        break
        return interface

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    vm_ref = params.get("domifstat_vm_ref", vm_name)
    nic_ref = params.get("domifstat_nic_ref", "")
    libvirtd = params.get("libvirtd", "on")
    status_error = params.get("status_error", "no")

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref == "name":
        vm_ref = vm_name

    interface = get_interface(vm_name)
    if nic_ref == "":
        interface = ""
    elif nic_ref == "error_interface":
        interface = params.get(nic_ref)
    interface = "%s %s" % (interface, params.get("domifstat_extra"))

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    status = virsh.domifstat(vm_ref, interface, ignore_status=True).exit_status

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # check status_error
    if status_error == "yes":
        if status == 0:
            if libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is "
                             "restarted and command should succeed")
            else:
                test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
