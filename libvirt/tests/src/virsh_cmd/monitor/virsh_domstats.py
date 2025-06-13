import logging as log
import re

from aexpect import ShellTimeoutError
from aexpect import ShellProcessTerminatedError

from multiprocessing.pool import ThreadPool

from virttest import utils_disk
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic

from virttest import libvirt_version
from avocado.utils import process

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def create_snap(vm_name, snap_option):
    """
    Create snap using snap options

    :param vm_name: VM name
    :param snap_option: snap option
    :return: snapshot created
    """
    cmd_result = virsh.snapshot_create_as(vm_name, snap_option,
                                          ignore_status=True, debug=True)
    libvirt.check_exit_status(cmd_result)
    return cmd_result


def prepare_vm_state(vm, vm_state):
    """
    Prepare the given state of the domain.

    :param vm: Libvirt VM instance
    :param vm_state: Domain state to set
    """
    if vm_state == "active":
        if not vm.is_alive():
            vm.start()
    elif vm_state == "inactive":
        if vm.is_alive():
            vm.destroy()
    elif vm_state == "persistent":
        if not vm.is_persistent():
            vm.define(vm.backup_xml())
    elif vm_state == "transient":
        if not vm.is_alive():
            vm.start()
        if vm.is_persistent():
            vm.undefine()
    elif vm_state == "running":
        if vm.state != "running":
            vm.destroy()
            vm.start()
    elif vm_state == "paused":
        if not vm.is_paused():
            vm.destroy()
            vm.start()
            vm.pause()
    elif vm_state == "shutoff":
        vm.destroy()
    elif vm_state == "panicked":
        if vm.state() != "running":
            vm.destroy()
            vm.start()
        try:
            session = vm.wait_for_login()
            session.cmd("service kdump stop", ignore_all_errors=True)
            session.cmd("echo 1 > /proc/sys/kernel/sysrq")
            # Send key ALT-SysRq-c to crash VM, and command will not
            # return as vm crashed, so fail early
            try:
                session.cmd("echo c > /proc/sysrq-trigger", timeout=3)
            except (ShellTimeoutError, ShellProcessTerminatedError):
                pass
            session.close()
        except Exception as info:
            logging.error("Crash domain failed: %s", info)
    else:
        logging.error("Unknown state for this test")


def check_output(output, vm, vm_state, options, params):
    """
    Check virsh domstats output according to vm state and command options;
    For now, we only check given state domain can be find by list option.

    :param output: Command result instance
    :param vm: Libvirt VM instance
    :param vm_state: Domain state
    :param options: Virsh command options
    :param params: Dictionary with the test parameters
    """
    check_pass = []
    list_option = ""
    list_option_pass = False
    state_option = ""
    state_option_pass = False
    block_option = ""
    block_option_pass = False
    cpu_option = ""
    cpu_option_pass = False
    vcpu_wait_sum_pass = False
    balloon_option = ""
    balloon_option_pass = False
    nowait_option = ""
    nowait_option_pass = False

    for option in options.split():
        if "--list" in option:
            list_option = option.strip()
            break
        if "--state" in option:
            state_option = option.strip()
            break
        if "--block" in option:
            block_option = option.strip()
            break
        if "--vcpu" in option:
            cpu_option = option.strip()
            break
        if "--balloon" in option:
            balloon_option = option.strip()
            break
        if "--nowait" in option:
            nowait_option = option.strip()
            break
    if list_option == '--list-active':
        if vm_state in ["active", "running", 'paused', 'transient']:
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-inactive':
        if vm_state in ["inactive", "shutoff", 'paniced']:
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-running':
        if vm_state == "running":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-shutoff':
        if vm_state == "shutoff":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-paused':
        if vm_state == "paused":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-other':
        if vm_state == "panic":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-persistent':
        if vm_state == "persistent":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    elif list_option == '--list-transient':
        if vm_state == "transient":
            list_option_pass = vm.name in output
        else:
            list_option_pass = vm.name not in output
    else:
        # All domains should be listed
        list_option_pass = vm.name in output
    if not list_option_pass:
        logging.error("Check '%s' option failed", list_option)
    #TODO: check all output(state, cpu-total, ballon, vcpu, interface, block)
    check_pass.append(list_option_pass)
    if state_option == '--state':
        if 'state' in output:
            state_option_pass = True
        check_pass.append(state_option_pass)
    if block_option == '--block':
        if 'block' in output:
            block_option_pass = True
        check_pass.append(block_option_pass)
    if cpu_option == '--vcpu':
        if 'vcpu' in output:
            cpu_option_pass = True
        check_pass.append(cpu_option_pass)
        if params.get("vcpu_wait_sum"):
            wait_sum = re.findall(r"vcpu.\d+.wait=(\d+)", output)
            for sum in wait_sum:
                if sum != "0":
                    vcpu_wait_sum_pass = True
                check_pass.append(vcpu_wait_sum_pass)
    if balloon_option == '--balloon':
        if 'balloon' in output:
            balloon_option_pass = True
        check_pass.append(balloon_option_pass)
    if nowait_option == '--nowait':
        composed_option = ['state', 'block', 'vcpu', 'balloon']
        if all(sub_option in output for sub_option in composed_option):
            nowait_option_pass = True
        check_pass.append(nowait_option_pass)
    return False not in check_pass


