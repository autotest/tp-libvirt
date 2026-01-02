from virttest import virsh
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_test
from virttest import utils_libvirtd

from avocado.utils import process
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

virsh_opt = {"debug": True, "ignore_status": False}


def domif_setlink(vm_name, device, operation, options):
    """
    Set the domain link state

    :param vm_name : domain name
    :param device : domain virtual interface
    :param operation : domain virtual interface state
    :param options : some options like --config
    """
    return virsh.domif_setlink(vm_name, device, operation, options, **virsh_opt)


def check_connectivity(test, ip, mac, session, is_linux_guest):
    """
    Check connectivity by pinging host from guest for both Linux and Windows

    :param test: Test instance for logging
    :param ip: Target IP address to ping
    :param mac: MAC address of the hotplug NIC
    :param session: Guest session
    :param is_linux_guest: Boolean indicating if guest is Linux
    """
    status, output = utils_test.ping(ip, 5, timeout=30)
    if status != 0:
        if not is_linux_guest:
            raise Exception(f"Ping failed with status {status}: {output}")
        ifname = utils_net.get_linux_ifname(session, mac)
        add_route_cmd = "route add %s dev %s" % (ip, ifname)
        del_route_cmd = "route del %s dev %s" % (ip, ifname)
        test.log.warning("Failed to ping %s from host." % ip)
        test.log.info("Add route and try again")
        session.cmd_output_safe(add_route_cmd)
        status, output = utils_test.ping(ip, 5, timeout=30)
        test.log.info("Del the route.")
        session.cmd_output_safe(del_route_cmd)
        if status != 0:
            raise Exception(f"Ping failed with status {status}: {output}")

    test.log.info("Ping succeeded")


def renew_ip_address(session, mac, is_linux_guest, params):
    if not is_linux_guest:
        utils_net.restart_windows_guest_network_by_key(session, "macaddress", mac)
        return None

    ifname = utils_net.get_linux_ifname(session, mac)
    if params.get("make_change") == "yes":
        # keep this configuration for guests running RHEL7 and earlier
        # as it is a more stable approach.
        p_cfg = "/etc/sysconfig/network-scripts/ifcfg-%s" % ifname
        cfg_con = "DEVICE=%s\\nBOOTPROTO=dhcp\\nONBOOT=yes" % ifname
        make_conf = "test -f %s || echo '%s' > %s" % (p_cfg, cfg_con, p_cfg)
    else:
        make_conf = (
            "nmcli connection add type ethernet con-name %s ifname"
            " %s autoconnect yes" % (ifname, ifname)
        )
    arp_clean = "arp -n|awk '/^[1-9]/{print \"arp -d \" $1}'|sh"
    session.cmd_output_safe(make_conf)
    session.cmd_output_safe("ip link set dev %s up" % ifname)
    dhcp_cmd = params.get("dhcp_cmd")
    session.cmd_output_safe(dhcp_cmd % ifname, timeout=240)
    session.cmd_output_safe(arp_clean)
    return None


def get_hotplug_nic_ip(test, vm, hotplug_mac, session, is_linux_guest, params):
    def __get_address():
        try:
            ip_addr = vm.address_cache.get(hotplug_mac)
            if ip_addr:
                return ip_addr

            if is_linux_guest:
                ifname = utils_net.get_linux_ifname(session, hotplug_mac)
                ip_cmd = f"ip addr show {ifname} | grep 'inet ' | awk '{{print $2}}' | cut -d'/' -f1"
                try:
                    result = session.cmd_output_safe(ip_cmd)
                    if result.strip():
                        return result.strip()
                except Exception as e:
                    test.log.debug(f"Failed to get IP from guest interface: {e}")
            return None
        except Exception as e:
            test.log.debug(f"Error getting IP, attempting to renew: {e}")
            return None

    nic_ip = utils_misc.wait_for(__get_address, timeout=360)
    if nic_ip:
        return nic_ip

    cached_ip = vm.address_cache.get(hotplug_mac)
    arps = process.system_output("arp -aen").decode()
    test.log.debug("Can't get IP address:")
    test.log.debug("\tCached IP: %s", cached_ip)
    test.log.debug("\tARP table: %s", arps)
    return None


def attach_hotplug_interface(test, vm_name, original_iface):

    new_mac = utils_net.generate_mac_address_simple()

    iface_type = original_iface.type_name
    source = ""
    model = ""

    if hasattr(original_iface, 'source') and original_iface.source:
        if iface_type == 'network':
            source = original_iface.source.get('network', 'default')
        elif iface_type == 'bridge':
            source = original_iface.source.get('bridge', 'virbr0')

    if hasattr(original_iface, 'model') and original_iface.model:
        model = str(original_iface.model)

    attach_options = f"--type {iface_type} --mac {new_mac}"
    if source:
        attach_options += f" --source {source}"
    if model:
        attach_options += f" --model {model}"
    attach_options += " --live"

    test.log.info(f"Hotplug interface: {attach_options}")

    virsh.attach_interface(
        vm_name,
        option=attach_options,
        **virsh_opt
    )

    test.log.info(f"Hotplug succeed: {new_mac}")
    return new_mac, iface_type


