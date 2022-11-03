import logging as log
import types
import re
import signal                                        # pylint: disable=W0611
import time

from avocado.core import exceptions

from virttest import virsh                           # pylint: disable=W0611
from virttest import utils_misc                      # pylint: disable=W0611
from virttest import utils_libvirtd                  # pylint: disable=W0611
from virttest import utils_conn

from virttest.libvirt_xml import vm_xml
from virttest.migration import MigrationTest
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_monitor
from virttest.utils_libvirt import libvirt_network   # pylint: disable=W0611
from virttest.utils_libvirt import libvirt_service   # pylint: disable=W0611
from virttest.utils_test import libvirt_domjobinfo   # pylint: disable=W0611
from virttest.utils_test import libvirt


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def parse_funcs(action_during_mig, test, params):
    """
    Parse action_during_mig parameter

    :param action_during_mig: function or list of function
            For example,
            action_during_mig = '[{"func_name": "check_established",
                                   "after_event": "iteration: '1'",
                                   "before_pause": "yes",
                                   "func_param": params},
                                  {"func_name": "virsh.domjobabort",
                                   "before_pause": "yes"}]'
            Or
            action_during_mig = "libvirt_network.check_established"
    :param test:  test object
    :param params: dict, this is implicitly used in this function,
                         by providing required dependent parameters
    :return: list or None, the function list
    """
    if not action_during_mig:
        return None
    tmp_action = eval(action_during_mig)
    action_during_mig = []
    if isinstance(tmp_action, types.FunctionType):
        return tmp_action
    elif isinstance(tmp_action, list):
        for one_action in tmp_action:
            if 'func' not in one_action:
                test.error("Key 'func' for dict 'action_during_mig or "
                           "action_during_mig_again' is required")
            act_dict = {}
            func_param = one_action.get('func_param')
            if func_param:
                func_param = eval(func_param)

            act_dict.update({'func': eval(one_action.get('func')),
                             'after_event': one_action.get('after_event'),
                             'before_event': one_action.get('before_event'),
                             'before_pause': one_action.get('before_pause'),
                             'need_sleep_time': one_action.get('need_sleep_time'),
                             'func_param': func_param})
            action_during_mig.append(act_dict)
        return action_during_mig
    else:
        test.error("'action_during_mig' value format is invalid, only "
                   "function name and list are supported")


def do_migration(do_mig_param):
    """
    The wrapper function to call migration

    :param do_mig_param: do migration parameters, dict, contains vm object,
                         MigrationTest object, source uri, target uri, migration
                         options, virsh options, extra options for migration, list
                         or single function to run during migration, arguments for test
    """
    vm = do_mig_param['vm']
    mig_test = do_mig_param['mig_test']
    src_uri = do_mig_param['src_uri']
    dest_uri = do_mig_param['dest_uri']
    options = do_mig_param['options']
    virsh_options = do_mig_param['virsh_options']
    extra = do_mig_param['extra']
    action_during_mig = do_mig_param['action_during_mig']
    extra_args = do_mig_param['extra_args']
    vm_name = None

    if "--dname" in extra:
        if action_during_mig:
            for i in range(len(action_during_mig)):
                if action_during_mig[i]['func_param']:
                    vm_name = action_during_mig[i]['func_param'].get('main_vm')
                    break
            vm.name = vm_name

    logging.info("Starting migration...")
    vms = [vm]
    if not action_during_mig or isinstance(action_during_mig,
                                           types.FunctionType):
        mig_test.do_migration(vms, src_uri, dest_uri, 'orderly',
                              options, thread_timeout=900,
                              ignore_status=True,
                              virsh_opt=virsh_options,
                              extra_opts=extra,
                              func=action_during_mig,
                              multi_funcs=None,
                              **extra_args)
    elif isinstance(action_during_mig, list):
        mig_test.do_migration(vms, src_uri, dest_uri, 'orderly',
                              options, thread_timeout=900,
                              ignore_status=True,
                              virsh_opt=virsh_options,
                              extra_opts=extra,
                              func=None,
                              multi_funcs=action_during_mig,
                              **extra_args)


