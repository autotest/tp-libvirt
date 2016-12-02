"""
Test all options of command: virt-v2v
"""
import os
import re
import pwd
import logging
import shutil
import string

import aexpect

from avocado.core import exceptions
from avocado.utils import process

from virttest import data_dir
from virttest import ssh_key
from virttest import utils_misc
from virttest import utils_v2v
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test various options of virt-v2v.
    """
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    for v in params.itervalues():
        if "V2V_EXAMPLE" in v:
            raise exceptions.TestSkipError("Please set real value for %s" % v)

    vm_name = params.get("main_vm", "EXAMPLE")
    new_vm_name = params.get("new_vm_name")
    input_mode = params.get("input_mode")
    v2v_options = params.get("v2v_options", "")
    hypervisor = params.get("hypervisor", "kvm")
    remote_host = params.get("remote_host", "EXAMPLE")
    vpx_dc = params.get("vpx_dc", "EXAMPLE")
    esx_ip = params.get("esx_ip", "EXAMPLE")
    output_mode = params.get("output_mode")
    output_storage = params.get("output_storage", "default")
    disk_img = params.get("input_disk_image", "")
    nfs_storage = params.get("nfs_storage")
    no_root = 'yes' == params.get('no_root', 'no')
    mnt_point = params.get("mount_point")
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
    pool_target = params.get("pool_target_path", "v2v_pool")
    emulated_img = params.get("emulated_image_path", "v2v-emulated-img")
    pvt = utlv.PoolVolumeTest(test, params)
    new_v2v_user = False
    address_cache = env.get('address_cache')
    params['vmcheck_flag'] = False
    checkpoint = params.get('checkpoint', '')

    def create_pool(user_pool=False, pool_name=pool_name, pool_target=pool_target):
        """
        Create libvirt pool as the output storage
        """
        if output_uri == "qemu:///session" or user_pool:
            target_path = os.path.join("/home", v2v_user, pool_target)
            cmd = su_cmd + "'mkdir %s'" % target_path
            process.system(cmd, verbose=True)
            cmd = su_cmd + "'virsh pool-create-as %s dir" % pool_name
            cmd += " --target %s'" % target_path
            process.system(cmd, verbose=True)
        else:
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_img)

    def cleanup_pool(user_pool=False, pool_name=pool_name, pool_target=pool_target):
        """
        Clean up libvirt pool
        """
        if output_uri == "qemu:///session" or user_pool:
            cmd = su_cmd + "'virsh pool-destroy %s'" % pool_name
            process.system(cmd, verbose=True)
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
            raise exceptions.TestError("Fail to find tmp target file name when"
                                       " converting vm disk image")
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
            ret = process.system_output("grep -R \"%s\" %s" %
                                        (ovf_id, export_vm_dir))
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
            raise exceptions.TestFail("VmType check failed")

    def check_image(img_path, check_point, expected_value):
        """
        Verify image file allocation mode and format
        """
        if not img_path or not os.path.isfile(img_path):
            raise exceptions.TestError("Image path: '%s' is invalid" % img_path)
        img_info = utils_misc.get_image_info(img_path)
        logging.debug("Image info: %s", img_info)
        if check_point == "allocation":
            if expected_value == "sparse":
                if img_info['vsize'] > img_info['dsize']:
                    logging.info("%s is a sparse image", img_path)
                else:
                    raise exceptions.TestFail("%s is not a sparse image" % img_path)
            elif expected_value == "preallocated":
                if img_info['vsize'] <= img_info['dsize']:
                    logging.info("%s is a preallocated image", img_path)
                else:
                    raise exceptions.TestFail("%s is not a preallocated image"
                                              % img_path)
        if check_point == "format":
            if expected_value == img_info['format']:
                logging.info("%s format is %s", img_path, expected_value)
            else:
                raise exceptions.TestFail("%s format is not %s"
                                          % (img_path, expected_value))

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
            raise exceptions.TestFail("Rename guest failed")

    def check_nocopy(output):
        """
        Verify no image created if convert command use --no-copy option
        """
        img_path = get_img_path(output)
        if not os.path.isfile(img_path):
            logging.info("No image created with --no-copy option")
        else:
            raise exceptions.TestFail("Find %s" % img_path)

    def check_connection(output, expected_uri):
        """
        Check output connection uri used when converting guest
        """
        init_msg = "Initializing the target -o libvirt -oc %s" % expected_uri
        if init_msg in output:
            logging.info("Find message: %s", init_msg)
        else:
            raise exceptions.TestFail("Not find message: %s" % init_msg)

    def check_ovf_snapshot_id(ovf_content):
        """
        Check if snapshot id in ovf file consists of '0's
        """
        search = re.search("ovf:vm_snapshot_id='(.*?)'", ovf_content)
        if search:
            snapshot_id = search.group(1)
            logging.debug('vm_snapshot_id = %s', snapshot_id)
            if snapshot_id.count('0') >= 32:
                raise exceptions.TestFail('vm_snapshot_id consists with "0"')
        else:
            raise exceptions.TestFail('Fail to find snapshot_id')

    def check_source(output):
        """
        Check if --print-source option print the correct info
        """
        # Parse source info
        source = output.split('\n')[2:]
        for i in range(len(source)):
            if source[i].startswith('\t'):
                source[i-1] += source[i]
                source[i] = ''
        source_strip = [x.strip() for x in source if x.strip()]
        source_info = {}
        for line in source_strip:
            source_info[line.split(':')[0]] = line.split(':', 1)[1].strip()
        logging.debug('Source info to check: %s', source_info)
        checklist = ['nr vCPUs',  'hypervisor type', 'source name', 'memory',
                     'display', 'CPU features', 'disks', 'NICs']
        for key in checklist:
            if key not in source_info:
                raise exceptions.TestFail('%s info missing' % key)

        # Check single values
        fail = []
        xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        check_map = {}
        check_map['nr vCPUs'] = xml.vcpu
        check_map['hypervisor type'] = xml.hypervisor_type
        check_map['source name'] = xml.vm_name
        check_map['memory'] = str(int(xml.max_mem) * 1024) + ' (bytes)'
        check_map['display'] = xml.get_graphics_devices()[0].type_name

        logging.info('KEY:\tSOURCE<-> XML')
        for key in check_map:
            logging.info('%-15s:%18s <-> %s', key, source_info[key], check_map[key])
            if source_info[key] != str(check_map[key]):
                fail.append(key)

        # Check disk info
        disk = xml.get_disk_all().values()[0]
        bus, type = disk.find('target').get('bus'), disk.find('driver').get('type')
        path = disk.find('source').get('file')
        disks_info = "%s (%s) [%s]" % (path, type, bus)
        logging.info('disks:%s<->%s', source_info['disks'], disks_info)
        if source_info['disks'] != disks_info:
            fail.append('disks')

        # Check nic info
        nic = xml.get_iface_all().values()[0]
        type = nic.get('type')
        mac = nic.find('mac').get('address')
        nic_source = nic.find('source')
        name = nic_source.get(type)
        nic_info = '%s "%s" mac: %s' % (type, name, mac)
        logging.info('NICs:%s<->%s', source_info['NICs'], nic_info)
        if source_info['NICs'].lower() != nic_info.lower():
            fail.append('NICs')

        # Check cpu features
        feature_list = xml.features.get_feature_list()
        logging.info('CPU features:%s<->%s', source_info['CPU features'], feature_list)
        if sorted(source_info['CPU features'].split(',')) != sorted(feature_list):
            fail.append('CPU features')

        if fail:
            raise exceptions.TestFail('Source info not correct for: %s' % fail)

    def check_result(cmd, result, status_error):
        """
        Check virt-v2v command result
        """
        utlv.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if status_error:
            if checkpoint == 'length_of_error':
                log_lines = output.split('\n')
                v2v_start = False
                for line in log_lines:
                    if line.startswith('virt-v2v:'):
                        v2v_start = True
                    if line.startswith('libvirt:'):
                        v2v_start = False
                    if v2v_start and len(line) > 72:
                        raise exceptions.TestFail('Error log longer than 72 '
                                                  'charactors: %s', line)
            if checkpoint == 'disk_not_exist':
                vol_list = virsh.vol_list(pool_name)
                logging.info(vol_list)
                if vm_name in vol_list.stdout:
                    raise exceptions.TestFail('Disk exists for vm %s' % vm_name)
            else:
                error_map = {
                    'conflict_options': ['option used more than once'],
                    'xen_no_output_format': ['The input metadata did not define'
                                             ' the disk format'],
                    'in_place': ['virt-v2v: error: --in-place cannot be used in RHEL 7']
                }
                if error_map.has_key(checkpoint) and not utils_v2v.check_log(
                        output, error_map[checkpoint]):
                    raise exceptions.TestFail('Not found error message %s' %
                                              error_map[checkpoint])
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
                if not utils_misc.wait_for(check_alloc, timeout=600, step=10.0):
                    raise exceptions.TestFail('Allocation check failed.')
            if '-of' in cmd and '--no-copy' not in cmd and checkpoint != 'quiet':
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
                    raise exceptions.TestFail("Import VM failed")
                else:
                    params['vmcheck_flag'] = True
            if output_mode == "libvirt":
                if "qemu:///session" not in v2v_options and not no_root:
                    virsh.start(vm_name, debug=True, ignore_status=False)
            if checkpoint == 'quiet':
                if len(output.strip()) != 0:
                    raise exceptions.TestFail('Output is not empty in quiet mode')
            if checkpoint == 'dependency':
                if 'libguestfs-winsupport' not in output:
                    raise exceptions.TestFail('libguestfs-winsupport not in dependency')
                if 'qemu-kvm-rhev' in output:
                    raise exceptions.TestFail('qemu-kvm-rhev is in dependency')
                win_img = params.get('win_image')
                command = 'guestfish -a %s -i'
                if process.run(command % win_img, ignore_status=True).exit_status == 0:
                    raise exceptions.TestFail('Command "%s" success' % command % win_img)
            if checkpoint == 'no_dcpath':
                if not utils_v2v.check_log(output, ['--dcpath'], expect=False):
                    raise exceptions.TestFail('"--dcpath" is not removed')
            if checkpoint == 'debug_overlays':
                search = re.search('Overlay saved as(.*)', output)
                if not search:
                    raise exceptions.TestFail('Not find log of saving overlays')
                overlay_path = search.group(1).strip()
                logging.debug('Overlay file location: %s' % overlay_path)
                if os.path.isfile(overlay_path):
                    logging.info('Found overlay file: %s' % overlay_path)
                else:
                    raise exceptions.TestFail('Overlay file not saved')
            if checkpoint.startswith('empty_nic_source'):
                target_str = '%s "eth0" mac: %s' % (params[checkpoint][0], params[checkpoint][1])
                logging.info('Expect log: %s', target_str)
                if target_str not in result.stdout.lower():
                    raise exceptions.TestFail('Expect log not found: %s' % target_str)
            if checkpoint == 'print_source':
                check_source(result.stdout)
            if checkpoint == 'machine_readable':
                if os.path.exists(params.get('example_file', '')):
                    expect_output = open(params['example_file']).read().strip()
                    logging.debug(expect_output)
                    logging.debug(expect_output == result.stdout.strip())
                else:
                    raise exceptions.TestError('No content to compare with')
            if checkpoint == 'compress':
                img_path = get_img_path(output)
                logging.info('Image path: %s', img_path)
                disk_check = process.run('qemu-img check %s' % img_path).stdout
                logging.info(disk_check)
                compress_info = disk_check.split(',')[-1].split('%')[0].strip()
                compress_rate = float(compress_info)
                logging.info('%s%% compressed', compress_rate)
                if compress_rate < 0.1:
                    raise exceptions.TestFail('Disk image NOT compressed')
            if checkpoint == 'tail_log':
                messages = params['tail'].get_output()
                logging.info('Content of /var/log/messages during conversion:')
                logging.info(messages)
                msg_content = params['msg_content']
                if not utils_v2v.check_log(messages, [msg_content], expect=False):
                    raise exceptions.TestFail('Found "%s" in /var/log/messages' %
                                              msg_content)

    backup_xml = None
    vdsm_domain_dir, vdsm_image_dir, vdsm_vm_dir = ("", "", "")
    try:
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
            if checkpoint == 'with_ic':
                ic_uri = 'qemu:///session'
            input_option = "-i %s -ic %s %s" % (input_mode, ic_uri, vm_name)
            if checkpoint == 'without_ic':
                input_option = '-i %s %s' % (input_mode, vm_name)
            # Build network&bridge option to avoid network error
            v2v_options += " -b %s -n %s" % (params.get("output_bridge"),
                                             params.get("output_network"))
        elif input_mode == "disk":
            input_option += "-i %s %s" % (input_mode, disk_img)
        elif input_mode == 'libvirtxml':
            input_xml = params.get('input_xml')
            input_option += '-i %s %s' % (input_mode, input_xml)
        elif input_mode in ['ova']:
            raise exceptions.TestSkipError("Unsupported input mode: %s" % input_mode)
        else:
            raise exceptions.TestError("Unknown input mode %s" % input_mode)
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
            output_option = "-o %s -os %s" % (output_mode, output_storage)
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
                raise exceptions.TestError("Mount NFS Failed")
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

        # Output more messages except quiet mode
        if checkpoint == 'quiet':
            v2v_options += ' -q'
        elif checkpoint not in ['length_of_error', 'empty_nic_source_network',
                                'empty_nic_source_bridge']:
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
                disk_path = os.path.join(data_dir.get_tmp_dir(), os.path.basename(disk_img))
                logging.info('Copy image file %s to %s', disk_img, disk_path)
                shutil.copyfile(disk_img, disk_path)
                input_option = string.replace(input_option, disk_img, disk_path)
                os.chown(disk_path, user_info.pw_uid, user_info.pw_gid)
            elif not no_root:
                raise exceptions.TestSkipError("Only support convert local disk")

        # Setup ssh-agent access to xen hypervisor
        if hypervisor == 'xen':
            user = params.get("xen_host_user", "root")
            passwd = params.get("xen_host_passwd", "redhat")
            logging.info("set up ssh-agent access ")
            ssh_key.setup_ssh_key(remote_host, user=user,
                                  port=22, password=passwd)
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
            vpx_passwd = params.get("vpx_passwd")
            vpx_passwd_file = os.path.join(test.tmpdir, "vpx_passwd")
            logging.info("Building ESX no password interactive verification.")
            pwd_f = open(vpx_passwd_file, 'w')
            pwd_f.write(vpx_passwd)
            pwd_f.close()
            output_option += " --password-file %s" % vpx_passwd_file

        # Create libvirt dir pool
        if output_mode == "libvirt":
            create_pool()

        if hypervisor in ['esx', 'xen'] or input_mode in ['disk', 'libvirtxml']:
            os.environ['LIBGUESTFS_BACKEND'] = 'direct'

        if checkpoint in ['with_ic', 'without_ic']:
            new_v2v_user = True
            v2v_options += ' -on %s' % new_vm_name
            create_pool(user_pool=True, pool_name='src_pool', pool_target='v2v_src_pool')
            create_pool(user_pool=True)
            logging.debug(virsh.pool_list(uri='qemu:///session'))
            sh_install_vm = params.get('sh_install_vm')
            if not sh_install_vm:
                raise exceptions.TestError('Source vm installing script missing')
            process.run('su - %s -c %s' % (v2v_user, sh_install_vm))

        # Running virt-v2v command
        cmd = "%s %s %s %s" % (utils_v2v.V2V_EXEC, input_option,
                               output_option, v2v_options)
        if v2v_user:
            cmd = su_cmd + "'%s'" % cmd

        if checkpoint in ['dependency', 'no_dcpath']:
            cmd = params.get('check_command')

        # Set timeout to kill v2v process before conversion succeed
        if checkpoint == 'disk_not_exist':
            v2v_timeout = 30
        # Get tail content of /var/log/messages
        if checkpoint == 'tail_log':
            params['tail_log'] = os.path.join(data_dir.get_tmp_dir(), 'tail_log')
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
        if hypervisor == "xen":
            process.run("ssh-agent -k")
        if hypervisor == "esx":
            process.run("rm -rf %s" % vpx_passwd_file)
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
                except:
                    logging.error('Undefine "%s" failed', vm_name)
                if no_root:
                    cleanup_pool(user_pool=True, pool_name='src_pool', pool_target='v2v_src_pool')
                    cleanup_pool(user_pool=True)
            else:
                virsh.remove_domain(vm_name)
            cleanup_pool()
        vmcheck_flag = params.get("vmcheck_flag")
        if vmcheck_flag:
            vmcheck = utils_v2v.VMCheck(test, params, env)
            vmcheck.cleanup()
        if new_v2v_user:
            process.system("userdel -f %s" % v2v_user)
        if backup_xml:
            backup_xml.sync()
