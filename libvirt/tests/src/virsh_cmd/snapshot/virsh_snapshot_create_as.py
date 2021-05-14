import re
import os
import time
import string
import logging

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import xml_utils
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import gluster
from virttest import utils_split_daemons
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.libvirt_xml.devices import disk
from virttest import libvirt_storage

from virttest import libvirt_version


def xml_recover(vmxml):
    """
    Recover older xml config with backup vmxml.

    :params: vmxml: VMXML object
    """
    try:
        options = "--snapshots-metadata"
        vmxml.undefine(options)
        vmxml.define()
        return True

    except xcepts.LibvirtXMLError as detail:
        logging.error("Recover older xml failed:%s.", detail)
        return False


def check_snap_in_image(vm_name, snap_name):
    """
    check the snapshot info in image

    :params: vm_name: VM name
    :params: snap_name: Snapshot name
    """

    domxml = virsh.dumpxml(vm_name).stdout.strip()
    xtf_dom = xml_utils.XMLTreeFile(domxml)
    # Check whether qemu-img need add -U suboption since locking feature was added afterwards qemu-2.10
    qemu_img_locking_feature_support = libvirt_storage.check_qemu_image_lock_support()

    cmd = "qemu-img info " + xtf_dom.find("devices/disk/source").get("file")
    if qemu_img_locking_feature_support:
        cmd = "qemu-img info -U " + xtf_dom.find("devices/disk/source").get("file")
    img_info = process.getoutput(cmd).strip()

    if re.search(snap_name, img_info):
        logging.info("Find snapshot info in image")
        return True
    else:
        return False


def compose_disk_options(test, params, opt_names, tmp_dir):
    """
    Compose the {disk,mem}spec options

    The diskspec file need to add suitable dir with the name which is configed
    individually, The 'value' after 'file=' is a parameter which also need to
    get from cfg

    :param test & params: system parameters
    :param opt_names: params get from cfg of {disk,mem}spec options
    :param tmp_dir: temp directory
    """
    if "snapshot=no" in opt_names:
        return opt_names
    if opt_names.find("file=") >= 0:
        opt_disk = opt_names.split("file=")
        opt_list = opt_disk[1].split(",")

        if len(opt_list) > 1:
            left_opt = opt_list[1]
        else:
            left_opt = ""

        if params.get("bad_disk") is not None or \
           params.get("reuse_external") == "yes":
            spec_disk = os.path.join(tmp_dir, params.get(opt_list[0]))
        else:
            spec_disk = os.path.join(tmp_dir, opt_list[0])

        return opt_disk[0] + "file=" + spec_disk + left_opt


