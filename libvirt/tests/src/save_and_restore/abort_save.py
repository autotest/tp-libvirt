import logging
import re
import os

from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.save import save_base

LOG = logging.getLogger('avocado.test.' + __name__)


def run(test, params, env):
    """
    Test the scenario to abort the vm save process
    Steps:
    1. Start the vm, run stress in the vm to slow down the save process;
    2. Run "virsh save" to save the vm;
    3. During the save process, run domjobabort;
    4. Check the VM's states after the abort operation;
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vm.start()
        session = vm.wait_for_login()
        pid_ping, upsince = save_base.pre_save_setup(vm)
        LOG.debug('TEST_STEP1: run stress on the vm:')
        sh_cmd1 = "dnf install -y stress"
        session.cmd(sh_cmd1, ignore_all_errors=True)
        sh_cmd2 = "stress --cpu 8 --io 4 --vm 2 --vm-bytes 128M --vm-keep"
        session.cmd(sh_cmd2, ignore_all_errors=True, timeout=10)
        save_path = os.path.join(data_dir.get_tmp_dir(), 'rhel.save')
        LOG.debug('TEST_STEP2: Save the VM:')
        cmd = "save %s %s" % (vm_name, save_path)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)
        LOG.debug('TEST_STEP3: Abort the save process')
        # check if the save process is succeed, if save succeed, cancel the test
        st = virsh.domjobinfo(vm_name, ignore_status=True).stdout_text.strip()
        LOG.debug("domjobinfo: %s", st)
        if not re.search("Unbounded", st):
            test.cancel("Test cancel since save process completed before abort.")
        virsh.domjobabort(vm_name)
        LOG.debug("vm state is %s after abort the save process", vm.state())
        if vm.state() != 'running':
            test.fail(f'VM should be running after abort restore, not {vm.state()}')
        save_base.post_save_check(vm, pid_ping, upsince)
        LOG.debug("TEST_STEP4: Check the VM's state details after save abort")
        outputs = virsh.domstate(vm_name, "--reason").stdout_text.strip()
        LOG.debug("vm state detail is %s", outputs)
        if not re.search("save canceled", outputs):
            test.fail("There is no 'save canceled' words in the domstate outputs!")
        virsh.shutdown(vm_name, debug=True, ignore_status=False)
    finally:
        virsh_session.close()
        bkxml.sync()
        if os.path.exists(save_path):
            os.remove(save_path)
