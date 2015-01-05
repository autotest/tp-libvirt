import os
import signal
from autotest.client import utils
from autotest.client.shared import error
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Kill started qemu VM process with different signals and check
    the status of the VM changes accordingly.
    """
    vm_name = params.get('main_vm')
    sig_name = params.get('signal', 'SIGSTOP')
    vm_state = params.get('vm_state', 'running')
    expect_stop = params.get('expect_stop', 'yes') == 'yes'
    vm = env.get_vm(vm_name)

    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        if vm_state == 'running':
            pass
        elif vm_state == 'paused':
            vm.pause()
        elif vm_state == 'pmsuspended':
            vm.prepare_guest_agent()
            vm.pmsuspend()
        else:
            raise error.TestError("Unhandled VM state %s" % vm_state)

        os.kill(vm.get_pid(), getattr(signal, sig_name))

        stopped = bool(utils.wait_for(lambda: vm.state() == 'shut off', 10))
        if stopped != expect_stop:
            raise error.TestFail('Expected VM stop is "%s", got "%s"'
                                 % (expect_stop, vm.state()))
    finally:
        xml_backup.sync()
