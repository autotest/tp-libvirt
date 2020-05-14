import re
import time
import logging

import aexpect

from virttest import virsh
from virttest import utils_test
from virttest import remote
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test command: snapshot-edit
    Test options: --current, --rename, --clone
    """

    vm_name = params.get("main_vm")
    status_error = params.get("status_error", "no")
    snap_desc = params.get("snapshot_edit_description")
    snap_cur = params.get("snapshot_edit_current", "")
    snap_opt = params.get("snapshot_edit_option", "")
    snap_name = params.get("snapshot_edit_snapname", "")
    snap_newname = params.get("snapshot_edit_newname", "new-snap")
    snap_create_opt1 = params.get("snapshot_create_option1", "")
    snap_create_opt2 = params.get("snapshot_create_option2", "")

    # Do xml backup for final recovery
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    def edit_snap_xml(dom_name, edit_opts, edit_cmd):
        """
        Edit domain snapshot xml

        :param dom_name: name of domain
        :param snap_name: name of snapshot
        :param edit_opts: snapshot-edit options
        :param edit_cmd: edit command list in interactive mode
        """

        session = aexpect.ShellSession("sudo -s")
        try:
            logging.debug("snapshot-edit options is: %s" % edit_opts)
            logging.debug("edit cmd is: %s" % edit_cmd)
            session.sendline("virsh snapshot-edit %s %s"
                             % (dom_name, edit_opts))
            for i in edit_cmd:
                session.sendline(i)
            # Press ESC
            session.send('\x1b')
            # Save and quit
            session.send('ZZ')
            # use sleep(1) to make sure the modify has been completed.
            remote.handle_prompts(session, None, None, r"[\#\$]\s*$")
            session.close()
            logging.info("Succeed to do snapshot edit")
        except (aexpect.ShellError, aexpect.ExpectError) as details:
            log = session.get_output()
            session.close()
            test.fail("Failed to do snapshot-edit: %s\n%s"
                      % (details, log))

    def snap_xml_compare(pre_xml, after_xml):
        """
        Do xml compare when snapshot-edit have --clone or --name option

        :param pre_xml: snapshot xml before edit
        :param after_xml: snapshot xml after edit
        """

        desc_sec = "<description>%s</description>" % snap_desc
        name_sec = "<name>%s</name>" % snap_newname
        name_pat = "<name>\S+</name>"
        desc_pat = "<description>.*?</description>"
        if re.search(r"%s\s+?%s" % (name_pat, desc_pat), pre_xml):
            pre_xml = re.sub(r"%s\s+?%s" % (name_pat, desc_pat),
                             name_sec + '\n' + desc_sec, pre_xml)
        else:
            pre_xml = re.subn(r"%s" % name_pat, name_sec + '\n' +
                              desc_sec, pre_xml, 1)[0]
        # change to list and remove the description element in list
        pre_xml_list = pre_xml.strip().splitlines()
        after_xml_list = after_xml.strip().splitlines()
        pre_list = [pl.strip() for pl in pre_xml_list]
        after_list = [al.strip() for al in after_xml_list]
        if not snap_desc:
            for i in pre_list:
                if desc_sec in i:
                    pre_list.remove(i)
        if pre_list == after_list:
            logging.info("Succeed to check the xml for description and name")
        else:
            # Print just the differences rather than printing both
            # files and forcing the eyeball comparison between lines
            elems = list(map(None, pre_xml.splitlines(), after_xml.splitlines()))
            for pre_line, aft_line in elems:
                if pre_line.lstrip().strip() != aft_line.lstrip().strip():
                    if pre_line is not None:
                        logging.debug("diff before='%s'",
                                      pre_line.lstrip().strip())
                    if aft_line is not None:
                        logging.debug("diff  after='%s'",
                                      aft_line.lstrip().strip())
            test.fail("Failed xml before/after comparison")

    def log_first_lines(pre_xml, after_xml, count=15):
        """
        Print only first lines of xml info

        :param pre_xml: snapshot xml before edit
        :param after_xml: snapshot xml after edit
        :param count: number of lines to be printed at most
        """

        pre = pre_xml.split()
        after = after_xml.split()

        for i in range(min(len(pre), len(after), count)):
            logging.debug("before xml=%s", pre[i].lstrip())
            logging.debug(" after xml=%s", after[i].lstrip())

    snapshot_oldlist = None
    try:
        # Create disk snapshot before all to make the origin image clean
        logging.debug("Create snap-temp --disk-only")
        ret = virsh.snapshot_create_as(vm_name, "snap-temp --disk-only")
        if ret.exit_status != 0:
            test.fail("Fail to create temp snap, Error: %s"
                      % ret.stderr.strip())

        # Create snapshots
        for opt in [snap_create_opt1, snap_create_opt2]:
            logging.debug("...use option %s", opt)
            result = virsh.snapshot_create_as(vm_name, opt)
            if result.exit_status:
                test.fail("Failed to create snapshot. Error:%s."
                          % result.stderr.strip())
            time.sleep(1)

        snapshot_oldlist = virsh.snapshot_list(vm_name)

        # Get the snapshot xml before edit
        if len(snap_name) > 0:
            pre_name = check_name = snap_name
        else:
            cmd_result = virsh.snapshot_current(vm_name)
            pre_name = check_name = cmd_result.stdout.strip()

        ret = virsh.snapshot_dumpxml(vm_name, pre_name)
        if ret.exit_status == 0:
            pre_xml = ret.stdout.strip()
        else:
            test.fail("Fail to dumpxml of snapshot %s:%s" %
                      (pre_name, ret.stderr.strip()))

        edit_cmd = []
        replace_cmd = '%s<\/name>/%s<\/name>' % (pre_name, pre_name)
        replace_cmd += '\\r<description>%s<\/description>' % snap_desc
        replace_cmd = ":%s/" + replace_cmd + "/"
        edit_cmd.append(replace_cmd)
        # if have --clone or --rename, need to change snapshot name in xml
        if len(snap_opt) > 0:
            edit_cmd.append(":2")
            edit_cmd.append(":s/<name>.*</<name>" + snap_newname + "<")
            check_name = snap_newname
        edit_opts = " " + snap_name + " " + snap_cur + " " + snap_opt

        # Do snapshot edit
        if status_error == "yes":
            output = virsh.snapshot_edit(vm_name, edit_opts)
            if output.exit_status == 0:
                test.fail("Succeed to do the snapshot-edit but"
                          " expect fail")
            else:
                logging.info("Fail to do snapshot-edit as expect: %s",
                             output.stderr.strip())
                return

        edit_snap_xml(vm_name, edit_opts, edit_cmd)

        # Do edit check
        snapshots = virsh.snapshot_list(vm_name)
        after_xml = virsh.snapshot_dumpxml(vm_name, check_name).stdout
        match_str = "<description>" + snap_desc + "</description>"
        if not re.search(match_str, after_xml.strip("\n")):
            if snap_desc:
                logging.debug("Failed to edit snapshot edit_opts=%s, match=%s",
                              edit_opts, match_str)
                log_first_lines(pre_xml, after_xml)

                test.fail("Failed to edit snapshot description")

        # Check edit options --clone
        if snap_opt == "--clone":
            if pre_name not in snapshots:
                test.fail("After clone, previous snapshot missing")
            snap_xml_compare(pre_xml, after_xml)

        if snap_opt == "--rename":
            if pre_name in snapshots:
                test.fail("After rename, snapshot %s still exist"
                          % pre_name)
            snap_xml_compare(pre_xml, after_xml)

        # Check if --current effect take effect
        if len(snap_cur) > 0 and len(snap_name) > 0:
            cmd_result = virsh.snapshot_current(vm_name)
            snap_cur = cmd_result.stdout.strip()
            if snap_cur == check_name:
                logging.info("Check current is same as set %s", check_name)
            else:
                test.fail("Fail to check --current, current is %s "
                          "but set is %s" % (snap_cur, check_name))

    finally:
        utils_test.libvirt.clean_up_snapshots(vm_name, snapshot_oldlist)
        vmxml_backup.sync("--snapshots-metadata")