def check_snapslist(test, vm_name, options, option_dict, output,
                    snaps_before, snaps_list):
    no_metadata = options.find("--no-metadata")
    fdisks = "disks"

    # command with print-xml will not really create snapshot
    if options.find("print-xml") >= 0:
        xtf = xml_utils.XMLTreeFile(output)

        # With --print-xml there isn't new snapshot created
        if len(snaps_before) != len(snaps_list):
            test.fail("--print-xml create new snapshot")

    else:
        # The following does not check with print-xml
        get_sname = output.split()[2]

        # check domain/snapshot xml depends on if have metadata
        if no_metadata < 0:
            output_dump = virsh.snapshot_dumpxml(vm_name,
                                                 get_sname).stdout.strip()
        else:
            output_dump = virsh.dumpxml(vm_name).stdout.strip()
            fdisks = "devices"

        xtf = xml_utils.XMLTreeFile(output_dump)

        find = 0
        for snap in snaps_list:
            if snap == get_sname:
                find = 1
                break

        # Should find snap in snaplist without --no-metadata
        if (find == 0 and no_metadata < 0):
            test.fail("Can not find snapshot %s!"
                      % get_sname)
        # Should not find snap in list without metadata
        elif (find == 1 and no_metadata >= 0):
            test.fail("Can find snapshot metadata even "
                      "if have --no-metadata")
        elif (find == 0 and no_metadata >= 0):
            logging.info("Can not find snapshot %s as no-metadata "
                         "is given" % get_sname)

            # Check snapshot only in qemu-img
            if (options.find("--disk-only") < 0 and
                    options.find("--memspec") < 0):
                ret = check_snap_in_image(vm_name, get_sname)

                if ret is False:
                    test.fail("No snap info in image")

        else:
            logging.info("Find snapshot %s in snapshot list."
                         % get_sname)

        # Check if the disk file exist when disk-only is given
        if options.find("disk-only") >= 0:
            for disk in xtf.find(fdisks).findall('disk'):
                if disk.get('snapshot') == 'no':
                    continue
                diskpath = disk.find('source').get('file')
                if os.path.isfile(diskpath):
                    logging.info("disk file %s exist" % diskpath)
                    os.remove(diskpath)
                else:
                    # Didn't find <source file="path to disk"/>
                    # in output - this could leave a file around
                    # wherever the main OS image file is found
                    logging.debug("output_dump=%s", output_dump)
                    test.fail("Can not find disk %s"
                              % diskpath)

        # Check if the guest is halted when 'halt' is given
        if options.find("halt") >= 0:
            domstate = virsh.domstate(vm_name)
            if re.match("shut off", domstate.stdout):
                logging.info("Domain is halted after create "
                             "snapshot")
            else:
                test.fail("Domain is not halted after "
                          "snapshot created")

    # Check the snapshot xml regardless of having print-xml or not
    if (options.find("name") >= 0 and no_metadata < 0):
        if xtf.findtext('name') == option_dict["name"]:
            logging.info("get snapshot name same as set")
        else:
            test.fail("Get wrong snapshot name %s" %
                      xtf.findtext('name'))

    if (options.find("description") >= 0 and no_metadata < 0):
        desc = xtf.findtext('description')
        if desc == option_dict["description"]:
            logging.info("get snapshot description same as set")
        else:
            test.fail("Get wrong description on xml")

    if options.find("diskspec") >= 0:
        if isinstance(option_dict['diskspec'], list):
            index = len(option_dict['diskspec'])
        else:
            index = 1

        disks = xtf.find(fdisks).findall('disk')

        for num in range(index):
            if isinstance(option_dict['diskspec'], list):
                option_disk = option_dict['diskspec'][num]
            else:
                option_disk = option_dict['diskspec']

            option_disk = "name=" + option_disk
            disk_dict = utils_misc.valued_option_dict(option_disk,
                                                      ",", 0, "=")
            logging.debug("disk_dict is %s", disk_dict)

            # For no metadata snapshot do not check name and
            # snapshot
            if no_metadata < 0:
                dname = disks[num].get('name')
                logging.debug("dname is %s", dname)
                if dname == disk_dict['name']:
                    logging.info("get disk%d name same as set in "
                                 "diskspec", num)
                else:
                    test.fail("Get wrong disk%d name %s"
                              % (num, dname))

                if option_disk.find('snapshot=') >= 0:
                    dsnap = disks[num].get('snapshot')
                    logging.debug("dsnap is %s", dsnap)
                    if dsnap == disk_dict['snapshot']:
                        logging.info("get disk%d snapshot type same"
                                     " as set in diskspec", num)
                    else:
                        test.fail("Get wrong disk%d "
                                  "snapshot type %s" %
                                  (num, dsnap))

            if option_disk.find('driver=') >= 0:
                dtype = disks[num].find('driver').get('type')
                if dtype == disk_dict['driver']:
                    logging.info("get disk%d driver type same as "
                                 "set in diskspec", num)
                else:
                    test.fail("Get wrong disk%d driver "
                              "type %s" % (num, dtype))

            if option_disk.find('file=') >= 0:
                sfile = disks[num].find('source').get('file')
                if sfile == disk_dict['file']:
                    logging.info("get disk%d source file same as "
                                 "set in diskspec", num)
                    if os.path.exists(sfile):
                        os.unlink(sfile)
                else:
                    test.fail("Get wrong disk%d source "
                              "file %s" % (num, sfile))

    # For memspec check if the xml is same as setting
    # Also check if the mem file exists
    if options.find("memspec") >= 0:
        memspec = option_dict['memspec']
        if not re.search('file=', option_dict['memspec']):
            memspec = 'file=' + option_dict['memspec']

        mem_dict = utils_misc.valued_option_dict(memspec, ",", 0,
                                                 "=")
        logging.debug("mem_dict is %s", mem_dict)

        if no_metadata < 0:
            if memspec.find('snapshot=') >= 0:
                snap = xtf.find('memory').get('snapshot')
                if snap == mem_dict['snapshot']:
                    logging.info("get memory snapshot type same as"
                                 " set in diskspec")
                else:
                    test.fail("Get wrong memory snapshot"
                              " type on print xml")

            memfile = xtf.find('memory').get('file')
            if memfile == mem_dict['file']:
                logging.info("get memory file same as set in "
                             "diskspec")
            else:
                test.fail("Get wrong memory file on "
                          "print xml %s", memfile)

        if options.find("print-xml") < 0:
            if os.path.isfile(mem_dict['file']):
                logging.info("memory file generated")
                os.remove(mem_dict['file'])
            else:
                test.fail("Fail to generate memory file"
                          " %s", mem_dict['file'])


