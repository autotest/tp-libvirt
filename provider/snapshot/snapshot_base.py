#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

from virttest import virsh
from virttest.libvirt_xml import snapshot_xml
from virttest.utils_test import libvirt


class SnapshotTest(object):
    """
    Utility class for snapshot tests
    """
    def __init__(self, vm, test, params):
        self.vm = vm
        self.test = test
        self.params = params
        self.virsh_dargs = {'ignore_status': False, 'debug': True}

    def check_snap_list(self, snap_name, options='', expect_exist=True):
        """
        Check snapshot list

        :param snap_name: str, snapshot name to be checked
        :param options: str, options for virsh snapshot-list
        :param expect_exist: boolean, True if expect the snapshot exist,
                                      otherswise, False
        """
        snap_names = virsh.snapshot_list(self.vm.name,
                                         options=options,
                                         **self.virsh_dargs)
        actual_result = snap_name in snap_names
        if expect_exist != actual_result:
            self.test.fail("The snapshot '%s' should '%s' "
                           "exist" % (snap_name,
                                      '' if expect_exist else 'not'))

    def create_snapshot_by_xml(self, snap_dict, snap_disk_list, options=''):
        """
        Create a snapshot of the vm

        :param snap_dict: dict, the parameters of the snapshot
        :param snap_disk_list: list, the disks' parameters for the snapshot
        :param options: str, options for virsh snapshot-create
        """
        snap_obj = snapshot_xml.SnapshotXML()
        snap_obj.setup_attrs(**snap_dict)
        snap_disks = []
        for snap_disk_dict in snap_disk_list:
            disk_obj = snap_obj.SnapDiskXML()
            disk_obj.setup_attrs(**snap_disk_dict)
            snap_disks.append(disk_obj)
        snap_obj.set_disks(snap_disks)
        snap_file = snap_obj.xml
        snap_options = " %s %s" % (snap_file, options)
        virsh.snapshot_create(self.vm.name, snap_options, **self.virsh_dargs)
        virsh.snapshot_dumpxml(self.vm.name, snap_dict['snap_name'], **self.virsh_dargs)

    def check_current_snapshot(self, snap_name):
        """
        Check vm current snapshot name is expected

        :param snap_name, expected snapshot name.
        """
        current_name = virsh.snapshot_current(
            self.vm.name, **self.virsh_dargs).stdout.strip()
        if current_name != snap_name:
            self.test.fail("Current snapshot name is %s, should be %s" % (
                current_name, snap_name))

    def delete_snapshot(self, snap_names, options=''):
        """
        Delete snapshots of the vm

        :param snap_names: list or str, the snapshot names
        :param options: str, options for virsh snapshot-delete
        """
        if isinstance(snap_names, str):
            snap_names = [snap_names]
        for snap_name in snap_names:
            virsh.snapshot_delete(self.vm.name,
                                  snap_name,
                                  options=options,
                                  **self.virsh_dargs)

    def teardown_test(self):
        """
        Basic teardown steps for the test
        """
        backup_vmxml = self.params.get('backup_vmxml')
        if self.vm.is_alive():
            self.vm.destroy()
        libvirt.clean_up_snapshots(self.vm.name)
        if backup_vmxml:
            backup_vmxml.sync('--nvram')