def add_iothread(vm_name, iothread_add_id_list):
    """
    Add iothread to the vm
    :param vm_name: vm name
    :param iothread_add_id_list: list, iothread id list to add
    """
    for one_iothread in iothread_add_id_list:
        virsh.iothreadadd(vm_name, one_iothread, '',
                          ignore_status=False, debug=True)


def del_iothread(vm_name, iothread_del_id_list):
    """
    Delete iothread from the vm
    :param vm_name: vm name
    :param iothread_del_id_list: list, iothread id list to delete
    """
    for one_iothread in iothread_del_id_list:
        virsh.iothreaddel(vm_name, one_iothread, "",
                          ignore_status=False, debug=True)


def check_domstats_for_iothread(iothread_add_id_list,
                                iothread_del_id_list,
                                domstats_output,
                                test):
    """
    Verify iothread info in domstats output
    :param iothread_add_id_list: list, iothread id list to add
    :param iothread_del_id_list: list, iothread id list to delete
    :param domstats_output: output of virsh domstats --iothread
    :param test: test object
    :raises: test.fail if the result is not expected
    """
    iothread_left_id_list = iothread_add_id_list
    if iothread_del_id_list:
        iothread_left_id_list = list(set(iothread_add_id_list).difference(set(iothread_del_id_list)))
    iothread_count_str = "iothread.count=%s" % len(iothread_left_id_list)
    if not re.findall(iothread_count_str, domstats_output):
        test.fail("iothread number is expected to {}, "
                  "but not found in domstats output "
                  "\n{}".format(len(iothread_left_id_list),
                                domstats_output))
    for one_id in iothread_left_id_list:
        iothread_str = 'iothread.%s' % one_id
        if not re.findall(iothread_str, domstats_output):
            test.fail("Expected iothread {} is "
                      "not found in domstats output "
                      "{}".format(one_id, domstats_output))


def test_iothread(vm_name, iothread_add_ids, iothread_del_ids,
                  domstats_output, test):
    """
    Check domstats by operating iothread
    :param vm_name: vm name
    :param iothread_add_ids: list, iothread id to be added
    :param iothread_del_ids: list, iothread id to be deleted
    :param domstats_output: output of virsh domstats --iothread
    :param test: test object
    """
    check_domstats_for_iothread(iothread_add_ids,
                                None,
                                domstats_output,
                                test)
    del_iothread(vm_name, iothread_del_ids)
    output = virsh.domstats(vm_name,
                            '--iothread',
                            ignore_status=False,
                            debug=True).stdout_text.strip()
    check_domstats_for_iothread(iothread_add_ids,
                                iothread_del_ids,
                                output,
                                test)


