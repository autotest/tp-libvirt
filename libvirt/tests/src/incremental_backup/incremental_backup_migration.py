import json
import logging
import os

from virttest import data_dir
from virttest import migration_template as mt
from virttest import utils_backup
from virttest import utils_disk
from virttest import virsh

from virttest.utils_test import libvirt


class MigrationWithCheckpoint(mt.MigrationTemplate):
    """
    The class of the migration test
    """
    virsh_dargs = {'debug': True, 'ignore_status': False}

    def __init__(self, test, env, params, *args, **dargs):
        """
        Init params and other necessary variables
        """
        super(MigrationWithCheckpoint, self).__init__(test, env, params, *args,
                                                      **dargs)
        self.checkpoint_names = []
        self.source_vm_checkpoints = []
        self.remote_vm_checkpoints = []
        self.test_disk_path = ''
        self.checkpoint_rounds = int(params.get('checkpoint_rounds', 2))
        self.test_disk_target = params.get('test_disk_target', 'vdb')
        self.test_disk_size = params.get('test_disk_size', '100M')
        self.tmp_dir = data_dir.get_data_dir()

    def add_test_disk_to_vm(self, vm):
        """
        Attach a disk to vm

        :param vm: The vm to attch disk to
        """
        test_disk_path = os.path.join(self.tmp_dir,
                                      self.test_disk_target + '.qcow2')
        libvirt.create_local_disk('file', test_disk_path,
                                  self.test_disk_size, 'qcow2')
        test_disk_params = {'device_type': 'disk',
                            'type_name': 'file',
                            'driver_type': 'qcow2',
                            'target_dev': self.test_disk_target,
                            'source_file': test_disk_path}
        test_disk_xml = libvirt.create_disk_xml(test_disk_params)
        virsh.attach_device(vm.name, test_disk_xml,
                            flagstr='--config', debug=True)
        self.test_disk_path = test_disk_path

    def create_checkpoint(self, vm, test_disk, checkpoint_name):
        """
        Create checkpoint to vm's specific disk

        :param vm: The vm to be operated
        :param test_disk: The vm's disk to be operated
        :param checkpoint_name: The name of checkpoint to be created
        """
        options = ('{0} --diskspec vda,checkpoint=no --diskspec {1},'
                   'checkpoint=bitmap,bitmap={0}'.format(checkpoint_name,
                                                         test_disk))
        virsh.checkpoint_create_as(vm.name, options, **self.virsh_dargs)

    def generate_data_in_test_disk(self, vm, test_disk, offset):
        """
        Generate 1M random data to vm's disk

        :param vm: The vm to be operated
        :param test_disk: The vm's disk to be operated
        :param offset: Generate data to specific offset of the disk
        """
        # Create some random data in test disk inside vm
        dd_count = '1'
        dd_bs = '1M'
        session = vm.wait_for_login()
        utils_disk.dd_data_to_vm_disk(session, test_disk, dd_bs,
                                      offset, dd_count)
        session.close()

    def get_checkpoint_hash_value(self, vm, test_disk_source_file, checkpoint_name):
        """
        Get the hash value of vm disk's checkpoint

        :param vm: The vm to be operated
        :param test_disk_source_file: The source file of the test disk
        :param checkpoint_name: From which checkpoint to retrieve the hash value
        :return: The checkpoint's hash value
        """
        virsh_instance = virsh.Virsh(uri=vm.connect_uri)

        def get_disk_node_name():
            """
            Get the node name for the test disk
            :return: The node name of the test disk
            """
            cmd = '{\"execute\":\"query-block\"}'
            result = virsh_instance.qemu_monitor_command(vm.name, cmd, '--pretty',
                                                         **self.virsh_dargs).stdout_text.strip()
            json_result = json.loads(result)
            blk_dev_list = json_result['return']
            for blk_dev in blk_dev_list:
                if test_disk_source_file == blk_dev['inserted']['file']:
                    if (checkpoint_name not in
                        [sub['name'] for sub in
                         blk_dev['inserted']['dirty-bitmaps']]):
                        self.test.fail('test image doesn\'t have checkpoint %s'
                                       % checkpoint_name)
                    return blk_dev['inserted']['node-name']

        disk_node_name = get_disk_node_name()
        logging.debug('test disk node name is: %s' % disk_node_name)
        if not disk_node_name:
            self.test.fail('%s not used by vm' % test_disk_source_file)
        cmd = ('{{\"execute\": \"x-debug-block-dirty-bitmap-sha256\",'
               '\"arguments\": {{\"node\":\"{0}\",'
               '\"name\":\"{1}\"}}}}').format(disk_node_name, checkpoint_name)
        result = virsh_instance.qemu_monitor_command(vm.name, cmd, '--pretty',
                                                     **self.virsh_dargs).stdout_text.strip()
        json_result = json.loads(result)
        return json_result['return']['sha256']

    def _pre_start_vm(self):
        """
        The steps to be executed before vm started
        """
        # Steps:
        # 1. Clean any existing checkponits
        # 2. Add test disk to the vm
        utils_backup.clean_checkpoints(self.main_vm.name)
        self.add_test_disk_to_vm(self.main_vm)

    def _post_start_vm(self):
        """
        The steps to be executed after vm started
        """
        # Steps:
        # 1. Create Checkpoint for test disk
        # 2. Generate 1M random data in that test disk
        # 3. Repeate 1~2 as required
        # 4. Get all checkpoints' hash values

        # create checkpoints and generate some random data in test disk
        for checkpoint_index in range(self.checkpoint_rounds):
            checkpoint_name = 'ck_' + str(checkpoint_index)
            self.create_checkpoint(self.main_vm, 'vdb', checkpoint_name)
            session = self.main_vm.wait_for_login()
            test_disk_in_vm = list(utils_disk.get_linux_disks(session).keys())[0]
            session.close()
            test_disk_in_vm = '/dev/' + test_disk_in_vm
            self.generate_data_in_test_disk(self.main_vm, test_disk_in_vm,
                                            checkpoint_index * 10 + 10)
            self.checkpoint_names.append(checkpoint_name)

        # get all checkpoints' hash value
        for checkpoint in self.checkpoint_names:
            checkpoint_info = {}
            checkpoint_info['name'] = checkpoint
            checkpoint_info['hash'] = self.get_checkpoint_hash_value(self.main_vm,
                                                                     self.test_disk_path,
                                                                     checkpoint)
            self.source_vm_checkpoints.append(checkpoint_info.copy())

    def _post_migrate(self):
        """
        The steps to be executed after migration
        """
        # Steps:
        # 1. Get all checkpoints' hash values of the migrated vm
        # 2. Compare the vm's checkpoints' hash values before and after the
        #    migration
        def compare_checkpoint_hash_values(source_vm_checkpoints,
                                           remote_vm_checkpoints):
            """
            Compare two sets of checkpoints' hash values
            """
            checkpoints = source_vm_checkpoints + remote_vm_checkpoints
            for checkpoint in checkpoints:
                if ((checkpoint not in source_vm_checkpoints) or
                        (checkpoint not in remote_vm_checkpoints)):
                    self.test.fail('source checkpoints and remote checkpoints '
                                   'are not identical.\n'
                                   'source checkpoints info: {0}\n'
                                   'remote checkpoints info: {1}\n'
                                   ''.format(source_vm_checkpoints,
                                             remote_vm_checkpoints))
            logging.debug('source checkpoints info: {0}\n'
                          'remote checkpoints info: {1}'.format(source_vm_checkpoints,
                                                                remote_vm_checkpoints))

        for checkpoint in self.checkpoint_names:
            checkpoint_info = {}
            checkpoint_info['name'] = checkpoint
            checkpoint_info['hash'] = self.get_checkpoint_hash_value(self.main_vm,
                                                                     self.test_disk_path,
                                                                     checkpoint)
            self.remote_vm_checkpoints.append(checkpoint_info.copy())
        compare_checkpoint_hash_values(self.remote_vm_checkpoints,
                                       self.source_vm_checkpoints)


def run(test, params, env):
    """
    Run test steps
    """
    mig_obj = MigrationWithCheckpoint(test, env, params)
    try:
        mig_obj.runtest()
    finally:
        mig_obj.cleanup()
