from virttest import libvirt_version
from virttest import virsh
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.virtual_network import network_base

VIRSH_ARGS = {"ignore_status": False, "debug": True}


def run(test, params, env):
    """
    Test update-device for interface link state
    """
    libvirt_version.is_libvirt_feature_supported(params)

    outside_ip = params.get("outside_ip")
    exist_attrs = eval(params.get('exist_attrs', '{}'))
    iface_attrs_ = eval(params.get("iface_attrs", "{}"))
    iface_attrs = {**iface_attrs_, **exist_attrs}
    update_setting = eval(params.get("update_setting", "{}"))
    test_states = [("yes", "up"), ("no", "down")]

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(
            vmxml, 'interface', {**iface_attrs})
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        vm.start()
        session = vm.wait_for_serial_login()
        vm_iface = interface_base.get_vm_iface(session)

        test.log.info("TEST_STEP: Check the link state in vm.")
        iflist = libvirt.get_interface_details(vm_name)
        test.log.debug(f'iflist of vm: {iflist}')
        iface_info = iflist[0]
        iface_mac = iface_info['mac']
        for exp_link_state, exp_domiflik_state in test_states:
            if exp_link_state == test_states[-1][0]:
                iface = vm_xml.VMXML.new_from_dumpxml(
                    vm.name).devices.by_device_tag("interface")[0]
                iface.setup_attrs(**update_setting)
                test.log.debug(f"iface xml to be updated: {iface}")
                test.log.info("TEST_STEP: Update interface device.")
                virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)

            link_info = virsh.domif_getlink(
                vm_name, iface_mac, **VIRSH_ARGS).stdout_text
            if exp_domiflik_state not in link_info:
                test.fail("Failed to get expected interface link state '%s'!"
                          % exp_domiflik_state)

            if exp_link_state == "yes":
                if exp_link_state == test_states[-1][0]:
                    utils_net.restart_guest_network(session)
                ips = {'outside_ip': outside_ip}
                network_base.ping_check(params, ips, session, force_ipv4=True)

            output = session.cmd_output("ethtool %s" % vm_iface)
            test.log.debug(output)
            if "Link detected: %s" % exp_link_state not in output:
                test.fail("Failed to get expected link state '%s' in ethtool "
                          "cmd!" % exp_link_state)
        vm_iface = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("interface")[0]

        test.log.info("TEST_STEP: Check the live xml for the current link state.")
        iface_attrs = vm_iface.fetch_attrs()
        test.log.debug(f"iface attrs: {iface_attrs}")
        if iface_attrs.get("link_state") != exp_domiflik_state:
            test.fail("Failed to get expected link state '%s' in live xml." % exp_domiflik_state)
        session.close()
    finally:
        bkxml.sync()
