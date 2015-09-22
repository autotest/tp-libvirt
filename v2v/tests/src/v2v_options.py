"""
Test all options of command: virt-v2v
"""
import os
import re
import pwd
import logging
import shutil

from autotest.client import utils
from autotest.client.shared import ssh_key
from autotest.client.shared import error

from virttest import virsh
from virttest import utils_v2v
from virttest import utils_misc
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test various options of virt-v2v.
    """
    if utils_v2v.V2V_EXEC is None:
        raise ValueError('Missing command: virt-v2v')
    vm_name = params.get("main_vm", "EXAMPLE")
    new_vm_name = params.get("new_vm_name")
    input_mode = params.get("input_mode")
    v2v_options = params.get("v2v_options", "EXAMPLE")
    hypervisor = params.get("hypervisor", "kvm")
    remote_host = params.get("remote_host", "EXAMPLE")
    vpx_dc = params.get("vpx_dc", "EXAMPLE")
    esx_ip = params.get("esx_ip", "EXAMPLE")
    vpx_passwd = params.get("vpx_passwd", "EXAMPLE")
    ovirt_engine_url = params.get("ovirt_engine_url", "EXAMPLE")
    ovirt_engine_user = params.get("ovirt_engine_user", "EXAMPLE")
    ovirt_engine_passwd = params.get("ovirt_engine_password", "EXAMPLE")
    output_mode = params.get("output_mode")
    output_storage = params.get("output_storage", "default")
    export_name = params.get("export_name", "EXAMPLE")
    storage_name = params.get("storage_name", "EXAMPLE")
    disk_img = params.get("input_disk_image")
    nfs_storage = params.get("nfs_storage")
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
    address_cache = env.get('address_cache')
    for param in [vm_name, remote_host, esx_ip, vpx_dc, ovirt_engine_url,
                  ovirt_engine_user, ovirt_engine_passwd, output_storage,
                  export_name, storage_name, disk_img, export_domain_uuid,
                  v2v_user]:
        if "EXAMPLE" in param:
            raise error.TestNAError("Please replace %s with real value" % param)

    su_cmd = "su - %s -c " % v2v_user
    output_uri = params.get("oc_uri", "")
    pool_name = params.get("pool_name", "v2v_test")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target_path", "v2v_pool")
    emulated_img = params.get("emulated_image_path", "v2v-emulated-img")
    pvt = utlv.PoolVolumeTest(test, params)
    global vm_imported
    vm_imported = False
    new_v2v_user = False
    restore_image_owner = False

    def create_pool():
        """
        Create libvirt pool as the output storage
        """
        if output_uri == "qemu:///session":
            target_path = os.path.join("/home", v2v_user, pool_target)
            cmd = su_cmd + "'mkdir %s'" % target_path
            utils.system(cmd, verbose=True)
            cmd = su_cmd + "'virsh pool-create-as %s dir" % pool_name
            cmd += " --target %s'" % target_path
            utils.system(cmd, verbose=True)
        else:
            pvt.pre_pool(pool_name, pool_type, pool_target, emulated_img)

    def cleanup_pool():
        """
        Clean up libvirt pool
        """
        if output_uri == "qemu:///session":
            cmd = su_cmd + "'virsh pool-destroy %s'" % pool_name
            utils.system(cmd, verbose=True)
            target_path = os.path.join("/home", v2v_user, pool_target)
            cmd = su_cmd + "'rm -rf %s'" % target_path
            utils.system(cmd, verbose=True)
        else:
            pvt.cleanup_pool(pool_name, pool_type, pool_target, emulated_img)

    def get_all_uuids(output):
        """
        Get export domain uuid, image uuid and vol uuid from command output.
        """
        tmp_target = re.findall(r"qemu-img\sconvert\s.+\s'(\S+)'\n", output)
        if len(tmp_target) != 1:
            raise error.TestError("Fail to find tmp target file name when"
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
            ret = utils.system_output("grep -R \"%s\" %s" % (ovf_id,
                                                             export_vm_dir))
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
        img_path = ""
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
            raise error.TestFail("VmType check failed")

    def check_image(output, check_point, expected_value):
        """
        Verify converted image file allocation mode and format
        """
        img_path = get_img_path(output)
        if not img_path or not os.path.isfile(img_path):
            logging.error("Fail to get image path: %s", img_path)
            return
        img_info = utils_misc.get_image_info(img_path)
        logging.info("Image info after converted: %s", img_info)
        if check_point == "allocation":
            if expected_value == "sparse":
                if img_info['vsize'] > img_info['dsize']:
                    logging.info("Image file is sparse")
                else:
                    raise error.TestFail("Image allocation check fail")
            elif expected_value == "preallocated":
                if img_info['vsize'] <= img_info['dsize']:
                    logging.info("Image file is preallocated")
                else:
                    raise error.TestFail("Image allocation check fail")
        if check_point == "format":
            if expected_value == img_info['format']:
                logging.info("Image file format is %s", expected_value)
            else:
                raise error.TestFail("Image format check fail")

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
            raise error.TestFail("Rename guest failed")

    def check_nocopy(output):
        """
        Verify no image created if convert command use --no-copy option
        """
        img_path = get_img_path(output)
        if not os.path.isfile(img_path):
            logging.info("No image created with --no-copy option")
        else:
            raise error.TestFail("Find %s" % img_path)

    def check_connection(output, expected_uri):
        """
        Check output connection uri used when converting guest
        """
        init_msg = "Initializing the target -o libvirt -oc %s" % expected_uri
        if init_msg in output:
            logging.info("Find message: %s", init_msg)
        else:
            raise error.TestFail("Not find message: %s" % init_msg)

    def check_result(cmd, result, status_error):
        """
        Check virt-v2v command result
        """
        utlv.check_exit_status(result, status_error)
        output = result.stdout + result.stderr
        if not status_error:
            if output_mode == "rhev":
                ovf = get_ovf_content(output)
                logging.debug("ovf content: %s", ovf)
                if '--vmtype' in cmd:
                    expected_vmtype = re.findall(r"--vmtype\s(\w+)", cmd)[0]
                    check_vmtype(ovf, expected_vmtype)
            if '-oa' in cmd and '--no-copy' not in cmd:
                expected_mode = re.findall(r"-oa\s(\w+)", cmd)[0]
                check_image(output, "allocation", expected_mode)
            if '-of' in cmd and '--no-copy' not in cmd:
                expected_format = re.findall(r"-of\s(\w+)", cmd)[0]
                check_image(output, "format", expected_format)
            if '-on' in cmd:
                expected_name = re.findall(r"-on\s(\w+)", cmd)[0]
                check_new_name(output, expected_name)
            if '--no-copy' in cmd:
                check_nocopy(output)
            if '-oc' in cmd:
                expected_uri = re.findall(r"-oc\s(\S+)", cmd)[0]
                check_connection(output, expected_uri)
            global vm_imported
            if output_mode == "rhev":
                if not utils_v2v.import_vm_to_ovirt(params, address_cache):
                    raise error.TestFail("Import VM failed")
                else:
                    vm_imported = True
            if output_mode == "libvirt":
                if "qemu:///session" not in v2v_options:
                    virsh.start(vm_name, debug=True, ignore_status=False)

    try:
        # Build input options
        input_option = ""
        if input_mode is None:
            pass
        elif input_mode == "libvirt":
            uri_obj = utils_v2v.Uri(hypervisor)
            ic_uri = uri_obj.get_uri(remote_host, vpx_dc, esx_ip)
            input_option = "-i %s -ic %s %s" % (input_mode, ic_uri, vm_name)
            # Build network/bridge option
            v2v_options += " -b %s -n %s" % (params.get("output_bridge"),
                                             params.get("output_network"))
        elif input_mode == "disk":
            input_option += "-i %s %s" % (input_mode, disk_img)
        elif input_mode in ['libvirtxml', 'ova']:
            raise error.TestNAError("Unsupported input mode: %s" % input_mode)
        else:
            raise error.TestError("Unknown input mode %s" % input_mode)

        # Build output options
        output_option = ""
        if output_mode:
            output_option = "-o %s -os %s" % (output_mode, output_storage)

        # Build vdsm related options
        vdsm_domain_dir, vdsm_image_dir, vdsm_vm_dir = ("", "", "")
        if output_mode in ['vdsm', 'rhev']:
            if not os.path.isdir(mnt_point):
                os.mkdir(mnt_point)
            if not utils_misc.mount(nfs_storage, mnt_point, "nfs"):
                raise error.TestError("Mount NFS Failed")
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
                os.mkdir(vdsm_domain_dir)
                os.mkdir(vdsm_image_dir)
                os.mkdir(vdsm_vm_dir)

        # Output more messages
        v2v_options += " -v -x"

        # Prepare for libvirt unprivileged user session connection
        if "qemu:///session" in v2v_options:
            try:
                pwd.getpwnam(v2v_user)
            except KeyError:
                # create new user
                utils.system("useradd %s" % v2v_user, ignore_status=True)
                new_v2v_user = True
            user_info = pwd.getpwnam(v2v_user)
            logging.info("Convert to qemu:///session by user '%s'", v2v_user)
            if input_mode == "disk":
                # Change the image owner and group
                ori_owner = os.stat(disk_img).st_uid
                ori_group = os.stat(disk_img).st_uid
                os.chown(disk_img, user_info.pw_uid, user_info.pw_gid)
                restore_image_owner = True
            else:
                raise error.TestNAError("Only support convert local disk")

        # Setup ssh-agent access to xen hypervisor
        if hypervisor == 'xen':
            os.environ['LIBGUESTFS_BACKEND'] = 'direct'
            user = params.get("xen_host_user", "root")
            passwd = params.get("xen_host_passwd", "redhat")
            logging.info("set up ssh-agent access ")
            ssh_key.setup_ssh_key(remote_host, user=user,
                                  port=22, password=passwd)
            utils_misc.add_identities_into_ssh_agent()
            # If the input format is not define, we need to either define
            # the original format in the source metadata(xml) or use '-of'
            # to force the output format, see BZ#1141723 for detail.
            if '-of' not in v2v_options:
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

        # Running virt-v2v command
        cmd = "%s %s %s %s" % (utils_v2v.V2V_EXEC, input_option,
                               output_option, v2v_options)
        if v2v_user:
            cmd = su_cmd + "'%s'" % cmd
        cmd_result = utils.run(cmd, timeout=v2v_timeout, verbose=True,
                               ignore_status=True)
        if new_vm_name:
            vm_name = new_vm_name
            params['main_vm'] = new_vm_name
        check_result(cmd, cmd_result, status_error)
    finally:
        if hypervisor == "xen":
            utils.run("ssh-agent -k")
        if hypervisor == "esx":
            utils.run("rm -rf %s" % vpx_passwd_file)
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
            if "qemu:///session" in v2v_options:
                cmd = su_cmd + "'virsh undefine %s'" % vm_name
                utils.system(cmd)
            else:
                virsh.remove_domain(vm_name)
            cleanup_pool()
        if output_mode == "rhev" and vm_imported:
            vm_c = utils_v2v.VMCheck(test, params, env)
            vm_c.cleanup()
        if new_v2v_user:
            utils.system("userdel -f %s" % v2v_user)
        if restore_image_owner:
            os.chown(disk_img, ori_owner, ori_group)
