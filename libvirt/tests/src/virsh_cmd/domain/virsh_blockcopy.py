import logging
import os
import time
import re
import signal

from random import randint

import aexpect

from multiprocessing.pool import ThreadPool

from avocado.utils import process
from avocado.core import exceptions
from avocado.utils import path

from virttest import utils_libvirtd
from virttest import virsh
from virttest import qemu_storage
from virttest import data_dir
from virttest import utils_misc
from virttest import utils_secret
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import snapshot_xml
from virttest.utils_test import libvirt as utl

from virttest.libvirt_xml.devices.disk import Disk

from provider import libvirt_version


class JobTimeout(Exception):

    """
    Blockjob timeout in given time.
    """

    def __init__(self, timeout):
        Exception.__init__(self)
        self.timeout = timeout

    def __str__(self):
        return "Block job timeout in %s seconds" % self.timeout


def check_xml(vm_name, target, dest_path, blk_options):
    """
    Check the domain XML for blockcopy job

    :param vm_name: Domain name
    :param target: Domain disk target device
    :param dest_path: Path of the copy to create
    :param blk_options: Block job command options
    """
    re1 = 0
    re2 = 0
    # set expect result
    if blk_options.count("--finish"):
        # no <mirror> element and can't find dest_path in vm xml
        expect_re = 0
    elif blk_options.count("--pivot"):
        # no <mirror> element, but can find dest_path in vm xml
        expect_re = 1
    else:
        # find <mirror> element and dest_path in vm xml
        expect_re = 2
    logging.debug('expect_re is %d', expect_re)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug("Current vm xml is: %s" % vmxml.xmltreefile)

    blk_list = vm_xml.VMXML.get_disk_blk(vm_name)
    disk_list = vm_xml.VMXML.get_disk_source(vm_name)
    dev_index = 0
    try:
        try:
            dev_index = blk_list.index(target)
            disk_elem = disk_list[dev_index]
            if blk_options.count("--blockdev"):
                disk_src = disk_elem.find('source').get('dev')
            else:
                disk_src = disk_elem.find('source').get('file')
            logging.debug('disk_src is %s', disk_src)

            # check disk type
            disk_type = disk_elem.get('type')
            if "--pivot" in blk_options:
                if "--blockdev" in blk_options:
                    if disk_type != 'block':
                        logging.error("Disk type '%s' is not expected when "
                                      "--blockdev option specified",
                                      disk_type)
                        return False
                else:
                    if disk_type != 'file':
                        logging.error("Disk type '%s' is not expected",
                                      disk_type)
                        return False

            if disk_src == dest_path:
                logging.debug("Disk source change to %s", dest_path)
                re1 = 1
            else:
                logging.debug("Disk source didn't change to %s", dest_path)
            disk_mirror = disk_list[dev_index].find('mirror')
            if disk_mirror is not None:
                # Prior to 1.2.6 - <mirror> was a subelement of the <disk>
                # with the disk_mirror_src file found as an attribute. As of
                # 1.2.6 the output only XML changed to utilize a <source>
                # sublement where <mirror> now has a "type" attribute and
                # <source> format/output depends on the mirror type value.
                # Although for a brief period of time between commit id
                # '7c6fc394' and 'b50e1049', the file="/path/to/file"
                # attribute was removed from the <mirror> element. This
                # test failing here resulted in the file attribute being
                # restored for a type="file" only <mirror> to avoid a possible
                # regression for other users/parsers that expected the XML
                # to have the "file" attribute in a <mirror> element.
                if libvirt_version.version_compare(1, 2, 6):
                    mirror_type = disk_mirror.get('type')
                    disk_mirror_source = disk_mirror.find('source')
                    #
                    # Because we want to use the new and preferred syntax,
                    # let's do so for any libvirt 1.2.6 and beyond...
                    #
                    # Mirror type (currently) can be file, block, or network.
                    # For now the test only handles "file". In the future it
                    # could be augmented to handle other types.
                    #
                    # For mirror_type == "file" means <source> uses file=
                    # even though the "file" attribute does exist in the
                    # <mirror> element.
                    #
                    # For mirror_type == "block" means <source> uses dev=
                    #
                    # When mirror_type == "network" is supported <source>
                    # will be defined...
                    #
                    if (mirror_type == "file" and
                            disk_mirror_source is not None):
                        disk_mirror_src = disk_mirror_source.get('file')
                    elif (mirror_type == "block" and
                            disk_mirror_source is not None):
                        disk_mirror_src = disk_mirror_source.get('dev')
                    else:
                        disk_mirror_src = None
                else:
                    disk_mirror_src = disk_mirror.get('file')
                if disk_mirror_src == dest_path:
                    logging.debug("Find %s in <mirror> element", dest_path)
                    re2 = 2
        except Exception as detail:
            logging.error(detail)
            return False
    finally:
        logging.debug('re1, re2 are: %d, %d', re1, re2)
        if re1 + re2 == expect_re:
            logging.debug("Domain XML check pass")
            return True
        else:
            logging.error("Domain XML check fail")
            return False


