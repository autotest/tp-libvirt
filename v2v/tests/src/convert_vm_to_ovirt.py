import os
import logging

from autotest.client import utils
from autotest.client.shared import ssh_key
from autotest.client.shared import error

from virttest import utils_v2v
from virttest import utils_misc


def get_args_dict(params):
    args_dict = {}
    keys_list = ['target', 'main_vm', 'ovirt_engine_url', 'ovirt_engine_user',
                 'ovirt_engine_password', 'hypervisor', 'storage',
                 'remote_node_user', 'v2v_opts', 'export_name', 'storage_name',
                 'cluster_name', 'remote_ip']

    if params.get('network'):
        keys_list.append('network')

    if params.get('bridge'):
        keys_list.append('bridge')

    hypervisor = params.get('hypervisor')
    if hypervisor == 'esx':
        esx_args_list = ['vpx_ip', 'vpx_pwd', 'vpx_pwd_file',
                         'vpx_dc', 'esx_ip', 'hostname']
        keys_list += esx_args_list

    if hypervisor == 'xen':
        xen_args_list = ['xen_ip', 'xen_pwd', 'hostname']
        keys_list += xen_args_list

    for key in keys_list:
        val = params.get(key)
        if val is None:
            raise KeyError("%s doesn't exist" % key)
        elif val.count("EXAMPLE"):
            raise error.TestNAError("Please provide specific value for %s: %s"
                                    % (key, val))
        else:
            args_dict[key] = val

    logging.debug(args_dict)
    return args_dict


def run(test, params, env):
    """
    Test convert vm to ovirt
    """
    args_dict = get_args_dict(params)
    hypervisor = args_dict.get('hypervisor')
    xen_ip = args_dict.get('xen_ip')
    xen_pwd = args_dict.get('xen_pwd')
    remote_node_user = args_dict.get('remote_node_user', 'root')
    vpx_pwd = args_dict.get('vpx_pwd')
    vpx_pwd_file = args_dict.get('vpx_pwd_file')
    address_cache = env.get('address_cache')
    if hypervisor == 'xen':
        # Set up ssh access using ssh-agent and authorized_keys
        ssh_key.setup_ssh_key(xen_ip, user=remote_node_user,
                              port=22, password=xen_pwd)
        try:
            utils_misc.add_identities_into_ssh_agent()
        except:
            utils.run("ssh-agent -k")
            raise error.TestError("Failed to start 'ssh-agent'")

    if hypervisor == 'esx':
        fp = open(vpx_pwd_file, 'w')
        fp.write(vpx_pwd)
        fp.close()

    try:
        # Set libguestfs environment variable
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        # Run virt-v2v command
        ret = utils_v2v.v2v_cmd(args_dict)
        logging.debug("virt-v2v verbose messages:\n%s", ret)
        if ret.exit_status != 0:
            raise error.TestFail("Convert VM failed")

        # Import the VM to oVirt Data Center from export domain
        if not utils_v2v.import_vm_to_ovirt(params, address_cache):
            raise error.TestFail("Import VM failed")
    finally:
        if hypervisor == "xen":
            utils.run("ssh-agent -k")
        if hypervisor == "esx":
            utils.run("rm -rf %s" % vpx_pwd_file)
