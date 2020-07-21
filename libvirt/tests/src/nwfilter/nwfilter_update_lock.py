import time
import threading
import logging

from virttest import virsh
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml.devices import interface

from virttest import libvirt_version


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
    bug_url = params.get("bug_url", "")
    vm_name = params.get("main_vm")

    if not libvirt_version.version_compare(1, 2, 6):
        test.cancel("Bug %s not fixed on current build" % bug_url)

    vm = env.get_vm(vm_name)
    # Prepare vm filterref parameters dict list
    filterref_dict = {}
    filterref_dict['name'] = filter_name

    # backup vm and filter xml
    vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_filter = libvirt_xml.NwfilterXML()
    filterxml = backup_filter.new_from_filter_dumpxml(filter_name)
    libvirtd = utils_libvirtd.LibvirtdSession()

    def nwfilter_sync_loop(filter_name, filerxml):
        """
        Undefine filter and redefine filter from xml in loop
        """
        for i in range(2400):
            virsh.nwfilter_undefine(filter_name, ignore_status=True)
            time.sleep(0.1)
            virsh.nwfilter_define(filterxml.xml, ignore_status=True)

    def vm_start_destory_loop(vm):
        """
        Start and destroy vm in loop
        """
        for i in range(2400):
            vm.start()
            time.sleep(0.1)
            vm.destroy(gracefully=False)

    try:
        libvirtd.start()
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

        filter_thread = threading.Thread(target=nwfilter_sync_loop,
                                         args=(filter_name, filterxml))
        vm_thread = threading.Thread(target=vm_start_destory_loop,
                                     args=(vm,))
        filter_thread.start()
        time.sleep(0.3)
        vm_thread.start()

        ret = utils_misc.wait_for(lambda: not libvirtd.is_working(),
                                  timeout=240,
                                  step=1)

        filter_thread.join()
        vm_thread.join()
        if ret:
            test.fail("Libvirtd hang, %s" % bug_url)

    finally:
        libvirtd.exit()
        # Clean env
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm and filter.
        vmxml_backup.sync()
        virsh.nwfilter_undefine(filter_name, ignore_status=True)
        virsh.nwfilter_define(filterxml.xml, ignore_status=True)
