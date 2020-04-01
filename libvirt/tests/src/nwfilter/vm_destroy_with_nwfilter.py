import logging

from avocado.utils import process
from avocado.utils import astring

from virttest import libvirt_xml
from virttest import virsh
from virttest.libvirt_xml.devices import interface
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test if domain destory with nwfilter will
    produce error messege in libvirt.log

    1) set env
    2) run command and check result
    3) clean env
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    status_error = "yes" == params.get("status_error")
    vmxml_backup = libvirt_xml.vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    filter_name = params.get("filter_name")
    check_cmd = params.get("check_cmd")

    def set_env():
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml
        new_iface.type_name = "network"
        new_iface.source = {'network': "default"}
        filter_dict = {}
        filter_dict['name'] = filter_name
        filter_dict['parameters'] = []
        new_iface.filterref = new_iface.new_filterref(**filter_dict)
        logging.debug("new iface is %s" % new_iface)
        vmxml.add_device(new_iface)
        vmxml.sync()

    try:
        # set env
        set_env()
        # start vm
        ret = virsh.start(vm_name, debug=True)
        utlv.check_exit_status(ret, status_error)
        # destory vm see if libvirtd.log will get error
        virsh.destroy(vm_name)
        utlv.check_exit_status(ret, status_error)
        out = astring.to_text(process.system_output(
            check_cmd, ignore_status=True, shell=True))
        if out:
            test.fail("libvirtd.log get error")

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
