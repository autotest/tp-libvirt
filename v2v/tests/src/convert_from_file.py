import os
import logging
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
    target = params.get('target')
    input_mode = params.get('input_mode')
    input_file = params.get('input_file')
    output_mode = params.get('output_mode')
    output_format = params.get('output_format')
    output_storage = params.get('output_storage', 'default')
    bridge = params.get('bridge')
    network = params.get('network')
    address_cache = env.get('address_cache')
    v2v_timeout = int(params.get('v2v_timeout', 1200))
    status_error = 'yes' == params.get('status_error', 'no')
    skip_check = 'yes' == params.get('skip_check', 'no')
    pool_name = params.get('pool_name', 'v2v_test')
    pool_type = params.get('pool_type', 'dir')
    pool_target = params.get('pool_target_path', 'v2v_pool')
    pvt = libvirt.PoolVolumeTest(test, params)
    checkpoint = params.get('checkpoint', '')
    datastore = params.get('datastore')
    esxi_host = params.get('esx_hostname')
    esxi_password = params.get('esxi_password')
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
        libvirt.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if skip_check:
            logging.info('Skip checking vm after conversion')
        elif not status_error:
            if output_mode == 'rhev':
                if not utils_v2v.import_vm_to_ovirt(params, address_cache,
                                                    timeout=v2v_timeout):
                    test.fail('Import VM failed')
            # Create vmchecker before virsh.start so that the vm can be undefined
            # if started failed.
            vmchecker = VMChecker(test, params, env)
            params['vmchecker'] = vmchecker
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
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            log_fail(log_check)
        if len(error_list):
            test.fail(
                '%d checkpoints failed: %s' %
                (len(error_list), error_list))

    try:
        v2v_params = {
            'main_vm': vm_name, 'target': target, 'v2v_opts': '-v -x',
            'storage': output_storage, 'network': network, 'bridge': bridge,
            'input_mode': input_mode, 'input_file': input_file,
            'new_name': 'ova_vm_' + utils_misc.generate_random_string(3),
            'datastore': datastore,
            'esxi_host': esxi_host,
            'esxi_password': esxi_password,
            'input_transport': input_transport,
            'vmx_nfs_src': vmx_nfs_src,
            'output_method': output_method,
            'storage_name': storage_name,
            'rhv_upload_opts': rhv_upload_opts
        }
        if input_mode == 'vmx':
            v2v_params.update(
                {'new_name': vm_name + utils_misc.generate_random_string(3)})
        if output_format:
            v2v_params.update({'output_format': output_format})
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
            v2v_params['storage'] = data_dir.get_tmp_dir()

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
        if params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if output_mode == 'rhev' and v2v_sasl:
            v2v_sasl.cleanup()
        if output_mode == 'libvirt':
            pvt.cleanup_pool(pool_name, pool_type, pool_target, '')
        if input_mode == 'vmx' and input_transport == 'ssh':
            process.run("ssh-agent -k")
