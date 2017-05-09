import os
import logging

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_v2v
from virttest import virsh
from virttest import ssh_key
from virttest import utils_misc
from virttest import utils_sasl

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert a remote vm to remote ovirt node.
    """
    for v in params.itervalues():
        if "V2V_EXAMPLE" in v:
            raise exceptions.TestSkipError("Please set real value for %s" % v)

    vm_name = params.get("main_vm")
    target = params.get("target")
    hypervisor = params.get("hypervisor")
    input_mode = params.get("input_mode")
    storage = params.get('storage')
    network = params.get('network')
    bridge = params.get('bridge')
    source_user = params.get("username", "root")
    xen_ip = params.get("xen_hostname")
    xen_pwd = params.get("xen_pwd")
    vpx_ip = params.get("vpx_hostname")
    vpx_pwd = params.get("vpx_pwd")
    vpx_passwd_file = params.get("vpx_passwd_file")
    vpx_dc = params.get("vpx_dc")
    esx_ip = params.get("esx_hostname")
    address_cache = env.get('address_cache')
    v2v_opts = params.get("v2v_opts")
    v2v_timeout = int(params.get('v2v_timeout', 1200))

    # Prepare step for different hypervisor
    if hypervisor == "esx":
        source_ip = vpx_ip
        source_pwd = vpx_pwd
        # Create password file to access ESX hypervisor
        with open(vpx_passwd_file, 'w') as f:
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
    elif hypervisor == "kvm":
        source_ip = None
        source_pwd = None
    else:
        raise exceptions.TestSkipError("Unspported hypervisor: %s" % hypervisor)

    # Create libvirt URI
    v2v_uri = utils_v2v.Uri(hypervisor)
    remote_uri = v2v_uri.get_uri(source_ip, vpx_dc, esx_ip)
    logging.debug("libvirt URI for converting: %s", remote_uri)

    # Make sure the VM exist before convert
    v2v_virsh = None
    close_virsh = False
    if hypervisor == 'kvm':
        v2v_virsh = virsh
    else:
        virsh_dargs = {'uri': remote_uri, 'remote_ip': source_ip,
                       'remote_user': source_user, 'remote_pwd': source_pwd,
                       'debug': True}
        v2v_virsh = virsh.VirshPersistent(**virsh_dargs)
        close_virsh = True
    try:
        if not v2v_virsh.domain_exists(vm_name):
            raise exceptions.TestError("VM '%s' not exist" % vm_name)
    finally:
        if close_virsh:
            v2v_virsh.close_session()

    # Create SASL user on the ovirt host
    user_pwd = "[['%s', '%s']]" % (params.get("sasl_user"),
                                   params.get("sasl_pwd"))
    v2v_sasl = utils_sasl.SASL(sasl_user_pwd=user_pwd)
    v2v_sasl.server_ip = params.get("remote_ip")
    v2v_sasl.server_user = params.get('remote_user')
    v2v_sasl.server_pwd = params.get('remote_pwd')
    v2v_sasl.setup(remote=True)

    # Maintain a single params for v2v to avoid duplicate parameters
    v2v_params = {'target': target, 'hypervisor': hypervisor,
                  'main_vm': vm_name, 'input_mode': input_mode,
                  'network': network, 'bridge': bridge,
                  'storage': storage, 'hostname': source_ip}
    if vpx_dc:
        v2v_params.update({"vpx_dc": vpx_dc})
    if esx_ip:
        v2v_params.update({"esx_ip": esx_ip})
    if v2v_opts:
        v2v_params.update({"v2v_opts": v2v_opts})
    output_format = params.get('output_format')
    if output_format:
        v2v_params.update({'output_format': 'qcow2'})

    # Set libguestfs environment variable
    os.environ['LIBGUESTFS_BACKEND'] = 'direct'
    try:
        # Execute virt-v2v command
        ret = utils_v2v.v2v_cmd(v2v_params)
        logging.debug("virt-v2v verbose messages:\n%s", ret)
        if ret.exit_status != 0:
            raise exceptions.TestFail("Convert VM failed")

        # Import the VM to oVirt Data Center from export domain, and start it
        if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                            timeout=v2v_timeout):
            raise exceptions.TestError("Import VM failed")

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
        if v2v_sasl:
            v2v_sasl.cleanup()
        if hypervisor == "esx":
            os.remove(vpx_passwd_file)
        if hypervisor == "xen":
            process.run("ssh-agent -k")
