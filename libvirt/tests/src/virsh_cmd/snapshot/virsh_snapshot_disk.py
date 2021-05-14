import os
import logging
import re
import tempfile

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import qemu_storage
from virttest import data_dir
from virttest import libvirt_xml
from virttest import libvirt_storage
from virttest import utils_libvirtd
from virttest import gluster
from virttest.utils_test import libvirt as utlv

from virttest import libvirt_version


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
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vm_state = params.get("vm_state", "running")
    image_format = params.get("snapshot_image_format", "qcow2")
    snapshot_del_test = "yes" == params.get("snapshot_del_test", "no")
    disk_cluster_size_set = "yes" == params.get("disk_cluster_size_set", "no")
    status_error = ("yes" == params.get("status_error", "no"))
    snapshot_from_xml = ("yes" == params.get("snapshot_from_xml", "no"))
    snapshot_current = ("yes" == params.get("snapshot_current", "no"))
    snapshot_revert_paused = ("yes" == params.get("snapshot_revert_paused",
                                                  "no"))
    replace_vm_disk = "yes" == params.get("replace_vm_disk", "no")
    disk_source_protocol = params.get("disk_source_protocol")
    vol_name = params.get("vol_name")
    tmp_dir = data_dir.get_data_dir()
    pool_name = params.get("pool_name", "gluster-pool")
    brick_path = os.path.join(tmp_dir, pool_name)
    multi_gluster_disks = "yes" == params.get("multi_gluster_disks", "no")
    transport = params.get("transport", "")

    # Pool variables.
    snapshot_with_pool = "yes" == params.get("snapshot_with_pool", "no")
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    emulated_image = params.get("emulated_image", "emulated-image")
    vol_format = params.get("vol_format")
    lazy_refcounts = "yes" == params.get("lazy_refcounts")
    options = params.get("snapshot_options", "")
    export_options = params.get("export_options", "rw,no_root_squash")

    # Set volume xml attribute dictionary, extract all params start with 'vol_'
    # which are for setting volume xml, except 'lazy_refcounts'.
    vol_arg = {}
    for key in list(params.keys()):
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
            test.cancel("%s not in support list %s" %
                        (pool_target, supported_pool_list))

    # Do xml backup for final recovery
    vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Some variable for xmlfile of snapshot.
    snapshot_memory = params.get("snapshot_memory", "internal")
    snapshot_disk = params.get("snapshot_disk", "internal")
    no_memory_snap = "yes" == params.get("no_memory_snap", "no")

    # Skip 'qed' cases for libvirt version greater than 1.1.0
    if libvirt_version.version_compare(1, 1, 0):
        if vol_format == "qed" or image_format == "qed":
            test.cancel("QED support changed, check bug: "
                        "https://bugzilla.redhat.com/show_bug.cgi"
                        "?id=731570")

    if not libvirt_version.version_compare(1, 2, 7):
        # As bug 1017289 closed as WONTFIX, the support only
        # exist on 1.2.7 and higher
        if disk_source_protocol == 'gluster':
            test.cancel("Snapshot on glusterfs not support in "
                        "current version. Check more info with "
                        "https://bugzilla.redhat.com/buglist.cgi?"
                        "bug_id=1017289,1032370")

    if disk_cluster_size_set and not libvirt_version.version_compare(6, 10, 0):
        test.cancel("current libvirt version doesn't support the feature that "
                    "snapshot and backing file share the cluster size ")

    # Init snapshot_name
    snapshot_name = None
    snapshot_external_disk = []
    snapshot_xml_path = None
    del_status = None
    image = None
    pvt = None
    # Get a tmp dir
    snap_cfg_path = "/var/lib/libvirt/qemu/snapshot/%s/" % vm_name
    try:
        if vm.is_dead():
            vm.start()
        vm.wait_for_login().close()
        if replace_vm_disk:
            utlv.set_vm_disk(vm, params, tmp_dir)
            if multi_gluster_disks:
                new_params = params.copy()
                new_params["pool_name"] = "gluster-pool2"
                new_params["vol_name"] = "gluster-vol2"
                new_params["disk_target"] = "vdf"
                new_params["image_convert"] = 'no'
                utlv.set_vm_disk(vm, new_params, tmp_dir)

        if snapshot_with_pool:
            # Create dst pool for create attach vol img
            pvt = utlv.PoolVolumeTest(test, params)
            kwargs = params.copy()
            update_items = {"image_size": "1G", "emulated_image": emulated_image,
                            "source_name": vol_name, "pre_disk_vol": ["20M"],
                            "export_options": export_options}
            kwargs.update(update_items)
            kwargs.pop("vol_name")
            pvt.pre_pool(**kwargs)

            if pool_type in ["iscsi", "disk"]:
                # iscsi and disk pool did not support create volume in libvirt,
                # logical pool could use libvirt to create volume but volume
                # format is not supported and will be 'raw' as default.
                pv = libvirt_storage.PoolVolume(pool_name)
                vols = list(pv.list_volumes().keys())
                if vols:
                    vol_name = vols[0]
                else:
                    test.cancel("No volume in pool: %s" % pool_name)
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
                    test.cancel("Failed to create attach volume.")

            cmd_result = virsh.vol_path(vol_name, pool_name, debug=True)
            if cmd_result.exit_status:
                test.cancel("Failed to get volume path from pool.")
            img_path = cmd_result.stdout.strip()

            if pool_type in ["logical", "iscsi", "disk"]:
                # Use qemu-img to format logical, iscsi and disk block device
                if vol_format != "raw":
                    cmd = "qemu-img create -f %s %s 10M" % (vol_format,
                                                            img_path)
                    cmd_result = process.run(cmd, ignore_status=True, shell=True)
                    if cmd_result.exit_status:
                        test.cancel("Failed to format volume, %s" %
                                    cmd_result.stdout_text.strip())
            extra = "--persistent --subdriver %s" % vol_format
        else:
            # Create a image.
            params['image_name'] = "snapshot_test"
            params['image_format'] = image_format
            params['image_size'] = "1M"
            image = qemu_storage.QemuImg(params, tmp_dir, "snapshot_test")
            img_path, _ = image.create(params)
            extra = "--persistent --subdriver %s" % image_format

        if not multi_gluster_disks:
            # Do the attach action.
            out = process.run("qemu-img info %s" % img_path, shell=True)
            logging.debug("The img info is:\n%s" % out.stdout.strip())
            result = virsh.attach_disk(vm_name, source=img_path, target="vdf",
                                       extra=extra, debug=True)
            if result.exit_status:
                test.error("Failed to attach disk %s to VM."
                           "Detail: %s." % (img_path, result.stderr))

        # Create snapshot.
        if snapshot_from_xml:
            snap_xml = libvirt_xml.SnapshotXML()
            snapshot_name = "snapshot_test"
            snap_xml.snap_name = snapshot_name
            snap_xml.description = "Snapshot Test"
            if not no_memory_snap:
                if "--disk-only" not in options:
                    if snapshot_memory == "external":
                        memory_external = os.path.join(tmp_dir,
                                                       "snapshot_memory")
                        snap_xml.mem_snap_type = snapshot_memory
                        snap_xml.mem_file = memory_external
                        snapshot_external_disk.append(memory_external)
                    else:
                        snap_xml.mem_snap_type = snapshot_memory

            # Add all disks into xml file.
            vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            disks = vmxml.devices.by_device_tag('disk')
            # Remove non-storage disk such as 'cdrom'
            for disk in disks:
                if disk.device != 'disk':
                    disks.remove(disk)
            new_disks = []
            for src_disk_xml in disks:
                disk_xml = snap_xml.SnapDiskXML()
                disk_xml.xmltreefile = src_disk_xml.xmltreefile
                del disk_xml.device
                del disk_xml.address
                disk_xml.snapshot = snapshot_disk
                disk_xml.disk_name = disk_xml.target['dev']

                # Only qcow2 works as external snapshot file format, update it
                # here
                driver_attr = disk_xml.driver
                driver_attr.update({'type': 'qcow2'})
                disk_xml.driver = driver_attr

                if snapshot_disk == 'external':
                    new_attrs = disk_xml.source.attrs
                    if 'file' in disk_xml.source.attrs:
                        new_file = "%s.snap" % disk_xml.source.attrs['file']
                        snapshot_external_disk.append(new_file)
                        new_attrs.update({'file': new_file})
                        hosts = None
                    elif 'name' in disk_xml.source.attrs:
                        new_name = "%s.snap" % disk_xml.source.attrs['name']
                        new_attrs.update({'name': new_name})
                        hosts = disk_xml.source.hosts
                    elif ('dev' in disk_xml.source.attrs and
                          disk_xml.type_name == 'block'):
                        # Use local file as external snapshot target for block type.
                        # As block device will be treat as raw format by default,
                        # it's not fit for external disk snapshot target. A work
                        # around solution is use qemu-img again with the target.
                        disk_xml.type_name = 'file'
                        del new_attrs['dev']
                        new_file = "%s/blk_src_file.snap" % tmp_dir
                        snapshot_external_disk.append(new_file)
                        new_attrs.update({'file': new_file})
                        hosts = None

                    new_src_dict = {"attrs": new_attrs}
                    if hosts:
                        new_src_dict.update({"hosts": hosts})
                    disk_xml.source = disk_xml.new_disk_source(**new_src_dict)
                else:
                    del disk_xml.source

                new_disks.append(disk_xml)

            snap_xml.set_disks(new_disks)
            snapshot_xml_path = snap_xml.xml
            logging.debug("The snapshot xml is: %s" % snap_xml.xmltreefile)

            options += " --xmlfile %s " % snapshot_xml_path

            if vm_state == "shut off":
                vm.destroy(gracefully=False)

            snapshot_result = virsh.snapshot_create(
                vm_name, options, debug=True)
            out_err = snapshot_result.stderr.strip()
            if snapshot_result.exit_status:
                if status_error:
                    return
                else:
                    if re.search("live disk snapshot not supported with this "
                                 "QEMU binary", out_err):
                        test.cancel(out_err)

                    if libvirt_version.version_compare(1, 2, 5):
                        # As commit d2e668e in 1.2.5, internal active snapshot
                        # without memory state is rejected. Handle it as SKIP
                        # for now. This could be supportted in future by bug:
                        # https://bugzilla.redhat.com/show_bug.cgi?id=1103063
                        if re.search("internal snapshot of a running VM" +
                                     " must include the memory state",
                                     out_err):
                            logging.warning("Got expected error. Please check "
                                            "Bug #1083345, %s" % out_err)
                            return

                    test.fail("Failed to create snapshot. Error:%s."
                              % out_err)
            if disk_cluster_size_set:
                disk_path, _ = vm.get_device_size('vdf')
                img_info = process.run("qemu-img info -U %s" % img_path, verbose=True, shell=True).stdout_text.strip()
                match_cluster_size = "cluster_size: %s" % params.get("image_cluster_size")
                if match_cluster_size not in img_info:
                    test.fail("%s snapshot image doesn't have the same cluster size with backing file" % disk_path)
        else:
            snapshot_result = virsh.snapshot_create(vm_name, options,
                                                    debug=True)
            if snapshot_result.exit_status:
                if status_error:
                    return
                else:
                    test.fail("Failed to create snapshot. Error:%s."
                              % snapshot_result.stderr.strip())
            snapshot_name = re.search(
                "\d+", snapshot_result.stdout.strip()).group(0)

            if snapshot_current:
                snap_xml = libvirt_xml.SnapshotXML()
                new_snap = snap_xml.new_from_snapshot_dumpxml(vm_name,
                                                              snapshot_name)
                # update an element
                new_snap.creation_time = snapshot_name
                snapshot_xml_path = new_snap.xml
                options += "--redefine %s --current" % snapshot_xml_path
                snapshot_result = virsh.snapshot_create(vm_name,
                                                        options, debug=True)
                if snapshot_result.exit_status:
                    test.fail("Failed to create snapshot --current."
                              "Error:%s." %
                              snapshot_result.stderr.strip())

        if status_error:
            if not snapshot_del_test:
                test.fail("Success to create snapshot in negative"
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
            test.fail("'%s' run failed with '%s'" %
                      (tmp_file_path, output))
        status, output = session.cmd_status_output("cat %s" % tmp_file_path)
        logging.debug("File created with content: '%s'", output)

        session.close()

        # As only internal snapshot revert works now, let's only do revert
        # with internal, and move the all skip external cases back to pass.
        # After external also supported, just move the following code back.
        if snapshot_disk == 'internal':
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
                # Attempts to revert external snapshots will FAIL with an error
                # "revert to external disk snapshot not supported yet" or "revert
                # to external snapshot not supported yet" since d410e6f. Thus,
                # let's check for that and handle as a SKIP for now. Check bug:
                # https://bugzilla.redhat.com/show_bug.cgi?id=1071264
                if re.search("revert to external \w* ?snapshot not supported yet",
                             revert_result.stderr):
                    test.cancel(revert_result.stderr.strip())
                else:
                    test.fail("Revert snapshot failed. %s" %
                              revert_result.stderr.strip())

            if vm.is_dead():
                test.fail("Revert snapshot failed.")

            if snapshot_revert_paused:
                if vm.is_paused():
                    vm.resume()
                else:
                    test.fail("Revert command successed, but VM is not "
                              "paused after reverting with --paused"
                              "  option.")
            # login vm.
            session = vm.wait_for_login()
            # Check the result of revert.
            status, output = session.cmd_status_output("cat %s" % tmp_file_path)
            logging.debug("After revert cat file output='%s'", output)
            if not status:
                test.fail("Tmp file exists, revert failed.")

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
                snap_xml_path = snap_cfg_path + "%s.xml" % snapshot_name
                if del_status:
                    if not status_error:
                        test.fail("Failed to delete snapshot.")
                    else:
                        if not os.path.exists(snap_xml_path):
                            test.fail("Snapshot xml file %s missing"
                                      % snap_xml_path)
                else:
                    if status_error:
                        err_msg = "Snapshot delete succeed but expect fail."
                        test.fail(err_msg)
                    else:
                        if os.path.exists(snap_xml_path):
                            test.fail("Snapshot xml file %s still"
                                      % snap_xml_path + " exist")

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        virsh.detach_disk(vm_name, target="vdf", extra="--persistent")
        if image:
            image.remove()
        if del_status and snapshot_name:
            virsh.snapshot_delete(vm_name, snapshot_name, "--metadata")
        for disk in snapshot_external_disk:
            if os.path.exists(disk):
                os.remove(disk)
        vmxml_backup.sync("--snapshots-metadata")

        libvirtd = utils_libvirtd.Libvirtd()
        if disk_source_protocol == 'gluster':
            gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **params)
            if multi_gluster_disks:
                brick_path = os.path.join(tmp_dir, "gluster-pool2")
                mul_kwargs = params.copy()
                mul_kwargs.update({"vol_name": "gluster-vol2"})
                gluster.setup_or_cleanup_gluster(False, brick_path=brick_path, **mul_kwargs)
            libvirtd.restart()

        if snapshot_xml_path:
            if os.path.exists(snapshot_xml_path):
                os.unlink(snapshot_xml_path)
        if pvt:
            try:
                pvt.cleanup_pool(**kwargs)
            except exceptions.TestFail as detail:
                libvirtd.restart()
                logging.error(str(detail))