def setup_conn_obj(conn_type, params, test):
    """
    Setup connection object, like TLS

    :param conn_type: str, connection type
    :param params: dict, used to setup the connection
    :param test: test object
    :return: the connection object
    """
    test.log.debug("Begin to create new {} connection".format(conn_type.upper()))
    conn_obj = None
    if conn_type.upper() == 'TLS':
        conn_obj = utils_conn.TLSConnection(params)
    elif conn_type.upper() == 'TCP':
        conn_obj = utils_conn.TCPConnection(params)
    elif conn_type.upper() == 'SSH':
        conn_obj = utils_conn.SSHConnection(params)
    elif conn_type.upper() == 'UNIX_PROXY':
        conn_obj = utils_conn.UNIXSocketConnection(params)
    elif conn_type.upper() == 'RDMA':
        test.cancel("TODO: rdma")
    else:
        test.log.error("Invalid parameter, only support tls/tcp/ssh/unix_socket/rdma")
        return None
    conn_obj.auto_recover = True
    conn_obj.conn_setup()
    return conn_obj


def cleanup_conn_obj(conn_obj_list, test):
    """
    Clean up TLS/SSH/TCP/UNIX/RDMA connection objects

    :param conn_obj_list: list, connection object list
    :param test: test object
    """
    if conn_obj_list is None:
        test.log.info("No connection object needs to be cleaned up")
    for one_conn in conn_obj_list:
        if one_conn:
            test.log.debug("Clean up one connection object")
            one_conn.__del__()
            one_conn.auto_recover = False


def monitor_event(params):
    """
    Monitor event on source/target host

    :param params: dict, get expected event string and remote parameters
    :return: virsh session and remote virsh session to catch events
    """
    expected_event_src = params.get("expected_event_src")
    expected_event_target = params.get("expected_event_target")
    remote_pwd = params.get("migrate_dest_pwd")
    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("remote_user", "root")

    virsh_session = None
    remote_virsh_session = None

    cmd = "event --loop --all"
    if expected_event_src:
        logging.debug("Running virsh command on source: %s", cmd)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)

    if expected_event_target:
        logging.debug("Running virsh command on target: %s", cmd)
        virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                       'remote_pwd': remote_pwd, 'unprivileged_user': None,
                       'virsh_exec': virsh.VIRSH_EXEC, 'auto_close': True,
                       'uri': 'qemu+ssh://%s/system' % remote_ip}
        remote_virsh_session = virsh.VirshSession(**virsh_dargs)
        remote_virsh_session.sendline(cmd)
    return virsh_session, remote_virsh_session


def check_output(output, expected_value_list, test):
    """
    Check if the output match expected value or not

    :param output: actual output
    :param expected_value_list: expected value
    :param test: test object
    :raise: test.fail if unable to find item(s)
    """
    logging.debug("Actual output is %s", output)
    for item in expected_value_list:
        if not re.findall(item, output):
            test.fail("Unalbe to find {}".format(item))


def check_event_output(params, test, virsh_session=None, remote_virsh_session=None):
    """
    Check event on source/target host

    :param params: dict, get expected event string
    :param test: test object
    :param virsh_session: virsh session to catch events
    :param remote_virsh_session: remote virsh session to catch events
    """
    expected_event_src = params.get("expected_event_src")
    expected_event_target = params.get("expected_event_target")
    if expected_event_src and virsh_session:
        source_output = virsh_session.get_stripped_output()
        check_output(source_output, eval(expected_event_src), test)

    if expected_event_target and remote_virsh_session:
        target_output = remote_virsh_session.get_stripped_output()
        check_output(target_output, eval(expected_event_target), test)


def poweroff_vm(params):
    """
    Poweroff guest on source or destination host

    :param params: dict, get vm session or migration object
    """
    poweroff_vm_dest = "yes" == params.get("poweroff_vm_dest", "no")
    migration_obj = params.get("migration_obj")
    test_case = params.get('test_case', '')

    if poweroff_vm_dest:
        dest_uri = params.get("virsh_migrate_desturi")
        if test_case == "poweroff_vm":
            time.sleep(90)
        backup_uri, migration_obj.vm.connect_uri = migration_obj.vm.connect_uri, dest_uri
        migration_obj.vm.cleanup_serial_console()
        migration_obj.vm.create_serial_console()
        remote_vm_session = migration_obj.vm.wait_for_serial_login(timeout=120)
        remote_vm_session.cmd("poweroff", ignore_all_errors=True)
        remote_vm_session.close()
        migration_obj.vm.cleanup_serial_console()
        migration_obj.vm.connect_uri = backup_uri
        vm_state_src = params.get("virsh_migrate_src_state", "shut off")
        if not libvirt.check_vm_state(migration_obj.vm.name, vm_state_src, uri=migration_obj.src_uri):
            raise exceptions.TestFail("Migrated VM failed to be in %s state at source" % vm_state_src)
    else:
        vm_session = params.get("vm_session")
        vm_session.cmd("poweroff", ignore_all_errors=True)
        vm_session.close()


