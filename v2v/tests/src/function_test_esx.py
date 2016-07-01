import os
import logging

from avocado.core import exceptions

from virttest import virsh
from virttest import utils_v2v
from virttest import utils_sasl
from virttest import data_dir
from virttest.utils_test import libvirt

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert specific esx guest
    """
    for v in params.itervalues():
        if "V2V_EXAMPLE" in v:
            raise exceptions.TestSkipError("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    remote_host = params.get('remote_host')
    esx_ip = params.get('esx_ip')
    vpx_dc = params.get('vpx_dc')
    vm_name = params.get('main_vm')
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    status_error = 'yes' == params.get('status_error', 'no')
    address_cache = env.get('address_cache')
    checkpoint = params.get('checkpoint')

    def check_device_exist(check, virsh_session_id):
        """
        Check if device exist after convertion
        """
        xml = virsh.dumpxml(vm_name, session_id=virsh_session_id).stdout
        if check == 'cdrom':
            if "device='cdrom'" not in xml:
                raise exceptions.TestFail('CDROM no longer exists')

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        libvirt.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if not status_error:
            if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                timeout=v2v_timeout):
                raise exceptions.TestFail('Import VM failed')
            if checkpoint:
                if checkpoint == 'cdrom':
                    virsh_session = utils_sasl.VirshSessionSASL(params)
                    virsh_session_id = virsh_session.get_id()
                    check_device_exist('cdrom', virsh_session_id)

    try:
        v2v_params = {
            'hostname': remote_host, 'hypervisor': 'esx', 'main_vm': vm_name,
            'vpx_dc': vpx_dc, 'esx_ip': esx_ip,
            'v2v_opts': '-v -x', 'input_mode': 'libvirt',
            'storage': params.get('output_storage', 'default'),
            'network': params.get('network'),
            'bridge':  params.get('bridge'),
            'target':  params.get('target')
        }

        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        # Create password file for access to ESX hypervisor
        vpx_passwd = params.get("vpx_password")
        logging.debug(vpx_passwd)
        vpx_passwd_file = os.path.join(data_dir.get_tmp_dir(), "vpx_passwd")
        with open(vpx_passwd_file, 'w') as pwd_f:
            pwd_f.write(vpx_passwd)
        v2v_params['v2v_opts'] += " --password-file %s" % vpx_passwd_file

        if params.get('output_format'):
            v2v_params.update({'output_format': params.get('output_format')})

        # Create SASL user on the ovirt host
        user_pwd = "[['%s', '%s']]" % (params.get("sasl_user"),
                                       params.get("sasl_pwd"))
        v2v_sasl = utils_sasl.SASL(sasl_user_pwd=user_pwd)
        v2v_sasl.server_ip = params.get("remote_ip")
        v2v_sasl.server_user = params.get('remote_user')
        v2v_sasl.server_pwd = params.get('remote_pwd')
        v2v_sasl.setup(remote=True)

        v2v_result = utils_v2v.v2v_cmd(v2v_params)
        check_result(v2v_result, status_error)

        # Check guest following the checkpoint document after convertion
        if not status_error:
            vmchecker = VMChecker(test, params, env)
            ret = vmchecker.run()
            if ret == 0:
                logging.info("All checkpoints passed")
            else:
                raise exceptions.TestFail("%s checkpoints failed" % ret)
    finally:
        vmcheck = utils_v2v.VMCheck(test, params, env)
        vmcheck.cleanup()
