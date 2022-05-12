import logging
import re

from avocado.utils import process

from virttest import utils_disk
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test domblkthreshold for domain which has backing chain
    """

    def setup_domblkthreshold_inactivate_layer():
        """
        Prepare backingchain
        """
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test_obj.prepare_snapshot(snap_num=1)

    def test_domblkthreshold_inactivate_layer():
        """
        Do domblkthreshold for a device which is not the active layer image
        """
        # Get backingstore index value and set domblkthreshold
        bs_index = ''
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        for disk in vmxml.devices.by_device_tag('disk'):
            if disk.target['dev'] == primary_target:
                bs_index = disk.xmltreefile.find('backingStore').get('index')

        virsh.domblkthreshold(vm_name, '%s[%s]' % (primary_target, bs_index),
                              domblk_threshold, debug=True, ignore_status=False)

        # Create some data in active layer image
        session = vm.wait_for_login()
        utils_disk.dd_data_to_vm_disk(session, '/tmp/file', bs='1M', count='100')
        session.close()

        # Check blockcommit will trigger threshold event
        event = r"\'block-threshold\' for domain .*%s.*: dev: %s\[%s\].*%s.*" \
                % (vm_name, primary_target, bs_index, domblk_threshold)
        LOG.debug('Checking event pattern is :%s ', event)

        event_session = virsh.EventTracker.start_get_event(vm_name)
        virsh.blockcommit(vm.name, primary_target, commit_options,
                          ignore_status=False, debug=True)
        event_output = virsh.EventTracker.finish_get_event(event_session)
        if not re.search(event, event_output):
            test.fail('Not find: %s from event output:%s' % (event,
                                                             event_output))

    def teardown_domblkthreshold_inactivate_layer():
        """
        Clean env
        """
        test_obj.backingchain_common_teardown()

        process.run('rm -f %s' % test_obj.new_image_path)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case_name = params.get('case_name', '')
    domblk_threshold = params.get('domblk_threshold')
    commit_options = params.get('commit_options')

    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    primary_target = vm.get_first_disk_devices()["target"]
    test_obj.new_dev = primary_target
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)

    run_test = eval("test_%s" % case_name)
    setup_test = eval("setup_%s" % case_name)
    teardown_test = eval("teardown_%s" % case_name)

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        teardown_test()
        bkxml.sync()
