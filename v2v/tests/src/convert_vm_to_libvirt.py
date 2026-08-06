import re
import logging

from avocado.core import exceptions

from provider import utils_v2v
from virttest import virsh

from virttest.utils_conn import update_crypto_policy
from virttest.utils_test import libvirt as utlv
from provider.utils_v2v import params_get
from virttest.libvirt_xml import vm_xml

from provider.v2v_vmcheck_helper import VMChecker

LOG = logging.getLogger('avocado.v2v.' + __name__)


def run(test, params, env):
    """
    Convert a remote vm to local libvirt(KVM).
    """
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.error("Please set real value for %s" % v)

    enable_legacy_policy = params_get(params, "enable_legacy_policy") == 'yes'
    vm_name = params.get("main_vm")
    source_user = params.get("username", "root")
    vpx_ip = params.get("vpx_hostname")
    vpx_pwd = params.get("vpx_pwd")
    vpx_pwd_file = params.get("vpx_passwd_file")
    vpx_dc = params.get("vpx_dc")
    esx_ip = params.get("esx_hostname")
    esxi_password = params.get('esxi_password')
    src_uri_type = params.get('src_uri_type')
    hypervisor = params.get("hypervisor")
    input_mode = params.get("input_mode")
    target = params.get("target")
    v2v_opts = '-v -x' if params.get('v2v_debug', 'on') == 'on' else ''
    if params.get('v2v_opts'):
        # Add a blank by force
        v2v_opts += ' ' + params.get("v2v_opts")
    # For VDDK
    input_transport = params.get("input_transport")
    vddk_libdir = params.get('vddk_libdir')
    # nfs mount source
    vddk_libdir_src = params.get('vddk_libdir_src')
    vddk_thumbprint = params.get('vddk_thumbprint')
    source_pwd = None

    # Prepare step for different hypervisor
    if enable_legacy_policy:
        update_crypto_policy("LEGACY")

    if hypervisor == "esx":
        source_ip = vpx_ip
        source_pwd = vpx_pwd if src_uri_type != 'esx' else esxi_password
        # Create password file to access ESX hypervisor
        with open(vpx_pwd_file, 'w') as f:
            f.write(source_pwd)
    else:
        raise exceptions.TestSkipError(
            "Unsupported hypervisor: %s" %
            hypervisor)

    # Create libvirt URI for the source node
    v2v_uri = utils_v2v.Uri(hypervisor)
    if src_uri_type == 'esx':
        vpx_dc = None
    remote_uri = v2v_uri.get_uri(source_ip, vpx_dc, esx_ip)
    LOG.debug("Remote host uri for converting: %s", remote_uri)

    # Make sure the VM exist before convert
    virsh_dargs = {'uri': remote_uri, 'remote_ip': source_ip,
                   'remote_user': source_user, 'remote_pwd': source_pwd,
                   'auto_close': True,
                   'debug': True}
    remote_virsh = virsh.VirshPersistent(**virsh_dargs)
    try:
        if not remote_virsh.domain_exists(vm_name):
            raise exceptions.TestError("VM '%s' not exist" % vm_name)
        raw_dumpxml = remote_virsh.dumpxml(vm_name)
        params['original_vmxml'] = raw_dumpxml.stdout_text
    finally:
        remote_virsh.close_session()

    # Prepare libvirt storage pool
    pool_type = params.get("pool_type")
    pool_name = params.get("pool_name")
    pool_target = params.get("pool_target")
    libvirt_pool = utlv.PoolVolumeTest(test, params)
    libvirt_pool.pre_pool(pool_name, pool_type, pool_target, '')

    # Prepare libvirt virtual network
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
                  'os_pool': pool_name, 'hostname': source_ip,
                  'password': source_pwd,
                  'input_transport': input_transport, 'vcenter_host': vpx_ip,
                  'vcenter_password': vpx_pwd,
                  'vddk_thumbprint': vddk_thumbprint,
                  'vddk_libdir': vddk_libdir,
                  'vddk_libdir_src': vddk_libdir_src,
                  'esxi_password': esxi_password,
                  'src_uri_type': src_uri_type,
                  'params': params
                  }
    if vpx_dc:
        v2v_params.update({"vpx_dc": vpx_dc})
    if esx_ip:
        v2v_params.update({"esx_ip": esx_ip})
    if v2v_opts:
        v2v_params.update({"v2v_opts": v2v_opts})

    try:
        # Execute virt-v2v command
        ret = utils_v2v.v2v_cmd(v2v_params)
        utils_v2v.check_exit_status(ret)

        LOG.debug("XML info:\n%s", virsh.dumpxml(vm_name))
        vm = env.create_vm("libvirt", "libvirt", vm_name, params, test.bindir)
        # Win10 is not supported by some cpu model,
        # need to modify to 'host-model'
        unsupport_list = ['win10', 'win2016', 'win2019']
        if params.get('os_version') in unsupport_list:
            LOG.info(
                'Set cpu mode to "host-model" for %s.',
                unsupport_list)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            cpu_xml = vm_xml.VMCPUXML()
            cpu_xml.mode = 'host-model'
            cpu_xml.fallback = 'allow'
            vmxml['cpu'] = cpu_xml
            vmxml.sync()
        vm.start()

        # Check all checkpoints after convert
        vmchecker = VMChecker(test, params, env)
        # Cannot do "params['vmchecker'] = vmchecker" if vm was to_libvirt,
        # or exception 'Fatal Python error: Cannot recover from stack overflow'
        # will happen. Not sure how it happens now, if somebody knows about it,
        # Please help to fix it.
        #
        # The exception happens at:
        # avocado/utils/stacktrace.py, line 98 in analyze_unpickable_item
        # <FIXIT> in future.
        try:
            ret = vmchecker.run()
        finally:
            vmchecker.cleanup()

        if len(ret) == 0:
            LOG.info("All checkpoints passed")
        else:
            raise exceptions.TestFail(
                "%d checkpoints failed: %s" %
                (len(ret), ret))
    finally:
        utils_v2v.cleanup_constant_files(params)
        if enable_legacy_policy:
            update_crypto_policy()
        # Clean libvirt VM
        virsh.remove_domain(vm_name, options="--nvram")
        # Clean libvirt pool
        if libvirt_pool:
            libvirt_pool.cleanup_pool(pool_name, pool_type, pool_target, '')
        # Clean libvirt network
        if libvirt_net:
            libvirt_net.cleanup()
