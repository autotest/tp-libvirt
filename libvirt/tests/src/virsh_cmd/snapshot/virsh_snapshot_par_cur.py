import os
import re
import time
import logging

from virttest import virsh
from virttest import data_dir
from virttest import utils_test
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Do virsh snapshot-parent and virsh snapshot-current test
    with all parameters in readonly/readwrite mode
    """

    vm_name = params.get("main_vm")
    pstatus_error = params.get("snapshot_parent_status_error", "no")
    cstatus_error = params.get("snapshot_current_status_error", "no")
    snap_parent_opt = params.get("snapshot_parent_option")
    snap_cur_opt = params.get("snapshot_current_option")
    passwd = params.get("snapshot_current_passwd")
    snap_num = int(params.get("snapshot_num"))
    readonly = ("yes" == params.get("readonly", "no"))
    without_snapshot = "yes" == params.get("without_snapshot", "no")

    snap_opt = []
    for i in range(1, snap_num + 1):
        screate_opt = params.get("screate_opt%s" % i)
        if "SNAPSHOT_TMPFILE" in screate_opt:
            tmp_file = os.path.join(data_dir.get_tmp_dir(), "tmpfile")
            screate_opt = re.sub("SNAPSHOT_TMPFILE", tmp_file, screate_opt)
        snap_opt.append(screate_opt)

    # Do xml backup for final recovery
    vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Add passwd for snapshot-current --security-info testing
    if snap_cur_opt is not None and "security-info" in snap_cur_opt:
        vm = env.get_vm(vm_name)
        if vm.is_alive():
            vm.destroy()
        vm_xml.VMXML.add_security_info(vmxml_backup.copy(), passwd)
        vm.start()

    def current_snapshot_test():
        """
        Do current snapshot test and xml check
        """
        output = virsh.snapshot_current(vm_name, snap_cur_opt,
                                        ignore_status=True,
                                        debug=True,
                                        readonly=readonly)

        # If run fail with cstatus_error = no, then error will raise in command
        if cstatus_error == "yes":
            if output.exit_status == 0:
                test.fail("Unexpected snapshot-current success")
            else:
                logging.info("Failed to run snapshot-current as expected:%s",
                             output.stderr)
                return

        # Check if snapshot xml have security info
        if "--security-info" in snap_cur_opt and \
           "--name" not in snap_cur_opt:
            devices = vm_xml.VMXML.new_from_dumpxml(vm_name,
                                                    "--security-info").devices
            first_graphic = devices.by_device_tag('graphics')[0]
            try:
                if passwd == first_graphic.passwd:
                    logging.info("Success to check current snapshot with"
                                 " security info")
                else:
                    test.fail("Passwd is not same as set")
            except KeyError:
                test.fail("Can not find passwd in snapshot xml")

        # Check if --snapshotname may change current snapshot
        if "--snapshotname" in snap_cur_opt:
            cmd_result = virsh.snapshot_current(vm_name,
                                                ignore_status=True,
                                                debug=True,
                                                readonly=readonly)
            current_snap = cmd_result.stdout.strip()
            if current_snap == snap_cur_opt.split()[1]:
                logging.info("Success to check current snapshot changed to %s",
                             current_snap)
            else:
                test.fail("Failed to change current snapshot to %s,"
                          "current is %s" %
                          (snap_cur_opt.split()[1], current_snap))

    def parent_snapshot_check(snap_parent):
        """
        Do parent snapshot check
        :params: snap_parent: parent snapshot name that need to check
        """

        # get snapshot name which is parent snapshot's child
        if "--current" in snap_parent_opt:
            cmd_result = virsh.snapshot_current(vm_name)
            snap_name = cmd_result.stdout.strip()
        else:
            snap_name = snap_parent_opt.split()[-1]

        # check parent snapshot in snapshot-list
        output = virsh.command("snapshot-list %s --parent" % vm_name).stdout
        for i in range(2, snap_num + 3):
            if output.splitlines()[i].split()[0] == snap_name:
                expect_name = output.split('\n')[i].split()[-1]
                break

        if snap_parent == expect_name:
            logging.info("Success to check parent snapshot")
        else:
            test.fail("Failed to check parent "
                      "snapshot, expect %s, get %s"
                      % (expect_name, snap_parent))

    def parent_snapshot_test():
        """
        Do parent snapshot test
        """

        cmd_result = virsh.snapshot_parent(vm_name, snap_parent_opt,
                                           debug=True,
                                           readonly=readonly)

        # check status
        if pstatus_error == "yes":
            if cmd_result.exit_status == 0:
                test.fail("Unexpected success")
            else:
                logging.info("Run failed as expected:%s", cmd_result.stderr)
        elif cmd_result.exit_status != 0:
            test.fail("Run failed with right command:%s" %
                      cmd_result.stderr)
        else:
            parent_snapshot_check(cmd_result.stdout.strip())

    try:
        if not without_snapshot:
            # Create disk snapshot before all to make the origin image clean
            ret = virsh.snapshot_create_as(vm_name, "snap-temp --disk-only")
            if ret.exit_status != 0:
                test.fail("Fail to create temp snap, Error: %s"
                          % ret.stderr.strip())

            # Create snapshots
            for opt in snap_opt:
                result = virsh.snapshot_create_as(vm_name, opt)
                if result.exit_status:
                    test.fail("Failed to create snapshot. Error:%s."
                              % result.stderr.strip())
                time.sleep(1)

        # Do parent snapshot test
        if snap_parent_opt is not None:
            parent_snapshot_test()

        # Do current snapshot test
        if snap_cur_opt is not None:
            current_snapshot_test()

    finally:
        if not without_snapshot:
            utils_test.libvirt.clean_up_snapshots(vm_name)
            vmxml_backup.sync("--snapshots-metadata")
        try:
            os.remove(tmp_file)
        except (OSError, NameError):  # tmp_file defined inside conditional
            pass
