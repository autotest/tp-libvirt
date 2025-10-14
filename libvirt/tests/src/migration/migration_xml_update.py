import logging as log
import tempfile
from xml.etree import ElementTree

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps

logging = log.getLogger('avocado.' + __name__)


def prepare_vm(vm, vm_name, vmxml, params):
    """
    Prepares the VM by adding the device under test

    :param vm: The VM object
    :param vm_name: The VM name
    :param vmxml: The VM XML
    :param params: The test parameters
    """

    if vm.is_alive():
        vm.destroy()
    dev_type = params.get("remove_all")
    vmxml.remove_all_device_by_type(dev_type)
    vmxml.sync()
    dev_dir = eval(params.get("add_device"))
    dev = libvirt_vmxml.create_vm_device_by_type(dev_type, dev_dir)
    virsh.attach_device(vm_name, dev.xml, flagstr="--config", debug="True")


def create_migration_xml(test, vm_name, params):
    """
    Creates the xml to be passed to '--xml' option

    :param test: The test object
    :param vm_name: The VM name
    :param params: The Test parameters
    """
    tmp_file = xml_file = tempfile.mktemp(dir=data_dir.get_tmp_dir())
    params['virsh_migrate_extra'] = "--xml %s" % tmp_file
    xmlout = virsh.dumpxml(vm_name, "--migratable", debug=True).stdout_text.strip()
    vmxml = ElementTree.fromstring(xmlout)
    modify = params.get("modify")
    dev = vmxml.find(".//" + params.get("remove_all"))
    if modify == "alias":
        alias = ElementTree.SubElement(dev, "alias")
        alias.attrib['name'] = "ua-otherName"
    elif modify == "address":
        new_address = ElementTree.fromstring(params.get("new_address"))
        old_address = dev.find("address")
        dev.remove(old_address)
        dev.append(new_address)
    elif modify == "mtu":
        # Update MTU size for interface element
        mtu_element = dev.find("mtu")
        mtu_element.attrib['size'] = params.get("updated_mtu")

    ElementTree.ElementTree(vmxml).write(tmp_file)


def check_alias(vm_name, params, test):
    """
    Checks if the alias on the destination is as set
    in migration xml.

    :param vm_name: The VM name
    :param params: The test parameters
    :param test: The test object
    """
    dest_xml = virsh.dumpxml(vm_name, uri=params.get("virsh_migrate_desturi")).stdout_text
    if "ua-otherName" not in dest_xml:
        test.fail("Expected rng alias 'ua-otherName' not found in "
                  "destination xml: %s" % dest_xml)


def run(test, params, env):
    """
    Tests migration behavior when the destination xml
    is altered for a device.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    prepare_vm(vm, vm_name, vmxml, params)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        create_migration_xml(test, vm_name, params)
        migration_obj.run_migration()
        if params.get("modify") == "alias":
            check_alias(vm_name, params, test)
    finally:
        migration_obj.cleanup_connection()
        backup_xml.sync()
