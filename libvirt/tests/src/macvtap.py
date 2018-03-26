import os

import aexpect

from avocado.utils import process

from virttest import remote
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import ping


def run(test, params, env):
    """
    This test is for macvtap nic

    1. Check and backup environment
    2. Configure guest, add new nic and set a static ip address
    3. According to nic mode, start test
    4. Recover environment
    """
    vm_names = params.get("vms").split()
    remote_ip = params.get("remote_ip", "ENTER.YOUR.REMOTE.IP")
    iface_mode = params.get("mode", "vepa")
    eth_card_no = params.get("eth_card_no", "ENTER.YOUR.DEV.NAME")
    vm1_ip = params.get("vm1_ip", "ENTER.YOUR.GUEST1.IP")
    vm2_ip = params.get("vm2_ip", "ENTER.YOUR.GUEST2.IP")
    eth_config_file = params.get("eth_config_file",
                                 "ENTER.YOUR.CONFIG.FILE.PATH")
    persistent_net_file = params.get("persistent_net_file",
                                     "ENTER.YOUR.RULE.FILE.PATH")

    param_keys = ["remote_ip", "vm1_ip", "vm2_ip", "eth_card_no",
                  "eth_config_file", "persistent_net_file"]
    param_values = [remote_ip, vm1_ip, vm2_ip, eth_card_no,
                    eth_config_file, persistent_net_file]
    for key, value in zip(param_keys, param_values):
        if value.count("ENTER.YOUR"):
            test.cancel("Parameter '%s'(%s) is not configured."
                        % (key, value))

    vm1 = env.get_vm(vm_names[0])
    vm2 = None
    if len(vm_names) > 1:
        vm2 = env.get_vm(vm_names[1])

    if eth_card_no not in utils_net.get_net_if():
        test.cancel("Device %s do not exists." % eth_card_no)
    try:
        iface_cls = utils_net.Interface(eth_card_no)
        origin_status = iface_cls.is_up()
        if not origin_status:
            iface_cls.up()
    except process.CmdError as detail:
        test.cancel(str(detail))
    br_cls = utils_net.Bridge()
    if eth_card_no in br_cls.list_iface():
        test.cancel("%s has been used!" % eth_card_no)
    vmxml1 = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[0])
    if vm2:
        vmxml2 = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[1])

    def guest_config(vm, ip_addr):
        """
        Add a new nic to guest and set a static ip address

        :param vm: Configured guest
        :param ip_addr: Set ip address
        """
        # Attach an interface device
        # Use attach-device, not attach-interface, because attach-interface
        # doesn't support 'direct'
        interface_class = vm_xml.VMXML.get_device_class('interface')
        interface = interface_class(type_name="direct")
        interface.source = dict(dev=str(eth_card_no), mode=str(iface_mode))
        interface.model = "virtio"
        interface.xmltreefile.write()
        if vm.is_alive():
            vm.destroy(gracefully=False)
        virsh.attach_device(vm.name, interface.xml, flagstr="--config")
        os.remove(interface.xml)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        new_nic = vmxml.get_devices(device_type="interface")[-1]

        # Modify new interface's IP
        vm.start()
        session = vm.wait_for_login()
        eth_name = utils_net.get_linux_ifname(session, new_nic.mac_address)
        eth_config_detail_list = ['DEVICE=%s' % eth_name,
                                  'HWADDR=%s' % new_nic.mac_address,
                                  'ONBOOT=yes',
                                  'BOOTPROTO=static',
                                  'IPADDR=%s' % ip_addr]
        remote_file = remote.RemoteFile(vm.get_address(), 'scp', 'root',
                                        params.get('password'), 22,
                                        eth_config_file)
        remote_file.truncate()
        remote_file.add(eth_config_detail_list, linesep='\n')
        try:
            # Attached interface maybe already active
            session.cmd("ifdown %s" % eth_name)
        except aexpect.ShellCmdError:
            test.fail("ifdown %s failed." % eth_name)

        try:
            session.cmd("ifup %s" % eth_name)
        except aexpect.ShellCmdError:
            test.fail("ifup %s failed." % eth_name)
        return session

    def guest_clean(vm, vmxml):
        """
        Recover guest configuration

        :param: Recovered guest
        """
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        session.cmd("rm -f %s" % eth_config_file)
        session.cmd("sync")
        try:
            # Delete the last 3 lines
            session.cmd('sed -i "$[$(cat %s | wc -l) - 2],$"d %s'
                        % (persistent_net_file, persistent_net_file))
            session.cmd("sync")
        except aexpect.ShellCmdError:
            # This file may not exists
            pass
        vm.destroy()
        vmxml.sync()

    def vepa_test(session):
        """
        vepa mode test.
        Check guest can ping remote host
        """
        ping_s, _ = ping(remote_ip, count=1, timeout=5, session=session)
        if ping_s:
            test.fail("%s ping %s failed." % (vm1.name, remote_ip))

    def private_test(session):
        """
        private mode test.
        Check guest cannot ping other guest, but can pin remote host
        """
        ping_s, _ = ping(remote_ip, count=1, timeout=5, session=session)
        if ping_s:
            test.fail("%s ping %s failed." % (vm1.name, remote_ip))
        ping_s, _ = ping(vm2_ip, count=1, timeout=5, session=session)
        if not ping_s:
            test.fail("%s ping %s succeed, but expect failed."
                      % (vm1.name, vm2.name))
        try:
            iface_cls.down()
        except process.CmdError as detail:
            test.cancel(str(detail))
        ping_s, _ = ping(vm2_ip, count=1, timeout=5, session=session)
        if not ping_s:
            test.fail("%s ping %s succeed, but expect failed."
                      % (vm1.name, remote_ip))

    def passthrough_test(session):
        """
        passthrough mode test.
        Check guest can ping remote host.
        When guest is running, local host cannot ping remote host,
        When guest is poweroff, local host can ping remote host,
        """
        ping_s, _ = ping(remote_ip, count=1, timeout=5, session=session)
        if ping_s:
            test.fail("%s ping %s failed."
                      % (vm1.name, remote_ip))
        ping_s, _ = ping(remote_ip, count=1, timeout=5)
        if not ping_s:
            test.fail("host ping %s succeed, but expect fail."
                      % remote_ip)
        vm1.destroy(gracefully=False)
        ping_s, _ = ping(remote_ip, count=1, timeout=5)
        if ping_s:
            test.fail("host ping %s failed."
                      % remote_ip)

    def bridge_test(session):
        """
        bridge mode test.
        Check guest can ping remote host
        guest can ping other guest when macvtap nic is up
        guest cannot ping remote host when macvtap nic is up
        """
        ping_s, _ = ping(remote_ip, count=1, timeout=5, session=session)
        if ping_s:
            test.fail("%s ping %s failed."
                      % (vm1.name, remote_ip))
        ping_s, _ = ping(vm2_ip, count=1, timeout=5, session=session)
        if ping_s:
            test.fail("%s ping %s failed."
                      % (vm1.name, vm2.name))
        try:
            iface_cls.down()
        except process.CmdError as detail:
            test.cancel(str(detail))
        ping_s, _ = ping(remote_ip, count=1, timeout=5, session=session)
        if not ping_s:
            test.fail("%s ping %s success, but expected fail."
                      % (vm1.name, remote_ip))
    # Test start
    try:
        try:
            session = guest_config(vm1, vm1_ip)
        except remote.LoginTimeoutError as fail:
            test.fail(str(fail))
        if vm2:
            try:
                guest_config(vm2, vm2_ip)
            except remote.LoginTimeoutError as fail:
                test.fail(str(fail))

        # Four mode test
        if iface_mode == "vepa":
            vepa_test(session)
        elif iface_mode == "bridge":
            bridge_test(session)
        elif iface_mode == "private":
            private_test(session)
        elif iface_mode == "passthrough":
            passthrough_test(session)
    finally:
        if iface_cls.is_up():
            if not origin_status:
                iface_cls.down()
        else:
            if origin_status:
                iface_cls.up()
        guest_clean(vm1, vmxml1)
        if vm2:
            guest_clean(vm2, vmxml2)
