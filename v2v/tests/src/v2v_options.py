"""
Test all options of command: virt-v2v
"""
import os
import re
import logging

from avocado.core import exceptions
from avocado.utils import process
from avocado.utils.astring import to_text

from virttest import data_dir
from virttest import utils_misc
from provider import utils_v2v
from virttest import virsh
from virttest import libvirt_storage
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv

from provider.v2v_vmcheck_helper import VMChecker

LOG = logging.getLogger('avocado.v2v.' + __name__)


def run(test, params, env):
    """
    Test various options of virt-v2v.
    """
    V2V_UNSUPPORT_GLANCE_VER = "[virt-v2v-1.45.2-1,)"

    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.error("Please set real value for %s" % v)

    version_required = params.get("version_required")
    utils_v2v.prime_rpm_cache(['virt-v2v'])
    vm_name = params.get("main_vm", "EXAMPLE")
    new_vm_name = params.get("new_vm_name")
    input_mode = params.get("input_mode")
    v2v_options = params.get("v2v_options", "")
    hypervisor = params.get("hypervisor", "kvm")
    remote_host = params.get("remote_host", "EXAMPLE")
    vpx_dc = params.get("vpx_dc", "EXAMPLE")
    esx_ip = params.get("esx_ip", "EXAMPLE")
    source_user = params.get("username", "root")
    # For VDDK
    input_transport = params.get("input_transport")
    vddk_libdir = params.get('vddk_libdir')
    src_uri_type = params.get("src_uri_type")
    esxi_password = params.get('esxi_password')
    vpx_passwd = params.get("vpx_password")
    # nfs mount source
    vddk_libdir_src = params.get('vddk_libdir_src')
    vddk_thumbprint = params.get('vddk_thumbprint')
    output_mode = params.get("output_mode")
    output_storage = params.get("output_storage", "default")
    disk_img = params.get("input_disk_image", "")
    v2v_timeout = int(params.get("v2v_timeout", 1200))
    status_error = "yes" == params.get("status_error", "no")
    pool_name = params.get("pool_name", "v2v_test")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target", "v2v_pool")
    emulated_img = params.get("emulated_image_path", "v2v-emulated-img")
    pvt = utlv.PoolVolumeTest(test, params)
    address_cache = env.get('address_cache')
    params['vmcheck_flag'] = False
    checkpoint = params.get('checkpoint', '')
    error_flag = 'strict'

    def get_img_path():
        """
        Get the full path of the converted image.
        """
        img_name = vm_name + "-sda"
        if output_mode == "libvirt":
            img_path = virsh.vol_path(img_name, output_storage).stdout.strip()
        elif output_mode == "local":
            img_path = os.path.join(output_storage, img_name)
        return img_path

    def check_image(img_path, check_point, expected_value):
        """
        Verify image file allocation mode and format
        """
        if not img_path or not os.path.isfile(img_path):
            test.error("Image path: '%s' is invalid" % img_path)
        img_info = utils_misc.get_image_info(img_path)
        LOG.debug("Image info: %s", img_info)
        if check_point == "allocation":
            if expected_value == "sparse":
                if img_info['vsize'] > img_info['dsize']:
                    LOG.info("%s is a sparse image", img_path)
                else:
                    test.fail("%s is not a sparse image" % img_path)
            elif expected_value == "preallocated":
                if img_info['vsize'] <= img_info['dsize']:
                    LOG.info("%s is a preallocated image", img_path)
                else:
                    test.fail("%s is not a preallocated image" % img_path)
        if check_point == "format":
            if expected_value == img_info['format']:
                LOG.info("%s format is %s", img_path, expected_value)
            else:
                test.fail("%s format is not %s" % (img_path, expected_value))

    def check_new_name(output, expected_name):
        """
        Verify guest name changed to the new name.
        """
        found = False
        if output_mode == "libvirt":
            found = virsh.domain_exists(expected_name)
        elif output_mode == "local":
            found = os.path.isfile(os.path.join(output_storage,
                                                expected_name + "-sda"))
        else:
            return
        if found:
            LOG.info("Guest name renamed when converting it")
        else:
            test.fail("Rename guest failed")

    def check_nocopy(output):
        """
        Verify no image created if convert command use --no-copy option
        """
        img_path = get_img_path()
        if not os.path.isfile(img_path):
            LOG.info("No image created with --no-copy option")
        else:
            test.fail("Find %s" % img_path)

    def check_connection(output, expected_uri):
        """
        Check output connection uri used when converting guest
        """
        init_msg = "Initializing the target -o libvirt -oc %s" % expected_uri
        if init_msg in output:
            LOG.info("Find message: %s", init_msg)
        else:
            test.fail("Not find message: %s" % init_msg)

    def check_source(output):
        """
        Check if --print-source option print the correct info
        """
        # Parse source info
        source = output.split('\n')[2:]
        for i in range(len(source)):
            if source[i].startswith('\t'):
                source[i - 1] += source[i]
                source[i] = ''
        source_strip = [x.strip() for x in source if x.strip()]
        source_info = {}
        for line in source_strip:
            source_info[line.split(':')[0]] = line.split(':', 1)[1].strip()
        LOG.debug('Source info to check: %s', source_info)
        checklist = ['nr vCPUs', 'hypervisor type', 'source name', 'memory',
                     'disks', 'NICs']
        if hypervisor == 'kvm':
            checklist.extend(['display', 'CPU features'])
        for key in checklist:
            if key not in source_info:
                test.fail('%s info missing' % key)

        v2v_virsh = None
        close_virsh = False
        if hypervisor == 'kvm':
            v2v_virsh = virsh
        else:
            virsh_dargs = {'uri': ic_uri,
                           'remote_ip': remote_host,
                           'remote_user': source_user,
                           'remote_pwd': source_pwd,
                           'auto_close': True,
                           'debug': True}
            LOG.debug("creating virsh persistent session with args: %s" % virsh_dargs)
            v2v_virsh = virsh.VirshPersistent(**virsh_dargs)
            LOG.debug('a new virsh session %s was created', v2v_virsh)
            close_virsh = True

        # Check single values
        fail = []
        try:
            xml = vm_xml.VMXML.new_from_inactive_dumpxml(
                vm_name, virsh_instance=v2v_virsh)
        finally:
            if close_virsh:
                LOG.debug('virsh session %s is closing', v2v_virsh)
                v2v_virsh.close_session()

        check_map = {}
        check_map['nr vCPUs'] = xml.vcpu
        check_map['hypervisor type'] = xml.hypervisor_type
        check_map['source name'] = xml.vm_name
        check_map['memory'] = str(int(xml.max_mem) * 1024) + ' (bytes)'

        if hypervisor == 'kvm':
            check_map['display'] = xml.get_graphics_devices()[0].type_name

        LOG.info('KEY:\tSOURCE<-> XML')
        for key in check_map:
            LOG.info(
                '%-15s:%18s <-> %s',
                key,
                source_info[key],
                check_map[key])
            if str(check_map[key]) not in source_info[key]:
                fail.append(key)

        # Check nic info
        nic = list(xml.get_iface_all().values())[0]
        type = nic.get('type')
        mac = nic.find('mac').get('address')
        nic_source = nic.find('source')
        name = nic_source.get(type)
        nic_info = '%s "%s" mac: %s' % (type, name, mac)
        LOG.info('NICs:%s<->%s', source_info['NICs'], nic_info)
        if nic_info.lower() not in source_info['NICs'].lower():
            fail.append('NICs')

        # Check cpu features
        if hypervisor == 'kvm':
            feature_list = xml.features.get_feature_list()
            LOG.info(
                'CPU features:%s<->%s',
                source_info['CPU features'],
                feature_list)
            if sorted(source_info['CPU features'].split(
                    ',')) != sorted(feature_list):
                fail.append('CPU features')

        if fail:
            test.fail('Source info not correct for: %s' % fail)

    def check_man_page(in_man, not_in_man):
        """
        Check if content of man page or help info meets expectation
        """
        man_page = process.run(
            'man virt-v2v', verbose=False).stdout_text.strip()
        if in_man:
            LOG.info('Checking man page of virt-v2v for "%s"', in_man)
            if in_man not in man_page:
                test.fail('"%s" not in man page' % in_man)
        if not_in_man:
            LOG.info('Checking man page of virt-v2v for "%s"', not_in_man)
            if not_in_man in man_page:
                test.fail('"%s" not removed from man page' % not_in_man)

    def check_result(cmd, result, status_error):
        """
        Check virt-v2v command result
        """
        utils_v2v.check_exit_status(result, status_error, error_flag)
        output = to_text(result.stdout + result.stderr, errors=error_flag)
        output_stdout = to_text(result.stdout, errors=error_flag)
        if status_error:
            if checkpoint in ['length_of_error', 'line_no_wrap']:
                log_lines = output.split('\n')
                for line in log_lines:
                    if line.startswith('virt-v2v:'):
                        v2v_start = True
                    else:
                        v2v_start = False
                    # 76 is the max length in v2v
                    if v2v_start:
                        if checkpoint == 'length_of_error' and len(line) > 76:
                            test.fail(
                                'Error log longer than 76 characters: %s' %
                                line)
                        if checkpoint == 'line_no_wrap' and len(line) < 76:
                            test.fail(
                                'Error log is shorter than 76 characters: %s' %
                                line)
        else:
            if '-oa' in cmd and '--no-copy' not in cmd:
                expected_mode = re.findall(r"-oa\s(\w+)", cmd)[0]
                img_path = get_img_path()

                def check_alloc():
                    try:
                        check_image(img_path, "allocation", expected_mode)
                        return True
                    except exceptions.TestFail:
                        pass
                if not utils_misc.wait_for(
                        check_alloc, timeout=600, step=10.0):
                    test.fail('Allocation check failed.')
            if '-of' in cmd and '--no-copy' not in cmd and '--print-source' not in cmd:
                expected_format = re.findall(r"-of\s(\w+)", cmd)[0]
                img_path = get_img_path()
                check_image(img_path, "format", expected_format)
            if '-on' in cmd:
                expected_name = re.findall(r"-on\s+(\S+)", cmd)[0]
                check_new_name(output, expected_name)
            if '--no-copy' in cmd:
                check_nocopy(output)
            if '-oc' in cmd:
                expected_uri = re.findall(r"-oc\s(\S+)", cmd)[0]
                check_connection(output, expected_uri)
            if output_mode == "libvirt":
                virsh.start(vm_name, debug=True, ignore_status=False)
            if checkpoint == 'vmx':
                vmchecker = VMChecker(test, params, env)
                params['vmchecker'] = vmchecker
                params['vmcheck_flag'] = True
                ret = vmchecker.run()
                if len(ret) == 0:
                    LOG.info("All common checkpoints passed")
            if checkpoint == 'no_dcpath':
                if '--dcpath' in output:
                    test.fail('"--dcpath" is not removed')
            if checkpoint.startswith('empty_nic_source'):
                target_str = '%s "eth0" mac: %s' % (
                    params[checkpoint][0], params[checkpoint][1])
                LOG.info('Expect log: %s', target_str)
                if target_str not in output_stdout.lower():
                    test.fail('Expect log not found: %s' % target_str)
            if checkpoint == 'print_source':
                check_source(output_stdout)
            if checkpoint == 'machine_readable':
                example_file = params.get('example_file', '')
                if not os.path.exists(example_file):
                    test.error("example_file not found: %s" % example_file)
                # Checking items in example_file exist in latest
                # output regardless of the orders and new items.
                with open(example_file) as f:
                    for line in f:
                        if line.strip() not in output_stdout.strip():
                            if utils_v2v.multiple_versions_compare(
                                    V2V_UNSUPPORT_GLANCE_VER) and 'glance' in line:
                                continue
            if checkpoint == 'compress':
                img_path = get_img_path()
                LOG.info('Image path: %s', img_path)

                qemu_img_cmd = 'qemu-img check %s' % img_path
                qemu_img_locking_feature_support = libvirt_storage.check_qemu_image_lock_support()
                if qemu_img_locking_feature_support:
                    qemu_img_cmd = 'qemu-img check %s -U' % img_path

                disk_check = process.run(qemu_img_cmd).stdout_text
                LOG.info(disk_check)
                compress_info = disk_check.split(',')[-1].split('%')[0].strip()
                compress_rate = float(compress_info)
                LOG.info('%s%% compressed', compress_rate)
                if compress_rate < 0.1:
                    test.fail('Disk image NOT compressed')
            if checkpoint == 'check_version':
                get_v2v_version = process.run('rpm -q virt-v2v', shell=True, ignore_status=True)
                get_rpm_version = re.search(r'(\d+\.\d+\.\d+-\d+\.el\d+)', str(get_v2v_version.stdout))
                rpm_version = get_rpm_version.group(1)
                get_virt_v2v_version = re.search(r'(\d+\.\d+\.\d+).*?(\d+\.el\d+)', str(output_stdout))
                virt_v2v_version = get_virt_v2v_version.group(1) + '-' + get_virt_v2v_version.group(2)
                if (rpm_version != virt_v2v_version):
                    test.fail('v2v version is incorrect in v2v version option')
            if checkpoint == 'in_place':
                if not os.path.exists(xml_path):
                    test.fail('Not found guest xml file')
            if checkpoint == 'virt_v2v_open':
                with open(xml_path, 'r', encoding='utf-8', errors='ignore') as fp:
                    xml_output = fp.read()
                if not re.search(r'<root\b.*?>.*</root>', xml_output, flags=re.DOTALL):
                    test.fail('fail to test virt-v2v-open tool')
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            test.fail(log_check)
        check_man_page(params.get('in_man'), params.get('not_in_man'))

    backup_xml = None
    mount_nfs_kvm_images = None
    mount_nfs_vmx = None
    try:
        if version_required and not utils_v2v.multiple_versions_compare(
                version_required):
            test.cancel("Testing requires version: %s" % version_required)

        nfs_kvm_images = params.get('nfs_kvm_images')
        if nfs_kvm_images:
            mount_nfs_kvm_images = utils_v2v.v2v_mount(
                nfs_kvm_images, 'nfs_kvm_images')
            if disk_img and not os.path.isabs(disk_img):
                disk_img = os.path.join(mount_nfs_kvm_images, disk_img)
            example_file = params.get('example_file')
            if example_file and not os.path.isabs(example_file):
                params['example_file'] = os.path.join(
                    mount_nfs_kvm_images, example_file)
            win_image = params.get('win_image')
            if win_image and not os.path.isabs(win_image):
                full_win_image = os.path.join(
                    mount_nfs_kvm_images, win_image)
                params['win_image'] = full_win_image
                check_cmd = params.get('check_command', '')
                if win_image in check_cmd:
                    params['check_command'] = check_cmd.replace(
                        win_image, full_win_image)
            if '-i disk ' in v2v_options:
                raw_disk_img = params.get('input_disk_image', '')
                if raw_disk_img and not os.path.isabs(raw_disk_img):
                    v2v_options = v2v_options.replace(
                        '-i disk %s' % raw_disk_img,
                        '-i disk %s' % disk_img)

        if disk_img and not os.path.exists(disk_img):
            test.error("Disk image not found: %s" % disk_img)

        if checkpoint.startswith('empty_nic_source'):
            xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            iface = xml.get_devices('interface')[0]
            disks = xml.get_devices('disk')
            del iface.source
            iface.type_name = checkpoint.split('_')[-1]
            iface.source = {iface.type_name: ''}
            params[checkpoint] = [iface.type_name, iface.mac_address]
            LOG.debug(iface.source)
            devices = vm_xml.VMXMLDevices()
            devices.extend(disks)
            devices.append(iface)
            xml.set_devices(devices)
            LOG.info(xml.xmltreefile)
            params['input_xml'] = xml.xmltreefile.name
        # Build input options
        input_option = ""
        if input_mode is None:
            pass
        elif input_mode == "libvirt":
            uri_obj = utils_v2v.Uri(hypervisor)
            if hypervisor == 'esx' and src_uri_type == 'esx':
                vpx_dc = None
            ic_uri = uri_obj.get_uri(remote_host, vpx_dc, esx_ip)
            input_option = "-i %s -ic %s %s" % (input_mode, ic_uri, vm_name)
            # Build network&bridge option to avoid network error
            if checkpoint not in ['virt_v2v_open', 'virt_v2v_inspector', 'detect_gpo_av']:
                v2v_options += " -b %s -n %s" % (params.get("output_bridge"),
                                                 params.get("output_network"))
        elif input_mode == "disk":
            input_option += "-i %s %s" % (input_mode, disk_img)
        elif input_mode == 'libvirtxml':
            input_xml = params.get('input_xml')
            input_option += '-i %s %s' % (input_mode, input_xml)
        elif input_mode in ['ova']:
            test.cancel("Unsupported input mode: %s" % input_mode)
        else:
            test.error("Unknown input mode %s" % input_mode)
        input_format = params.get("input_format", "")
        input_allo_mode = params.get("input_allo_mode")
        if input_format:
            input_option += " -if %s" % input_format
            if not status_error:
                LOG.info("Check image before convert")
                check_image(disk_img, "format", input_format)
                if input_allo_mode:
                    check_image(disk_img, "allocation", input_allo_mode)

        # Build output options
        output_option = ""
        if output_mode:
            output_option = "-o %s" % output_mode
            if output_mode != 'null':
                output_option += " -os %s" % output_storage
        output_format = params.get("output_format")
        if output_format and output_format != input_format:
            output_option += " -of %s" % output_format
        output_allo_mode = params.get("output_allo_mode")
        if output_allo_mode:
            output_option += " -oa %s" % output_allo_mode

        if checkpoint in ['vddk_compression_zlib', 'vddk_compression_skipz', 'vddk_compression_fastlz']:
            v2v_options += " -v -x"
        if checkpoint == 'vddk_compression_zlib':
            v2v_options += ' -io vddk-compression=zlib'
        if checkpoint == 'vddk_compression_skipz':
            v2v_options += ' -io vddk-compression=skipz'
        if checkpoint == 'vddk_compression_fastlz':
            v2v_options += ' -io vddk-compression=fastlz'
        # Create password file for access to ESX hypervisor
        if hypervisor == 'esx':
            if params.get('cmd_has_ip', 'yes') == 'yes':
                source_pwd = vpx_passwd if src_uri_type != 'esx' else esxi_password
                source_passwd_file = os.path.join(
                    data_dir.get_tmp_dir(), "source_passwd")
                LOG.info("Building ESX no password interactive verification.")
                pwd_f = open(source_passwd_file, 'w')
                pwd_f.write(source_pwd)
                pwd_f.close()
                output_option += " -ip %s" % source_passwd_file
                # rhel8 slow stream doesn't support option 'ip' temporarily
                # so use option 'password-file' instead.
                if not utils_v2v.v2v_supported_option("-ip <filename>"):
                    output_option = output_option.replace(
                        '-ip', '--password-file', 1)
            # For VDDK
            if input_transport == 'vddk':
                if vddk_thumbprint is None:
                    thumbprint_host = esx_ip if src_uri_type == 'esx' else remote_host
                    vddk_thumbprint = utils_v2v.get_vddk_thumbprint(thumbprint_host)
                vddk_libdir_local = vddk_libdir
                if not vddk_libdir_local and vddk_libdir_src:
                    vddk_libdir_local = utils_v2v.prepare_vddk_libdir(
                        vddk_libdir_src)
                v2v_options += ' -it vddk  -io vddk-libdir=%s -io vddk-thumbprint=%s' % (vddk_libdir_local, vddk_thumbprint)
        # if don't specify any output option for virt-v2v, 'default' pool
        # will be used.
        if output_mode is None:
            # Cleanup first to avoid failure if 'default' pool exists.
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_img)

        # Create libvirt dir pool
        if output_mode == "libvirt":
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_img)

        # Work around till bug fixed
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        if checkpoint == 'vmx':
            nfs_vmx = params.get('nfs_vmx')
            mount_nfs_vmx = utils_v2v.v2v_mount(nfs_vmx, 'nfs_vmx')
            vmx = os.path.join(mount_nfs_vmx, vm_name, '%s.vmx' % vm_name)
            if not os.path.exists(vmx):
                test.error('VMX file not found: %s\n'
                           'Available in %s: %s' %
                           (vmx, mount_nfs_vmx,
                            os.listdir(mount_nfs_vmx)))
            input_option = '-i vmx %s' % vmx
            v2v_options += " -b %s -n %s" % (params.get("output_bridge"),
                                             params.get("output_network"))
        if checkpoint == 'length_of_error' and utils_v2v.v2v_supported_option('--wrap'):
            v2v_options += ' --wrap'

        if checkpoint == 'in_place':
            disk_path = params.get('disk_path')
            xml_path = os.path.join(data_dir.get_tmp_dir(), 'output.xml')
            v2v_options += '-i disk %s -O %s' % (disk_path, xml_path)

        if checkpoint == 'virt_v2v_open':
            xml_path = os.path.join(data_dir.get_tmp_dir(), 'output.xml')
            v2v_options += ' --run "virt-inspector --format=raw @@ > %s"' % xml_path

        if checkpoint == 'virt_v2v_inspector':
            # Create libvirt URI
            v2v_uri = utils_v2v.Uri('esx')
            remote_uri = v2v_uri.get_uri(remote_host, vpx_dc, esx_ip)
            LOG.debug("libvirt URI for converting: %s", remote_uri)
            virsh_dargs = {'uri': remote_uri, 'remote_ip': remote_host,
                           'remote_user': 'root', 'remote_pwd': source_pwd,
                           'auto_close': True,
                           'debug': True}
            remote_virsh = virsh.VirshPersistent(**virsh_dargs)
            raw_dumpxml = remote_virsh.dumpxml(vm_name)
            remote_virsh.close_session()
            disk_info = ''.join(re.findall(r"source\s+file=.*\.*vmdk", raw_dumpxml.stdout,
                                           flags=re.DOTALL | re.IGNORECASE)).split('=')[-1].strip("'")

            def snapshot(disk_info):
                # Find the last number in the string
                matches = list(re.finditer(r'\b\d+\b', disk_info))
                # Take the last match
                last_match = matches[-1]
                num_str = last_match.group()
                if not num_str:
                    return disk_info  # No standalone number found, return original string
                new_num = str(int(num_str) - 1)
                new_match = num_str.replace(str(int(num_str)), new_num)
                # Replace only the last number found
                start, end = last_match.span()
                return disk_info[:start] + new_match + disk_info[end:]

            snapshot_info = snapshot(disk_info)
            v2v_options += " -io vddk-file='%s'" % snapshot_info

        if checkpoint == 'check_parallel_option':
            v2v_options += ' --parallel 2'

        # Running virt-v2v command
        cmd = "%s %s %s %s" % (utils_v2v.V2V_EXEC, input_option,
                               output_option, v2v_options)
        if hypervisor == 'esx':
            v2v_uri = utils_v2v.Uri('esx')
            if src_uri_type == 'esx':
                vpx_dc = None
            remote_uri = v2v_uri.get_uri(remote_host, vpx_dc, esx_ip)
            cmd = re.sub(r'(vpx|esx)://\S+?\?no_verify=1', remote_uri, cmd)
        if params.get('cmd_free') == 'yes':
            cmd = params.get('check_command')
            # only set error to 'ignore' to avoid exception for RHEL7-84978
            if "guestfish" in cmd:
                error_flag = "replace"

        if checkpoint == 'in_place':
            cmd = re.sub(r".*/bin/virt-v2v", '/usr/libexec/virt-v2v-in-place', cmd)
        if checkpoint == 'virt_v2v_open':
            cmd = re.sub(r".*/bin/virt-v2v", 'virt-v2v-open', cmd)
        if checkpoint in ['virt_v2v_inspector', 'detect_gpo_av']:
            cmd = re.sub(r".*/bin/virt-v2v", 'virt-v2v-inspector', cmd)
            cmd = cmd.replace('-i libvirt', '')
        if checkpoint in ['vddk_compression_zlib', 'vddk_compression_skipz']:
            cmd = re.sub(r"-io vddk-thumbprint=\S+\s*", '', cmd)
        if checkpoint == 'esx_ipv6':
            ipv4_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
            esx_ipv6_addr = params.get('esx_ipv6_addr')
            if not esx_ipv6_addr:
                test.error("esx ipv6_addr parameter is missing")
            cmd = re.sub(ipv4_pattern, esx_ipv6_addr, cmd)
        if checkpoint == 'vpx_with_invalid_esx_ipv6':
            ipv4_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
            vpx_ipv6_addr = params.get('vpx_ipv6_addr')
            if not vpx_ipv6_addr:
                test.error("vpx_ipv6_addr parameter is missing")
            cmd = re.sub(ipv4_pattern, vpx_ipv6_addr, cmd)
            cmd = re.sub(r'/\[(.*?)\]/', r'/\1/', cmd)
            cmd_export_env = 'LIBVIRT_DEBUG=1'
            cmd = "%s %s" % (cmd_export_env, cmd)

        if checkpoint == 'vpx_with_invalid_esx_ipv6':
            cmd_result = process.run(cmd, timeout=v2v_timeout, verbose=True,
                                     ignore_status=True, shell=True)
        else:
            cmd_result = process.run(cmd, timeout=v2v_timeout, verbose=True,
                                     ignore_status=True)
        if new_vm_name:
            vm_name = new_vm_name
            params['main_vm'] = new_vm_name
        check_result(cmd, cmd_result, status_error)
    finally:
        if output_mode == "local":
            image_name = vm_name + "-sda"
            img_file = os.path.join(output_storage, image_name)
            xml_file = img_file + ".xml"
            for local_file in [img_file, xml_file]:
                if os.path.exists(local_file):
                    os.remove(local_file)
        if output_mode == "libvirt":
            virsh.remove_domain(vm_name, options="--nvram")
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)
        if output_mode is None:
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)
        vmcheck_flag = params.get("vmcheck_flag")
        if vmcheck_flag and params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if backup_xml:
            backup_xml.sync()
        if mount_nfs_vmx:
            utils_misc.umount(
                params.get('nfs_vmx'), mount_nfs_vmx, None)
        if mount_nfs_kvm_images:
            utils_misc.umount(
                params.get('nfs_kvm_images'), mount_nfs_kvm_images, None)