def set_migrate_speed_to_high(params):
    """
    Set migrate speed to high value

    :param params: dict, get vm name, migrate speed and postcopy options
    """
    vm_name = params.get("migrate_main_vm")
    migrate_speed_high = params.get("migrate_speed_high", "8796093022207")
    postcopy_options = params.get("postcopy_options")

    mode = 'both' if postcopy_options else 'precopy'
    MigrationTest().control_migrate_speed(vm_name, int(migrate_speed_high), mode)


def execute_statistics_command(params):
    """
    Execute statistics command

    :param params: dict, get vm name and disk type
    """
    vm_name = params.get("migrate_main_vm")
    disk_type = params.get("loop_disk_type")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    if disk_type:
        disks = vmxml.get_disk_all_by_expr('type==%s' % disk_type, 'device==disk')
    else:
        disks = vmxml.get_disk_all_by_expr('device==disk')
    logging.debug("disks: %s", disks)
    debug_kargs = {'ignore_status': False, 'debug': True}
    for disk in list(disks.values()):
        disk_source = disk.find('source').get('file')
        disk_target = disk.find('target').get('dev')
        logging.debug("disk_source: %s", disk_source)
        logging.debug("disk_target: %s", disk_target)
        virsh.domblkstat(vm_name, disk_target, "", **debug_kargs)
        virsh.domblkinfo(vm_name, disk_source, **debug_kargs)
        virsh.domstats(vm_name, **debug_kargs)
        virsh.dommemstat(vm_name, **debug_kargs)


def check_qemu_mem_lock_hard_limit(params):
    """
    Check qemu process memlock hard limit

    :param params: dict, get expected hard limit and comapred hard limit
    :raise: test fail if hard limit is not expected
    """
    expect_hard_limit = params.get("expect_hard_limit")
    compared_hard_limit = params.get("compared_hard_limit")
    output = libvirt_memory.get_qemu_process_memlock_hard_limit()
    if expect_hard_limit:
        if int(output) != int(expect_hard_limit) * 1024:
            raise exceptions.TestFail("'%s' is not matched expect hard limit '%s'" % (output, expect_hard_limit))
    if compared_hard_limit:
        if int(output) == int(compared_hard_limit) * 1024:
            raise exceptions.TestFail("'%s' is matched hard limit '%s'" % (output, compared_hard_limit))


