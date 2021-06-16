import os
import pwd
import logging
import shutil
import time

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_v2v
from virttest import utils_sasl
from virttest import data_dir
from virttest import ppm_utils
from virttest import remote
from virttest.utils_test import libvirt

from provider.v2v_vmcheck_helper import VMChecker
from provider.v2v_vmcheck_helper import check_json_output
from provider.v2v_vmcheck_helper import check_local_output


def run(test, params, env):
    """
    convert specific kvm guest to rhev
    """
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)
    if utils_v2v.V2V_EXEC is None:
        test.error('Missing command: virt-v2v')
    # Guest name might be changed, we need a new variant to save the original
    # name
    vm_name = params['original_vm_name'] = params.get('main_vm', 'EXAMPLE')
    unprivileged_user = params.get('unprivileged_user')
    target = params.get('target')
    input_mode = params.get('input_mode')
    input_file = params.get('input_file')
    output_mode = params.get('output_mode')
    output_format = params.get('output_format')
    os_pool = output_storage = params.get('output_storage', 'default')
    bridge = params.get('bridge')
    network = params.get('network')
    address_cache = env.get('address_cache')
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    status_error = 'yes' == params.get('status_error', 'no')
    skip_vm_check = params.get('skip_vm_check', 'no')
    skip_virsh_pre_conn = params.get('skip_virsh_pre_conn', 'no')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = libvirt.PoolVolumeTest(test, params)
    checkpoint = params.get('checkpoint', '')
    datastore = params.get('datastore')
    esxi_host = params.get('esx_hostname')
    esxi_password = params.get('esxi_password')
    hypervisor = params.get("hypervisor")
    input_transport = params.get("input_transport")
    vmx_nfs_src = params.get("vmx_nfs_src")
    # for construct rhv-upload option in v2v cmd
    output_method = params.get("output_method")
    rhv_upload_opts = params.get("rhv_upload_opts")
    storage_name = params.get('storage_name')
    # for get ca.crt file from ovirt engine
    rhv_passwd = params.get("rhv_upload_passwd")
    rhv_passwd_file = params.get("rhv_upload_passwd_file")
    ovirt_engine_passwd = params.get("ovirt_engine_password")
    ovirt_hostname = params.get("ovirt_engine_url").split(
        '/')[2] if params.get("ovirt_engine_url") else None
    ovirt_ca_file_path = params.get("ovirt_ca_file_path")
    local_ca_file_path = params.get("local_ca_file_path")
    vpx_dc = params.get("vpx_dc")
    vpx_hostname = params.get("vpx_hostname")
    vpx_password = params.get("vpx_password")
    src_uri_type = params.get('src_uri_type')
    v2v_opts = '-v -x' if params.get('v2v_debug',
                                     'on') in ['on', 'force_on'] else ''
    v2v_sasl = ''

    if params.get('v2v_opts'):
        # Add a blank by force
        v2v_opts += ' ' + params.get("v2v_opts")
    error_list = []

    # create different sasl_user name for different job
    if output_mode == 'rhev':
        params.update({'sasl_user': params.get("sasl_user") +
                       utils_misc.generate_random_string(3)})
        logging.info('sals user name is %s' % params.get("sasl_user"))
        if output_method == 'rhv_upload':
            # Create password file for '-o rhv_upload' to connect to ovirt
            with open(rhv_passwd_file, 'w') as f:
                f.write(rhv_passwd)
            # Copy ca file from ovirt to local
            remote.scp_from_remote(ovirt_hostname, 22, 'root',
                                   ovirt_engine_passwd,
                                   ovirt_ca_file_path,
                                   local_ca_file_path)

    def log_fail(msg):
        """
        Log error and update error list
        """
        logging.error(msg)
        error_list.append(msg)

    def check_BSOD():
        """
        Check if boot up into BSOD
        """
        bar = 0.999
        match_img = params.get('image_to_match')
        screenshot = '%s/BSOD_screenshot.ppm' % data_dir.get_tmp_dir()
        if match_img is None:
            test.error('No BSOD screenshot to match!')
        cmd_man_page = 'man virt-v2v|grep -i "Boot failure: 0x0000007B"'
        if process.run(cmd_man_page, shell=True).exit_status != 0:
            log_fail('Man page not contain boot failure msg')
        for i in range(100):
            virsh.screenshot(vm_name, screenshot)
            similar = ppm_utils.image_histogram_compare(screenshot, match_img)
            if similar > bar:
                logging.info('Meet BSOD with similarity %s' % similar)
                return
            time.sleep(1)
        log_fail('No BSOD as expected')

    def check_result(result, status_error):
        """
        Check virt-v2v command result
        """
        def vm_check():
            """
            Checking the VM
            """
            if output_mode == 'json' and not check_json_output(params):
                test.fail('check json output failed')
            if output_mode == 'local' and not check_local_output(params):
                test.fail('check local output failed')
            if output_mode in ['null', 'json', 'local']:
                return

            # Create vmchecker before virsh.start so that the vm can be undefined
            # if started failed.
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            if output_mode == 'libvirt':
                try:
                    virsh.start(vm_name, debug=True, ignore_status=False)
                except Exception as e:
                    test.fail('Start vm failed: %s' % str(e))
            # Check guest following the checkpoint document after convertion
            if params.get('skip_vm_check') != 'yes':
                if checkpoint != 'win2008r2_ostk':
                    ret = vmchecker.run()
                    if len(ret) == 0:
                        logging.info("All common checkpoints passed")
                if checkpoint == 'win2008r2_ostk':
                    check_BSOD()
                # Merge 2 error lists
                error_list.extend(vmchecker.errors)

        libvirt.check_exit_status(result, status_error)
        output = result.stdout_text + result.stderr_text
        if not status_error:
            vm_check()
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            log_fail(log_check)
        if len(error_list):
            test.fail(
                '%d checkpoints failed: %s' %
                (len(error_list), error_list))

    try:
        if checkpoint == 'regular_user_sudo':
            regular_sudo_config = '/etc/sudoers.d/v2v_test'
            with open(regular_sudo_config, 'w') as fd:
                fd.write('%s ALL=(ALL)  NOPASSWD: ALL' % unprivileged_user)

            # create user
            try:
                pwd.getpwnam(unprivileged_user)
            except KeyError:
                process.system("useradd %s" % unprivileged_user)

            # generate ssh-key
            rsa_private_key_path = '/home/%s/.ssh/id_rsa' % unprivileged_user
            rsa_public_key_path = '/home/%s/.ssh/id_rsa.pub' % unprivileged_user
            process.system(
                'su - %s -c \'ssh-keygen -t rsa -q -N "" -f %s\'' %
                (unprivileged_user, rsa_private_key_path))

            with open(rsa_public_key_path) as fd:
                pub_key = fd.read()

        v2v_params = {
            'main_vm': vm_name, 'target': target, 'v2v_opts': v2v_opts,
            'os_storage': output_storage, 'network': network, 'bridge': bridge,
            'input_mode': input_mode, 'input_file': input_file,
            'new_name': 'ova_vm_' + utils_misc.generate_random_string(3),
            'datastore': datastore,
            'esxi_host': esxi_host,
            'esxi_password': esxi_password,
            'input_transport': input_transport,
            'vmx_nfs_src': vmx_nfs_src,
            'output_method': output_method,
            'os_storage_name': storage_name,
            'os_pool': os_pool,
            'rhv_upload_opts': rhv_upload_opts,
            'params': params
        }
        if input_mode == 'vmx':
            v2v_params.update(
                {'new_name': vm_name + utils_misc.generate_random_string(3),
                 'hypervisor': hypervisor,
                 'vpx_dc': vpx_dc,
                 'password': vpx_password if src_uri_type != 'esx' else esxi_password,
                 'hostname': vpx_hostname,
                 'skip_virsh_pre_conn': skip_virsh_pre_conn})
            if checkpoint == 'regular_user_sudo':
                v2v_params.update({'pub_key': pub_key})
        # copy ova from nfs storage before v2v conversion
        if input_mode == 'ova':
            src_dir = params.get('ova_dir')
            dest_dir = params.get('ova_copy_dir')
            if os.path.isfile(src_dir) and not os.path.exists(dest_dir):
                os.makedirs(dest_dir, exist_ok=True)
            if os.path.isdir(src_dir) and os.path.exists(dest_dir):
                shutil.rmtree(dest_dir)

            if os.path.isdir(src_dir):
                shutil.copytree(src_dir, dest_dir)
            else:
                shutil.copy(src_dir, dest_dir)
            logging.info('Copy ova from %s to %s', src_dir, dest_dir)
        if output_format:
            v2v_params.update({'of_format': output_format})
        # Create libvirt dir pool
        if output_mode == 'libvirt':
            pvt.pre_pool(pool_name, pool_type, pool_target, '')
        # Build rhev related options
        if output_mode == 'rhev':
            # Create SASL user on the ovirt host
            user_pwd = "[['%s', '%s']]" % (params.get("sasl_user"),
                                           params.get("sasl_pwd"))
            v2v_sasl = utils_sasl.SASL(sasl_user_pwd=user_pwd)
            v2v_sasl.server_ip = params.get("remote_ip")
            v2v_sasl.server_user = params.get('remote_user')
            v2v_sasl.server_pwd = params.get('remote_pwd')
            v2v_sasl.setup(remote=True)
        if output_mode == 'local':
            v2v_params['os_directory'] = data_dir.get_tmp_dir()

        if checkpoint == 'ova_relative_path':
            logging.debug('Current dir: %s', os.getcwd())
            ova_dir = params.get('ova_dir')
            logging.info('Change to dir: %s', ova_dir)
            os.chdir(ova_dir)

        # Set libguestfs environment variable
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'
        if checkpoint == 'permission':
            os.environ['LIBGUESTFS_BACKEND'] = ''
        process.run('echo $LIBGUESTFS_BACKEND', shell=True)

        v2v_result = utils_v2v.v2v_cmd(v2v_params)

        if 'new_name' in v2v_params:
            vm_name = params['main_vm'] = v2v_params['new_name']

        check_result(v2v_result, status_error)
    finally:
        # Cleanup constant files
        utils_v2v.cleanup_constant_files(params)
        if input_mode == 'ova' and os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'rhev' and v2v_sasl:
            v2v_sasl.cleanup()
            v2v_sasl.close_session()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if checkpoint == 'regular_user_sudo' and os.path.exists(
                regular_sudo_config):
            os.remove(regular_sudo_config)
        if unprivileged_user:
            process.system("userdel -fr %s" % unprivileged_user)
        if input_mode == 'vmx' and input_transport == 'ssh':
            process.run("killall ssh-agent")
