from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
from virttest import qemu_storage
import logging
import shutil
import os
import re
import commands


def prepare_image(params):
    """
    (1) Create a image
    (2) Create file system on the image
    """
    params["image_path"] = utils_test.libguestfs.preprocess_image(params)

    if not params.get("image_path"):
        raise error.TestFail("Image could not be created for some reason.")

    gf = utils_test.libguestfs.GuestfishTools(params)
    status, output = gf.create_fs()
    if status is False:
        gf.close_session()
        raise error.TestFail(output)
    gf.close_session()


def test_add_domain(vm, params):
    """
    Test command add_domain:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    pv_name = params.get("pv_name")
    test_domain_name = "libguestfs_test_domain"
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_xml = test_dir + '/test_domain.xml'

    xml_content = "<domain type='kvm'>\n\
      <memory>500000</memory>\n\
      <name>%s</name>\n\
      <vcpu>1</vcpu>\n\
      <os>\n\
        <type>hvm</type>\n\
        <boot dev='hd'/>\n\
      </os>\n\
      <devices>\n\
        <disk type='file' device='disk'>\n\
          <source file='%s'/>\n\
          <target dev='hda' bus='ide'/>\n\
        </disk>\n\
      </devices>\n\
    </domain>\n\
    " % (test_domain_name, image_path)
    f = open(test_xml, "w")
    f.write(xml_content)
    f.close()

    os.system("virsh define %s > /dev/null" % test_xml)
    gf.add_domain(test_domain_name)
    gf.run()
    gf_result = gf.list_devices()

    if '/dev/sd' not in gf_result.stdout:
        gf.close_session()
        logging.error(gf_result)
        os.system('virsh undefine %s > /dev/null' % test_xml)
        os.system('rm -f %s' % test_xml)
        raise error.TestFail("test_add_domain failed")
    gf.close_session()
    os.system('virsh undefine %s > /dev/null' % test_xml)
    os.system('rm -f %s' % test_xml)


def test_add_drive(vm, params):
    """
    Test command add_drive:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    pv_name = params.get("pv_name")
    alloc_file = '~/../../home/test_add_drive.img'
    gf.add_drive(image_path)
    gf.alloc(alloc_file, '10M')
    gf.run()
    gf_result = gf.list_devices()

    if ('/dev/sda' not in gf_result.stdout) or (
       '/dev/sdb' not in gf_result.stdout):
        gf.close_session()
        logging.error(gf_result)
        os.system('rm -f %s' % alloc_file)
        raise error.TestFail("test_add_drive failed")
    gf.close_session()
    os.system('rm -f %s' % alloc_file)


def test_add_drive_opts(vm, params):
    """
    Test command add_drive_opts:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    pv_name = params.get("pv_name")
    image_format = params.get("image_format")
    gf.add_drive_opts(image_path, None, image_format)
    gf.run()
    gf_result = gf.list_devices()

    if '/dev/sda' not in gf_result.stdout:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail("test_add_drive_opts failed")

    # add this for disk hotplug feature
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    hotplug_image = img_dir + '/test_add_hotplug.img'
    label = "hplabel"
    os.system('dd if=/dev/zero of=%s bs=1M count=100 > /dev/null' % hotplug_image)
    ad_result = gf.add_drive_opts(hotplug_image, None, None, None, None, label)
    ld_result = gf.list_devices()
    ldl_result = gf.list_disk_labels()

    if ('/dev/sda' not in ld_result.stdout) or ('/dev/sdc' not in
       ld_result.stdout) or (label not in ldl_result.stdout):
        gf.close_session()
        logging.error(ad_result)
        logging.error(ld_result)
        logging.error(ldl_result)
        os.system('rm -f %s' % hotplug_image)
        raise error.TestFail("test_add_drive_opts failed")
    gf.close_session()
    os.system('rm -f %s' % hotplug_image)


def test_add_drive_ro(vm, params):
    """
    Test command add_drive_ro:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    pv_name = params.get("pv_name")
    gf.add_drive_ro(image_path)
    gf.run()
    gf_result = gf.list_devices()

    if '/dev/sda' not in gf_result.stdout:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail("test_add_drive_ro failed")
    gf.close_session()