def finish_job(vm_name, target, timeout):
    """
    Make sure the block copy job finish.

    :param vm_name: Domain name
    :param target: Domain disk target dev
    :param timeout: Timeout value of this function
    """
    job_time = 0
    while job_time < timeout:
        # As BZ#1359679, blockjob may disappear during the process,
        # so we need check it all the time
        if utl.check_blockjob(vm_name, target, 'none', '0'):
            raise exceptions.TestFail("No blockjob find for '%s'" % target)

        if utl.check_blockjob(vm_name, target, "progress", "100"):
            logging.debug("Block job progress up to 100%")
            break
        else:
            job_time += 2
            time.sleep(2)
    if job_time >= timeout:
        raise JobTimeout(timeout)


def chk_libvirtd_log(file_path, pattern, log_type):
    """
    Check whether libvirtd log contains specific info
    :param file_path: libvirtd log path
    :param pattern: string, pattern to check
    :param log_type: string, debug, error or warning

    :return: True or False
    """
    with open(file_path) as f:
        for line in f:
            if pattern in line and log_type in line:
                return True
    return False


def kill_blockcopy_process():
    """
    Kill running blockcopy process
    """
    kill_cmd = "ps aux|grep -i 'blockcopy'|grep -v grep|grep -v transient_job|awk '{print $2}'"
    pid_list = process.run(kill_cmd, shell=True).stdout_text.strip().split('\n')
    for pid in pid_list:
        utils_misc.safe_kill(pid, signal.SIGKILL)


def blockcopy_thread(vm_name, target, dest_path, options):
    """
    Create one separate thread to do blockcopy
    :param vm_name: string, VM name
    :param target: string, target disk
    :param dest_path: string, the path of copied disk
    :param options: string, some options applied

    :return:
    """
    # Run the real testing command
    virsh.blockcopy(vm_name, target, dest_path, options)


