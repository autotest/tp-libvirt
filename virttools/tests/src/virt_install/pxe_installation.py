import logging as log
import os

from avocado.utils import process

from virttest import virsh
from virttest.utils_misc import cmd_status_output
from virttest.utils_test import libvirt


logging = log.getLogger("avocado." + __name__)

cleanup_actions = []

tftp_dir = "/var/lib/tftpboot"
boot_file = "pxelinux.cfg"
net_name = "tftpnet"


def create_tftp_content(install_tree_url, kickstart_url):
    """
    Creates the folder for the tftp server,
    downloads images assuming they are below /images,
    and creates the pxe configuration file

    :param install_tree_url: url of the installation tree
    :param kickstart_url: url of the kickstart file
    """

    process.run("mkdir " + tftp_dir, ignore_status=False, shell=True, verbose=True)
    cleanup_actions.insert(0, lambda: process.run("rm -rf " + tftp_dir, ignore_status=False, shell=True, verbose=True))

    pxeconfig_content = """# pxelinux
default linux
label linux
kernel kernel.img
initrd initrd.img
append ip=dhcp inst.repo=%s inst.ks=%s
""" % (install_tree_url, kickstart_url)

    with open(os.path.join(tftp_dir, boot_file), "w") as f:
        f.write(pxeconfig_content)

    cmds = []

    initrd_img_url = install_tree_url + "/images/initrd.img"
    kernel_img_url = install_tree_url + "/images/kernel.img"

    cmds.append("curl %s -o %s/initrd.img" % (initrd_img_url, tftp_dir))
    cmds.append("curl %s -o %s/kernel.img" % (kernel_img_url, tftp_dir))

    cmds.append("chmod -R a+r " + tftp_dir)
    cmds.append("chown -R nobody: " + tftp_dir)
    cmds.append("chcon -R --reference /usr/sbin/dnsmasq " + tftp_dir)
    cmds.append("chcon -R --reference /usr/libexec/libvirt_leaseshelper " + tftp_dir)

    for cmd in cmds:
        process.run(cmd, ignore_status=False, shell=True, verbose=True)


def create_tftp_network():
    """
    Creates a libvirt network that will serve
    the tftp content
    """

    net_params = {
            "net_forward": "{'mode':'nat'}",
            "net_ip_address": "192.168.150.1",
            "dhcp_start_ipv4": "192.168.150.2",
            "dhcp_end_ipv4": "192.168.150.254",
            "tftp_root": tftp_dir,
            "bootp_file": boot_file
            }

    net_xml = libvirt.create_net_xml(net_name, net_params)
    virsh.net_create(net_xml.xml, debug=True, ignore_status=False)
    cleanup_actions.insert(0, lambda: virsh.net_destroy(net_name))


def run(test, params, env):
    """
    Install VM with s390x specific pxe configuration
    """

    vm_name = 'pxe_installation'
    vm = None

    try:
        install_tree_url = params.get("install_tree_url")
        kickstart_url = params.get("kickstart_url")
        create_tftp_content(install_tree_url, kickstart_url)
        create_tftp_network()

        cmd = ("virt-install --pxe --name %s"
               " --disk size=10"
               " --vcpus 2 --memory 2048"
               " --osinfo detect=on,require=off"
               " --nographics"
               " --wait 10"
               " --noreboot"
               " --network network=%s" %
               (vm_name, net_name))

        cmd_status_output(cmd, shell=True, timeout=600)
        logging.debug("Installation finished")
        env.create_vm(vm_type='libvirt', target=None, name=vm_name, params=params, bindir=test.bindir)
        vm = env.get_vm(vm_name)
        cleanup_actions.insert(0, lambda: vm.undefine(options="--remove-all-storage"))

        if vm.is_dead():  # kickstart might shut machine down
            logging.debug("VM is dead, starting")
            vm.start()

        session = vm.wait_for_login().close()

    finally:
        if vm and vm.is_alive():
            vm.destroy()
        for action in cleanup_actions:
            try:
                action()
            except:
                logging.debug("There were errors during cleanup. Please check the log.")