def test_add_drive_ro_with_if(vm, params):
    """
    Test command add_drive_ro_with_if:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    old_env = gf.get_backend()
    gf.set_backend('direct')
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    interfaces = ['ide', 'virtio']
    test_image_ide = img_dir + '/test-ide-ro.img'
    test_image_virtio = img_dir + '/test-virtio-ro.img'
    for iface in interfaces:
        if iface == 'ide':
            test_image = test_image_ide
        else:
            test_image = test_image_virtio
        os.system('qemu-img create %s 100M > /dev/null' % test_image)
        gf.set_backend
        add_result = gf.add_drive_ro_with_if(test_image, iface)
        run_result = gf.run()
        ld_result = gf.list_devices()
        if '/dev/sda' not in ld_result.stdout:
            gf.set_backend(old_env)
            gf.close_session()
            os.system('rm -f %s' % test_image)
            logging.debug(add_result)
            logging.debug(run_result)
            logging.debug(ld_result)
            raise error.TestFail("test_add_drive_ro_with_if failed")
    gf.set_backend(old_env)
    gf.close_session()
    os.system('rm -f %s' % test_image_ide)
    os.system('rm -f %s' % test_image_virtio)


def test_add_drive_with_if(vm, params):
    """
    Test command add_drive_with_if:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    old_env = gf.get_backend()
    gf.set_backend('direct')
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    interfaces = ['ide', 'virtio']
    test_image_ide = img_dir + '/test-ide.img'
    test_image_virtio = img_dir + '/test-virtio.img'
    for iface in interfaces:
        if iface == 'ide':
            test_image = test_image_ide
        else:
            test_image = test_image_virtio
        os.system('qemu-img create %s 100M > /dev/null' % test_image)
        gf.set_backend
        add_result = gf.add_drive_with_if(test_image, iface)
        run_result = gf.run()
        ld_result = gf.list_devices()
        if '/dev/sda' not in ld_result.stdout:
            gf.set_backend(old_env)
            gf.close_session()
            os.system('rm -f %s' % test_image)
            logging.debug(add_result)
            logging.debug(run_result)
            logging.debug(ld_result)
            raise error.TestFail("test_add_drive_with_if failed")
    gf.set_backend(old_env)
    gf.close_session()
    os.system('rm -f %s' % test_image_ide)
    os.system('rm -f %s' % test_image_virtio)


def test_available(vm, params):
    """
    Test command available:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()

    groups = 'inotify linuxfsuuid linuxmodules \
    linuxxattrs lvm2 mknod ntfs3g ntfsprogs realpath scrub selinux xz'
    gf_result = gf.available(groups)
    if gf_result.exit_status != 0 or 'error' in gf_result.stdout:
        gf.close_session()
        raise error.TestFail('test_available failed')
    gf.close_session()


def test_available_all_groups(vm, params):
    """
    Test command available_all_groups:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()

    gf_result = gf.available_all_groups()
    if gf_result.exit_status != 0 or 'error' in gf_result.stdout:
        gf.close_session()
        raise error.TestFail('test_available_all_groups failed')
    gf.close_session()


def test_help(vm, params):
    """
    Test command help:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    h_result = gf.help()
    hr_result = gf.help('run')
    ha_result = gf.help('add')
    ht_result = gf.help('this-is-not-a-command')
    if (h_result.exit_status != 0) or (hr_result.exit_status != 0) or (
       ha_result.exit_status != 0) or ('command not known' not in ht_result.stdout):
        gf.close_session()
        raise error.TestFail('test_help failed')
    gf.close_session()


def test_echo(vm, params):
    """
    Test command echo:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    gf_result = gf.echo('1')
    if gf_result.stdout.split('\n')[0] != "1":
        gf.close_session()
        raise error.TestFail('test_echo failed')

    gf_result = gf.echo('    1    2     3')
    if gf_result.stdout.split('\n')[0] != '1 2 3':
        gf.close_session()
        raise error.TestFail('test_echo failed')

    gf_result = gf.echo("\"   1, 2,   3\"")
    if gf_result.stdout.split('\n')[0] != "   1, 2,   3":
        gf.close_session()
        raise error.TestFail('test_echo failed')

    gf.close_session()


def test_echo_daemon(vm, params):
    """
    Test command echo_daemon:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()

    gf_result = gf.echo_daemon("12345678")
    if gf_result.stdout.split('\n')[0] != "12345678":
        gf.close_session()
        raise error.TestFail('test_echo_daemon failed')

    gf_result = gf.echo_daemon("'regress bug 503134'")
    if gf_result.stdout.split('\n')[0] != 'regress bug 503134':
        gf.close_session()
        raise error.TestFail('test_echo_daemon failed')

    gf_result = gf.echo_daemon("\"hello hello   'baby you called'   'I   can\\\\\\'t' hear a thing\"")
    if gf_result.stdout.split('\n')[0] != "hello hello baby you called I   can't hear a thing":
        gf.close_session()
        raise error.TestFail('test_echo_daemon failed')


def test_dmesg(vm, params):
    """
    Test command dmesg:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()

    gf_result = gf.dmesg()
    if 'kernel' not in gf_result.stdout:
        gf.close_session()
        raise error.TestFail('test_echo_dmesg failed')
    gf.close_session()


