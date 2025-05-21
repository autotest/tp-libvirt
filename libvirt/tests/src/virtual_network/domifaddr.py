import logging
import random

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {"ignore_status": False, "debug": True}

LOG = logging.getLogger("avocado." + __name__)


def run(test, params, env):
    """
    Test domifaddr for bridge and network type interface
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    net_name = params.get("net_name", "default")
    net_ipv6_attrs = eval(params.get("net_ipv6_attrs", "{}"))

    rand_id = utils_misc.generate_random_string(3)
    br_name = "linux_br_" + rand_id
    host_iface = params.get("host_iface")
    host_iface = (
        host_iface
        if host_iface
        else utils_net.get_default_gateway(iface_name=True, force_dhcp=True).split()[0]
    )
    iface_a_attrs = eval(params.get("iface_a_attrs", "{}"))
    iface_b_attrs = eval(params.get("iface_b_attrs", "{}"))
    err_msg = params.get("err_msg")

    net_xml = network_xml.NetworkXML.new_from_net_dumpxml(net_name)
    bk_net = net_xml.copy()
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bk_xml = vmxml.copy()

    try:
        utils_net.create_linux_bridge_tmux(br_name, host_iface)
        net_ipv6 = network_xml.IPXML(ipv6=True)
        net_ipv6.setup_attrs(**net_ipv6_attrs)
        net_xml.add_ip(net_ipv6)
        LOG.debug(f"Define network {net_name} with xml:\n{net_xml}")
        net_xml.sync()

        vmxml.del_device("interface", by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, "interface", iface_a_attrs, 0)
        libvirt_vmxml.modify_vm_device(vmxml, "interface", iface_b_attrs, 1)
        LOG.debug(virsh.dumpxml(vm_name).stdout_text)

        iface_a = network_base.get_iface_xml_inst(vm_name, "interface A", 0)
        iface_b = network_base.get_iface_xml_inst(vm_name, "interface B", 1)
        # Make sure iface_b is network type, switch if not
        if iface_b.type_name != "network":
            iface_a, iface_b = iface_b, iface_a
        mac_a, mac_b = iface_a.mac_address, iface_b.mac_address

        vm.start()
        vm.set_state_guest_agent(True, serial=True)
        session = vm.wait_for_serial_login()
        ip_output = session.cmd_output("ip addr")
        LOG.debug(ip_output)
        ip_a = network_base.get_vm_ip(session, mac_a, ignore_error=True)
        ip_b = network_base.get_vm_ip(session, mac_b, ignore_error=True)

        ipv6_a = network_base.get_vm_ip(session, mac_a, "ipv6", ignore_error=True)
        ipv6_b = network_base.get_vm_ip(session, mac_b, "ipv6", ignore_error=True)
        LOG.debug(ip_a)
        LOG.debug(ip_b)

        vm_iface_a = utils_net.get_linux_iface_info(mac=mac_a, session=session)[
            "ifname"
        ]
        vm_iface_b = utils_net.get_linux_iface_info(mac=mac_b, session=session)[
            "ifname"
        ]

        ip_gen = utils_net.gen_ipv4_addr("173.173.173.0")
        ip_alias_a, ip_alias_b = random.choices(list(ip_gen), k=2)

        cmd_set_alias_a = f"ip addr add {ip_alias_a}/24 dev {vm_iface_a}"
        cmd_set_alias_b = f"ip addr add {ip_alias_b}/24 dev {vm_iface_b}"

        session.cmd(cmd_set_alias_a)
        session.cmd(cmd_set_alias_b)

        virsh.domiflist(vm_name, **VIRSH_ARGS)
        virsh.domifaddr(vm_name, **VIRSH_ARGS)
        libvirt.check_logfile(err_msg, params.get("libvirtd_debug_file"), False)

        domid = virsh.domid(vm_name).stdout_text.strip()
        out_default = virsh.domifaddr(domid, **VIRSH_ARGS)
        expect_info = ["vnet*", mac_b, "ipv4", ip_b, "ipv6", ipv6_b]
        for s in expect_info:
            libvirt.check_result(out_default, expected_match=s)

        domuuid = virsh.domuuid(vm_name).stdout_text.strip()
        out_uuid = virsh.domifaddr(domuuid, **VIRSH_ARGS)
        out_lease = virsh.domifaddr(domuuid, "--source lease", **VIRSH_ARGS)
        for output in (out_uuid, out_lease):
            if output.stdout_text.strip() == out_default.stdout_text.strip():
                LOG.info("Output match")
            else:
                test.fail("Output of domifaddr does not match, please check log")

        iface_info_a = utils_net.get_linux_iface_info(vm_iface_a, session=session)
        ips_a = [addr.get("local") for addr in iface_info_a.get("addr_info", {})]

        iface_info_b = utils_net.get_linux_iface_info(vm_iface_b, session=session)
        ips_b = [addr.get("local") for addr in iface_info_b.get("addr_info", {})]

        check_list_a = [mac_a] + ips_a
        LOG.debug(check_list_a)
        check_list_b = [mac_b] + ips_b
        LOG.debug(check_list_b)

        out_agent = virsh.domifaddr(domuuid, "--source agent", **VIRSH_ARGS)
        for str in check_list_a + check_list_b:
            libvirt.check_result(out_agent, expected_match=str)

        out_agent_a = virsh.domifaddr(
            vm_name, f"--source agent {vm_iface_a}", **VIRSH_ARGS
        )
        for str in check_list_a:
            libvirt.check_result(out_agent_a, expected_match=str)
        if any([str in out_agent_a.stdout_text for str in check_list_b]):
            test.fail(
                f"Output with --source agent {vm_iface_a} should not "
                f"contain info of other interface, please check log"
            )

        out_agent_a_full = virsh.domifaddr(
            vm_name, f"--source agent {vm_iface_a} --full", **VIRSH_ARGS
        )
        for str in check_list_a:
            libvirt.check_result(out_agent_a_full, expected_match=str)

        out_arp = virsh.domifaddr(domuuid, "--source arp", **VIRSH_ARGS)
        for str in (mac_b, ip_b):
            libvirt.check_result(out_arp, expected_match=str)

    finally:
        bk_xml.sync()
        bk_net.sync()
        utils_net.delete_linux_bridge_tmux(br_name, host_iface)
