import os
import shutil

from avocado.utils import process

from virttest import test_setup
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.vm_xml import VMXML


def run(test, params, env):
    """
    Test libvirt creates all the files which could be accessed by qemu user
    """
    umask_value = params.get("umask_value")
    hp_path = params.get('hp_path', "/dev/hugepages/libvirt")

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    org_umask = process.run('umask', verbose=True).stdout_text.strip()
    try:
        test.log.info(f"TEST_STEP: Set umask to {umask_value}.")
        process.run('umask %s' % umask_value)

        test.log.info(f"TEST_STEP: Delete {hp_path} and create hugepages in the host.")
        if os.path.exists(hp_path):
            shutil.rmtree(hp_path)
        hp_cfg = test_setup.HugePageConfig(params)
        hp_cfg.set_hugepages()

        test.log.info("TEST_STEP: Update VM's hugepages setting.")
        mem_backing = vm_xml.VMMemBackingXML()
        mem_backing_attrs = eval(params.get('mem_backing_attrs', '{}'))
        mem_backing.setup_attrs(**mem_backing_attrs)
        test.log.debug('memoryBacking xml is: %s', mem_backing)
        vmxml.mb = mem_backing
        vmxml.sync()
        test.log.debug(f"vmxml: {vmxml}")

        test.log.info("TEST_STEP: Start the VM.")
        vm.start()

        test.log.info(f"TEST_STEP: Check permission of {hp_path}.")
        stat_re = os.lstat(hp_path)
        if stat_re.st_mode != 16877:
            test.fail(f'Incorrect permission of {hp_path}!, it should be 755, but got {oct(stat_re.st_mode)}')

    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        backup_xml.sync()

        process.run('umask %s' % org_umask)
