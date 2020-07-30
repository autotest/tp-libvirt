import logging
import os
import re

from virttest.libvirt_xml.devices import interface
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import data_dir
from virttest.libvirt_xml import nwfilter_binding
from virttest import utils_package


def run(test, params, env):
    """
    1. prepare env
    2. check if nwfilter binding
    3. run test
    4. destroy vm and restore the status
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    status_error = "yes" == params.get("status_error")
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    filter_name = params.get("filter_name")
    is_nwfilter_define = "yes" == params.get("is_nwfilter_define")
    vnet0_binding = os.path.join(data_dir.get_tmp_dir(), "vnet0_binding.xml")
    filter_binding_name = params.get("filter_binding_name")
    failed_msg = params.get("expected_failed")
    target_dev = params.get("target_dev")
    source_network = params.get("source_network")
    source_bridge = params.get("source_bridge")
    alias_name = params.get("alias_name")

    def set_env():
        """
        prepare the vm interface xml
        this xml can both use in two senario.
        but little different for two senario
        """
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml
        new_iface.type_name = "network"
        iface_target = {'dev': target_dev}
        new_iface.target = iface_target
        source = {'network': source_network, 'bridge': source_bridge}
        new_iface.source = source
        filterrefs_dict = {}
        filterrefs_dict['name'] = filter_name
        filterrefs_dict['parameters'] = []
        new_filterref = new_iface.new_filterref(**filterrefs_dict)
        new_iface.filterref = new_filterref
        alias_dict = {'name': alias_name}
        new_iface.alias = alias_dict
        vmxml.add_device(new_iface)
        logging.debug("new interface xml is: %s" % new_iface)
        vmxml.sync()
        return new_iface

    def check_binding_port(cmd_res, match, is_match=True):
        """
        check the list binding ports
        """
        list_res = cmd_res.stdout_text.strip()
        if list_res and re.search(match, list_res):
            if not is_match:
                test.fail("expected not match %s" % match)
        elif is_match:
            test.fail("expected match %s but not match" % match)

    try:
        # set new interface env
        new_iface = set_env()
        # create binding dump file
        pkg_mgr = utils_package.package_manager(None, 'libvirt-daemon-config-nwfilter')
        pkg_mgr.install()
        virsh.start(vm_name, debug=True)
        ret = virsh.nwfilter_binding_dumpxml(new_iface.target['dev'],
                                             to_file=vnet0_binding,
                                             debug=True)
        utlv.check_exit_status(ret, status_error)
        binding = nwfilter_binding.NwfilterBinding()
        binding.xml = vnet0_binding
        filterrefs_dict = {}
        filterrefs_dict['name'] = filter_binding_name
        filterrefs_dict['parameters'] = [
            {'name': "MAC", 'value': new_iface.mac_address}]
        binding.filterref = binding.new_filterref(**filterrefs_dict)
        logging.debug("binding is %s" % binding)
        # list filter
        if not is_nwfilter_define:
            virsh.nwfilter_binding_delete(new_iface.target['dev'], debug=True)
        if is_nwfilter_define:
            ret = virsh.nwfilter_binding_list(debug=True)
            utlv.check_exit_status(ret, status_error)
            check_binding_port(ret, filter_name, is_match=True)

        ret_create = virsh.nwfilter_binding_create(binding.xml, debug=True)
        # two Senario
        if is_nwfilter_define:
            utlv.check_result(ret_create, failed_msg)

        elif not is_nwfilter_define:
            # get params for senario2
            check_cmd = params.get("check_cmd")
            expected_match = params.get("expected_match")
            filter_binding_copy = params.get("filter_binding_copy")

            ret = virsh.nwfilter_binding_list(debug=True)
            check_binding_port(ret, filter_binding_name, is_match=True)

            utlv.check_cmd_output(check_cmd, expected_match, True)
            utils_libvirtd.libvirtd_restart()
            ret = virsh.nwfilter_binding_list(debug=True)
            check_binding_port(ret, filter_binding_name, is_match=True)
            # use check command to check result
            utlv.check_cmd_output(check_cmd, expected_match, True)
            new_binding = nwfilter_binding.NwfilterBinding()
            new_binding.xml = binding.xml
            filterrefs_dict = {}
            filterrefs_dict['name'] = filter_binding_copy
            filterrefs_dict['parameters'] = [
                {'name': "MAC", 'value': new_iface.mac_address}]
            binding.filterref = binding.new_filterref(**filterrefs_dict)
            logging.debug("binding is %s" % new_binding)
            ret_create = virsh.nwfilter_binding_create(new_binding.xml,
                                                       debug=True)
            utlv.check_result(ret_create, failed_msg)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
