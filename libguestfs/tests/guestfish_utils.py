from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
from virttest import qemu_storage
import logging
import shutil
import os
import re
import commands
import time


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


def test_time(vm, params):
    """
    Test command time:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    time_result = gf.time("version")
    if time_result.exit_status != 0 or 'elapsed time' not in time_result.stdout:
        gf.close_session()
        logging.error(time_result)
        raise error.TestFail('test_time failed')
    gf.close_session()


def test_launch(vm, params):
    """
    Test command launch:
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
        raise error.TestFail("test_launch failed")

    gf.close_session()


def test_man(vm, params):
    """
    Test command man:
    """
    gf = utils_test.libguestfs.GuestfishTools(params)
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    tmp_file = "test_man_file"
    os.system("guestfish -- man > %s" % tmp_file)
    temp, result = commands.getstatusoutput("grep 'libguestfs' %s" % tmp_file)
    if 'libguestfs' not in result:
        os.system("rm -f %s" % tmp_file)
        gf.close_session()
        raise error.TestFail("test_man failed")

    os.system("rm -f %s" % tmp_file)
    gf.close_session()


def test_quit(vm, params):
    """
    Test command sleep:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.set_attach_method("appliance")
    gf.run()
    gf_result = gf.get_pid()
    temp, pids = commands.getstatusoutput("pgrep qemu-kvm")
    if gf_result.stdout.split()[0] not in pids:
        gf.close_session()
        raise error.TestFail('test_quit failed, can not get_pid')
    gf.close_session()
    utils.CmdResult(quit)
    temp, new_pids = commands.getstatusoutput("pgrep qemu-kvm")
    if gf_result.stdout.split()[0] in new_pids:
        gf.close_session()
        raise error.TestFail('test_quit failed, pid killed failed')
    gf.close_session()


def test_sleep(vm, params):
    """
    Test command sleep:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()
    start_time = time.time()
    gf.sleep("3")
    end_time = time.time()
    gap_time = end_time - start_time

    if gap_time < 3.0:
        gf.close_session()
        logging.error(gap_time)
        raise error.TestFail("test_sleep failed")


def test_config(vm, params):
    """
    Test command config:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    gf.inner_cmd("trace 1")
    cf_result = gf.config("-name", "libguestfs-appliance")
    if cf_result.exit_status != 0 or 'libguestfs-appliance' not in cf_result.stdout:
        gf.close_session()
        logging.error(cf_result)
        raise error.TestFail('test_config failed')
    cf_result = gf.config("-dieqemudie", "\"\"")
    if cf_result.exit_status != 0 or '-dieqemudie' not in cf_result.stdout:
        gf.close_session()
        logging.error(cf_result)
        raise error.TestFail('test_config failed')
    gf.close_session()


def test_debug(vm, params):
    """
    Test command debug:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()
    ls_result = gf.debug("ls", "/dev")
    ll_result = gf.debug("ll", "/")
    env_result = gf.debug("env", "''")
    fds_result = gf.debug("fds", "''")
    test_result = gf.debug("not_a_command", "''")
    if (ls_result.exit_status != 0) or (ll_result.exit_status != 0) or (env_result.exit_status
       != 0) or (fds_result.exit_status != 0) or (test_result.exit_status == 0):
        gf.close_session()
        raise error.TestFail('test_debug failed')
    gf.close_session()


def test_kill_subprocess(vm, params):
    """
    Test command kill_subprocess:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()
    bf_result = gf.echo_daemon("1")
    gf.kill_subprocess()
    af_result = gf.echo_daemon("1")
    if bf_result.exit_status != 0 or af_result.exit_status == 0:
        gf.close_session()
        logging.error(bf_result)
        logging.error(af_result)
        raise error.TestFail('test_kill_subprocess failed')
    gf.close_session()


def test_shutdown(vm, params):
    """
    Test command shutdown:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()
    bf_result = gf.echo_daemon("1")
    gf.shutdown()
    af_result = gf.echo_daemon("1")
    if bf_result.exit_status != 0 or af_result.exit_status == 0:
        gf.close_session()
        logging.error(bf_result)
        logging.error(af_result)
        raise error.TestFail('test_shutdown failed')
    gf.close_session()


