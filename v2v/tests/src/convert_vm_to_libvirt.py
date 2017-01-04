import re
import os
import logging

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_v2v
from virttest import virsh
from virttest import ssh_key
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import vm_xml

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert a remote vm to local libvirt(KVM).
    """
    for v in params.itervalues():
        if "V2V_EXAMPLE" in v:
            raise exceptions.TestSkipError("Please set real value for %s" % v)

    vm_name = params.get("main_vm")
    source_user = params.get("username", "root")
    xen_ip = params.get("xen_ip")
    xen_pwd = params.get("xen_pwd")
    vpx_ip = params.get("vpx_ip")
    vpx_pwd = params.get("vpx_pwd")
    vpx_pwd_file = params.get("vpx_passwd_file")
    vpx_dc = params.get("vpx_dc")
    esx_ip = params.get("esx_ip")
    hypervisor = params.get("hypervisor")
    input_mode = params.get("input_mode")
    target = params.get("target")
    v2v_opts = params.get("v2v_opts")

    # Prepare step for different hypervisor
    if hypervisor == "esx":
        source_ip = vpx_ip
        source_pwd = vpx_pwd
        # Create password file to access ESX hypervisor
        with open(vpx_pwd_file, 'w') as f:
            f.write(vpx_pwd)
    elif hypervisor == "xen":
        source_ip = xen_ip
        source_pwd = xen_pwd
        # Set up ssh access using ssh-agent and authorized_keys
        ssh_key.setup_ssh_key(source_ip, source_user, source_pwd)
        try:
            utils_misc.add_identities_into_ssh_agent()
        except:
            process.run("ssh-agent -k")
            raise exceptions.TestError("Fail to setup ssh-agent")
    else:
        raise exceptions.TestSkipError("Unspported hypervisor: %s" % hypervisor)

    # Create libvirt URI for the source node
    v2v_uri = utils_v2v.Uri(hypervisor)
    remote_uri = v2v_uri.get_uri(source_ip, vpx_dc, esx_ip)
    logging.debug("Remote host uri for converting: %s", remote_uri)

    # Make sure the VM exist before convert
    virsh_dargs = {'uri': remote_uri, 'remote_ip': source_ip,
                   'remote_user': source_user, 'remote_pwd': source_pwd,
                   'debug': True}
    remote_virsh = virsh.VirshPersistent(**virsh_dargs)
    try:
        if not remote_virsh.domain_exists(vm_name):
            raise exceptions.TestError("VM '%s' not exist" % vm_name)
    finally:
        remote_virsh.close_session()

    # Prepare libvirt storage pool
    pool_type = params.get("pool_type")
    pool_name = params.get("pool_name")
    target_path = params.get("target_path")
    libvirt_pool = utlv.PoolVolumeTest(test, params)
    libvirt_pool.pre_pool(pool_name, pool_type, target_path, '')

    # Preapre libvirt virtual network
    network = params.get("network")
    net_kwargs = {'net_name': network,
                  'address': params.get('network_addr'),
                  'dhcp_start': params.get('network_dhcp_start'),
                  'dhcp_end': params.get('network_dhcp_end')}
    libvirt_net = utlv.LibvirtNetwork('vnet', **net_kwargs)
    net_info = virsh.net_info(network).stdout.strip()
    bridge = re.search(r'Bridge:\s+(\S+)', net_info).group(1)
    params['netdst'] = bridge

    # Maintain a single params for v2v to avoid duplicate parameters
    v2v_params = {'target': target, 'hypervisor': hypervisor,
                  'main_vm': vm_name, 'input_mode': input_mode,
                  'network': network, 'bridge': bridge,
                  'storage': pool_name, 'hostname': source_ip}
    if vpx_dc:
        v2v_params.update({"vpx_dc": vpx_dc})
    if esx_ip:
        v2v_params.update({"esx_ip": esx_ip})
    if v2v_opts:
        v2v_params.update({"v2v_opts": v2v_opts})

    # Set libguestfs environment
    os.environ['LIBGUESTFS_BACKEND'] = 'direct'
    try:
        # Execute virt-v2v command
        ret = utils_v2v.v2v_cmd(v2v_params)
        logging.debug("virt-v2v verbose messages:\n%s", ret)
        if ret.exit_status != 0:
            raise exceptions.TestFail("Convert VM failed")

        logging.debug("XML info:\n%s", virsh.dumpxml(vm_name))
        vm = env.create_vm("libvirt", "libvirt", vm_name, params, test.bindir)
        # Win10 is not supported by some cpu model,
        # need to modify to 'host-model'
        if params.get('os_version') == 'win10':
            logging.info('Set cpu mode to "host-model" for win10')
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            cpu_xml = vm_xml.VMCPUXML()
            cpu_xml.mode = 'host-model'
            cpu_xml.fallback = 'allow'
            vmxml['cpu'] = cpu_xml
            vmxml.sync()
        vm.start()

        # Check all checkpoints after convert
        vmchecker = VMChecker(test, params, env)
        ret = vmchecker.run()
        if len(ret) == 0:
            logging.info("All checkpoints passed")
        else:
            raise exceptions.TestFail("%d checkpoints failed: %s" % (len(ret), ret))
    finally:
        vmcheck = utils_v2v.VMCheck(test, params, env)
        vmcheck.cleanup()
        if hypervisor == "esx":
            os.remove(vpx_pwd_file)
        if hypervisor == "xen":
            process.run("ssh-agent -k")
        # Clean libvirt VM
        virsh.remove_domain(vm_name)
        # Clean libvirt pool
        if libvirt_pool:
            libvirt_pool.cleanup_pool(pool_name, pool_type, target_path, '')
        # Clean libvirt network
        if libvirt_net:
            libvirt_net.cleanup()
