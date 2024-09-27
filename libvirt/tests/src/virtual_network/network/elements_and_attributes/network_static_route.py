import re

from avocado.utils import process

from virttest import virsh
from virttest.utils_libvirt import libvirt_network


def run(test, params, env):
    """
    Test network static route.
    """
    net_name = params.get("net_name")
    net_attrs = eval(params.get('net_attrs', '{}'))
    ipv4_associated_route = params.get("ipv4_associated_route")
    ipv4_defined_route = params.get("ipv4_defined_route")
    ipv6_associated_route = params.get("ipv6_associated_route")
    ipv6_defined_routes = eval(params.get("ipv6_defined_routes"))

    def get_ip_route(bridge, ipv6=False):
        """
        get ipv4 or ipv6 route on host for vnet bridge.

        :param ipv6: get ipv6 route or not(ipv4)
        :param bridge: vnet bridge name
        """
        ip_route_cmd = "ip -6 route" if ipv6 else "ip route"
        ip_route_cmd += "|grep %s" % bridge
        result = process.run(ip_route_cmd, shell=True, ignore_status=False)
        return result

    try:
        libvirt_network.create_or_del_network(net_attrs)
        virsh.net_dumpxml(net_name, ignore_status=True)
        net_info = virsh.net_info(net_name).stdout.strip()
        bridge = re.search(r'Bridge:\s+(\S+)', net_info).group(1)
        cmd_ret1 = get_ip_route(bridge)
        if not re.search(ipv4_associated_route, cmd_ret1.stdout_text):
            test.fail("The ip addr associated route '%s' is not in result." % ipv4_associated_route)
        if not re.search(ipv4_defined_route, cmd_ret1.stdout_text):
            test.fail("The defined static route '%s' is not in result." % ipv4_defined_route)
        cmd_ret2 = get_ip_route(bridge, True)
        if not re.search(ipv6_associated_route, cmd_ret2.stdout_text):
            test.fail("The ipv6 addr associated route '%s' is not in result." % ipv6_associated_route)
        for ipv6_def_route in ipv6_defined_routes:
            if not re.search(ipv6_def_route, cmd_ret2.stdout_text):
                test.fail("The defined ipv6 static route '%s' is not in result." % ipv6_def_route)
    finally:
        libvirt_network.create_or_del_network(net_attrs, is_del=True)
