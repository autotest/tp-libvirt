import logging
import os
import re

from virttest import virsh
from virttest import utils_test
from virttest.libvirt_xml import vm_xml

from provider.save import save_base

LOG = logging.getLogger('avocado.test.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test the scenario to abort the vm ManagedSave process
    Steps:
    1. Start the vm, run stress in the vm to slow down the ManagedSave process;
    2. Run "virsh managedsave" to save the vm;
    3. During the managedsave process, run domjobabort;
    4. Check the VM's states after the abort operation and check the events;
    """

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    default_path = params.get('default_path')
    file_path = default_path + vm_name + '.save'
    stress_package = params.get("stress_package", "stress")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    event_cmd = params.get("event_cmd")
    expected_event = eval(params.get('expected_event'))
    try:
        vm.start()
        pid_ping, upsince = save_base.pre_save_setup(vm)
        LOG.debug('TEST_STEP1: run stress on the vm:')
        vm_stress = utils_test.VMStress(vm, stress_package, params)
        vm_stress.load_stress_tool()
        LOG.debug('TEST_STEP2: ManagedSave the VM:')
        # Start event session to catch the events
        event_session = virsh.EventTracker.start_get_event(vm_name, event_cmd=event_cmd)
        cmd = "managedsave %s" % vm_name
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=False)
        virsh_session.sendline(cmd)
        LOG.debug('TEST_STEP3: Abort the ManagedSave process')
        # check if the save process is succeed, cancel the test if save succeed
        st = virsh.domjobinfo(vm_name, ignore_status=True).stdout_text.strip()
        LOG.debug("domjobinfo: %s", st)
        if not re.search("Unbounded", st):
            test.cancel("Test cancel since managedsave process completed before abort.")
        virsh.domjobabort(vm_name).stdout_text.strip()
        LOG.debug("Check the VM's state details after save abort")
        save_base.post_save_check(vm, pid_ping, upsince)
        # check the events for abort
        LOG.debug("TEST_STEP4: Check the event:")
        event_output = virsh.EventTracker.finish_get_event(event_session)
        for event in expected_event:
            if not re.search(event, event_output):
                test.fail('Not find: %s from event output:%s' % (event, event_output))
        # check VM states details
        outputs_ = virsh.domstate(vm_name, "--reason").stdout_text.strip()
        LOG.debug("TEST_STEP5: check the domstate: %s and ensure no saved file", outputs_)
        if not re.search("save canceled", outputs_):
            test.fail("There is no 'save canceled' words in the domstate outputs!")
        if os.path.exists(file_path):
            test.fail("There should not be the save file since managedsave aborted")
        virsh_session.close()
        virsh.shutdown(vm_name, **VIRSH_ARGS)
    finally:
        bkxml.sync()
        if os.path.exists(file_path):
            os.remove(file_path)