def run(test, params, env):
    """
    Test snapshot-create-as command
    Make sure that the clean repo can be used because qemu-guest-agent need to
    be installed in guest

    The command create a snapshot (disk and RAM) from arguments which including
    the following point
    * virsh snapshot-create-as --print-xml --diskspec --name --description
    * virsh snapshot-create-as --print-xml with multi --diskspec
    * virsh snapshot-create-as --print-xml --memspec
    * virsh snapshot-create-as --description
    * virsh snapshot-create-as --no-metadata
    * virsh snapshot-create-as --no-metadata --print-xml (negative test)
    * virsh snapshot-create-as --atomic --disk-only
    * virsh snapshot-create-as --quiesce --disk-only (positive and negative)
    * virsh snapshot-create-as --reuse-external
    * virsh snapshot-create-as --disk-only --diskspec
    * virsh snapshot-create-as --memspec --reuse-external --atomic(negative)
    * virsh snapshot-create-as --disk-only and --memspec (negative)
    * Create multi snapshots with snapshot-create-as
    * Create snapshot with name a--a a--a--snap1
    """

    if not virsh.has_help_command('snapshot-create-as'):
        test.cancel("This version of libvirt does not support "
                    "the snapshot-create-as test")

    vm_name = params.get("main_vm")
    status_error = params.get("status_error", "no")
    machine_type = params.get("machine_type", "")
    disk_device = params.get("disk_device", "")
    options = params.get("snap_createas_opts")
    multi_num = params.get("multi_num", "1")
    diskspec_num = params.get("diskspec_num", "1")
    bad_disk = params.get("bad_disk")
    reuse_external = "yes" == params.get("reuse_external", "no")
    start_ga = params.get("start_ga", "yes")
    domain_state = params.get("domain_state")
    memspec_opts = params.get("memspec_opts")
    config_format = "yes" == params.get("config_format", "no")
    snapshot_image_format = params.get("snapshot_image_format")
    diskspec_opts = params.get("diskspec_opts")
    create_autodestroy = 'yes' == params.get("create_autodestroy", "no")
    unix_channel = "yes" == params.get("unix_channel", "yes")
    dac_denial = "yes" == params.get("dac_denial", "no")
    check_json_no_savevm = "yes" == params.get("check_json_no_savevm", "no")
    disk_snapshot_attr = params.get('disk_snapshot_attr', 'external')
    set_snapshot_attr = "yes" == params.get("set_snapshot_attr", "no")

    # gluster related params
    replace_vm_disk = "yes" == params.get("replace_vm_disk", "no")
    disk_src_protocol = params.get("disk_source_protocol")
    restart_tgtd = params.get("restart_tgtd", "no")
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_data_dir()
    pool_name = params.get("pool_name", "gluster-pool")
    brick_path = os.path.join(tmp_dir, pool_name)
    transport = params.get("transport", "")

    uri = params.get("virsh_uri")
    usr = params.get('unprivileged_user')
    if usr:
        if usr.count('EXAMPLE'):
            usr = 'testacl'

    if disk_device == 'lun' and machine_type == 's390-ccw-virtio':
        params['disk_target_bus'] = 'scsi'
        logging.debug("Setting target bus scsi because machine type has virtio 1.0."
                      " See https://bugzilla.redhat.com/show_bug.cgi?id=1365823")

    if disk_src_protocol == 'iscsi':
        if not libvirt_version.version_compare(1, 0, 4):
            test.cancel("'iscsi' disk doesn't support in"
                        " current libvirt version.")

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    if not libvirt_version.version_compare(1, 2, 7):
        # As bug 1017289 closed as WONTFIX, the support only
        # exist on 1.2.7 and higher
        if disk_src_protocol == 'gluster':
            test.cancel("Snapshot on glusterfs not support in "
                        "current version. Check more info with "
                        "https://bugzilla.redhat.com/buglist.cgi?"
                        "bug_id=1017289,1032370")

    if libvirt_version.version_compare(5, 5, 0):
        # libvirt-5.5.0-2 commit 68e1a05f starts to allow --no-metadata and
        # --print-xml to be used together.
        if "--no-metadata" in options and "--print-xml" in options:
            logging.info("--no-metadata and --print-xml can be used together "
                         "in this libvirt version. Not expecting a failure.")
            status_error = "no"

    opt_names = locals()
    if memspec_opts is not None:
        mem_options = compose_disk_options(test, params, memspec_opts, tmp_dir)
        # if the parameters have the disk without "file=" then we only need to
        # add testdir for it.
        if mem_options is None:
            mem_options = os.path.join(tmp_dir, memspec_opts)
        options += " --memspec " + mem_options

    tag_diskspec = 0
    dnum = int(diskspec_num)
    if diskspec_opts is not None:
        tag_diskspec = 1
        opt_names['diskopts_1'] = diskspec_opts

    # diskspec_opts[n] is used in cfg when more than 1 --diskspec is used
    if dnum > 1:
        tag_diskspec = 1
        for i in range(1, dnum + 1):
            opt_names["diskopts_%s" % i] = params.get("diskspec_opts%s" % i)

    if tag_diskspec == 1:
        for i in range(1, dnum + 1):
            disk_options = compose_disk_options(test, params,
                                                opt_names["diskopts_%s" % i],
                                                tmp_dir)
            options += " --diskspec " + disk_options

    logging.debug("options are %s", options)

    vm = env.get_vm(vm_name)
    option_dict = {}
    option_dict = utils_misc.valued_option_dict(options, r' --(?!-)')
    logging.debug("option_dict is %s", option_dict)

    # A backup of original vm
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    logging.debug("original xml is %s", vmxml_backup)

    # Generate empty image for negative test
    if bad_disk is not None:
        bad_disk = os.path.join(tmp_dir, bad_disk)
        with open(bad_disk, 'w') as bad_file:
            pass

    # Generate external disk
    if reuse_external:
        disk_path = ''
        for i in range(dnum):
            external_disk = "external_disk%s" % i
            if params.get(external_disk):
                disk_path = os.path.join(tmp_dir,
                                         params.get(external_disk))
                process.run("qemu-img create -f qcow2 %s 1G" % disk_path, shell=True)
        # Only chmod of the last external disk for negative case
        if dac_denial:
            process.run("chmod 500 %s" % disk_path, shell=True)

    qemu_conf = None
    libvirtd_conf = None
    libvirtd_conf_dict = {}
    libvirtd_log_path = None
    conf_type = "libvirtd"
    if utils_split_daemons.is_modular_daemon():
        conf_type = "virtqemud"
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # Config "snapshot_image_format" option in qemu.conf
        if config_format:
            qemu_conf = utils_config.LibvirtQemuConfig()
            qemu_conf.snapshot_image_format = snapshot_image_format
            logging.debug("the qemu config file content is:\n %s" % qemu_conf)
            libvirtd.restart()

        if check_json_no_savevm:
            libvirtd_conf_dict["log_level"] = '1'
            libvirtd_conf_dict["log_filters"] = '"1:json 3:remote 4:event"'
            libvirtd_log_path = os.path.join(data_dir.get_tmp_dir(), "libvirtd.log")
            libvirtd_conf_dict["log_outputs"] = '"1:file:%s"' % libvirtd_log_path
            libvirtd_conf = libvirt.customize_libvirt_config(libvirtd_conf_dict,
                                                             conf_type,)
            logging.debug("the libvirtd config file content is:\n %s" %
                          libvirtd_conf)
            libvirtd.restart()

        if replace_vm_disk:
            libvirt.set_vm_disk(vm, params, tmp_dir)

        if set_snapshot_attr:
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm_name)
            disk_xml = vmxml_backup.get_devices(device_type="disk")[0]
            vmxml_new.del_device(disk_xml)
            # set snapshot attribute in disk xml
            disk_xml.snapshot = disk_snapshot_attr
            new_disk = disk.Disk(type_name='file')
            new_disk.xmltreefile = disk_xml.xmltreefile
            vmxml_new.add_device(new_disk)
            logging.debug("The vm xml now is: %s" % vmxml_new.xmltreefile)
            vmxml_new.sync()
            vm.start()

        # Start qemu-ga on guest if have --quiesce
        if unix_channel and options.find("quiesce") >= 0:
            vm.prepare_guest_agent()
            session = vm.wait_for_login()
            if start_ga == "no":
                # The qemu-ga could be running and should be killed
                session.cmd("kill -9 `pidof qemu-ga`")
                # Check if the qemu-ga get killed
                stat_ps = session.cmd_status("ps aux |grep [q]emu-ga")
                if not stat_ps:
                    # As managed by systemd and set as autostart, qemu-ga
                    # could be restarted, so use systemctl to stop it.
                    session.cmd("systemctl stop qemu-guest-agent")
                    stat_ps = session.cmd_status("ps aux |grep [q]emu-ga")
                    if not stat_ps:
                        test.cancel("Fail to stop agent in "
                                    "guest")

            if domain_state == "paused":
                virsh.suspend(vm_name)
        else:
            # Remove channel if exist
            if vm.is_alive():
                vm.destroy(gracefully=False)
            xml_inst = vm_xml.VMXML.new_from_dumpxml(vm_name)
            xml_inst.remove_agent_channels()
            vm.start()

        # Record the previous snapshot-list
        snaps_before = virsh.snapshot_list(vm_name)

        # Attach disk before create snapshot if not print xml and multi disks
        # specified in cfg
        if dnum > 1 and "--print-xml" not in options:
            for i in range(1, dnum):
                disk_path = os.path.join(tmp_dir, 'disk%s.qcow2' % i)
                process.run("qemu-img create -f qcow2 %s 200M" % disk_path, shell=True)
                virsh.attach_disk(vm_name, disk_path,
                                  'vd%s' % list(string.ascii_lowercase)[i],
                                  debug=True)

        # Run virsh command
        # May create several snapshots, according to configuration
        for count in range(int(multi_num)):
            if create_autodestroy:
                # Run virsh command in interactive mode
                vmxml_backup.undefine()
                vp = virsh.VirshPersistent()
                vp.create(vmxml_backup['xml'], '--autodestroy')
                cmd_result = vp.snapshot_create_as(vm_name, options,
                                                   ignore_status=True,
                                                   debug=True)
                vp.close_session()
                vmxml_backup.define()
            else:
                cmd_result = virsh.snapshot_create_as(vm_name, options,
                                                      unprivileged_user=usr,
                                                      uri=uri,
                                                      ignore_status=True,
                                                      debug=True)
                # for multi snapshots without specific snapshot name, the
                # snapshot name is using time string with 1 second
                # incremental, to avoid get snapshot failure with same name,
                # sleep 1 second here.
                if int(multi_num) > 1:
                    time.sleep(1.1)
            output = cmd_result.stdout.strip()
            status = cmd_result.exit_status

            # check status_error
            if status_error == "yes":
                if status == 0:
                    test.fail("Run successfully with wrong command!")
                else:
                    # Check memspec file should be removed if failed
                    if (options.find("memspec") >= 0 and
                            options.find("atomic") >= 0):
                        if os.path.isfile(option_dict['memspec']):
                            os.remove(option_dict['memspec'])
                            test.fail("Run failed but file %s exist"
                                      % option_dict['memspec'])
                        else:
                            logging.info("Run failed as expected and memspec"
                                         " file already been removed")
                    # Check domain xml is not updated if reuse external fail
                    elif reuse_external and dac_denial:
                        output = virsh.dumpxml(vm_name).stdout.strip()
                        if "reuse_external" in output:
                            test.fail("Domain xml should not be "
                                      "updated with snapshot image")
                    else:
                        logging.info("Run failed as expected")

            elif status_error == "no":
                if status != 0:
                    test.fail("Run failed with right command: %s"
                              % output)
                else:
                    # Check the special options
                    snaps_list = virsh.snapshot_list(vm_name)
                    logging.debug("snaps_list is %s", snaps_list)

                    check_snapslist(test, vm_name, options, option_dict, output,
                                    snaps_before, snaps_list)

                    # For cover bug 872292
                    if check_json_no_savevm:
                        pattern = "The command savevm has not been found"
                        with open(libvirtd_log_path) as f:
                            for line in f:
                                if pattern in line and "error" in line:
                                    test.fail("'%s' was found: %s"
                                              % (pattern, line))

    finally:
        if vm.is_alive():
            vm.destroy()
        # recover domain xml
        xml_recover(vmxml_backup)
        path = "/var/lib/libvirt/qemu/snapshot/" + vm_name
        if os.path.isfile(path):
            test.fail("Still can find snapshot metadata")

        if disk_src_protocol == 'gluster':
            gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **params)
            libvirtd.restart()

        if disk_src_protocol == 'iscsi':
            libvirt.setup_or_cleanup_iscsi(False, restart_tgtd=restart_tgtd)

        # rm bad disks
        if bad_disk is not None:
            os.remove(bad_disk)
        # rm attach disks and reuse external disks
        if dnum > 1 and "--print-xml" not in options:
            for i in range(dnum):
                disk_path = os.path.join(tmp_dir, 'disk%s.qcow2' % i)
                if os.path.exists(disk_path):
                    os.unlink(disk_path)
                if reuse_external:
                    external_disk = "external_disk%s" % i
                    disk_path = os.path.join(tmp_dir,
                                             params.get(external_disk))
                    if os.path.exists(disk_path):
                        os.unlink(disk_path)

        # restore config
        if config_format and qemu_conf:
            qemu_conf.restore()

        if libvirtd_conf:
            libvirtd_conf.restore()

        if libvirtd_conf or (config_format and qemu_conf):
            libvirtd.restart()

        if libvirtd_log_path and os.path.exists(libvirtd_log_path):
            os.unlink(libvirtd_log_path)
