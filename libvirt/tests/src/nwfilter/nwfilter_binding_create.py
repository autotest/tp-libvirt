import logging
import re
import time

from avocado.utils import process
from virttest import utils_misc
from virttest.libvirt_xml.devices import interface
from virttest import virsh
from virttest import libvirt_xml
from virttest.compat_52lts import decode_to_text as to_text
from virttest.libvirt_xml import nwfilter_binding


def run(test, params, env):
    """
    Test virsh nwfilter-binding-create
    1)start a vm with interface
    2)perpare the building xml
    3)create binding
    4)check ebtables rule is added
    5)restart libvirtd and check the filter still there
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    check_cmd = params.get("check_cmd")
    expected_match = params.get("expected_match")
    cmd_restart = params.get("cmd_restart")
    filter_name = params.get("filter_name", "clean-traffic")
    wait_time = float(params.get("wait_time", 1))
    # back up for recovery
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # prepare filter parameters dict
    filter_param_dict = []
    param_dict = {}
    logging.debug("wait_time is : %s" % wait_time)

    try:
        # update vm interface
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]

        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml
        alias_dict = {'name': "net0"}
        new_iface.alias = alias_dict
        target_dict = {'dev': "vnet0"}
        new_iface.target = target_dict
        logging.debug("new interface xml is : %s" % new_iface)
        vmxml.add_device(new_iface)
        vmxml.sync()

        # create binding file
        binding = nwfilter_binding.NwfilterBinding()
        binding.owner = binding.new_owner(vm_name, vmxml.uuid)
        binding.mac_address = new_iface.mac_address
        portdev = "vnet0"
        binding.portdev = portdev
        param_dict['name'] = "MAC"
        param_dict['value'] = new_iface.mac_address
        filter_param_dict.append(param_dict)
        filterrefs_dict = {}
        filterrefs_dict['name'] = filter_name
        filterrefs_dict['parameters'] = filter_param_dict
        binding.filterref = binding.new_filterref(**filterrefs_dict)
        logging.debug("filter binding xml is: %s" % binding)

        # binding xml
        vm.start()
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        iface_target = iface_xml.target['dev']
        logging.debug("iface target dev name is  %s" % iface_target)
        virsh.nwfilter_binding_create(binding.xml, debug=True)

        # check ebtables rule is add
        # wait_for nwfilter-binding-create command exec finish
        time.sleep(wait_time)
        ret = utils_misc.wait_for(lambda: not
                                  process.system(check_cmd,
                                                 ignore_status=False,
                                                 shell=True),
                                  timeout=30)
        if not ret:
            test.fail("Run command '%s' failed" % check_cmd)

        out = to_text(process.system_output(check_cmd,
                                            ignore_status=False,
                                            shell=True))

        if expected_match and not re.search(expected_match, out):
            test.fail(" '%s' not found in output: %s"
                      % (expected_match, out))

        cmd_res = process.run(cmd_restart, shell=True)
        if cmd_res.exit_status:
            test.fail("fail to restart libvirtd")

        virsh.nwfilter_binding_list(debug=True)
        virsh.nwfilter_binding_dumpxml("vnet0", debug=True)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