def test_ntfs_3g_probe(vm, params):
    """
    Test command ntfs_3g_probe:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")

    test_format = params.get("image_format")
    test_size = "100M"
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_ntfs_3g_probe.img'
    test_pv = '/dev/sda'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    test_mountpoint = test_pv + "1"
    gf.part_disk(test_pv, "mbr")
    gf.mkfs('ntfs', test_mountpoint)
    gf_result = gf.ntfs_3g_probe('1', test_mountpoint)
    v1 = gf_result.stdout.split()[0]
    gf.zero(test_mountpoint)
    gf_result = gf.ntfs_3g_probe('0', test_mountpoint)
    v2 = gf_result.stdout.split()[0]
    gf.mkfs('ext2', test_mountpoint)
    gf_result = gf.ntfs_3g_probe('0', test_mountpoint)
    v3 = gf_result.stdout.split()[0]

    if v1 != '0' or v2 == '0' or v3 == '0':
        gf.close_session()
        os.system('rm -f %s' % test_img)
        raise error.TestFail('test_ntfs_3g_probe failed')
    gf.close_session()
    os.system('rm -f %s' % test_img)


def test_event(vm, params):
    """
    Test command event:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    gf.add_drive("/dev/null")

    name = "evt0"
    eventset = "launch_done"
    script = "\"echo $EVENT $@\""
    expected_script = "echo $EVENT $@"
    gf.event(name, eventset, script)
    le_result = gf.list_events()
    if eventset not in le_result.stdout or expected_script not in le_result.stdout:
        gf.close_session()
        logging.error(le_result)
        raise error.TestFail('test_event failed, list_event result not match')
    run_result = gf.run()
    if eventset not in run_result.stdout:
        gf.close_session()
        logging.error(run_result)
        raise error.TestFail('test_event failed, event do not work')

    name = "evt1"
    eventset = "close"
    script = "\"echo guestfish closed\""
    expected_script = "echo guestfish closed"
    gf.event(name, eventset, script)
    le_result = gf.list_events()
    if eventset not in le_result.stdout or expected_script not in le_result.stdout:
        gf.close_session()
        logging.error(le_result)
        raise error.TestFail('test_event failed, list_event result not match')

    # include the upstream regression test for events
    gf.reopen()
    gf.event("ev1", "*", "\"echo $EVENT $@\"")
    gf.event("ev1", "*", "\"echo $EVENT $@\"")
    gf.event("ev2", "*", "\"echo $EVENT $@\"")

    le_result = gf.list_events()
    expected_strs = ["\"ev1\" (1): *: echo $EVENT $@",
                     "\"ev1\" (2): *: echo $EVENT $@",
                     "\"ev2\" (3): *: echo $EVENT $@"]
    for expected_str in expected_strs:
        if expected_str not in le_result.stdout:
            gf.close_session()
            logging.error(le_result)
            raise error.TestFail('test_event failed, list_event result not match after reopen')

    gf.delete_event("ev1")
    le_result = gf.list_events()
    expected_str = expected_strs[2]
    if expected_str not in le_result.stdout:
        gf.close_session()
        logging.error(le_result)
        raise error.TestFail('test_event failed, list_event result not match after delete_event')
    gf.reopen()
    le_result = gf.list_events()
    for origin_str in expected_strs:
        if origin_str in le_result.stdout:
            gf.close_session()
            logging.error(le_result)
            raise error.TestFail('test_event failed, list_event result not match after reopen')

    gf.event("ev1", "close,subprocess_quit", "\"echo $EVENT $@\"")
    gf.event("ev2", "close,subprocess_quit", "\"echo $EVENT $@\"")
    gf.event("ev3", "launch", "\"echo $EVENT $@\"")

    le_result = gf.list_events()
    expected_strs = ["\"ev1\" (1): close,subprocess_quit: echo $EVENT $@",
                     "\"ev2\" (2): close,subprocess_quit: echo $EVENT $@",
                     "\"ev3\" (3): launch_done: echo $EVENT $@"]

    for expected_str in expected_strs:
        if expected_str not in le_result.stdout:
            gf.close_session()
            logging.error(le_result)
            raise error.TestFail('test_event failed, list_event result not match')
    # delete_event("ev4")
    gf.delete_event("ev4")
    le_result = gf.list_events()
    expected_strs = ["\"ev1\" (1): close,subprocess_quit: echo $EVENT $@",
                     "\"ev2\" (2): close,subprocess_quit: echo $EVENT $@",
                     "\"ev3\" (3): launch_done: echo $EVENT $@"]

    for expected_str in expected_strs:
        if expected_str not in le_result.stdout:
            gf.close_session()
            logging.error(le_result)
            raise error.TestFail('test_event failed, list_event result not match')

    # delete_event("ev1")
    gf.delete_event("ev1")
    le_result = gf.list_events()
    if expected_str[1] not in le_result.stdout or expected_str[2] not in le_result.stdout:
        gf.close_session()
        logging.error(le_result)
        raise error.TestFail('test_event failed, list_event result not match after delete_event')

    # delete_event("ev3")
    gf.delete_event("ev3")
    le_result = gf.list_events()
    if expected_str[1] not in le_result.stdout:
        gf.close_session()
        logging.error(le_result)
        raise error.TestFail('test_event failed, list_event result not match after delete_event')
    gf.close_session()


