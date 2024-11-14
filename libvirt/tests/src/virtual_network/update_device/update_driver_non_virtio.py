import re

from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.virtual_network import network_base

VIRSH_ARGS = {"ignore_status": False, "debug": True}


def run(test, params, env):
    """
    Test update-device for interface link state, live update the link state from up to down
    There are invalid driver attribute defined in the original xml or the update xml.
    The test is to ensure the update can succeed and get expected result.
    """
    libvirt_version.is_libvirt_feature_supported(params)

    outside_ip = params.get("outside_ip")
    exist_attrs = eval(params.get('exist_attrs', '{}'))
    iface_attrs_ = eval(params.get("iface_attrs", "{}"))
    iface_attrs = {**iface_attrs_, **exist_attrs}
    update_setting = eval(params.get("update_setting", "{}"))

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    def check_link_state(exp_link_state, exp_domiflik_state):
        """
        check the link state on vm and in xml should be consistent and expected
        """
        # get the link state in the live xml
        link_info = virsh.domif_getlink(vm_name, iface_mac, **VIRSH_ARGS).stdout_text
        test.log.debug("Get the interface info by domif-getlink: %s", link_info)
        # get "Link detected" value from ethtool outputs on vm
        output = session.cmd_output("ethtool %s" % vm_iface)
        test.log.debug(output)
        match = re.search(r"Link detected:\s*(\w+)", output)
        if match:
            link_status = match.group(1)
        else:
            test.cancel("Can not get expected Link status on vm!")
        # make sure the result is expected
        if exp_link_state == 'yes':
            ips = {'outside_ip': outside_ip}
            network_base.ping_check(params, ips, session, force_ipv4=True)
        test.log.debug("link_info is %s, link-status is %s" % (link_info, link_status))
        if link_info.split()[-1] != exp_domiflik_state:
            test.fail("The link states in vm xml is not expected")
        if link_status != exp_link_state:
            test.fail("The link status on vm is not expected")

    try:
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(
            vmxml, 'interface', {**iface_attrs})
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        vm.start()
        session = vm.wait_for_serial_login()
        vm_iface = interface_base.get_vm_iface(session)

        test.log.info("TEST_STEP1: Check the original link state in vm should be up")
        ifaces = libvirt.get_interface_details(vm_name)
        test.log.debug(f'ifaces of vm: {ifaces}')
        iface_info = ifaces[0]
        iface_mac = iface_info['mac']
        check_link_state("yes", "up")

        test.log.info("TEST_STEP2: Update the link state to be down")
        iface = network_base.get_iface_xml_inst(vm_name, f'on VM:{vm_name}')
        iface.setup_attrs(**update_setting)
        test.log.debug(f"iface xml to be updated: {iface}")
        ret = virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)
        libvirt.check_result(ret)

        test.log.info("TEST_STEP3: Check the interface status in xml and on vm")
        check_link_state("no", "down")
        session.close()
    finally:
        bkxml.sync()