def check_auto_converge_during_mig(params):
    """
    Check auto converge during migration

    :param params: dict, get initial throttle, increment, max converge
                   and vm name
    :raise: test fail if get domjobinfo failed or get invalid auto converge
            throttle or not found auto converge throttle in the domjobinfo
    """
    initial = int(params.get("initial_throttle"))
    increment = int(params.get("increment"))
    max_converge = int(params.get("max_converge", 99))
    vm_name = params.get("migrate_main_vm")

    allow_throttle_list = [initial + count * increment
                           for count in range(0, (100 - initial) // increment + 1)
                           if (initial + count * increment) < 100]
    allow_throttle_list.append(max_converge)

    throttle = 0
    jobtype = "None"

    while throttle < max_converge:
        cmd_result = virsh.domjobinfo(vm_name, debug=True, ignore_status=True)
        if cmd_result.exit_status:
            # Check if migration is completed
            if "domain is not running" in cmd_result.stderr:
                args = vm_name + " --completed"
                cmd_result = virsh.domjobinfo(args, debug=True,
                                              ignore_status=True)
                if cmd_result.exit_status:
                    raise exceptions.TestFail("Failed to get domjobinfo and domjobinfo "
                                              "--completed: %s" % cmd_result.stderr)
            else:
                raise exceptions.TestFail("Failed to get domjobinfo: %s" % cmd_result.stderr)
        jobinfo = cmd_result.stdout
        for line in jobinfo.splitlines():
            key = line.split(':')[0]
            if key.count("Job type"):
                jobtype = line.split(':')[-1].strip()
            elif key.count("Auto converge throttle"):
                throttle = int(line.split(':')[-1].strip())
                logging.debug("Auto converge throttle:%s", str(throttle))
        if throttle and throttle not in allow_throttle_list:
            raise exceptions.TestFail("Invalid auto converge throttle "
                                      "value '%s'" % throttle)
        if throttle == 99:
            logging.debug("'Auto converge throttle' reaches maximum "
                          "allowed value ")
            break
        if jobtype == "None" or jobtype == "Completed":
            logging.debug("Jobtype:%s", jobtype)
            if throttle == 0:
                raise exceptions.TestFail("'Auto converge throttle' is not found in the domjobinfo")
            break
        time.sleep(5)


def set_maxdowntime_during_mig(params):
    """
    Set maxdowntime during migration

    :param params: dict, get vm name and compared value
    :raise: test fail if set maxdowntime failed or maxdowntime is not expected
    """
    vm_name = params.get("migrate_main_vm")
    compared_value = params.get("compared_value")

    ret = virsh.migrate_setmaxdowntime(vm_name, compared_value, debug=True)
    if ret.exit_status:
        raise exceptions.TestFail("Set maxdowntime during migration failed.")
    maxdowntime = virsh.migrate_getmaxdowntime(vm_name).stdout.strip()
    if maxdowntime != compared_value:
        raise exceptions.TestFail("Get maxdowntime error: %s" % maxdowntime)


def check_domjobinfo_during_mig(params):
    """
    Check domjobinfo during migration

    :param params: dict, get vm object
    """
    vm = params.get("vm_obj")

    libvirt_domjobinfo.check_domjobinfo(vm, params)


def set_bandwidth_during_mig(params):
    """
    Set bandwidth during migration

    :param params: dict, get vm name and compared value
    """
    vm_name = params.get("migrate_main_vm")
    compared_value = params.get("compared_value")

    virsh_args = {"debug": True, "ignore_status": False}
    virsh.migrate_setspeed(vm_name, compared_value, **virsh_args)
    virsh.migrate_getspeed(vm_name, debug=True)


def check_vm_status_during_mig(params):
    """
    Check vm status during migration

    :param params: dict, get expected status of vm, vm name, destination uri, source uri and timeout value
    :raise: test fail when check vm status failed
    """
    vm_status_during_mig = params.get("vm_status_during_mig")
    vm_name = params.get("migrate_main_vm")
    dest_uri = params.get("virsh_migrate_desturi")
    src_uri = params.get("virsh_migrate_connect_uri")
    timeout_value = params.get("timeout_value")
    if timeout_value:
        time.sleep(int(timeout_value))
    for uri in [dest_uri, src_uri]:
        if not libvirt.check_vm_state(vm_name, vm_status_during_mig, uri=uri):
            raise exceptions.TestFail("VM status is not '%s' during migration on %s." % (vm_status_during_mig, uri))


def check_vm_state(params):
    """
    Check vm state

    :param params: dict, get vm name, dest uri, source uri, expected vm state
                   on dest, expected vm state on source and options
    """
    vm_name = params.get("migrate_main_vm")
    dest_uri = "qemu+ssh://%s/system" % params.get("server_ip")
    src_uri = params.get("virsh_migrate_connect_uri")
    expected_dest_state = params.get("expected_dest_state")
    expected_src_state = params.get("expected_src_state")
    migration_options = params.get("migration_options")
    if migration_options == "dname":
        dname_value = params.get("dname_value")
        if not libvirt.check_vm_state(dname_value, expected_dest_state, uri=dest_uri):
            raise exceptions.TestFail("Migrated VM failed to be in %s "
                                      "state at destination" % expected_dest_state)
    else:
        if expected_dest_state:
            if not libvirt.check_vm_state(vm_name, expected_dest_state, uri=dest_uri):
                raise exceptions.TestFail("Migrated VM failed to be in %s "
                                          "state at destination" % expected_dest_state)
            logging.debug("Guest state is '%s' at destination is as expected", expected_dest_state)
        if expected_src_state:
            if not libvirt.check_vm_state(vm_name, expected_src_state, uri=src_uri):
                raise exceptions.TestFail("Migrated VMs failed to be in %s "
                                          "state at source" % expected_src_state)
            logging.debug("Guest state is '%s' at source is as expected", expected_src_state)


def do_common_check(params):
    """
    Do some common check during migration

    :param params: dict, test parameters
    """
    migration_options = params.get("migration_options")
    second_bandwidth = params.get("second_bandwidth")
    migration_obj = params.get("migration_obj")

    if migration_options == "migrateuri":
        libvirt_network.check_established(params)
    if migration_options == "postcopy_bandwidth" and second_bandwidth:
        libvirt_domjobinfo.check_domjobinfo(migration_obj.vm, params)

    # check job info when migration is in paused status
    expected_list = {"Job type": "Unbounded", "Operation": "Outgoing migration"}
    libvirt_monitor.check_domjobinfo(params, expected_list)

    # check domain state with reason
    check_vm_state(params)

    # check statistic commands on source host
    execute_statistics_command(params)


def clear_pmsocat(params):
    """
    Clear pmsocat to break proxy

    :param params: dict, get vm name, migration object, dest uri and source uri
    """
    migration_obj = params.get("migration_obj")

    migration_obj.conn_list[0].clear_pmsocat()


def resume_migration_again(params):
    """
    Resume migration again

    :param params: dict, get migration object and transport type
    """
    migration_obj = params.get("migration_obj")
    virsh_options = params.get("virsh_options", "")
    options = params.get("virsh_migrate_options", "--live --verbose")
    dest_uri = params.get("virsh_migrate_desturi")
    extra = params.get("virsh_migrate_extra")
    postcopy_options = params.get("postcopy_options")
    postcopy_options_during_mig = params.get("postcopy_options_during_mig")
    status_error_during_mig_twice = params.get("status_error_during_mig_twice", "no")
    extra_args_twice_during_mig = migration_obj.migration_test.update_virsh_migrate_extra_args(params)

    if status_error_during_mig_twice:
        extra_args_twice_during_mig.update({'status_error': status_error_during_mig_twice})

    if postcopy_options_during_mig:
        extra_twice_during_mig = "%s %s %s" % (extra, postcopy_options, postcopy_options_during_mig)
    else:
        extra_twice_during_mig = "%s %s" % (extra, postcopy_options)

    do_mig_param = {"vm": migration_obj.vm, "mig_test": migration_obj.migration_test, "src_uri": None,
                    "dest_uri": dest_uri, "options": options, "virsh_options": virsh_options,
                    "extra": extra_twice_during_mig, "action_during_mig": None, "extra_args": extra_args_twice_during_mig}
    do_migration(do_mig_param)


def check_event_before_unattended(params):
    """
    Check event before unattended migration

    :param params: dict, get make_unattended
    """
    make_unattended = params.get("make_unattended")
    expected_event_src = params.get("expected_event_src")
    expected_event_target = params.get("expected_event_target")
    migration_obj = params.get("migration_obj")
    virsh_session = params.get("virsh_session")
    remote_virsh_session = params.get("remote_virsh_session")
    remote_ip = params.get("migrate_dest_host")

    time.sleep(5)
    if make_unattended == "kill_dest_virtqemud":
        if remote_virsh_session:
            expected_event = {"expected_event_target": expected_event_target}
            check_event_output(expected_event, migration_obj.test, remote_virsh_session=remote_virsh_session)
    else:
        if virsh_session:
            src_output = virsh_session.get_stripped_output()
            logging.debug("src event: %s", src_output)
            check_output(src_output, eval(expected_event_src), migration_obj.test)


def wait_for_unattended_mig(params):
    """
    Make migration becomes unattended migration

    :param params: dict, get make_unattended
    """
    make_unattended = params.get("make_unattended")
    expected_event_src = params.get("expected_event_src")
    expected_event_src_2 = params.get("expected_event_src_2")
    expected_event_target = params.get("expected_event_target")
    expected_event_target_2 = params.get("expected_event_target_2")
    migration_obj = params.get("migration_obj")
    virsh_session = params.get("virsh_session")
    remote_virsh_session = params.get("remote_virsh_session")
    remote_ip = params.get("migrate_dest_host")

    src_event = "Stopped Migrated"
    dest_event = "Resumed Migrated"
    if make_unattended == "kill_dest_virtqemud":
        time.sleep(3)
        _, dest_session = monitor_event({"expected_event_target": dest_event, "migrate_dest_host": remote_ip})
    if make_unattended != "kill_dest_virtqemud":
        src_session, _ = monitor_event({"expected_event_src": src_event})

    i = 0
    while i < 100:
        if make_unattended != "kill_dest_virtqemud":
            src_output = src_session.get_stripped_output()
        else:
            src_output = virsh_session.get_stripped_output()
        if src_event in src_output:
            break
        else:
            logging.debug("waitting for migration ...")
            time.sleep(5)
            i = i + 1
    logging.debug("Source event: %s", src_output)
    if make_unattended == "kill_dest_virtqemud":
        if expected_event_src:
            check_output(src_output, eval(expected_event_src), migration_obj.test)
        if expected_event_target_2:
            dest_output = dest_session.get_stripped_output()
            check_output(dest_output, eval(expected_event_target_2), migration_obj.test)
    if expected_event_src_2:
        check_output(src_output, eval(expected_event_src_2), migration_obj.test)
    if remote_virsh_session and expected_event_target:
        dest_output = remote_virsh_session.get_stripped_output()
        check_output(dest_output, eval(expected_event_target), migration_obj.test)