def setup_auth_enabled_iscsi_disk(vm, params):
    """
    Create one separate thread to do blockcopy

    :param vm: VM
    :param params: config params
    """
    disk_type = params.get("disk_type", "file")
    disk_target = params.get("disk_target", 'vda')
    disk_target_bus = params.get("disk_target_bus", "virtio")
    disk_format = params.get("disk_format", "qcow2")
    disk_device = params.get("disk_device", "lun")
    first_disk = vm.get_first_disk_devices()
    logging.debug("first disk is %s", first_disk)
    blk_source = first_disk['source']
    if vm.is_alive():
        vm.destroy(gracefully=False)
    image_size = params.get("image_size", "5G")
    chap_user = params.get("chap_user", "redhat")
    chap_passwd = params.get("chap_passwd", "password")
    auth_sec_usage = params.get("auth_sec_usage",
                                "libvirtiscsi")
    auth_sec_dict = {"sec_usage": "iscsi",
                     "sec_target": auth_sec_usage}
    auth_sec_uuid = utl.create_secret(auth_sec_dict)
    # Set password of auth secret
    virsh.secret_set_value(auth_sec_uuid, chap_passwd,
                           encode=True, debug=True)
    emu_image = params.get("emulated_image", "emulated-iscsi")
    utl.setup_or_cleanup_iscsi(is_setup=False)
    iscsi_target, lun_num = utl.setup_or_cleanup_iscsi(
        is_setup=True, is_login=False, image_size=image_size,
        chap_user=chap_user, chap_passwd=chap_passwd)
    # Copy first disk to emulated backing store path
    tmp_dir = data_dir.get_data_dir()
    emulated_path = os.path.join(tmp_dir, emu_image)
    cmd = "qemu-img convert -f %s -O %s %s %s" % ('qcow2',
                                                  disk_format,
                                                  blk_source,
                                                  emulated_path)
    process.run(cmd, ignore_status=False, shell=True)

    # ISCSI auth attributes for disk xml
    auth_sec_usage_type = params.get("auth_sec_usage_type", "iscsi")
    auth_sec_usage_target = params.get("auth_sec_usage_target", "libvirtiscsi")
    disk_auth_dict = {"auth_user": chap_user,
                      "secret_type": auth_sec_usage_type,
                      "secret_usage": auth_sec_usage_target}
    disk_src_dict = {"attrs": {"protocol": "iscsi",
                               "name": "%s/%s" % (iscsi_target, lun_num)},
                     "hosts": [{"name": '127.0.0.1', "port": '3260'}]}
    # Add disk xml.
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    # Delete disk elements
    disk_deleted = False
    disks = vmxml.get_devices(device_type="disk")
    for disk_ in disks:
        if disk_.target['dev'] == disk_target:
            vmxml.del_device(disk_)
            disk_deleted = True
    if disk_deleted:
        vmxml.sync()
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)

    disk_xml = Disk(type_name=disk_type)
    disk_xml.device = disk_device
    disk_xml.target = {"dev": disk_target, "bus": disk_target_bus}
    driver_dict = {"name": "qemu", "type": disk_format}
    disk_xml.driver = driver_dict
    disk_source = disk_xml.new_disk_source(**disk_src_dict)
    if disk_auth_dict:
        logging.debug("disk auth dict is: %s" % disk_auth_dict)
        auth_in_source = randint(0, 50) % 2 == 0
        if auth_in_source:
            disk_source.auth = disk_xml.new_auth(**disk_auth_dict)
        else:
            disk_xml.auth = disk_xml.new_auth(**disk_auth_dict)
    disk_xml.source = disk_source
    logging.debug("new disk xml is: %s", disk_xml)
    vmxml.add_device(disk_xml)
    vmxml.sync()
    vm.start()


