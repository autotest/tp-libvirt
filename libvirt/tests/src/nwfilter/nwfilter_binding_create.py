import logging
import time


from virttest.libvirt_xml.devices import interface
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest import libvirt_xml
from virttest.libvirt_xml import nwfilter_binding
from virttest import utils_libvirtd


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
    status_error = "yes" == params.get("status_error")
    filter_name = params.get("filter_name", "clean-traffic")
    wait_time = params.get("wait_time", 1)
    # back up for recovery
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # prepare filter parameters dict
    filter_param_dict = []
    param_dict = {}
    logging.debug("wait_time is : %s" % wait_time)
    wait_time = float(wait_time)
    libvirtd = utils_libvirtd.Libvirtd()

    def prepare_env():
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]

        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml
        new_iface.type_name = "network"
        new_iface.source = {'network': "default", 'bridge': "virbr0"}
        alias_dict = {'name': "net0"}
        new_iface.alias = alias_dict
        target_dict = {'dev': "tar"}
        new_iface.target = target_dict
        logging.debug("new interface xml is : %s" % new_iface)
        vmxml.add_device(new_iface)
        vmxml.sync()
        return new_iface

    def create_binding_file(new_iface):
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        binding = nwfilter_binding.NwfilterBinding()
        binding.owner = binding.new_owner(vm_name, vmxml.uuid)
        binding.mac_address = new_iface.mac_address
        portdev = "tar"
        binding.portdev = portdev
        param_dict['name'] = "MAC"
        param_dict['value'] = new_iface.mac_address
        filter_param_dict.append(param_dict)
        filterrefs_dict = {}
        filterrefs_dict['name'] = filter_name
        filterrefs_dict['parameters'] = filter_param_dict
        binding.filterref = binding.new_filterref(**filterrefs_dict)
        logging.debug("filter binding xml is: %s" % binding)
        return binding

    try:
        new_iface = prepare_env()
        binding = create_binding_file(new_iface)
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

        utlv.check_cmd_output(check_cmd, expected_match, True)

        if not libvirtd.restart():
            virsh.nwfilter_binding_list(debug=True)
            test.fail("fail to restart libvirtd")

        ret = virsh.nwfilter_binding_list(debug=True)
        utlv.check_exit_status(ret, status_error)

        ret = virsh.nwfilter_binding_dumpxml("tar", debug=True)
        utlv.check_exit_status(ret, status_error)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # delete the created binding
        ret = virsh.nwfilter_binding_list(debug=True)
        if "tar" in ret.stdout_text:
            re = virsh.nwfilter_binding_delete("tar", debug=True)
            utlv.check_exit_status(re, status_error)