def test_version(vm, params):
    """
    Test command version:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    gf_result = gf.version()
    expected_str_list = ['major:', 'minor:', 'release:', 'extra:']
    for expected_str in expected_str_list:
        if expected_str not in gf_result.stdout:
            gf.close_session()
            raise error.TestFail('test_version failed')
    gf.close_session()


def test_alloc(vm, params):
    """
    Test command alloc:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img_normal = img_dir + '/alloc_test_normal.img'
    test_img_error = img_dir + '/alloc_test_error.img'
    os.system('rm -f %s' % test_img_normal)
    os.system('rm -f %s' % test_img_error)
    gf.alloc(test_img_normal, '100M')
    if not os.path.exists(test_img_normal):
        gf.close_session()
        os.system('rm -f %s' % test_img_normal)
        raise error.TestFail("test_alloc failed, file not allocated correctly")

    temp, avil_size = commands.getstatusoutput("df -P -B 1G %s | awk 'NR==2{print $4}'" % img_dir)
    gf_result = gf.alloc(test_img_error, str(int(avil_size) + 10) + 'G')
    if gf_result.exit_status == 0 or os.path.exists(test_img_error):
        gf.close_session()
        logging.error(gf_result)
        os.system('rm -f %s' % test_img_error)
        raise error.TestFail("test_alloc failed, alloc doesn't fail without enough space")
    gf.close_session()
    os.system('rm -f %s' % test_img_normal)
    os.system('rm -f %s' % test_img_error)


def test_sparse(vm, params):
    """
    Test command sparse:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = img_dir + '/sparse_test.img'
    os.system('rm -f %s' % test_img)
    gf_result = gf.sparse(test_img, '1G')
    if not os.path.exists(test_img):
        gf.close_session()
        os.system('rm -f %s' % test_img)
        logging.error(gf_result)
        raise error.TestFail("test_alloc failed, file not allocated correctly")
    gf.close_session()
    os.system('rm -f %s' % test_img)


def test_modprobe(vm, params):
    """
    Test command modprobe:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()
    gf_result = gf.modprobe('fat')
    if gf_result.exit_status != 0:
        gf.close_session()
        raise error.TestFail("test_modprobe failed")
    gf.close_session()


def test_ping_daemon(vm, params):
    """
    Test command ping_daemon:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()
    gf_result = gf.ping_daemon()
    if gf_result.exit_status != 0:
        gf.close_session()
        raise error.TestFail("test_ping_daemon failed")
    gf.close_session()


def test_reopen(vm, params):
    """
    Test command reopen:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    re_result = gf.reopen()
    if re_result.exit_status != 0:
        gf.close_session()
        raise error.TestFail('test_version failed')
    gf_result = gf.version()
    expected_str_list = ['major:', 'minor:', 'release:', 'extra:']
    for expected_str in expected_str_list:
        if expected_str not in gf_result.stdout:
            gf.close_session()
            raise error.TestFail('test_version failed')
    gf.close_session()


def run(test, params, env):
    """
    Test of built-in fs_attr_ops related commands in guestfish.

    1) Get parameters for test
    2) Set options for commands
    3) Run key commands:
       a.add disk or domain with readonly or not
       b.launch
       c.mount root device
    4) Write a file to help result checking
    5) Check result
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if vm.is_alive():
        vm.destroy()

    operation = params.get("guestfish_function")
    testcase = globals()["test_%s" % operation]
    partition_types = params.get("partition_types")
    fs_type = params.get("fs_type")
    image_formats = params.get("image_formats")
    image_name = params.get("image_name", "gs_common")

    for image_format in re.findall("\w+", image_formats):
        params["image_format"] = image_format
        for partition_type in re.findall("\w+", partition_types):
            params["partition_type"] = partition_type
            image_dir = params.get("img_dir", data_dir.get_tmp_dir())
            image_path = image_dir + '/' + image_name + '.' + image_format
            image_name_with_fs_pt = image_name + '.' + fs_type + '.' + partition_type
            params['image_name'] = image_name_with_fs_pt
            image_path = image_dir + '/' + image_name_with_fs_pt + '.' + image_format

            if params["gf_create_img_force"] == "no" and os.path.exists(image_path):
                params["image_path"] = image_path
                # get mount_point
                if partition_type == 'lvm':
                    pv_name = params.get("pv_name", "/dev/sdb")
                    vg_name = params.get("vg_name", "vol_test")
                    lv_name = params.get("lv_name", "vol_file")
                    mount_point = "/dev/%s/%s" % (vg_name, lv_name)
                elif partition_type == "physical":
                    logging.info("create physical partition...")
                    pv_name = params.get("pv_name", "/dev/sdb")
                    mount_point = pv_name + "1"
                params["mount_point"] = mount_point

                logging.debug("Skip preparing image, " + image_path + " exists")
            else:
                prepare_image(params)
            testcase(vm, params)
