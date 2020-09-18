import logging
import os

from virttest import virsh
from virttest import libvirt_xml
from virttest import data_dir
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices import interface

from avocado.utils import process


def run(test, params, env):
    """
    Test virsh nwfilter-binding-list

    1)Prepare parameters
    2)Run nwfilter_binding_list command
    3)check result
    4)Clean env
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    new_filter_1 = params.get("newfilter_1")
    new_filter_2 = params.get("newfilter_2")
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    new_net0_xml = os.path.join(data_dir.get_tmp_dir(), "new_net0.xml")
    new_net1_xml = os.path.join(data_dir.get_tmp_dir(), "new_net1.xml")
    option = params.get("option")
    status_error = "yes" == params.get("status_error")
    alias_name = params.get("alias_name")
    new_filter_name = params.get("new_filter_name")
    source_network = params.get("source_network")
    # prepare vm filterrfer parameters dict list
    filter_param_list_1 = []
    params_key_1 = []
    filter_param_list_2 = []
    params_key_2 = []
    for i in params.keys():
        if 'parameters_name_' in i:
            params_key_1.append(i)
    params_key_1.sort()
    for i in range(len(params_key_1)):
        params_dict = {}
        params_dict['name'] = params[params_key_1[i]]
        params_dict['value'] = params['parameters_value_%s' % i]
        filter_param_list_1.append(params_dict)
    filterref_dict_1 = {}
    filterref_dict_1['name'] = new_filter_1
    filterref_dict_1['parameters'] = filter_param_list_1

    for i in params.keys():
        if 'parameters_dhcp_' in i:
            params_key_2.append(i)
    params_key_2.sort()
    for i in range(len(params_key_2)):
        params_dict = {}
        params_dict['name'] = params[params_key_2[i]]
        params_dict['value'] = params['dhcp_value_%s' % i]
        filter_param_list_2.append(params_dict)
    filterref_dict_2 = {}
    filterref_dict_2['name'] = new_filter_2
    filterref_dict_2['parameters'] = filter_param_list_2

    def set_env():
        """
        set two interface with different network filter
        and change interface type
        """
        virsh.attach_interface(vm_name, option)
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        devices = vmxml.get_devices('interface')
        iface_xml = devices[0]
        iface_xml_2 = devices[1]
        vmxml.del_device(iface_xml)
        vmxml.del_device(iface_xml_2)
        new_iface_1 = interface.Interface('network')
        new_iface_2 = interface.Interface('network')
        new_iface_1.xml = iface_xml.xml
        new_iface_2.xml = iface_xml_2.xml
        new_iface_1.type_name = "network"
        new_iface_2.type_name = "network"
        new_iface_1.source = {'network': source_network}
        new_iface_2.source = {'network': source_network}
        new_iface_1.target = {'dev': 'new_net0'}
        new_iface_2.target = {'dev': 'new_net1'}
        new_filterref = new_iface_1.new_filterref(**filterref_dict_1)
        new_iface_1.filterref = new_filterref
        new_filterref = new_iface_2.new_filterref(**filterref_dict_2)
        new_iface_2.filterref = new_filterref
        logging.debug("new interface xml is: %s \n %s" %
                      (new_iface_1, new_iface_2))
        vmxml.add_device(new_iface_1)
        vmxml.add_device(new_iface_2)
        vmxml.sync()
        return new_iface_1, new_iface_2

    try:
        new_iface_1, new_iface_2 = set_env()
        # start vm
        virsh.start(vm_name, debug=True)
        # list binding port dev
        ret = virsh.nwfilter_binding_list(debug=True)
        utlv.check_exit_status(ret, status_error)
        virsh.nwfilter_binding_dumpxml(new_iface_1.target['dev'],
                                       to_file=new_net0_xml, debug=True)
        virsh.nwfilter_binding_dumpxml(new_iface_2.target['dev'],
                                       to_file=new_net1_xml, debug=True)
        # check dump filterbinding can pass xml validate
        new_net0_cmd = "virt-xml-validate %s" % new_net0_xml
        new_net1_cmd = "virt-xml-validate %s" % new_net1_xml
        valid_0 = process.run(new_net0_cmd, ignore_status=True,
                              shell=True).exit_status
        valid_1 = process.run(new_net1_cmd, ignore_status=True,
                              shell=True).exit_status
        if valid_0 or valid_1:
            test.fail("the xml can not validate successfully")
        # create new xml and update device
        newnet_iface = interface.Interface('network')
        newnet_iface.xml = new_iface_1.xml
        filterref_list = []
        filterref_dict = {}
        filterref_dict['name'] = new_filter_name
        filterref_dict['parameters'] = filterref_list
        newnet_iface.alias = {'name': alias_name}
        newnet_iface.filterref = newnet_iface.new_filterref(**filterref_dict)

        ret = virsh.update_device(domainarg=vm_name,
                                  filearg=newnet_iface.xml,
                                  debug=True)
        utlv.check_exit_status(ret, status_error)
        ret_list = virsh.nwfilter_binding_list(debug=True)
        utlv.check_result(ret_list, expected_match="new_net1")

        ret_dump = virsh.nwfilter_binding_dumpxml('new_net0', debug=True)
        utlv.check_result(ret_dump, expected_match=new_filter_name)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