def test_set_get_append(vm, params):
    """
    Test command set_append and get_append:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)

    append = "LANG=en_US.UTF-8"
    gf.set_append(append)
    gf.run()
    gf_result = gf.get_append()
    if append != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_append failed')

    gf.close_session()


def test_set_get_smp(vm, params):
    """
    Test command set_smp and get_smp:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    gf.add_drive("/dev/null")

    temp, smp = commands.getstatusoutput("cat /proc/cpuinfo | grep -E '^processor' | wc -l")
    gf.set_smp(smp)
    gf.run()
    gf_result = gf.get_smp()
    if smp != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_smp failed')

    gf.close_session()


def test_set_get_pgroup(vm, params):
    """
    Test command set_pgroup and get_pgroup:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    gf.add_drive("/dev/null")

    pgroup = "1"
    expected = "true"
    gf.set_pgroup(pgroup)
    gf.run()
    gf_result = gf.get_pgroup()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_pgroup failed')

    gf.kill_subprocess()
    pgroup = "0"
    expected = "false"
    gf.set_pgroup(pgroup)
    gf.run()
    gf_result = gf.get_pgroup()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_pgroup failed')

    gf.close_session()


def test_set_get_attach_method(vm, params):
    """
    Test command set_attach_method and get_attach_method:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    gf.add_drive("/dev/null")

    method = "appliance"
    gf.set_attach_method(method)
    gf.run()
    gf_result = gf.get_attach_method()
    if method != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_attach_method failed')

    gf.close_session()


def test_set_get_autosync(vm, params):
    """
    Test command set_autosync and get_autosync:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    gf.set_autosync("0")
    expected = "false"
    gf_result = gf.get_autosync()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_autosync failed')

    gf.set_autosync("1")
    expected = "true"
    gf_result = gf.get_autosync()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_autosync failed')

    gf.close_session()


def test_set_get_direct(vm, params):
    """
    Test command set_direct and get_direct:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    gf.set_direct("0")
    expected = "false"
    gf_result = gf.get_direct()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_direct failed')

    gf.set_direct("1")
    expected = "true"
    gf_result = gf.get_direct()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_direct failed')

    gf.close_session()


def test_set_get_memsize(vm, params):
    """
    Test command set_memsize and get_memsize:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    memsize = "700"
    gf.set_memsize(memsize)
    gf_result = gf.get_memsize()
    if memsize != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_memsize failed')

    gf.close_session()


def test_set_get_path(vm, params):
    """
    Test command set_path and get_path:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    path = "/usr/lib/guestfs"
    gf.set_path(path)
    gf_result = gf.get_path()
    if path != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_path failed')

    gf.close_session()


def test_set_get_qemu(vm, params):
    """
    Test command set_qemu and get_qemu:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    qemu = "/usr/libexec/qemu-kvm"
    gf.set_path(qemu)
    gf_result = gf.get_qemu()
    if qemu != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_qemu failed')

    gf.close_session()


def test_set_get_recovery_proc(vm, params):
    """
    Test command set_recovery_proc and get_recovery_proc:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    recoveryproc = 0
    expected = "false"
    gf.set_recovery_proc(recoveryproc)
    gf_result = gf.get_recovery_proc()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_recovery_proc failed')

    recoveryproc = 1
    expected = "true"
    gf.set_recovery_proc(recoveryproc)
    gf_result = gf.get_recovery_proc()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_recovery_proc failed')

    gf.close_session()


