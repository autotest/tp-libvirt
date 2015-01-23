import re
import logging
from autotest.client.shared import utils, error
from virttest import virsh
from virttest import libvirt_xml
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices import interface


def run(test, params, env):
    """
    Test update filter rules when domain is running.

    1) Prepare parameters.
    2) Add filter to domain interface.
    3) Start domain.
    4) Update filter rule and check
    5) Cleanup
    """
    # Prepare parameters
    filter_name = params.get("filter_name", "testcase")
    check_cmd = params.get("check_cmd")
    expect_match = params.get("expect_match")
    check_vm_cmd = params.get("check_vm_cmd")
    vm_expect_match = params.get("vm_expect_match")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    filterref_dict = {}
    filterref_dict['name'] = filter_name

    # backup vm xml
    vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    new_filter = libvirt_xml.NwfilterXML()
    filter_backup = new_filter.new_from_filter_dumpxml(filter_name)

    try:
        # Update first vm interface with filter
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml
        new_filterref = new_iface.new_filterref(**filterref_dict)
        new_iface.filterref = new_filterref
        logging.debug("new interface xml is: %s" % new_iface)
        vmxml.add_device(new_iface)
        vmxml.sync()

        # Start vm
        vm.start()
        session = vm.wait_for_login()
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        iface_target = iface_xml.target['dev']
        logging.debug("iface target dev name is %s", iface_target)

        # Update filter rule by nwfilter-define
        filterxml = utlv.create_nwfilter_xml(params)

        # Define filter xml
        virsh.nwfilter_define(filterxml.xml, debug=True)

        # Check ebtables on host after filter update
        if "DEVNAME" in check_cmd:
            check_cmd = check_cmd.replace("DEVNAME", iface_target)
        wait_func = lambda: not utils.system(check_cmd, ignore_status=True)
        ret = utils.wait_for(wait_func, timeout=30)
        if not ret:
            raise error.TestFail("Rum command '%s' failed" % check_cmd)
        out = utils.system_output(check_cmd, ignore_status=False)
        if expect_match and not re.search(expect_match, out):
            raise error.TestFail("'%s' not found in output: %s"
                                 % (expect_match, out))

        # Check in vm
        if check_vm_cmd:
            output = session.cmd_output(check_vm_cmd)
            logging.debug("cmd output: %s", output)
            if vm_expect_match and not re.search(vm_expect_match, output):
                raise error.TestFail("'%s' not found in output: %s"
                                     % (vm_expect_match, output))
    finally:
        # Clean env
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync()
        # Restore created filter
        virsh.nwfilter_undefine(filter_name, debug=True)
        virsh.nwfilter_define(filter_backup.xml, debug=True)
