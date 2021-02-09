import os
import logging
import re

from avocado.utils import process

from virttest import utils_v2v
from virttest import virsh
from virttest import utils_misc
from virttest import utils_sasl
from virttest import remote

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Convert a remote vm to remote ovirt node.
    """
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)

    vm_name = params.get("main_vm")
    target = params.get("target")
    hypervisor = params.get("hypervisor")
    input_mode = params.get("input_mode")
    input_transport = params.get("input_transport")
    vddk_libdir = params.get('vddk_libdir')
    # nfs mount source
    vddk_libdir_src = params.get('vddk_libdir_src')
    vddk_thumbprint = params.get('vddk_thumbprint')
    os_pool = storage = params.get('storage')
    storage_name = params.get('storage_name')
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
    v2v_opts = '-v -x' if params.get('v2v_debug', 'on') == 'on' else ''
    if params.get("v2v_opts"):
        # Add a blank by force
        v2v_opts += ' ' + params.get("v2v_opts")
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    # for construct rhv-upload option in v2v cmd
    output_method = params.get("output_method")
    rhv_upload_opts = params.get("rhv_upload_opts")
    # for get ca.crt file from ovirt engine
    rhv_passwd = params.get("rhv_upload_passwd")
    rhv_passwd_file = params.get("rhv_upload_passwd_file")
    ovirt_engine_passwd = params.get("ovirt_engine_password")
    ovirt_hostname = params.get("ovirt_engine_url").split('/')[2]
    ovirt_ca_file_path = params.get("ovirt_ca_file_path")
    local_ca_file_path = params.get("local_ca_file_path")
    skip_vm_check = params.get('skip_vm_check', 'no')
    os_version = params.get('os_version')
    os_type = params.get('os_type')

    # create different sasl_user name for different job
    params.update({'sasl_user': params.get("sasl_user") +
                   utils_misc.generate_random_string(3)})
    logging.info('sals user name is %s' % params.get("sasl_user"))

    # Prepare step for different hypervisor
    if hypervisor == "xen":
        # See man virt-v2v-input-xen(1)
        process.run(
            'update-crypto-policies --set LEGACY',
            verbose=True,
            ignore_status=True,
            shell=True)

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
        xen_pubkey, xen_session = utils_v2v.v2v_setup_ssh_key(
            source_ip, source_user, source_pwd, auto_close=False)
        try:
            utils_misc.add_identities_into_ssh_agent()
        except Exception:
            process.run("ssh-agent -k")
            test.error("Fail to setup ssh-agent")
    elif hypervisor == "kvm":
        source_ip = None
        source_pwd = None
    else:
        test.cancel("Unspported hypervisor: %s" % hypervisor)

    if output_method == 'rhv_upload':
        # Create password file for '-o rhv_upload' to connect to ovirt
        with open(rhv_passwd_file, 'w') as f:
            f.write(rhv_passwd)
        # Copy ca file from ovirt to local
        remote.scp_from_remote(ovirt_hostname, 22, 'root',
                               ovirt_engine_passwd,
                               ovirt_ca_file_path,
                               local_ca_file_path)

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
                       'auto_close': True,
                       'debug': True}
        v2v_virsh = virsh.VirshPersistent(**virsh_dargs)
        close_virsh = True
    try:
        if not v2v_virsh.domain_exists(vm_name):
            test.error("VM '%s' not exist" % vm_name)
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
                  'os_storage': storage, 'hostname': source_ip,
                  # For virsh connection
                  'password': source_pwd,
                  'new_name': vm_name + utils_misc.generate_random_string(3),
                  'output_method': output_method, 'os_storage_name': storage_name,
                  'input_transport': input_transport, 'vcenter_host': vpx_ip,
                  'vcenter_password': vpx_pwd,
                  'vddk_thumbprint': vddk_thumbprint,
                  'vddk_libdir': vddk_libdir,
                  'vddk_libdir_src': vddk_libdir_src,
                  'params': params
                  }
    if vpx_dc:
        v2v_params.update({"vpx_dc": vpx_dc})
    if esx_ip:
        v2v_params.update({"esx_ip": esx_ip})
    if v2v_opts:
        v2v_params.update({"v2v_opts": v2v_opts})
    if rhv_upload_opts:
        v2v_params.update({"rhv_upload_opts": rhv_upload_opts})
    output_format = params.get('output_format')
    # output_format will be set to 'raw' in utils_v2v.v2v_cmd if it's None
    if output_format:
        v2v_params.update({'of_format': output_format})

    # Set libguestfs environment variable
    if hypervisor in ('xen', 'kvm'):
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'
    try:
        # Execute virt-v2v command
        v2v_ret = utils_v2v.v2v_cmd(v2v_params)
        if v2v_ret.exit_status != 0:
            test.fail("Convert VM failed")

        params['main_vm'] = v2v_params['new_name']

        logging.info("output_method is %s" % output_method)
        # Check all checkpoints after convert
        params['vmchecker'] = vmchecker = VMChecker(test, params, env)
        # Import the VM to oVirt Data Center from export domain, and start it
        if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                            timeout=v2v_timeout):
            test.error("Import VM failed")

        # When VM is on OSP, it can't obtain IP address, therefore
        # skipping the VM checking.
        if skip_vm_check == 'yes':
            logging.debug("skip vm checking")
            return

        ret = vmchecker.run()

        err_list = []
        virtio_win_ver = "[virtio-win-1.9.16-1,)"
        virtio_win_qxl_os = ['win2008r2', 'win7']
        virtio_win_qxldod_os = ['win10', 'win2016', 'win2019']

        if os_type == 'windows':
            virtio_win_support_qxldod = utils_v2v.multiple_versions_compare(
                virtio_win_ver)
            qxl_warning = "there is no QXL driver for this version of Windows"
            if virtio_win_support_qxldod and os_version in virtio_win_qxldod_os:
                has_qxl_warning = False
            elif os_version in virtio_win_qxl_os:
                has_qxl_warning = False
            else:
                has_qxl_warning = True

            if has_qxl_warning and not re.search(qxl_warning, v2v_ret.stdout_text):
                err_list.append('Not find QXL warning')
            if not has_qxl_warning and re.search(qxl_warning, v2v_ret.stdout_text):
                err_list.append('Unexpected QXL warning')

        ret.extend(err_list)

        if len(ret) == 0:
            logging.info("All checkpoints passed")
        else:
            test.fail("%d checkpoints failed: %s" % (len(ret), ret))
    finally:
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if v2v_sasl:
            v2v_sasl.cleanup()
            v2v_sasl.close_session()
        if hypervisor == "xen":
            # Restore crypto-policies to DEFAULT, the setting is impossible to be
            # other values by default in testing envrionment.
            process.run(
                'update-crypto-policies --set DEFAULT',
                verbose=True,
                ignore_status=True,
                shell=True)
            utils_v2v.v2v_setup_ssh_key_cleanup(xen_session, xen_pubkey)
            process.run("ssh-agent -k")
        # Cleanup constant files
        utils_v2v.cleanup_constant_files(params)