def test_set_get_trace(vm, params):
    """
    Test command set_trace and get_trace:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    trace = 0
    expected = "false"
    gf.set_trace(trace)
    gf_result = gf.get_trace()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_trace failed')

    trace = 1
    expected = "true"
    gf.set_trace(trace)
    gf_result = gf.get_trace()
    if expected not in gf_result.stdout:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_trace failed')

    gf.close_session()


def test_set_get_verbose(vm, params):
    """
    Test command set_verbose and get_verbose:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    verbose = "0"
    expected = "false"
    gf.set_verbose(verbose)
    gf_result = gf.get_verbose()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_verbose failed')

    verbose = "1"
    expected = "true"
    gf.set_verbose(verbose)
    gf_result = gf.get_verbose()
    if expected not in gf_result.stdout:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_verbose failed')

    gf.close_session()


def test_get_pid(vm, params):
    """
    Test command get_pid:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)

    gf.set_attach_method("appliance")
    gf.run()
    gf_result = gf.get_pid()
    temp, pids = commands.getstatusoutput("pgrep qemu-kvm")
    if gf_result.stdout.split()[0] not in pids:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_get_pid failed')

    gf.close_session()


def test_set_get_network(vm, params):
    """
    Test command set_network and get_network:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    network = 0
    expected = "false"
    gf.set_network(network)
    gf_result = gf.get_network()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_network failed')

    network = 1
    expected = "true"
    gf.set_network(network)
    gf_result = gf.get_network()
    if expected != gf_result.stdout.split()[0]:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail('test_set_get_network failed')

    gf.close_session()


def test_setenv(vm, params):
    """
    Test command setenv:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    var1 = "VAR1"
    value1 = "value1"
    gf.setenv(var1, value1)
    gf_result = gf.inner_cmd("!echo $%s" % var1)
    v1 = gf_result.stdout.split()[0]

    gf.add_drive("/dev/null")
    gf.run()
    var2 = "VAR2"
    value2 = "value2"
    gf.setenv(var2, value2)
    gf_result = gf.inner_cmd("!echo $%s" % var2)
    v2 = gf_result.stdout.split()[0]

    gf.reopen()
    var3 = "VAR3"
    value3 = "value3"
    gf.setenv(var3, value3)
    gf_result = gf.inner_cmd("!echo $%s" % var3)
    v3 = gf_result.stdout.split()[0]

    if value1 != v1 or value2 != v2 or value3 != v3:
        gf.close_session()
        raise error.TestFail('test_setenv failed')

    gf.close_session()


def test_unsetenv(vm, params):
    """
    Test command unsetenv:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)

    var1 = "VAR1"
    value1 = "value1"
    gf.setenv(var1, value1)
    gf.unsetenv(var1)
    gf_result = gf.inner_cmd("!echo $%s" % var1)
    v1 = gf_result.stdout

    var2 = "VAR2"
    value2 = "value2"
    gf.setenv(var2, value2)
    gf.add_drive("/dev/null")
    gf.run()
    gf.unsetenv(var2)
    gf_result = gf.inner_cmd("!echo $%s" % var2)
    v2 = gf_result.stdout

    var3 = "VAR3"
    value3 = "value3"
    gf.setenv(var3, value3)
    gf.reopen()
    gf.unsetenv(var3)
    gf_result = gf.inner_cmd("!echo $%s" % var3)
    v3 = gf_result.stdout

    if (value1 in v1) or (value2 in v2) or (value3 in v3):
        gf.close_session()
        raise error.TestFail('test_setenv failed')

    gf.close_session()


def test_is_config(vm, params):
    """
    Test command is_config:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")

    gf.add_drive(image_path)
    gf_result = gf.is_config()
    v1 = gf_result.stdout.split()[0]
    gf.run()
    gf_result = gf.is_config()
    v2 = gf_result.stdout.split()[0]

    if v1 != "true" or v2 != "false":
        gf.close_session()
        raise error.TestFail('test_is_config failed')

    gf.close_session()


def test_lcd(vm, params):
    """
    Test command lcd:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    os.system("rm -f /tmp/test_lcd.img")

    gf.lcd("/tmp")
    gf_result = gf.inner_cmd("!pwd")
    wd = gf_result.stdout.split()[0]
    gf.sparse("test_lcd.img", "10M")

    if (wd != '/tmp') or (not os.path.exists('/tmp/test_lcd.img')):
        gf.close_session()
        os.system("rm -f /tmp/test_lcd.img")
        raise error.TestFail('test_lcd failed')

    gf.close_session()
    os.system("rm -f /tmp/test_lcd.img")


