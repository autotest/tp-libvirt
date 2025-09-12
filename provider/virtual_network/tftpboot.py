# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: Red Hat Inc. 2024
# Author: Sebastian Mitterle <smitterl@redhat.com>
import logging as log
import os

from avocado.utils import process
from virttest import virsh
from virttest.utils_test import libvirt

logging = log.getLogger("avocado." + __name__)

tftp_dir = "/var/lib/tftpboot"
boot_file = "pxelinux.cfg"
net_name = "tftpnet"

cleanup_actions = []


def _pxeconfig_content(entries, install_tree_url, kickstart_url):
    """
    Returns the configuration file contents for given entry names.
    It will set the first entry as default and only use a single set
    of installation data for simplicity because we want to test the
    installation starts from the right folder, not the installation
    itself.

    :param entries: list of names for the configuration entries
    """
    kernel_cmdline = "append ip=dhcp inst.repo=%s inst.noverifyssl" % install_tree_url
    if kickstart_url:
        kernel_cmdline += " inst.ks=%s" % kickstart_url
    else:
        logging.debug("Create pxelinux.cfg without kickstart.")

    contents = ["# pxelinux", f"default {entries[0]}"]
    for entry in entries:
        contents.append(f"label {entry}")
        contents.append(f"kernel {entry}/kernel.img")
        contents.append(f"initrd {entry}/initrd.img")
        contents.append(kernel_cmdline)
    return "\n".join(contents)


def create_tftp_content(install_tree_url, kickstart_url, arch, entries):
    """
    Creates the folder for the tftp server,
    downloads images assuming they are below /images,
    and creates the pxe configuration file

    :param install_tree_url: url of the installation tree
    :param kickstart_url: url of the kickstart file
    :param arch: the architecture
    :params entries: list of entry names for the pxe configuration
    """

    if arch != "s390x":
        raise NotImplementedError(f"No implementation available for '{arch}'.")
    if not entries or len([x for x in entries if len(x) == 0]) > 0:
        raise ValueError(f"Expecting list of non-empty strings, got {entries}")

    cmds = []

    initrd_img_url = install_tree_url + "/images/initrd.img"
    kernel_img_url = install_tree_url + "/images/kernel.img"

    process.run("mkdir " + tftp_dir, ignore_status=False, shell=True, verbose=True)
    cleanup_actions.insert(
        0,
        lambda: process.run(
            "rm -rf " + tftp_dir, ignore_status=False, shell=True, verbose=True
        ),
    )

    for entry in entries:
        entry_dir = os.path.join(tftp_dir, entry)
        process.run("mkdir " + entry_dir, ignore_status=False, shell=True, verbose=True)
        cmds.append("curl %s -o %s/initrd.img" % (initrd_img_url, entry_dir))
        cmds.append("curl %s -o %s/kernel.img" % (kernel_img_url, entry_dir))

    pxeconfig_content = _pxeconfig_content(entries, install_tree_url, kickstart_url)

    with open(os.path.join(tftp_dir, boot_file), "w") as f:
        f.write(pxeconfig_content)

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
        "bootp_file": boot_file,
    }

    net_xml = libvirt.create_net_xml(net_name, net_params)
    virsh.net_create(net_xml.xml, debug=True, ignore_status=False)
    cleanup_actions.insert(0, lambda: virsh.net_destroy(net_name))


def cleanup():
    """
    Runs registered clean up actions
    """
    for action in cleanup_actions:
        try:
            action()
        except:
            logging.debug("There were errors during cleanup. Please check the log.")
