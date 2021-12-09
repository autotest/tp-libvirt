import logging
import os
from virttest import utils_package
from avocado.utils import process
from virttest.utils_test.__init__ import ping
from virttest import virsh
from virttest import libvirt_xml
from virttest.libvirt_xml.devices import interface


def check_nss(name):
    """
    on host, check the name can be resolved by ping and ssh

    :param name: guest's hostname or the domain name
    :return: True or False
    """
    ping_s, _ = ping(dest=name, count=5, timeout=10)
    if ping_s:
        logging.error("Failed to ping '%s': %s" % (name, ping_s))
        return False
    logging.debug("ping %s succeed" % name)
    return True


def set_guest_hostname(session, name):
    """
    set a hostname for the guest

    :param session: the guest's session
    :param name: the hostname for the guest
    :return: True or False
    """
    cmd = "hostnamectl set-hostname %s" % name
    session.cmd(cmd)
    current_name = session.cmd_output("hostname")
    if name not in current_name:
        logging.error("Set hostname on the guest fail, current hostname is %s" % current_name)
        return False
    else:
        return True


def run(test, params, env):
    """
    Test the libvirt-nss module did work properly

    1. Install the libvirt-nss package if it is not exists;
    2. Configure the "/etc/nsswitch.conf";
    3. Start the vm and check if the libvirt-nss module works properly by checking if the guest's hostname or
    domain name can be resolved successfully
    """
    nss_option = params.get("nss_option", None)
    guest_name = params.get("guest_name", "nssguest")
    net_name = params.get("net_name", "default")
    conf_file = "/etc/nsswitch.conf"
    bak_path = "/etc/nsswitch.conf.bak"
    try:
        if not utils_package.package_install(["libvirt-nss"]):
            test.error("Failed to install libvirt-nss on host")
        if nss_option:
            backup_conf_cmd = "cp %s  %s" % (conf_file, bak_path)
            edit_cmd = "sed -i 's/^hosts.*files/& %s/' %s" % (nss_option, conf_file)
            process.run(backup_conf_cmd, shell=True, ignore_status=True)
            process.run(edit_cmd, shell=True, ignore_status=False, verbose=True)
            out = process.run("grep ^hosts %s" % conf_file, shell=True).stdout_text
            logging.debug("current setting in %s: %s" % (conf_file, out))
        # confirm there is an interface connected to default, then start vm
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # make sure the vm is using default network
        vmxml = vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml
        new_iface.type_name = "network"
        new_iface.source = {'network': net_name}
        vmxml.add_device(new_iface)
        vmxml.sync()
        vm.start()
        if nss_option == "libvirt":
            test_name = guest_name
            session = vm.wait_for_login()
            s = set_guest_hostname(session, test_name)
            if not s:
                test.cancel("Set hostname on guest failed")
            # Restart the NetworkManager service on guest to ensure the new hostname
            # will be stated during dhcp ip address applying.
            cmd = "systemctl restart NetworkManager"
            if session.cmd_status(cmd):
                test.cancel("Restart NetworkManager on guest fail!")
            session.close()
            result = virsh.net_dhcp_leases(net_name, debug=False, ignore_status=True).stdout_text
            logging.debug("the net-dhcp-lease output: '%s'" % result)
            if test_name not in result:
                test.error("net-dhcp-leases does not show the guest's name correctly")
        elif nss_option == "libvirt_guest":
            test_name = vm_name
            # confirm guest boot successfully and get ip address
            session = vm.wait_for_login()
            session.close()
        if nss_option:
            state = check_nss(test_name)
            if not state:
                test.fail("Host can not access to guest by the %s" % test_name)
    finally:
        if os.path.exists(bak_path):
            process.run("mv %s %s" % (bak_path, conf_file), shell=True, ignore_status=False)
        vmxml_backup.sync()