def test_supported(vm, params):
    """
    Test command supported:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()

    gf_result = gf.available_all_groups()
    groups = gf_result.stdout.strip().split()
    gf_result = gf.supported()
    supported = gf_result.stdout
    for group in groups:
        if group not in supported:
            gf.close_session()
            logging.error(groups)
            logging.error(supported)
            raise error.TestFail('test_supported failed')

    gf.close_session()


def test_extlinux(vm, params):
    """
    Test command extlinux:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    test_format = params.get("image_format")
    test_size = "100M"
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_extlinux.img'
    test_pv = '/dev/sda'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    test_mountpoint = test_pv + "1"
    gf.part_disk(test_pv, "mbr")
    gf.mkfs('ext2', test_mountpoint)
    gf.mount(test_mountpoint, '/')
    gf.extlinux('/')
    gf.download("/ldlinux.sys", "ldlinux.sys.ext2")
    gf.umount('/')

    gf.mkfs('ext3', test_mountpoint)
    gf.mount(test_mountpoint, '/')
    gf.extlinux('/')
    gf.download("/ldlinux.sys", "ldlinux.sys.ext3")
    gf.umount('/')

    gf.mkfs('ext4', test_mountpoint)
    gf.mount(test_mountpoint, '/')
    gf.extlinux('/')
    gf.download("/ldlinux.sys", "ldlinux.sys.ext4")
    gf.umount('/')

    temp, v1 = commands.getstatusoutput("file ldlinux.sys.ext2 | grep data")
    temp, v2 = commands.getstatusoutput("file ldlinux.sys.ext3 | grep data")
    temp, v3 = commands.getstatusoutput("file ldlinux.sys.ext4 | grep data")
    if ('ext' not in v1) or ('ext' not in v2) or ('ext' not in v3):
        gf.close_session()
        os.system("rm -f %s" % test_img)
        os.system("rm -f ldlinux.sys.ext2")
        os.system("rm -f ldlinux.sys.ext3")
        os.system("rm -f ldlinux.sys.ext4")
        raise error.TestFail('test_extlinux failed')

    gf.close_session()
    os.system("rm -f %s" % test_img)
    os.system("rm -f ldlinux.sys.ext2")
    os.system("rm -f ldlinux.sys.ext3")
    os.system("rm -f ldlinux.sys.ext4")


def test_syslinux(vm, params):
    """
    Test command syslinux:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    test_format = params.get("image_format")
    test_size = "100M"
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_extlinux.img'
    test_pv = '/dev/sda'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    test_mountpoint = test_pv + "1"
    gf.part_disk(test_pv, "mbr")
    gf.mkfs('vfat', test_mountpoint)
    gf.syslinux(test_mountpoint)
    gf.mount(test_mountpoint, '/')
    gf.download("/ldlinux.sys", "ldlinux.sys.vfat")
    gf.umount('/')

    temp, v = commands.getstatusoutput("file ldlinux.sys.vfat | grep data")
    if 'vfat' not in v:
        gf.close_session()
        os.system("rm -f %s" % test_img)
        os.system("rm -f ldlinux.sys.vfat")
        raise error.TestFail('test_syslinux failed')

    gf.close_session()
    os.system("rm -f %s" % test_img)
    os.system("rm -f ldlinux.sys.vfat")


def test_feature_available(vm, params):
    """
    Test command feature_available:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()
    gf_result = gf.available_all_groups()
    groups = gf_result.stdout.strip().split()
    for group in groups:
        gf_result = gf.feature_available(group)
        ret = gf_result.stdout.split()[0]
        if (ret == 'true') and (group == 'grub' or group == 'ldm'
           or group == 'zerofree'):
            gf.close_session()
            raise error.TestFail('test_feature_available failed: %s supported' % group)
        elif (ret == 'false') and (group != 'grub' and group != 'ldm' and group != 'zerofree'):
            gf.close_session()
            raise error.TestFail('test_feature_available failed: %s not supported' % group)

    gf.close_session()


def test_set_get_program(vm, params):
    """
    Test command set_program and get_program:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    gf.add_drive(image_path)
    gf.run()

    gf_result = gf.get_program()
    if gf_result.stdout.split()[0] != 'guestfish':
        gf.close_session()
        logging.debug(gf_result)
        raise error.TestFail("test_set_get_program failed")

    gf.set_program('testalais')
    gf_result = gf.get_program()
    if gf_result.stdout.split()[0] != 'testalais':
        gf.close_session()
        logging.debug(gf_result)
        raise error.TestFail("test_set_get_program failed")


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
