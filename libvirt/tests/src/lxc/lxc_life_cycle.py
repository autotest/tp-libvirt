import os
import re
import shutil
import logging

import aexpect

from avocado.utils import process
from avocado.core.exceptions import TestFail

from virttest import virsh
from virttest import utils_net
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.devices.emulator import Emulator
from virttest.libvirt_xml.devices.console import Console
from virttest.libvirt_xml.devices.filesystem import Filesystem
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    LXC container life cycle testing by virsh command
    """
    uri = params.get("connect_uri", "lxc:///")
    vm_name = params.get("main_vm")
    dom_type = params.get("lxc_domtype", "lxc")
    vcpu = int(params.get("lxc_vcpu", 1))
    max_mem = int(params.get("lxc_max_mem", 500000))
    current_mem = int(params.get("lxc_current_mem", 500000))
    os_type = params.get("lxc_ostype", "exe")
    os_arch = params.get("lxc_osarch", "x86_64")
    os_init = params.get("lxc_osinit", "/bin/sh")
    emulator_path = params.get("lxc_emulator",
                               "/usr/libexec/libvirt_lxc")
    interface_type = params.get("lxc_interface_type", "network")
    net_name = params.get("lxc_net_name", "default")
    full_os = ("yes" == params.get("lxc_full_os", "no"))
    install_root = params.get("lxc_install_root", "/")
    fs_target = params.get("lxc_fs_target", "/")
    fs_accessmode = params.get("lxc_fs_accessmode", "passthrough")
    passwd = params.get("lxc_fs_passwd", "redhat")

    def generate_container_xml():
        """
        Generate container xml
        """
        vmxml = vm_xml.VMXML(dom_type)
        vmxml.vm_name = vm_name
        vmxml.max_mem = max_mem
        vmxml.current_mem = current_mem
        vmxml.vcpu = vcpu
        # Generate os
        vm_os = vm_xml.VMOSXML()
        vm_os.type = os_type
        vm_os.arch = os_arch
        vm_os.init = os_init
        vmxml.os = vm_os
        # Generate emulator
        emulator = Emulator()
        emulator.path = emulator_path
        # Generate console
        console = Console()
        filesystem = Filesystem()
        filesystem.accessmode = fs_accessmode
        filesystem.source = {'dir': install_root}
        filesystem.target = {'dir': fs_target}
        # Add emulator and console in devices
        devices = vm_xml.VMXMLDevices()
        devices.append(emulator)
        devices.append(console)
        devices.append(filesystem)
        # Add network device
        network = Interface(type_name=interface_type)
        network.mac_address = utils_net.generate_mac_address_simple()
        network.source = {interface_type: net_name}
        devices.append(network)
        vmxml.set_devices(devices)
        return vmxml

    def check_state(expected_state):
        result = virsh.domstate(vm_name, uri=uri)
        utlv.check_exit_status(result)
        vm_state = result.stdout.strip()
        if vm_state == expected_state:
            logging.info("Get expected state: %s", vm_state)
        else:
            raise TestFail("Get unexpected state: %s", vm_state)

    virsh_args = {'uri': uri, 'debug': True}
    try:
        vmxml = generate_container_xml()
        with open(vmxml.xml, 'r') as f:
            logging.info("Container XML:\n%s", f.read())

        if full_os:
            if not os.path.exists(install_root):
                os.mkdir(install_root)
            # Install core os under installroot
            cmd = "yum --releasever=/ --installroot=%s" % install_root
            cmd += " --nogpgcheck -y groupinstall core"
            process.run(cmd, shell=True)
            # Fix root login on console
            process.run("echo 'pts/0' >> %s/etc/securetty" % install_root,
                        shell=True)
            for i in ["session    required     pam_selinux.so close",
                      "session    required     pam_selinux.so open",
                      "session    required     pam_loginuid.so"]:
                process.run('sed -i s/"%s\"/"#%s"/g %s/etc/pam.d/login' %
                            (i, i, install_root), shell=True)
                # Fix root login for sshd
                process.run('sed -i s/"%s\"/"#%s"/g %s/etc/pam.d/sshd' %
                            (i, i, install_root), shell=True)

            # Config basic network
            net_file = install_root + '/etc/sysconfig/network'
            with open(net_file, 'w') as f:
                f.write('NETWORKING=yes\nHOSTNAME=%s\n' % vm_name)
            net_script = install_root + '/etc/sysconfig/network-scripts/ifcfg-eth0'
            with open(net_script, 'w') as f:
                f.write('DEVICE=eth0\nBOOTPROTO=dhcp\nONBOOT=yes\n')

            # Set root password and enable sshd
            session = aexpect.ShellSession("chroot %s" % install_root)
            session.sendline('echo %s|passwd root --stdin' % passwd)
            session.sendline('chkconfig sshd on')
            session.close()

        # Create
        result = virsh.create(vmxml.xml, **virsh_args)
        utlv.check_exit_status(result)
        check_state('running')

        # Destroy
        result = virsh.destroy(vm_name, **virsh_args)
        utlv.check_exit_status(result)
        if not virsh.domain_exists(vm_name, **virsh_args):
            logging.info("Destroy transient LXC domain successfully")
        else:
            raise TestFail("Transient LXC domain still exist after destroy")

        # Define
        result = virsh.define(vmxml.xml, **virsh_args)
        utlv.check_exit_status(result)
        check_state('shut off')

        # List
        result = virsh.dom_list('--inactive', **virsh_args)
        utlv.check_exit_status(result)
        if re.findall("(%s)\s+shut off" % vm_name, result.stdout.strip()):
            logging.info("Find %s in virsh list output", vm_name)
        else:
            raise TestFail("Not find %s in virsh list output")

        # Dumpxml
        result = virsh.dumpxml(vm_name, uri=uri, debug=False)
        utlv.check_exit_status(result)

        # Edit
        edit_vcpu = '2'
        logging.info("Change vcpu of LXC container to %s", edit_vcpu)
        edit_cmd = [r":%s /[0-9]*<\/vcpu>/" + edit_vcpu + r"<\/vcpu>"]
        if not utlv.exec_virsh_edit(vm_name, edit_cmd, connect_uri=uri):
            raise TestFail("Run edit command fail")
        else:
            result = virsh.dumpxml(vm_name, **virsh_args)
            new_vcpu = re.search(r'(\d*)</vcpu>', result.stdout.strip()).group(1)
            if new_vcpu == edit_vcpu:
                logging.info("vcpu number is expected after do edit")
            else:
                raise TestFail("vcpu number is unexpected after do edit")

        # Start
        result = virsh.start(vm_name, **virsh_args)
        utlv.check_exit_status(result)
        check_state('running')

        # Suspend
        result = virsh.suspend(vm_name, **virsh_args)
        utlv.check_exit_status(result)
        check_state('paused')

        # Resume
        result = virsh.resume(vm_name, **virsh_args)
        utlv.check_exit_status(result)
        check_state('running')

        # Reboot(not supported on RHEL6)
        result = virsh.reboot(vm_name, **virsh_args)
        supported_err = 'not supported by the connection driver: virDomainReboot'
        if supported_err in result.stderr.strip():
            logging.info("Reboot is not supported")
        else:
            utlv.check_exit_status(result)

        # Destroy
        result = virsh.destroy(vm_name, **virsh_args)
        utlv.check_exit_status(result)
        check_state('shut off')

        # Undefine
        result = virsh.undefine(vm_name, **virsh_args)
        utlv.check_exit_status(result)
        if not virsh.domain_exists(vm_name, **virsh_args):
            logging.info("Undefine LXC domain successfully")
        else:
            raise TestFail("LXC domain still exist after undefine")

    finally:
        virsh.remove_domain(vm_name, **virsh_args)
        if full_os and os.path.exists(install_root):
            shutil.rmtree(install_root)