def run(test, params, env):
    """
    Test command: virsh blockcopy.

    This command can copy a disk backing image chain to dest.
    1. Positive testing
        1.1 Copy a disk to a new image file.
        1.2 Reuse existing destination copy.
        1.3 Valid blockcopy timeout and bandwidth test.
    2. Negative testing
        2.1 Copy a disk to a non-exist directory.
        2.2 Copy a disk with invalid options.
        2.3 Do block copy for a persistent domain.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target = params.get("target_disk", "")
    replace_vm_disk = "yes" == params.get("replace_vm_disk", "no")
    disk_source_protocol = params.get("disk_source_protocol")
    disk_type = params.get("disk_type")
    pool_name = params.get("pool_name")
    image_size = params.get("image_size")
    emu_image = params.get("emulated_image")
    copy_to_nfs = "yes" == params.get("copy_to_nfs", "no")
    mnt_path_name = params.get("mnt_path_name")
    options = params.get("blockcopy_options", "")
    bandwidth = params.get("blockcopy_bandwidth", "")
    bandwidth_byte = "yes" == params.get("bandwidth_byte", "no")
    reuse_external = "yes" == params.get("reuse_external", "no")
    persistent_vm = params.get("persistent_vm", "no")
    status_error = "yes" == params.get("status_error", "no")
    active_error = "yes" == params.get("active_error", "no")
    active_snap = "yes" == params.get("active_snap", "no")
    active_save = "yes" == params.get("active_save", "no")
    check_state_lock = "yes" == params.get("check_state_lock", "no")
    check_finish_job = "yes" == params.get("check_finish_job", "yes")
    with_shallow = "yes" == params.get("with_shallow", "no")
    with_blockdev = "yes" == params.get("with_blockdev", "no")
    setup_libvirt_polkit = "yes" == params.get('setup_libvirt_polkit')
    bug_url = params.get("bug_url", "")
    timeout = int(params.get("timeout", 1200))
    relative_path = params.get("relative_path")
    rerun_flag = 0
    blkdev_n = None
    back_n = 'blockdev-backing-iscsi'
    snapshot_external_disks = []
    snapshots_take = int(params.get("snapshots_take", '0'))
    external_disk_only_snapshot = "yes" == params.get("external_disk_only_snapshot", "no")
    enable_iscsi_auth = "yes" == params.get("enable_iscsi_auth", "no")

    # Skip/Fail early
    if with_blockdev and not libvirt_version.version_compare(1, 2, 13):
        raise exceptions.TestSkipError("--blockdev option not supported in "
                                       "current version")
    if not target:
        raise exceptions.TestSkipError("Require target disk to copy")
    if setup_libvirt_polkit and not libvirt_version.version_compare(1, 1, 1):
        raise exceptions.TestSkipError("API acl test not supported in current"
                                       " libvirt version")
    if copy_to_nfs and not libvirt_version.version_compare(1, 1, 1):
        raise exceptions.TestSkipError("Bug will not fix: %s" % bug_url)
    if bandwidth_byte and not libvirt_version.version_compare(1, 3, 3):
        raise exceptions.TestSkipError("--bytes option not supported in "
                                       "current version")
    if relative_path == "yes" and not libvirt_version.version_compare(3, 0, 0):
        test.cancel("Forbid using relative path or file name only is added since libvirt-3.0.0")

    if "--transient-job" in options and not libvirt_version.version_compare(4, 5, 0):
        test.cancel("--transient-job option is supported until libvirt 4.5.0 version")

    # Check the source disk
    if vm_xml.VMXML.check_disk_exist(vm_name, target):
        logging.debug("Find %s in domain %s", target, vm_name)
    else:
        raise exceptions.TestFail("Can't find %s in domain %s" % (target,
                                                                  vm_name))

    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    tmp_dir = data_dir.get_data_dir()

    # Prepare dest path params
    dest_path = params.get("dest_path", "")
    dest_format = params.get("dest_format", "")
    # Ugh... this piece of chicanery brought to you by the QemuImg which
    # will "add" the 'dest_format' extension during the check_format code.
    # So if we create the file with the extension and then remove it when
    # doing the check_format later, then we avoid erroneous failures.
    dest_extension = ""
    if dest_format != "":
        dest_extension = ".%s" % dest_format

    # Prepare for --reuse-external option
    if reuse_external:
        options += "--reuse-external --wait"
        # Set rerun_flag=1 to do blockcopy twice, and the first time created
        # file can be reused in the second time if no dest_path given
        # This will make sure the image size equal to original disk size
        if dest_path == "/path/non-exist":
            if os.path.exists(dest_path) and not os.path.isdir(dest_path):
                os.remove(dest_path)
        else:
            rerun_flag = 1

    # Prepare other options
    if dest_format == "raw":
        options += " --raw"
    if with_blockdev:
        options += " --blockdev"
    if len(bandwidth):
        options += " --bandwidth %s" % bandwidth
    if bandwidth_byte:
        options += " --bytes"
    if with_shallow:
        options += " --shallow"

    # Prepare acl options
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    extra_dict = {'uri': uri, 'unprivileged_user': unprivileged_user,
                  'debug': True, 'ignore_status': True, 'timeout': timeout}

    libvirtd_utl = utils_libvirtd.Libvirtd()
    libvirtd_log_path = os.path.join(data_dir.get_tmp_dir(), "libvirt_daemons.log")
    libvirtd_conf_dict = {"log_filter": '"3:json 1:libvirt 1:qemu"',
                          "log_outputs": '"1:file:%s"' % libvirtd_log_path}
    logging.debug("the libvirtd conf file content is :\n %s" % libvirtd_conf_dict)
    libvirtd_conf = utl.customize_libvirt_config(libvirtd_conf_dict)

    def check_format(dest_path, dest_extension, expect):
        """
        Check the image format

        :param dest_path: Path of the copy to create
        :param expect: Expect image format
        """
        # And now because the QemuImg will add the extension for us
        # we have to remove it here.
        path_noext = dest_path.strip(dest_extension)
        params['image_name'] = path_noext
        params['image_format'] = expect
        image = qemu_storage.QemuImg(params, "/", path_noext)
        if image.get_format() == expect:
            logging.debug("%s format is %s", dest_path, expect)
        else:
            raise exceptions.TestFail("%s format is not %s" % (dest_path,
                                                               expect))

    def _blockjob_and_libvirtd_chk(cmd_result):
        """
        Raise TestFail when blockcopy fail with block-job-complete error or
        blockcopy hang with state change lock.
        This is a specific bug verify, so ignore status_error here.
        """
        failure_msg = ""
        err_msg = "internal error: unable to execute QEMU command"
        err_msg += " 'block-job-complete'"
        if err_msg in cmd_result.stderr:
            failure_msg += "Virsh cmd error happened: %s\n" % err_msg
        err_pattern = "Timed out during operation: cannot acquire"
        err_pattern += " state change lock"
        ret = chk_libvirtd_log(libvirtd_log_path, err_pattern, "error")
        if ret:
            failure_msg += "Libvirtd log error happened: %s\n" % err_pattern
        if failure_msg:
            if not libvirt_version.version_compare(1, 3, 2):
                bug_url_ = "https://bugzilla.redhat.com/show_bug.cgi?id=1197592"
                failure_msg += "Hit on bug: %s " % bug_url_
            test.fail(failure_msg)

    def _make_snapshot(snapshot_numbers_take):
        """
        Make external disk snapshot

        :param snapshot_numbers_take: snapshot numbers.
        """
        for count in range(0, snapshot_numbers_take):
            snap_xml = snapshot_xml.SnapshotXML()
            snapshot_name = "blockcopy_snap"
            snap_xml.snap_name = snapshot_name + "_%s" % count
            snap_xml.description = "blockcopy snapshot"

            # Add all disks into xml file.
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            disks = vmxml.devices.by_device_tag('disk')
            # Remove non-storage disk such as 'cdrom'
            for disk in disks:
                if disk.device != 'disk':
                    disks.remove(disk)
            new_disks = []
            src_disk_xml = disks[0]
            disk_xml = snap_xml.SnapDiskXML()
            disk_xml.xmltreefile = src_disk_xml.xmltreefile
            del disk_xml.device
            del disk_xml.address
            disk_xml.snapshot = "external"
            disk_xml.disk_name = disk_xml.target['dev']

            # Only qcow2 works as external snapshot file format, update it
            # here
            driver_attr = disk_xml.driver
            driver_attr.update({'type': 'qcow2'})
            disk_xml.driver = driver_attr

            new_attrs = disk_xml.source.attrs
            if 'file' in disk_xml.source.attrs:
                new_file = os.path.join(tmp_dir, "blockcopy_shallow_%s.snap" % count)
                snapshot_external_disks.append(new_file)
                new_attrs.update({'file': new_file})
                hosts = None
            elif ('dev' in disk_xml.source.attrs or
                  'name' in disk_xml.source.attrs or
                  'pool' in disk_xml.source.attrs):
                if (disk_xml.type_name == 'block' or
                        disk_source_protocol == 'iscsi'):
                    disk_xml.type_name = 'block'
                    if 'name' in new_attrs:
                        del new_attrs['name']
                        del new_attrs['protocol']
                    elif 'pool' in new_attrs:
                        del new_attrs['pool']
                        del new_attrs['volume']
                        del new_attrs['mode']
                    back_path = utl.setup_or_cleanup_iscsi(is_setup=True,
                                                           is_login=True,
                                                           image_size="1G",
                                                           emulated_image=back_n)
                    emulated_iscsi.append(back_n)
                    cmd = "qemu-img create -f qcow2 %s 1G" % back_path
                    process.run(cmd, shell=True)
                    new_attrs.update({'dev': back_path})
                    hosts = None

            new_src_dict = {"attrs": new_attrs}
            if hosts:
                new_src_dict.update({"hosts": hosts})
            disk_xml.source = disk_xml.new_disk_source(**new_src_dict)

            new_disks.append(disk_xml)

            snap_xml.set_disks(new_disks)
            snapshot_xml_path = snap_xml.xml
            logging.debug("The snapshot xml is: %s" % snap_xml.xmltreefile)

            options = "--disk-only --xmlfile %s " % snapshot_xml_path

            snapshot_result = virsh.snapshot_create(
                vm_name, options, debug=True)

            if snapshot_result.exit_status != 0:
                raise exceptions.TestFail(snapshot_result.stderr)

    snap_path = ''
    save_path = ''
    emulated_iscsi = []
    nfs_cleanup = False
    try:
        # Prepare dest_path
        tmp_file = time.strftime("%Y-%m-%d-%H.%M.%S.img")
        tmp_file += dest_extension
        if not dest_path:
            if enable_iscsi_auth:
                utils_secret.clean_up_secrets()
                setup_auth_enabled_iscsi_disk(vm, params)
                dest_path = os.path.join(tmp_dir, tmp_file)
            elif with_blockdev:
                blkdev_n = 'blockdev-iscsi'
                dest_path = utl.setup_or_cleanup_iscsi(is_setup=True,
                                                       is_login=True,
                                                       image_size=image_size,
                                                       emulated_image=blkdev_n)
                emulated_iscsi.append(blkdev_n)
                # Make sure the new disk show up
                utils_misc.wait_for(lambda: os.path.exists(dest_path), 5)
            else:
                if copy_to_nfs:
                    tmp_dir = "%s/%s" % (tmp_dir, mnt_path_name)
                dest_path = os.path.join(tmp_dir, tmp_file)

        # Domain disk replacement with desire type
        if replace_vm_disk:
            # Calling 'set_vm_disk' is bad idea as it left lots of cleanup jobs
            # after test, such as pool, volume, nfs, iscsi and so on
            # TODO: remove this function in the future
            if disk_source_protocol == 'iscsi':
                emulated_iscsi.append(emu_image)
            if disk_source_protocol == 'netfs':
                nfs_cleanup = True
            utl.set_vm_disk(vm, params, tmp_dir, test)
            new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        if with_shallow or external_disk_only_snapshot or enable_iscsi_auth:
            _make_snapshot(snapshots_take)

        # Prepare transient/persistent vm
        if persistent_vm == "no" and vm.is_persistent():
            vm.undefine()
        elif persistent_vm == "yes" and not vm.is_persistent():
            new_xml.define()

        # Run blockcopy command to create destination file
        if rerun_flag == 1:
            options1 = "--wait %s --finish --verbose" % dest_format
            if with_blockdev:
                options1 += " --blockdev"
            if with_shallow:
                options1 += " --shallow"
            cmd_result = virsh.blockcopy(vm_name, target,
                                         dest_path, options1,
                                         **extra_dict)
            status = cmd_result.exit_status
            if status != 0:
                raise exceptions.TestFail("Run blockcopy command fail: %s" %
                                          cmd_result.stdout.strip() + cmd_result.stderr)
            elif not os.path.exists(dest_path):
                raise exceptions.TestFail("Cannot find the created copy")

        if "--transient-job" in options:
            pool = ThreadPool(processes=1)
            async_result = pool.apply_async(blockcopy_thread, (vm_name, target, dest_path, options))
            kill_blockcopy_process()
            utl.check_blockjob(vm_name, target)
            return

        # Run the real testing command
        cmd_result = virsh.blockcopy(vm_name, target, dest_path,
                                     options, **extra_dict)

        # check BZ#1197592
        _blockjob_and_libvirtd_chk(cmd_result)
        status = cmd_result.exit_status

        if not libvirtd_utl.is_running():
            raise exceptions.TestFail("Libvirtd service is dead")

        if not status_error:
            if status == 0:
                ret = utils_misc.wait_for(
                    lambda: check_xml(vm_name, target, dest_path, options), 5)
                if not ret:
                    raise exceptions.TestFail("Domain xml not expected after"
                                              " blockcopy")
                if options.count("--bandwidth"):
                    if options.count('--bytes'):
                        bandwidth += 'B'
                    else:
                        bandwidth += 'M'
                    if not (bandwidth in ['0B', '0M']) and not utl.check_blockjob(vm_name, target, "bandwidth",
                                                                                  bandwidth):
                        raise exceptions.TestFail("Check bandwidth failed")
                val = options.count("--pivot") + options.count("--finish")
                # Don't wait for job finish when using --byte option
                val += options.count('--bytes')
                if val == 0 and check_finish_job:
                    try:
                        finish_job(vm_name, target, timeout)
                    except JobTimeout as excpt:
                        raise exceptions.TestFail("Run command failed: %s" %
                                                  excpt)
                if options.count("--raw") and not with_blockdev:
                    check_format(dest_path, dest_extension, dest_format)
                if active_snap:
                    snap_path = "%s/%s.snap" % (tmp_dir, vm_name)
                    snap_opt = "--disk-only --atomic --no-metadata "
                    snap_opt += "vda,snapshot=external,file=%s" % snap_path
                    ret = virsh.snapshot_create_as(vm_name, snap_opt,
                                                   ignore_status=True,
                                                   debug=True)
                    utl.check_exit_status(ret, active_error)
                if active_save:
                    save_path = "%s/%s.save" % (tmp_dir, vm_name)
                    ret = virsh.save(vm_name, save_path,
                                     ignore_status=True,
                                     debug=True)
                    utl.check_exit_status(ret, active_error)
                if check_state_lock:
                    # Run blockjob pivot in subprocess as it will hang
                    # for a while, run blockjob info again to check
                    # job state
                    command = "virsh blockjob %s %s --pivot" % (vm_name,
                                                                target)
                    session = aexpect.ShellSession(command)
                    ret = virsh.blockjob(vm_name, target, "--info")
                    err_info = "cannot acquire state change lock"
                    if err_info in ret.stderr:
                        raise exceptions.TestFail("Hit on bug: %s" % bug_url)
                    utl.check_exit_status(ret, status_error)
                    session.close()
            else:
                raise exceptions.TestFail(cmd_result.stdout.strip() + cmd_result.stderr)
        else:
            if status:
                logging.debug("Expect error: %s", cmd_result.stderr)
            else:
                # Commit id '4c297728' changed how virsh exits when
                # unexpectedly failing due to timeout from a fail (1)
                # to a success(0), so we need to look for a different
                # marker to indicate the copy aborted. As "stdout: Now
                # in mirroring phase" could be in stdout which fail the
                # check, so also do check in libvirtd log to confirm.
                if options.count("--timeout") and options.count("--wait"):
                    log_pattern = "Copy aborted"
                    if (re.search(log_pattern, cmd_result.stdout.strip()) or
                            chk_libvirtd_log(libvirtd_log_path,
                                             log_pattern, "debug")):
                        logging.debug("Found success a timed out block copy")
                else:
                    raise exceptions.TestFail("Expect fail, but run "
                                              "successfully: %s" % bug_url)
    finally:
        # Recover VM may fail unexpectedly, we need using try/except to
        # proceed the following cleanup steps
        try:
            # Abort exist blockjob to avoid any possible lock error
            virsh.blockjob(vm_name, target, '--abort', ignore_status=True)
            vm.destroy(gracefully=False)
            # It may take a long time to shutdown the VM which has
            # blockjob running
            utils_misc.wait_for(
                lambda: virsh.domstate(vm_name,
                                       ignore_status=True).exit_status, 180)
            if virsh.domain_exists(vm_name):
                if active_snap or with_shallow:
                    option = "--snapshots-metadata"
                else:
                    option = None
                original_xml.sync(option)
            else:
                original_xml.define()
        except Exception as e:
            logging.error(e)
        for disk in snapshot_external_disks:
            if os.path.exists(disk):
                os.remove(disk)
        # Clean up libvirt pool, which may be created by 'set_vm_disk'
        if disk_type == 'volume':
            virsh.pool_destroy(pool_name, ignore_status=True, debug=True)
        # Restore libvirtd conf and restart libvirtd
        libvirtd_conf.restore()
        libvirtd_utl.restart()
        if libvirtd_log_path and os.path.exists(libvirtd_log_path):
            os.unlink(libvirtd_log_path)
        # Clean up NFS
        try:
            if nfs_cleanup:
                utl.setup_or_cleanup_nfs(is_setup=False)
        except Exception as e:
            logging.error(e)
        # Clean up iSCSI
        try:
            for iscsi_n in list(set(emulated_iscsi)):
                utl.setup_or_cleanup_iscsi(is_setup=False, emulated_image=iscsi_n)
                # iscsid will be restarted, so give it a break before next loop
                time.sleep(5)
        except Exception as e:
            logging.error(e)
        if os.path.exists(dest_path):
            os.remove(dest_path)
        if os.path.exists(snap_path):
            os.remove(snap_path)
        if os.path.exists(save_path):
            os.remove(save_path)
        # Restart virtlogd service to release VM log file lock
        try:
            path.find_command('virtlogd')
            process.run('systemctl reset-failed virtlogd')
            process.run('systemctl restart virtlogd ')
        except path.CmdNotFoundError:
            pass
