import logging

from virttest.libvirt_xml.devices import interface
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest import libvirt_xml
from avocado.utils import process


def run(test, params, env):
    """
    Test virsh nwfilter-binding-delete
    1) prepare parameters
    2) Run command
    3) check result
    4) clean env
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    check_cmd = params.get("check_cmd")
    filter_name = params.get("filter_name")
    status_error = "yes" == params.get("status_error")
    expected_not_match = params.get("expected_not_match")
    filter_param_list = []
    restart_cmd = params.get("restart_cmd")
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    params_key = []
    for i in params.keys():
        if 'parameters_name_' in i:
            params_key.append(i)
    params_key.sort()
    for i in range(len(params_key)):
        params_dict = {}
        params_dict['name'] = params[params_key[i]]
        params_dict['value'] = params['parameters_value_%s' % i]
        filter_param_list.append(params_dict)
    filterref_dict = {}
    filterref_dict['name'] = filter_name
    filterref_dict['parameters'] = filter_param_list

    def set_env():
        vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml

        new_filterref = new_iface.new_filterref(**filterref_dict)
        new_iface.filterref = new_filterref
        logging.debug("new interface xml is: %s" % new_iface)
        vmxml.add_device(new_iface)
        vmxml.sync()
        return new_iface

    try:
        # set_env
        new_iface = set_env()
        # start vm
        virsh.start(vm_name, debug=True)
        # list filter
        ret = virsh.nwfilter_binding_list(debug=True)
        utlv.check_exit_status(ret, status_error)
        # delete nwfilter binding
        ret = virsh.nwfilter_binding_delete(
            new_iface.target['dev'], debug=True)
        utlv.check_exit_status(ret, status_error)
        # check rule
        utlv.check_cmd_expected(check_cmd, expected_not_match, False)

        # restart libvirtd, the nwfilter-binding will restore
        cmd_res = process.run(restart_cmd, shell=True)
        if cmd_res.exit_status:
            test.fail("fail to restart libvirtd")

        ret = virsh.nwfilter_binding_list(debug=True)
        utlv.check_exit_status(ret, status_error)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
