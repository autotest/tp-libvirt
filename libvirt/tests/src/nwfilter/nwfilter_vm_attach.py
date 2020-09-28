import re
import logging

from avocado.utils import process
from avocado.utils import astring

from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices import interface


def run(test, params, env):
    """
    Test start domain with nwfilter rules.

    1) Prepare parameters.
    2) Prepare nwfilter rule and update domain interface to apply.
    3) Start domain and check rule.
    4) Clean env
    """
    # Prepare parameters
    filter_name = params.get("filter_name", "testcase")
    attach_option = params.get("attach_option", "")
    check_cmd = params.get("check_cmd")
    expect_match = params.get("expect_match")
    attach_twice_invalid = "yes" == params.get("attach_twice_invalid", "no")
    status_error = "yes" == params.get("status_error", "no")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Prepare vm filterref parameters dict list
    filterref_dict = {}
    filterref_dict['name'] = filter_name

    # Prepare interface parameters
    iface_type = 'network'
    iface_source = {'network': 'default'}
    iface_target = params.get("iface_target", 'vnet1')

    # backup vm xml
    vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    daemon_serv = utils_libvirtd.Libvirtd("virtqemud")
    try:
        # Prepare interface xml for attach
        new_iface = interface.Interface(type_name=iface_type)
        new_iface.source = iface_source
        new_iface.target = {'dev': iface_target}
        new_filterref = new_iface.new_filterref(**filterref_dict)
        new_iface.filterref = new_filterref
        new_iface.model = "virtio"
        logging.debug("new interface xml is: %s" % new_iface)

        # Attach interface to vm
        ret = virsh.attach_device(vm_name, new_iface.xml,
                                  flagstr=attach_option,
                                  debug=True,
                                  ignore_status=True)
        utlv.check_exit_status(ret, status_error)

        if attach_twice_invalid:
            ret = virsh.attach_device(vm_name, new_iface.xml,
                                      flagstr=attach_option,
                                      debug=True,
                                      ignore_status=True)
            utlv.check_exit_status(ret, status_error)

        if not daemon_serv.is_running():
            test.fail("daemon not running after attach "
                      "interface.")

        # Check iptables or ebtables on host
        if check_cmd:
            if "DEVNAME" in check_cmd:
                check_cmd = check_cmd.replace("DEVNAME", iface_target)
            ret = utils_misc.wait_for(lambda: not
                                      process.system(check_cmd,
                                                     ignore_status=True,
                                                     shell=True),
                                      timeout=30)
            if not ret:
                test.fail("Rum command '%s' failed" % check_cmd)
            out = astring.to_text(process.system_output(check_cmd, ignore_status=False, shell=True))
            if expect_match and not re.search(expect_match, out):
                test.fail("'%s' not found in output: %s"
                          % (expect_match, out))

    finally:
        if attach_twice_invalid:
            daemon_serv.restart()
        # Clean env
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync()
