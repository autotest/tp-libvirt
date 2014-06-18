import os
import logging
import re
import tempfile
from autotest.client.shared import error
from autotest.client import utils
from virttest import virsh, qemu_storage, data_dir
from virttest import libvirt_xml
from virttest import libvirt_storage
from virttest.utils_test import libvirt as utlv
from provider import libvirt_version


def run(test, params, env):
    """
    Test virsh snapshot command when disk in all kinds of type.

    (1). Init the variables from params.
    (2). Create a image by specifice format.
    (3). Attach disk to vm.
    (4). Snapshot create.
    (5). Snapshot revert.
    (6). cleanup.
    """
    # Init variables.
    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)
    image_format = params.get("snapshot_image_format", "qcow2")
    snapshot_del_test = "yes" == params.get("snapshot_del_test", "no")
    status_error = ("yes" == params.get("status_error", "no"))
    snapshot_from_xml = ("yes" == params.get("snapshot_from_xml", "no"))
    snapshot_current = ("yes" == params.get("snapshot_current", "no"))
    snapshot_revert_paused = ("yes" == params.get("snapshot_revert_paused",
                                                  "no"))
    # Pool variables.
    snapshot_with_pool = "yes" == params.get("snapshot_with_pool", "no")
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    emulated_image = params.get("emulated_image")
    vol_name = params.get("vol_name")
    vol_format = params.get("vol_format")
    lazy_refcounts = "yes" == params.get("lazy_refcounts")

    # Set volume xml attribute dictionary, extract all params start with 'vol_'
    # which are for setting volume xml, except 'lazy_refcounts'.
    vol_arg = {}
    for key in params.keys():
        if key.startswith('vol_'):
            if key[4:] in ['capacity', 'allocation', 'owner', 'group']:
                vol_arg[key[4:]] = int(params[key])
            else:
                vol_arg[key[4:]] = params[key]
    vol_arg['lazy_refcounts'] = lazy_refcounts

    supported_pool_list = ["dir", "fs", "netfs", "logical", "iscsi",
                           "disk", "gluster"]
    if snapshot_with_pool:
        if pool_type not in supported_pool_list:
            raise error.TestNAError("%s not in support list %s" %
                                    (pool_target, supported_pool_list))

    # Do xml backup for final recovery
    vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Some variable for xmlfile of snapshot.
    snapshot_memory = params.get("snapshot_memory", "internal")
    snapshot_disk = params.get("snapshot_disk", "internal")

    # Skip 'qed' cases for libvirt version greater than 1.1.0
    if libvirt_version.version_compare(1, 1, 0):
        if vol_format == "qed":
            raise error.TestNAError("QED support changed, check bug: "
                                    "https://bugzilla.redhat.com/show_bug.cgi"
                                    "?id=731570")

    # Init snapshot_name
    snapshot_name = None
    snapshot_external_disk = []
    snapshot_xml_path = None
    del_status = None
    image = None
    pvt = None
    # Get a tmp dir
    tmp_dir = data_dir.get_tmp_dir()
    try:
        if snapshot_with_pool:
            # Create dst pool for create attach vol img
            pvt = utlv.PoolVolumeTest(test, params)
            pvt.pre_pool(pool_name, pool_type, pool_target,
                         emulated_image, image_size="1G",
                         pre_disk_vol=["20M"])

            if pool_type in ["iscsi", "disk"]:
                # iscsi and disk pool did not support create volume in libvirt,
                # logical pool could use libvirt to create volume but volume
                # format is not supported and will be 'raw' as default.
                pv = libvirt_storage.PoolVolume(pool_name)
                vols = pv.list_volumes().keys()
                if vols:
                    vol_name = vols[0]
                else:
                    raise error.TestNAError("No volume in pool: %s", pool_name)
            else:
                # Set volume xml file
                volxml = libvirt_xml.VolXML()
                newvol = volxml.new_vol(**vol_arg)
                vol_xml = newvol['xml']

                # Run virsh_vol_create to create vol
                logging.debug("create volume from xml: %s" % newvol.xmltreefile)
                cmd_result = virsh.vol_create(pool_name, vol_xml,
                                              ignore_status=True,
                                              debug=True)
                if cmd_result.exit_status:
                    raise error.TestNAError("Failed to create attach volume.")

            cmd_result = virsh.vol_path(vol_name, pool_name, debug=True)
            if cmd_result.exit_status:
                raise error.TestNAError("Failed to get volume path from pool.")
            img_path = cmd_result.stdout.strip()

            if pool_type in ["logical", "iscsi", "disk"]:
                # Use qemu-img to format logical, iscsi and disk block device
                if vol_format != "raw":
                    cmd = "qemu-img create -f %s %s 10M" % (vol_format,
                                                            img_path)
                    cmd_result = utils.run(cmd, ignore_status=True)
                    if cmd_result.exit_status:
                        raise error.TestNAError("Failed to format volume, %s" %
                                                cmd_result.stdout.strip())
            extra = "--persistent --subdriver %s" % vol_format
        else:
            # Create a image.
            params['image_name'] = "snapshot_test"
            params['image_format'] = image_format
            params['image_size'] = "1M"
            image = qemu_storage.QemuImg(params, tmp_dir, "snapshot_test")
            img_path, _ = image.create(params)
            extra = "--persistent --subdriver %s" % image_format

        # Do the attach action.
        out = utils.run("qemu-img info %s" % img_path)
        logging.debug("The img info is:\n%s" % out.stdout.strip())
        result = virsh.attach_disk(vm_name, source=img_path, target="vdf",
                                   extra=extra, debug=True)
        if result.exit_status:
            raise error.TestNAError("Failed to attach disk %s to VM."
                                    "Detail: %s." % (img_path, result.stderr))

        # Create snapshot.
        if snapshot_from_xml:
            snapshot_name = "snapshot_test"
            lines = ["<domainsnapshot>\n",
                     "<name>%s</name>\n" % snapshot_name,
                     "<description>Snapshot Test</description>\n"]
            if snapshot_memory == "external":
                memory_external = os.path.join(tmp_dir, "snapshot_memory")
                snapshot_external_disk.append(memory_external)
                lines.append("<memory snapshot=\'%s\' file='%s'/>\n" %
                             (snapshot_memory, memory_external))
            else:
                lines.append("<memory snapshot='%s'/>\n" % snapshot_memory)

            # Add all disks into xml file.
            disks = vm.get_disk_devices().values()
            lines.append("<disks>\n")
            for disk in disks:
                lines.append("<disk name='%s' snapshot='%s'>\n" %
                             (disk['source'], snapshot_disk))
                if snapshot_disk == "external":
                    snap_path = "%s.snap" % os.path.basename(disk['source'])
                    disk_external = os.path.join(tmp_dir, snap_path)
                    snapshot_external_disk.append(disk_external)
                    lines.append("<source file='%s'/>\n" % disk_external)
                lines.append("</disk>\n")
            lines.append("</disks>\n")
            lines.append("</domainsnapshot>")

            snapshot_xml_path = "%s/snapshot_xml" % tmp_dir
            snapshot_xml_file = open(snapshot_xml_path, "w")
            snapshot_xml_file.writelines(lines)
            snapshot_xml_file.close()
            logging.debug("The xml content for snapshot create is:")
            with open(snapshot_xml_path, 'r') as fin:
                logging.debug(fin.read())
            snapshot_result = virsh.snapshot_create(
                vm_name, ("--xmlfile %s" % snapshot_xml_path), debug=True)
            out_err = snapshot_result.stderr.strip()
            if snapshot_result.exit_status:
                if status_error:
                    return
                else:
                    if libvirt_version.version_compare(1, 2, 5):
                        # As commit d2e668e in 1.2.5, internal active snapshot
                        # without memory state is rejected. Handle it as SKIP
                        # for now. This could be supportted in future by bug:
                        # https://bugzilla.redhat.com/show_bug.cgi?id=1103063
                        if re.search("internal snapshot of a running VM" +
                                     " must include the memory state",
                                     out_err):
                            raise error.TestNAError("Check Bug #1083345, %s" %
                                                    out_err)
                    raise error.TestFail("Failed to create snapshot. Error:%s."
                                         % out_err)
        else:
            options = ""
            snapshot_result = virsh.snapshot_create(vm_name, options)
            if snapshot_result.exit_status:
                if status_error:
                    return
                else:
                    raise error.TestFail("Failed to create snapshot. Error:%s."
                                         % snapshot_result.stderr.strip())
            snapshot_name = re.search(
                "\d+", snapshot_result.stdout.strip()).group(0)
            if snapshot_current:
                lines = ["<domainsnapshot>\n",
                         "<description>Snapshot Test</description>\n",
                         "<state>running</state>\n",
                         "<creationTime>%s</creationTime>" % snapshot_name,
                         "</domainsnapshot>"]
                snapshot_xml_path = "%s/snapshot_xml" % tmp_dir
                snapshot_xml_file = open(snapshot_xml_path, "w")
                snapshot_xml_file.writelines(lines)
                snapshot_xml_file.close()
                logging.debug("The xml content for snapshot create is:")
                with open(snapshot_xml_path, 'r') as fin:
                    logging.debug(fin.read())
                options += "--redefine %s --current" % snapshot_xml_path
                if snapshot_result.exit_status:
                    raise error.TestFail("Failed to create snapshot --current."
                                         "Error:%s." %
                                         snapshot_result.stderr.strip())

        if status_error:
            if not snapshot_del_test:
                raise error.TestFail("Success to create snapshot in negative"
                                     " case\nDetail: %s" % snapshot_result)

        # Touch a file in VM.
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()

        # Init a unique name for tmp_file.
        tmp_file = tempfile.NamedTemporaryFile(prefix=("snapshot_test_"),
                                               dir="/tmp")
        tmp_file_path = tmp_file.name
        tmp_file.close()

        echo_cmd = "echo SNAPSHOT_DISK_TEST >> %s" % tmp_file_path
        status, output = session.cmd_status_output(echo_cmd)
        logging.debug("The echo output in domain is: '%s'", output)
        if status:
            raise error.TestFail("'%s' run failed with '%s'" %
                                 (tmp_file_path, output))
        status, output = session.cmd_status_output("cat %s" % tmp_file_path)
        logging.debug("File created with content: '%s'", output)

        session.close()

        # Destroy vm for snapshot revert.
        if not libvirt_version.version_compare(1, 2, 3):
            virsh.destroy(vm_name)
        # Revert snapshot.
        revert_options = ""
        if snapshot_revert_paused:
            revert_options += " --paused"
        revert_result = virsh.snapshot_revert(vm_name, snapshot_name,
                                              revert_options,
                                              debug=True)
        if revert_result.exit_status:
            # As commit d410e6f for libvirt 1.2.3, attempts to revert external
            # snapshots will FAIL with an error "revert to external snapshot
            # not supported yet". Thus, let's check for that and handle as a
            # SKIP for now. Check bug:
            # https://bugzilla.redhat.com/show_bug.cgi?id=1071264
            if libvirt_version.version_compare(1, 2, 3):
                if re.search("revert to external snapshot not supported yet",
                             revert_result.stderr):
                    raise error.TestNAError(revert_result.stderr.strip())
            else:
                raise error.TestFail("Revert snapshot failed. %s" %
                                     revert_result.stderr.strip())

        if vm.is_dead():
            raise error.TestFail("Revert snapshot failed.")

        if snapshot_revert_paused:
            if vm.is_paused():
                vm.resume()
            else:
                raise error.TestFail("Revert command successed, but VM is not "
                                     "paused after reverting with --paused"
                                     "  option.")
        # login vm.
        session = vm.wait_for_login()
        # Check the result of revert.
        status, output = session.cmd_status_output("cat %s" % tmp_file_path)
        logging.debug("After revert cat file output='%s'", output)
        if not status:
            raise error.TestFail("Tmp file exists, revert failed.")

        # Close the session.
        session.close()

        # Test delete snapshot without "--metadata", delete external disk
        # snapshot will fail for now.
        # Only do this when snapshot creat succeed which filtered in cfg file.
        if snapshot_del_test:
            if snapshot_name:
                del_result = virsh.snapshot_delete(vm_name, snapshot_name,
                                                   debug=True,
                                                   ignore_status=True)
                del_status = del_result.exit_status
                if del_status:
                    if not status_error:
                        raise error.TestFail("Failed to delete snapshot.")
                else:
                    if status_error:
                        err_msg = "Snapshot delete succeed but expect fail."
                        raise error.TestFail(err_msg)

    finally:
        virsh.detach_disk(vm_name, target="vdf", extra="--persistent")
        if image:
            image.remove()
        if del_status and snapshot_name:
            virsh.snapshot_delete(vm_name, snapshot_name, "--metadata")
        for disk in snapshot_external_disk:
            if os.path.exists(disk):
                os.remove(disk)
        vmxml_backup.sync("--snapshots-metadata")
        if snapshot_xml_path:
            if os.path.exists(snapshot_xml_path):
                os.unlink(snapshot_xml_path)
        if pvt:
            try:
                pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                 emulated_image)
            except error.TestFail, detail:
                logging.error(str(detail))