def run(test, params, env):
    """
    Test command: virsh domstats.

    1.Prepare vm state.
    2.Perform virsh domstats operation.
    3.Confirm the test result.
    4.Recover test environment.
    """
    default_vm_name = params.get("main_vm", "avocado-vt-vm1")
    default_vm = env.get_vm(default_vm_name)
    vm_list = params.get("vm_list", "")
    vm_state = params.get("vm_state", "")
    domstats_option = params.get("domstats_option")
    raw_print = "yes" == params.get("raw_print", "no")
    enforce_command = "yes" == params.get("enforce_command", "no")
    iothread_add_ids = eval(params.get("iothread_add_ids", '[]'))
    iothread_del_ids = eval(params.get("iothread_del_ids", '[]'))
    status_error = (params.get("status_error", "no") == "yes")
    params.update(
        {"vcpu_wait_sum": vm_state == "running" and domstats_option == "--vcpu"})

    if "--nowait" in domstats_option and not libvirt_version.version_compare(4, 5, 0):
        test.cancel("--nowait option is supported until libvirt 4.5.0 version...")
    vms = [default_vm]
    if vm_list:
        for name in vm_list.split():
            if name != default_vm_name:
                if env.get_vm(name):
                    vms.append(env.get_vm(name))
    backup_xml_list = []
    try:
        if not status_error:
            for vm in vms:
                # Back up xml file
                vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
                backup_xml_list.append(vmxml.copy())
                if vm_state == "crash":
                    if vm.is_alive():
                        vm.destroy(gracefully=False)
                    vmxml.on_crash = "preserve"
                    # Add <panic> device to domain
                    panic_dev = Panic()
                    panic_dev.addr_type = "isa"
                    panic_dev.addr_iobase = "0x505"
                    vmxml.add_device(panic_dev)
                    vmxml.sync()
                    virsh.start(vm.name, ignore_status=False)
                    vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm.name)
                    # Skip this test if no panic device find
                    if not vmxml_new.xmltreefile.find('devices').findall('panic'):
                        test.cancel("No 'panic' device in the guest, maybe "
                                    "your libvirt version doesn't support it.")
                prepare_vm_state(vm, vm_state)
                if params.get("vcpu_wait_sum"):
                    session = vm.wait_for_login()
                    utils_disk.dd_data_to_vm_disk(session, "/tmp/file1")
                    session.close()

        if enforce_command:
            domstats_option += " --enforce"
        if raw_print:
            domstats_option += " --raw"
        if "--nowait" in domstats_option:
            pool = ThreadPool(processes=1)
            async_result = pool.apply_async(create_snap, (vm_list, '--no-metadata'))
            return_val = async_result.get()
        if "--iothread" in domstats_option:
            add_iothread(vm_list, iothread_add_ids)
        # Run virsh command
        process.run("echo 1 > /proc/sys/kernel/sched_schedstats", shell=True)
        logging.debug("enable sched_schedstats success")
        result = virsh.domstats(vm_list, domstats_option, ignore_status=True,
                                debug=True)
        status = result.exit_status
        output = result.stdout.strip()

        # check status_error
        if status_error:
            if not status:
                if "unsupported flags" in result.stderr:
                    test.cancel(result.stderr)
                test.fail("Run successfully with wrong command!")
        else:
            if status:
                test.fail("Run failed with right command")
            else:
                for vm in vms:
                    if not check_output(output, vm, vm_state, domstats_option, params):
                        test.fail("Check command output failed "
                                  "due to %s option" % domstats_option)
            if "--iothread" in domstats_option:
                test_iothread(vm_list, iothread_add_ids,
                              iothread_del_ids, output, test)
    finally:
        try:
            for vm in vms:
                vm.destroy(gracefully=False)
        except AttributeError:
            pass
        for backup_xml in backup_xml_list:
            backup_xml.sync()
