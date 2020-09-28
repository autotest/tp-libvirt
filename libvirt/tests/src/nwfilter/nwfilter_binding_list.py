import logging
import time
import re

from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices import interface
from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_package


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
    status_error = "yes" == params.get("status_error")
    new_filter_1 = params.get("newfilter_1")
    new_filter_2 = params.get("newfilter_2")
    time_wait = params.get("time_wait", 10)
    option = params.get("option")
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
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

    utils_package.package_install('libvirt-daemon-config-nwfilter')

    def env_setting(filterref_dict_1, filterref_dict_2):
        ret = virsh.attach_interface(vm_name, option)
        utlv.check_exit_status(ret, status_error)
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        devices = vmxml.get_devices('interface')
        iface_xml = devices[0]
        logging.debug("iface_xml : %s" % iface_xml)
        iface_xml_2 = devices[1]
        vmxml.del_device(iface_xml)
        vmxml.del_device(iface_xml_2)
        new_iface_1 = interface.Interface('network')
        logging.debug("new_iface_1 : %s" % new_iface_1)
        new_iface_2 = interface.Interface('network')
        new_iface_1.xml = iface_xml.xml
        new_iface_1.type_name = "network"
        logging.debug("new_iface_1 : %s" % new_iface_1)
        new_iface_2.xml = iface_xml_2.xml

        new_iface_1.source = {'network': "default"}
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

    def attach_new_device():
        newnet_iface = interface.Interface('network')
        newnet_iface.source = {'network': "default"}
        newnet_iface.model = 'virtio'
        filterref_dict = {}
        filterref_list = [{'name': "CTRL_IP_LEARNING", 'value': "dhcp"}]
        filterref_dict['name'] = "clean-traffic"
        filterref_dict['parameters'] = filterref_list
        newnet_iface.filterref = newnet_iface.new_filterref(**filterref_dict)
        ret = virsh.attach_device(domainarg=vm_name,
                                  filearg=newnet_iface.xml,
                                  debug=True)
        utlv.check_exit_status(ret, status_error)

    try:
        # set env of start vm
        new_iface_1, new_iface_2 = env_setting(
            filterref_dict_1, filterref_dict_2)
        # start vm
        virsh.start(vm_name, debug=True)
        # list binding port dev
        logging.debug("check nwfilter binding for 2 interfaces")
        ret = virsh.nwfilter_binding_list(debug=True)
        utlv.check_result(ret, expected_match=[r"vnet\d+\s+clean-traffic"])
        utlv.check_result(ret, expected_match=[r"vnet\d+\s+allow-dhcp-server"])
        # detach a interface
        option = "--type network" + " --mac " + new_iface_1.mac_address
        ret = virsh.detach_interface(vm_name, option, debug=True)
        time.sleep(time_wait)
        utlv.check_exit_status(ret, status_error)
        logging.debug("check nwfilter binding after detach one interface:")
        time.sleep(3)
        ret = virsh.nwfilter_binding_list(debug=True)
        if re.search(r'vnet\d+\s+clean-traffic.*', ret.stdout):
            test.fail("vnet binding clean-traffic still exists after detach the interface!")
        utlv.check_result(ret, expected_match=[r"vnet\d+\s+allow-dhcp-server"])

        # update_device to delete the filter
        iface_dict = {'del_filter': True}
        new_xml = utlv.modify_vm_iface(vm_name, 'get_xml', iface_dict)
        virsh.update_device(domainarg=vm_name,
                            filearg=new_xml,
                            debug=True)
        logging.debug("check nwfilter-binding after delete the only interface")
        ret = virsh.nwfilter_binding_list(debug=True)
        if re.search(r'vnet\d+\s+allow-dhcp-server.*', ret.stdout):
            test.fail("vnet binding allow-dhcp-server still exists after detach the interface!")
        utlv.check_exit_status(ret, status_error)

        # attach new interface
        attach_new_device()
        ret = virsh.nwfilter_binding_list(debug=True)
        logging.debug("Check nwfilter-binding exists after attach device")
        utlv.check_result(ret, expected_match=[r"vnet\d+\s+clean-traffic"])

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