def detach_hotplug_interface(test, vm_name, mac_address, expected_type=None,
                             wait_for_event=True, event_type="device-removed"):
    # Add event check in this cases. however, it's better suited to be added to
    # inside framework so it can used by other device in the future.
    test.log.info(f"Hot unplug interface: {mac_address}")

    iface_type = expected_type or "network"

    detach_options = f"--type {iface_type} --mac {mac_address} --live"

    virsh.detach_interface(
        vm_name,
        option=detach_options,
        **virsh_opt,
        wait_for_event=wait_for_event,
        event_type=event_type
    )

    test.log.info(f"Hot unplug succeed: {mac_address}")
    return True


def run(test, params, env):
    """
    Test hotplug of NIC devices

    Test Steps:
    1. Boot guest with a device
    2. setlink down
    3. hotplug a device
    4. check connection
    5. stop&resume
    6. check connection again
    7. setlink up
    8. unplug this device
    """
    login_timeout = params.get_numeric("login_timeout", 360)
    hotplug_cycles = params.get_numeric("hotplug_cycles", 100)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    test.log.debug("vm xml:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_name))
    is_linux_guest = "linux" == params.get("os_type", "")
    host_ip_addr = utils_net.get_host_ip_address(params)
    ips = {'host_ip': host_ip_addr}
    test.log.info("VM started to got the org vm interface info")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    original_iface = libvirt.get_vm_device(vmxml, "interface", index=-1)[0]
    original_mac = original_iface.mac_address
    test.log.info(f"original interface: MAC={original_mac}, Type={original_iface.type_name}")

    # This is a workaround to let virt-install create vm can be management by domif-setlink
    # and cleanup exists serial session, since restart libvirtd will render the original invalid
    test.log.info("Restart libvirtd server")
    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd.restart()
    if vm.serial_console:
        vm.cleanup_serial_console()

    vm.create_serial_console()
    test.log.info("Create a new serial console")

    # Starting hotplug/unplug loop tests, 100 times
    for iteration in range(hotplug_cycles):
        test.log.info(f"\n=== Round {iteration + 1}/{hotplug_cycles} ===")

        # STEP 1: Setlink original nic down
        test.log.info("STEP 1: Setlink original nic down")
        domif_setlink(vm_name, original_mac, "down", "")
        test.log.info("Setlink down succeed")

        # STEP 2: Hotplug nic
        test.log.info("STEP 2: Hotplug nic")
        hotplug_mac, hotplug_type = attach_hotplug_interface(test, vm_name, original_iface)

        # STEP 3: Get hotplug nic ip and check connectivity
        test.log.info("STEP 3: Get hotplug nic ip and check connectivity")
        session = vm.wait_for_serial_login(timeout=login_timeout)
        test.log.info("First time to obtain hotplug nic ip address...")
        hotnic_ip = get_hotplug_nic_ip(
            test, vm, hotplug_mac, session, is_linux_guest, params
        )

        if not hotnic_ip:
            test.log.info("First time failed, try to renew ip address...")
            try:
                renew_ip_address(session, hotplug_mac, is_linux_guest, params)
                hotnic_ip = get_hotplug_nic_ip(
                    test, vm, hotplug_mac, session, is_linux_guest, params
                )
            except Exception as e:
                test.log.warning(f"Still failed after renew ip address: {e}")

        if not hotnic_ip:
            test.log.info("Rebooting vm after renew ip address failed...")
            session = vm.reboot(session=session, serial=True)
            vm.verify_alive()

            hotnic_ip = get_hotplug_nic_ip(
                test, vm, hotplug_mac, session, is_linux_guest, params
            )

        if not hotnic_ip:
            session.close()
            test.fail(f"Round {iteration + 1}: Hotplug nic still can't obtain ip after reboot vm")
        test.log.info(f"Obtain the hotplug nic ip: {hotnic_ip}")

        # Check connectivity
        host_ip = ips['host_ip']
        try:
            check_connectivity(test, host_ip, hotplug_mac, session, is_linux_guest)
        except Exception as e:
            session.close()
            test.fail(f"Round {iteration + 1}: ping failed: {e}")

        session.close()

        # STEP 4: Suspend & Resume guest
        test.log.info("STEP 4: Suspend & Resume guest")
        virsh.suspend(vm_name, **virsh_opt)
        virsh.resume(vm_name, **virsh_opt)

        # STEP 5: Check connectivity again
        test.log.info("STEP 5: Check connectivity again")
        session = vm.wait_for_serial_login(timeout=login_timeout)

        # Check connectivity again
        try:
            check_connectivity(test, host_ip, hotplug_mac, session, is_linux_guest)
        except Exception as e:
            session.close()
            test.fail(f"Round {iteration + 1}: ping failed after resume: {e}")

        session.close()

        # STEP 6: Setlink original nic up
        test.log.info("STEP 6: Setlink original nic up")
        domif_setlink(vm_name, original_mac, "up", "")
        test.log.info("Setlink up succeed")

        # STEP 7: Hot unplug nic
        test.log.info("STEP 7: Hot unplug nic")
        detach_hotplug_interface(test, vm_name, hotplug_mac, hotplug_type)

        test.log.info(f"Round {iteration + 1} finished")
