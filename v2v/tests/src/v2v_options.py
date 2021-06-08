"""
Test all options of command: virt-v2v
"""
import os
import re
import pwd
import logging
import shutil

import aexpect

from avocado.core import exceptions
from avocado.utils import process
from avocado.utils.astring import to_text

from virttest import data_dir
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_v2v
from virttest import utils_sasl
from virttest import virsh
from virttest import libvirt_storage
from virttest import ssh_key
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv

from provider.v2v_vmcheck_helper import VMChecker


def run(test, params, env):
    """
    Test various options of virt-v2v.
    """
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    for v in list(params.values()):
        if "V2V_EXAMPLE" in v:
            test.cancel("Please set real value for %s" % v)

    version_requried = params.get("version_requried")
    vm_name = params.get("main_vm", "EXAMPLE")
    new_vm_name = params.get("new_vm_name")
    input_mode = params.get("input_mode")
    input_file = params.get("input_file")
    v2v_options = params.get("v2v_options", "")
    hypervisor = params.get("hypervisor", "kvm")
    remote_host = params.get("remote_host", "EXAMPLE")
    vpx_dc = params.get("vpx_dc", "EXAMPLE")
    esx_ip = params.get("esx_ip", "EXAMPLE")
    source_user = params.get("username", "root")
    output_mode = params.get("output_mode")
    output_storage = params.get("output_storage", "default")
    disk_img = params.get("input_disk_image", "")
    nfs_storage = params.get("storage")
    no_root = 'yes' == params.get('no_root', 'no')
    mnt_point = params.get("mnt_point")
    export_domain_uuid = params.get("export_domain_uuid", "")
    fake_domain_uuid = params.get("fake_domain_uuid")
    vdsm_image_uuid = params.get("vdsm_image_uuid")
    vdsm_vol_uuid = params.get("vdsm_vol_uuid")
    vdsm_vm_uuid = params.get("vdsm_vm_uuid")
    vdsm_ovf_output = params.get("vdsm_ovf_output")
    v2v_user = params.get("unprivileged_user", "")
    v2v_timeout = int(params.get("v2v_timeout", 1200))
    status_error = "yes" == params.get("status_error", "no")
    su_cmd = "su - %s -c " % v2v_user
    output_uri = params.get("oc_uri", "")
    pool_name = params.get("pool_name", "v2v_test")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target", "v2v_pool")
    emulated_img = params.get("emulated_image_path", "v2v-emulated-img")
    pvt = utlv.PoolVolumeTest(test, params)
    new_v2v_user = False
    address_cache = env.get('address_cache')
    params['vmcheck_flag'] = False
    checkpoint = params.get('checkpoint', '')
    error_flag = 'strict'
    estimate_file = ''

    def create_pool(
            user_pool=False,
            pool_name=pool_name,
            pool_target=pool_target):
        """
        Create libvirt pool as the output storage
        """
        if output_uri == "qemu:///session" or user_pool:
            target_path = os.path.join("/home", v2v_user, pool_target)
            cmd = su_cmd + "'mkdir -p %s'" % target_path
            process.system(cmd, verbose=True)
            # Sometimes pool_creat_as returns sucess, but the pool can
            # not be found in user session.
            virsh.pool_create_as(
                pool_name,
                'dir',
                target_path,
                unprivileged_user=v2v_user,
                debug=True)

            res = virsh.pool_info(
                pool_name,
                unprivileged_user=v2v_user,
                debug=True)
            if res.exit_status != 0:
                return False
        else:
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_img)

        return True

    def cleanup_pool(
            user_pool=False,
            pool_name=pool_name,
            pool_target=pool_target):
        """
        Clean up libvirt pool
        """
        if output_uri == "qemu:///session" or user_pool:
            virsh.pool_destroy(
                pool_name,
                unprivileged_user=v2v_user,
                debug=True)
            target_path = os.path.join("/home", v2v_user, pool_target)
            cmd = su_cmd + "'rm -rf %s'" % target_path
            process.system(cmd, verbose=True)
        else:
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)

    def get_all_uuids(output):
        """
        Get export domain uuid, image uuid and vol uuid from command output.
        """
        tmp_target = re.findall(r"qemu-img\s'convert'\s.+\s'(\S+)'\n", output)
        if len(tmp_target) < 1:
            test.error("Fail to find tmp target file name when converting vm"
                       " disk image")
        targets = tmp_target[0].split('/')
        return (targets[3], targets[5], targets[6])

    def get_ovf_content(output):
        """
        Find and read ovf file.
        """
        export_domain_uuid, _, vol_uuid = get_all_uuids(output)
        export_vm_dir = os.path.join(mnt_point, export_domain_uuid,
                                     'master/vms')
        ovf_content = ""
        if os.path.isdir(export_vm_dir):
            ovf_id = "ovf:id='%s'" % vol_uuid
            ret = to_text(process.system_output("grep -R \"%s\" %s" %
                                                (ovf_id, export_vm_dir)))
            ovf_file = ret.split(":")[0]
            if os.path.isfile(ovf_file):
                ovf_f = open(ovf_file, "r")
                ovf_content = ovf_f.read()
                ovf_f.close()
        else:
            logging.error("Can't find ovf file to read")
        return ovf_content

    def get_img_path(output):
        """
        Get the full path of the converted image.
        """
        img_name = vm_name + "-sda"
        if output_mode == "libvirt":
            img_path = virsh.vol_path(img_name, output_storage).stdout.strip()
        elif output_mode == "local":
            img_path = os.path.join(output_storage, img_name)
        elif output_mode in ["rhev", "vdsm"]:
            export_domain_uuid, image_uuid, vol_uuid = get_all_uuids(output)
            img_path = os.path.join(mnt_point, export_domain_uuid, 'images',
                                    image_uuid, vol_uuid)
        return img_path

    def check_vmtype(ovf, expected_vmtype):
        """
        Verify vmtype in ovf file.
        """
        if output_mode != "rhev":
            return
        if expected_vmtype == "server":
            vmtype_int = 1
        elif expected_vmtype == "desktop":
            vmtype_int = 0
        else:
            return
        if "<VmType>%s</VmType>" % vmtype_int in ovf:
            logging.info("Find VmType=%s in ovf file",
                         expected_vmtype)
        else:
            test.fail("VmType check failed")

    def check_image(img_path, check_point, expected_value):
        """
        Verify image file allocation mode and format
        """
        if not img_path or not os.path.isfile(img_path):
            test.error("Image path: '%s' is invalid" % img_path)
        img_info = utils_misc.get_image_info(img_path)
        logging.debug("Image info: %s", img_info)
        if check_point == "allocation":
            if expected_value == "sparse":
                if img_info['vsize'] > img_info['dsize']:
                    logging.info("%s is a sparse image", img_path)
                else:
                    test.fail("%s is not a sparse image" % img_path)
            elif expected_value == "preallocated":
                if img_info['vsize'] <= img_info['dsize']:
                    logging.info("%s is a preallocated image", img_path)
                else:
                    test.fail("%s is not a preallocated image" % img_path)
        if check_point == "format":
            if expected_value == img_info['format']:
                logging.info("%s format is %s", img_path, expected_value)
            else:
                test.fail("%s format is not %s" % (img_path, expected_value))

    def check_new_name(output, expected_name):
        """
        Verify guest name changed to the new name.
        """
        found = False
        if output_mode == "libvirt":
            found = virsh.domain_exists(expected_name)
        if output_mode == "local":
            found = os.path.isfile(os.path.join(output_storage,
                                                expected_name + "-sda"))
        if output_mode in ["rhev", "vdsm"]:
            ovf = get_ovf_content(output)
            found = "<Name>%s</Name>" % expected_name in ovf
        else:
            return
        if found:
            logging.info("Guest name renamed when converting it")
        else:
            test.fail("Rename guest failed")

    def check_nocopy(output):
        """
        Verify no image created if convert command use --no-copy option
        """
        img_path = get_img_path(output)
        if not os.path.isfile(img_path):
            logging.info("No image created with --no-copy option")
        else:
            test.fail("Find %s" % img_path)

    def check_connection(output, expected_uri):
        """
        Check output connection uri used when converting guest
        """
        init_msg = "Initializing the target -o libvirt -oc %s" % expected_uri
        if init_msg in output:
            logging.info("Find message: %s", init_msg)
        else:
            test.fail("Not find message: %s" % init_msg)

    def check_ovf_snapshot_id(ovf_content):
        """
        Check if snapshot id in ovf file consists of '0's
        """
        search = re.search("ovf:vm_snapshot_id='(.*?)'", ovf_content)
        if search:
            snapshot_id = search.group(1)
            logging.debug('vm_snapshot_id = %s', snapshot_id)
            if snapshot_id.count('0') >= 32:
                test.fail('vm_snapshot_id consists with "0"')
        else:
            test.fail('Fail to find snapshot_id')

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
        logging.debug('Source info to check: %s', source_info)
        checklist = ['nr vCPUs', 'hypervisor type', 'source name', 'memory',
                     'disks', 'NICs']
        if hypervisor in ['kvm', 'xen']:
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
            v2v_virsh = virsh.VirshPersistent(**virsh_dargs)
            logging.debug('a new virsh session %s was created', v2v_virsh)
            close_virsh = True

        # Check single values
        fail = []
        try:
            xml = vm_xml.VMXML.new_from_inactive_dumpxml(
                vm_name, virsh_instance=v2v_virsh)
        finally:
            if close_virsh:
                logging.debug('virsh session %s is closing', v2v_virsh)
                v2v_virsh.close_session()

        check_map = {}
        check_map['nr vCPUs'] = xml.vcpu
        check_map['hypervisor type'] = xml.hypervisor_type
        check_map['source name'] = xml.vm_name
        check_map['memory'] = str(int(xml.max_mem) * 1024) + ' (bytes)'

        if hypervisor in ['kvm', 'xen']:
            check_map['display'] = xml.get_graphics_devices()[0].type_name

        logging.info('KEY:\tSOURCE<-> XML')
        for key in check_map:
            logging.info('%-15s:%18s <-> %s', key,
                         source_info[key], check_map[key])
            if str(check_map[key]) not in source_info[key]:
                fail.append(key)

        # Check disk info
        disk = list(xml.get_disk_all_by_expr('device==disk').values())[0]

        def _get_disk_subelement_attr_value(obj, attr, subattr):
            if obj.find(attr) is not None:
                return obj.find(attr).get(subattr)
        bus = _get_disk_subelement_attr_value(disk, 'target', 'bus')
        driver_type = _get_disk_subelement_attr_value(disk, 'driver', 'type')
        path = _get_disk_subelement_attr_value(disk, 'source', 'file')

        # For esx, disk output is like "disks: json: { ... } (raw) [scsi]"
        # For xen, disk output is like "disks: json: { ... } [ide]"
        # For kvm, disk output is like "/rhel8.0-2.qcow2 (qcow2) [virtio-blk]"
        if hypervisor == 'kvm':
            disks_info_pattern = r"%s \(%s\) \[%s" % (path, driver_type, bus)
        elif hypervisor == 'esx':
            # replace '.vmdk' with '-flat.vmdk', this is done in v2v
            path_pattern1 = path.split()[1].replace('.vmdk', '-flat.vmdk')
            # In newer qemu version, '_' is replaced with '%5f'.
            path_pattern2 = path_pattern1.replace('_', '%5f')
            # nbd:unix:/tmp/v2vnbdkit.u44G6C/nbdkit1.sock:exportname=/ (raw)
            # [scsi]
            path_pattern_nbd = r'nbd:unix:/.*? \(raw\) \[%s\]' % bus
            # For esx, '(raw)' is fixed? Let's see if others will be met.
            disks_info_pattern = '|'.join(
                [path_pattern_nbd] + [
                    r"https://%s/folder/%s\?dcPath=data&dsName=esx.*} \(raw\) \[%s" %
                    (remote_host, i, bus) for i in [
                        path_pattern1, path_pattern2]])
        elif hypervisor == 'xen':
            disks_info_pattern = "file\.path.*%s.*file\.host.*%s.* \[%s" % (
                path, remote_host, bus)

        source_disks = source_info['disks'].split()
        logging.info('disks:%s<->%s', source_info['disks'], disks_info_pattern)
        if not re.search(disks_info_pattern, source_info['disks']):
            fail.append('disks')

        # Check nic info
        nic = list(xml.get_iface_all().values())[0]
        type = nic.get('type')
        mac = nic.find('mac').get('address')
        nic_source = nic.find('source')
        name = nic_source.get(type)
        nic_info = '%s "%s" mac: %s' % (type, name, mac)
        logging.info('NICs:%s<->%s', source_info['NICs'], nic_info)
        if nic_info.lower() not in source_info['NICs'].lower():
            fail.append('NICs')

        # Check cpu features
        if hypervisor in ['kvm', 'xen']:
            feature_list = xml.features.get_feature_list()
            logging.info('CPU features:%s<->%s',
                         source_info['CPU features'], feature_list)
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
            logging.info('Checking man page of virt-v2v for "%s"', in_man)
            if in_man not in man_page:
                test.fail('"%s" not in man page' % in_man)
        if not_in_man:
            logging.info('Checking man page of virt-v2v for "%s"', not_in_man)
            if not_in_man in man_page:
                test.fail('"%s" not removed from man page' % not_in_man)

    def check_print_estimate(estimate_file):
        """
        Check disk size and total size in file of estimate created by v2v
        """
        import json

        content = None
        buf = ''
        with open(estimate_file) as fp:
            all_content = fp.read()
            fp.seek(0)
            for line in fp:
                buf += line
                if '}' not in line:
                    continue
                if 'disks' in buf and 'total' in buf:
                    content = json.loads(buf)
                    break
                buf = ''

        logging.debug('json file content:\n%s' % all_content)
        if not content or sum(content['disks']) != content['total']:
            test.fail("The disks' size doesn't same as total value")

    def check_result(cmd, result, status_error):
        """
        Check virt-v2v command result
        """
        utils_v2v.check_exit_status(result, status_error, error_flag)
        output = to_text(result.stdout + result.stderr, errors=error_flag)
        output_stdout = to_text(result.stdout, errors=error_flag)
        if status_error:
            if checkpoint == 'length_of_error':
                log_lines = output.split('\n')
                v2v_start = False
                for line in log_lines:
                    if line.startswith('virt-v2v:'):
                        v2v_start = True
                    if line.startswith('libvirt:'):
                        v2v_start = False
                    # 76 is the max length in v2v
                    if v2v_start and len(line) > 76:
                        test.fail('Error log longer than 76 charactors: %s' %
                                  line)
            if checkpoint == 'disk_not_exist':
                vol_list = virsh.vol_list(pool_name)
                logging.info(vol_list)
                if vm_name in vol_list.stdout:
                    test.fail('Disk exists for vm %s' % vm_name)
        else:
            if output_mode == "rhev" and checkpoint != 'quiet':
                ovf = get_ovf_content(output)
                logging.debug("ovf content: %s", ovf)
                check_ovf_snapshot_id(ovf)
                if '--vmtype' in cmd:
                    expected_vmtype = re.findall(r"--vmtype\s(\w+)", cmd)[0]
                    check_vmtype(ovf, expected_vmtype)
            if '-oa' in cmd and '--no-copy' not in cmd:
                expected_mode = re.findall(r"-oa\s(\w+)", cmd)[0]
                img_path = get_img_path(output)

                def check_alloc():
                    try:
                        check_image(img_path, "allocation", expected_mode)
                        return True
                    except exceptions.TestFail:
                        pass
                if not utils_misc.wait_for(
                        check_alloc, timeout=600, step=10.0):
                    test.fail('Allocation check failed.')
            if '-of' in cmd and '--no-copy' not in cmd and '--print-source' not in cmd and checkpoint != 'quiet' and not no_root:
                expected_format = re.findall(r"-of\s(\w+)", cmd)[0]
                img_path = get_img_path(output)
                check_image(img_path, "format", expected_format)
            if '-on' in cmd:
                expected_name = re.findall(r"-on\s(\w+)", cmd)[0]
                check_new_name(output, expected_name)
            if '--no-copy' in cmd:
                check_nocopy(output)
            if '-oc' in cmd:
                expected_uri = re.findall(r"-oc\s(\S+)", cmd)[0]
                check_connection(output, expected_uri)
            if output_mode == "rhev":
                if not utils_v2v.import_vm_to_ovirt(params, address_cache):
                    test.fail("Import VM failed")
                else:
                    vmchecker = VMChecker(test, params, env)
                    params['vmchecker'] = vmchecker
                    params['vmcheck_flag'] = True
            if output_mode == "libvirt":
                if "qemu:///session" not in v2v_options and not no_root:
                    virsh.start(vm_name, debug=True, ignore_status=False)
            if checkpoint in ['vmx', 'vmx_ssh']:
                vmchecker = VMChecker(test, params, env)
                params['vmchecker'] = vmchecker
                params['vmcheck_flag'] = True
                ret = vmchecker.run()
                if len(ret) == 0:
                    logging.info("All common checkpoints passed")
            if checkpoint == 'quiet':
                if len(output.strip().splitlines()) > 10:
                    test.fail('Output is not empty in quiet mode')
            if checkpoint == 'dependency':
                if 'libguestfs-winsupport' not in output:
                    test.fail('libguestfs-winsupport not in dependency')
                if all(
                    pkg_pattern not in output for pkg_pattern in [
                        'VMF',
                        'edk2-ovmf']):
                    test.fail('OVMF/AAVMF not in dependency')
                if 'qemu-kvm-rhev' in output:
                    test.fail('qemu-kvm-rhev is in dependency')
                if 'libX11' in output:
                    test.fail('libX11 is in dependency')
                if 'kernel-rt' in output:
                    test.fail('kernel-rt is in dependency')
                win_img = params.get('win_image')
                command = 'guestfish -a %s -i'
                if process.run(
                        command %
                        win_img,
                        ignore_status=True).exit_status == 0:
                    test.fail('Command "%s" success' % command % win_img)
            if checkpoint == 'no_dcpath':
                if '--dcpath' in output:
                    test.fail('"--dcpath" is not removed')
            if checkpoint == 'debug_overlays':
                search = re.search('Overlay saved as(.*)', output)
                if not search:
                    test.fail('Not find log of saving overlays')
                overlay_path = search.group(1).strip()
                logging.debug('Overlay file location: %s' % overlay_path)
                if os.path.isfile(overlay_path):
                    logging.info('Found overlay file: %s' % overlay_path)
                else:
                    test.fail('Overlay file not saved')
            if checkpoint.startswith('empty_nic_source'):
                target_str = '%s "eth0" mac: %s' % (
                    params[checkpoint][0], params[checkpoint][1])
                logging.info('Expect log: %s', target_str)
                if target_str not in output_stdout.lower():
                    test.fail('Expect log not found: %s' % target_str)
            if checkpoint == 'print_source':
                check_source(output_stdout)
            if checkpoint == 'machine_readable':
                if os.path.exists(params.get('example_file', '')):
                    # Checking items in example_file exist in latest
                    # output regardless of the orders and new items.
                    with open(params['example_file']) as f:
                        for line in f:
                            if line.strip() not in output_stdout.strip():
                                test.fail(
                                    '%s not in --machine-readable output' %
                                    line.strip())
                else:
                    test.error('No content to compare with')
            if checkpoint == 'compress':
                img_path = get_img_path(output)
                logging.info('Image path: %s', img_path)

                qemu_img_cmd = 'qemu-img check %s' % img_path
                qemu_img_locking_feature_support = libvirt_storage.check_qemu_image_lock_support()
                if qemu_img_locking_feature_support:
                    qemu_img_cmd = 'qemu-img check %s -U' % img_path

                disk_check = process.run(qemu_img_cmd).stdout_text
                logging.info(disk_check)
                compress_info = disk_check.split(',')[-1].split('%')[0].strip()
                compress_rate = float(compress_info)
                logging.info('%s%% compressed', compress_rate)
                if compress_rate < 0.1:
                    test.fail('Disk image NOT compressed')
            if checkpoint == 'tail_log':
                messages = params['tail'].get_output()
                logging.info('Content of /var/log/messages during conversion:')
                logging.info(messages)
                msg_content = params['msg_content']
                if msg_content in messages:
                    test.fail('Found "%s" in /var/log/messages' % msg_content)
            if checkpoint == 'print_estimate_tofile':
                check_print_estimate(estimate_file)
        log_check = utils_v2v.check_log(params, output)
        if log_check:
            test.fail(log_check)
        check_man_page(params.get('in_man'), params.get('not_in_man'))

    backup_xml = None
    vdsm_domain_dir, vdsm_image_dir, vdsm_vm_dir = ("", "", "")
    try:
        if version_requried and not utils_v2v.multiple_versions_compare(
                version_requried):
            test.cancel("Testing requries version: %s" % version_requried)

        if hypervisor == "xen":
            # See man virt-v2v-input-xen(1)
            process.run(
                'update-crypto-policies --set LEGACY',
                verbose=True,
                ignore_status=True,
                shell=True)

        if checkpoint.startswith('empty_nic_source'):
            xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            iface = xml.get_devices('interface')[0]
            disks = xml.get_devices('disk')
            del iface.source
            iface.type_name = checkpoint.split('_')[-1]
            iface.source = {iface.type_name: ''}
            params[checkpoint] = [iface.type_name, iface.mac_address]
            logging.debug(iface.source)
            devices = vm_xml.VMXMLDevices()
            devices.extend(disks)
            devices.append(iface)
            xml.set_devices(devices)
            logging.info(xml.xmltreefile)
            params['input_xml'] = xml.xmltreefile.name
        # Build input options
        input_option = ""
        if input_mode is None:
            pass
        elif input_mode == "libvirt":
            uri_obj = utils_v2v.Uri(hypervisor)
            ic_uri = uri_obj.get_uri(remote_host, vpx_dc, esx_ip)
            # Remote libvirt connection is not offically supported by
            # v2v and may fail. Just use localhost to simulate a remote
            # connection to test the warnings.
            if checkpoint == 'remote_libvirt_conn':
                ic_uri = 'qemu+ssh://localhost/system'
            input_option = "-i %s -ic %s %s" % (input_mode, ic_uri, vm_name)
            if checkpoint == 'with_ic':
                ic_uri = 'qemu:///session'
                input_option = "-i ova %s -ic %s -of qcow2" % (
                    input_file, ic_uri)
            if checkpoint == 'without_ic':
                input_option = "-i ova %s -of raw" % input_file
            # Build network&bridge option to avoid network error
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
                logging.info("Check image before convert")
                check_image(disk_img, "format", input_format)
                if input_allo_mode:
                    check_image(disk_img, "allocation", input_allo_mode)

        # Build output options
        output_option = ""
        if output_mode:
            output_option = "-o %s" % output_mode
            if output_mode != 'null':
                output_option += " -os %s" % output_storage
            if checkpoint == 'rhv':
                output_option = output_option.replace('rhev', 'rhv')
            if checkpoint in ['with_ic', 'without_ic']:
                output_option = output_option.replace('v2v_dir', 'src_pool')
        output_format = params.get("output_format")
        if output_format and output_format != input_format:
            output_option += " -of %s" % output_format
        output_allo_mode = params.get("output_allo_mode")
        if output_allo_mode:
            output_option += " -oa %s" % output_allo_mode

        # Build vdsm related options
        if output_mode in ['vdsm', 'rhev']:
            if not os.path.isdir(mnt_point):
                os.mkdir(mnt_point)
            if not utils_misc.mount(nfs_storage, mnt_point, "nfs"):
                test.error("Mount NFS Failed")
            if output_mode == 'vdsm':
                v2v_options += " --vdsm-image-uuid %s" % vdsm_image_uuid
                v2v_options += " --vdsm-vol-uuid %s" % vdsm_vol_uuid
                v2v_options += " --vdsm-vm-uuid %s" % vdsm_vm_uuid
                v2v_options += " --vdsm-ovf-output %s" % vdsm_ovf_output
                vdsm_domain_dir = os.path.join(mnt_point, fake_domain_uuid)
                vdsm_image_dir = os.path.join(mnt_point, export_domain_uuid,
                                              "images", vdsm_image_uuid)
                vdsm_vm_dir = os.path.join(mnt_point, export_domain_uuid,
                                           "master/vms", vdsm_vm_uuid)
                # For vdsm_domain_dir, just create a dir to test BZ#1176591
                os.makedirs(vdsm_domain_dir)
                os.makedirs(vdsm_image_dir)
                os.makedirs(vdsm_vm_dir)

            if output_mode == 'rhev':
                # create different sasl_user name for different job
                params.update({'sasl_user': params.get("sasl_user") +
                               utils_misc.generate_random_string(3)})
                logging.info('sals user name is %s' % params.get("sasl_user"))

                user_pwd = "[['%s', '%s']]" % (params.get("sasl_user"),
                                               params.get("sasl_pwd"))
                v2v_sasl = utils_sasl.SASL(sasl_user_pwd=user_pwd)
                v2v_sasl.server_ip = params.get("remote_ip")
                v2v_sasl.server_user = params.get('remote_user')
                v2v_sasl.server_pwd = params.get('remote_pwd')
                v2v_sasl.setup(remote=True)
                logging.debug('A SASL session %s was created', v2v_sasl)

        # Output more messages except quiet mode
        if checkpoint == 'quiet':
            v2v_options += ' -q'
        elif checkpoint not in ['length_of_error', 'empty_nic_source_network',
                                'empty_nic_source_bridge', 'machine_readable']:
            v2v_options += " -v -x"

        # Prepare for libvirt unprivileged user session connection
        if "qemu:///session" in v2v_options or no_root:
            try:
                pwd.getpwnam(v2v_user)
            except KeyError:
                # create new user
                process.system("useradd %s" % v2v_user, ignore_status=True)
            new_v2v_user = True
            user_info = pwd.getpwnam(v2v_user)
            logging.info("Convert to qemu:///session by user '%s'", v2v_user)
            if input_mode == "disk":
                # Copy image from souce and change the image owner and group
                disk_path = os.path.join(
                    data_dir.get_tmp_dir(), os.path.basename(disk_img))
                logging.info('Copy image file %s to %s', disk_img, disk_path)
                shutil.copyfile(disk_img, disk_path)
                input_option = input_option.replace(disk_img, disk_path)
                os.chown(disk_path, user_info.pw_uid, user_info.pw_gid)
            elif not no_root:
                test.cancel("Only support convert local disk")

        # Setup ssh-agent access to xen hypervisor
        if hypervisor == 'xen':
            user = params.get("xen_host_user", "root")
            source_pwd = passwd = params.get("xen_host_passwd", "redhat")
            logging.info("set up ssh-agent access ")
            xen_pubkey, xen_session = utils_v2v.v2v_setup_ssh_key(
                remote_host, user, passwd, auto_close=False)
            utils_misc.add_identities_into_ssh_agent()
            # Check if xen guest exists
            uri = utils_v2v.Uri(hypervisor).get_uri(remote_host)
            if not virsh.domain_exists(vm_name, uri=uri):
                logging.error('VM %s not exists', vm_name)
            # If the input format is not define, we need to either define
            # the original format in the source metadata(xml) or use '-of'
            # to force the output format, see BZ#1141723 for detail.
            if '-of' not in v2v_options and checkpoint != 'xen_no_output_format':
                v2v_options += ' -of %s' % params.get("default_output_format",
                                                      "qcow2")

        # Create password file for access to ESX hypervisor
        if hypervisor == 'esx':
            source_pwd = vpx_passwd = params.get("vpx_password")
            vpx_passwd_file = os.path.join(
                data_dir.get_tmp_dir(), "vpx_passwd")
            logging.info("Building ESX no password interactive verification.")
            pwd_f = open(vpx_passwd_file, 'w')
            pwd_f.write(vpx_passwd)
            pwd_f.close()
            output_option += " -ip %s" % vpx_passwd_file
            # rhel8 slow stream doesn't support option 'ip' temporarily
            # so use option 'password-file' instead.
            if not utils_v2v.v2v_supported_option("-ip <filename>"):
                output_option = output_option.replace(
                    '-ip', '--password-file', 1)

        # if don't specify any output option for virt-v2v, 'default' pool
        # will be used.
        if output_mode is None:
            # Cleanup first to avoid failure if 'default' pool exists.
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_img)

        # Create libvirt dir pool
        if output_mode == "libvirt":
            utils_misc.wait_for(create_pool, timeout=30, step=3)

        # Work around till bug fixed
        os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        if checkpoint in ['with_ic', 'without_ic']:
            new_v2v_user = True
            v2v_options += ' -on %s' % new_vm_name
            utils_misc.wait_for(
                lambda: create_pool(
                    user_pool=True,
                    pool_name='src_pool',
                    pool_target='v2v_src_pool'),
                timeout=30,
                step=3)

        if checkpoint == 'vmx':
            mount_point = params.get('mount_point')
            if not os.path.isdir(mount_point):
                os.mkdir(mount_point)
            nfs_vmx = params.get('nfs_vmx')
            if not utils_misc.mount(nfs_vmx, mount_point, 'nfs', verbose=True):
                test.error('Mount nfs for vmx failed')
            vmx = params.get('vmx')
            input_option = '-i vmx %s' % vmx
            v2v_options += " -b %s -n %s" % (params.get("output_bridge"),
                                             params.get("output_network"))

        if checkpoint == 'vmx_ssh':
            esx_user = params.get("esx_host_user", "root")
            esx_pwd = params.get("esx_host_passwd")
            vmx = params.get('vmx')
            esx_pubkey, esx_session = utils_v2v.v2v_setup_ssh_key(
                esx_ip, esx_user, esx_pwd, server_type='esx', auto_close=False)
            utils_misc.add_identities_into_ssh_agent()
            input_option = '-i vmx -it ssh %s' % vmx
            v2v_options += " -b %s -n %s" % (params.get("output_bridge"),
                                             params.get("output_network"))

        if checkpoint == 'simulate_nfs':
            simulate_images = params.get("simu_images_path")
            simulate_vms = params.get("simu_vms_path")
            simulate_dom_md = params.get("simu_dom_md_path")
            os.makedirs(simulate_images)
            os.makedirs(simulate_vms)
            process.run('touch %s' % simulate_dom_md)
            process.run('chmod -R 777 /tmp/rhv/')

        if checkpoint == 'print_estimate_tofile':
            estimate_file = utils_misc.generate_tmp_file_name(
                'v2v_print_estimate')
            v2v_options += " --machine-readable=file:%s" % estimate_file

        if checkpoint == 'remote_libvirt_conn':
            # Add localhost to known_hosts
            cmd = 'ssh-keyscan -t ecdsa localhost >> ~/.ssh/known_hosts'
            process.run(cmd, shell=True)
            # Setup remote login without password
            public_key = ssh_key.get_public_key().rstrip()
            cmd = 'echo "%s" >> ~/.ssh/authorized_keys' % public_key
            process.run(cmd, shell=True)

        # Running virt-v2v command
        cmd = "%s %s %s %s" % (utils_v2v.V2V_EXEC, input_option,
                               output_option, v2v_options)
        if v2v_user:
            cmd_export_env = 'export LIBGUESTFS_BACKEND=direct'
            cmd = "%s '%s;%s'" % (su_cmd, cmd_export_env, cmd)

        if params.get('cmd_free') == 'yes':
            cmd = params.get('check_command')
            # only set error to 'ignore' to avoid exception for RHEL7-84978
            if "guestfish" in cmd:
                error_flag = "replace"

        # Set timeout to kill v2v process before conversion succeed
        if checkpoint == 'disk_not_exist':
            v2v_timeout = 30
        # Get tail content of /var/log/messages
        if checkpoint == 'tail_log':
            params['tail_log'] = os.path.join(
                data_dir.get_tmp_dir(), 'tail_log')
            params['tail'] = aexpect.Tail(
                command='tail -f /var/log/messages',
                output_func=utils_misc.log_line,
                output_params=(params['tail_log'],)
            )
        cmd_result = process.run(cmd, timeout=v2v_timeout, verbose=True,
                                 ignore_status=True)
        if new_vm_name:
            vm_name = new_vm_name
            params['main_vm'] = new_vm_name
        check_result(cmd, cmd_result, status_error)
    finally:
        if hypervisor == "esx":
            process.run("rm -rf %s" % vpx_passwd_file)
        if checkpoint == "weak_dendency":
            utils_package.package_install(['libguestfs-xfs', 'virt-v2v'])
        for vdsm_dir in [vdsm_domain_dir, vdsm_image_dir, vdsm_vm_dir]:
            if os.path.exists(vdsm_dir):
                shutil.rmtree(vdsm_dir)
        if os.path.exists(mnt_point):
            utils_misc.umount(nfs_storage, mnt_point, "nfs")
            os.rmdir(mnt_point)
        if output_mode == "local":
            image_name = vm_name + "-sda"
            img_file = os.path.join(output_storage, image_name)
            xml_file = img_file + ".xml"
            for local_file in [img_file, xml_file]:
                if os.path.exists(local_file):
                    os.remove(local_file)
        if output_mode == "libvirt":
            if "qemu:///session" in v2v_options or no_root:
                cmd = su_cmd + "'virsh undefine %s'" % vm_name
                try:
                    process.system(cmd)
                except Exception:
                    logging.error('Undefine "%s" failed', vm_name)
                if no_root:
                    cleanup_pool(user_pool=True, pool_name='src_pool',
                                 pool_target='v2v_src_pool')
            else:
                virsh.remove_domain(vm_name)
            cleanup_pool()
        if output_mode is None:
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)
        vmcheck_flag = params.get("vmcheck_flag")
        if vmcheck_flag and params.get('vmchecker'):
            params['vmchecker'].cleanup()
        if new_v2v_user:
            process.system("userdel -fr %s" % v2v_user)
        if backup_xml:
            backup_xml.sync()
        if output_mode == 'rhev' and v2v_sasl:
            v2v_sasl.cleanup()
            logging.debug('SASL session %s is closing', v2v_sasl)
            v2v_sasl.close_session()
        if checkpoint == 'vmx':
            utils_misc.umount(params['nfs_vmx'], params['mount_point'], 'nfs')
            os.rmdir(params['mount_point'])
        if checkpoint == 'vmx_ssh':
            utils_v2v.v2v_setup_ssh_key_cleanup(esx_session, esx_pubkey, 'esx')
            process.run("ssh-agent -k")
        if checkpoint == 'simulate_nfs':
            process.run('rm -rf /tmp/rhv/')
        if os.path.exists(estimate_file):
            os.remove(estimate_file)
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
        if checkpoint == 'remote_libvirt_conn':
            cmd = r"sed -i '/localhost/d' ~/.ssh/known_hosts"
            process.run(cmd, shell=True, ignore_status=True)
            if locals().get('public_key'):
                key = public_key.rstrip().split()[1].split('/')[0]
                cmd = r"sed -i '/%s/d' ~/.ssh/authorized_keys" % key
                process.run(cmd, shell=True, ignore_status=True)
