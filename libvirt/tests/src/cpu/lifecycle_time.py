from time import sleep
import logging as log

from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh


logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test that the clock works after some action

    :param test: test object
    :param params: Dict with the test parameters
    :param env: Dict with the test environment
    :return:
    """
    vm_name = params.get("main_vm")

    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    sleep_before_resume = int(params.get('sleep_before_resume', 5))

    try:
        session = vm.wait_for_login()
        session.close()

        virsh.suspend(vm_name)
        sleep(sleep_before_resume)
        virsh.resume(vm_name)

        session = vm.wait_for_login()
        cmd = 'time sleep 1'
        out = session.cmd_output(cmd, print_func=logging.debug)
        session.close()

        lines = out.split('\n')
        if len(lines) < 2 or 'real\t0m1.0' not in lines[1]:
            test.fail("VM seems to have slept longer than expected: %s" % out)
    except Exception as e:
        test.error("Test error: %s" % e)
    finally:
        vm.destroy()
        vmxml_backup.sync()
