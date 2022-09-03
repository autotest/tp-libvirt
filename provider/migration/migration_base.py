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

from virttest.migration import MigrationTest
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_network   # pylint: disable=W0611
from virttest.utils_test import libvirt_domjobinfo   # pylint: disable=W0611


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
                             'func_param': func_param})
            action_during_mig.append(act_dict)
        return action_during_mig
    else:
        test.error("'action_during_mig' value format is invalid, only "
                   "function name and list are supported")


def do_migration(vm, mig_test, src_uri, dest_uri, options, virsh_options,
                 extra, action_during_mig, extra_args):
    """
    The wrapper function to call migration

    :param vm: vm object
    :param mig_test: MigrationTest object
    :param src_uri: source uri
    :param dest_uri: target uri
    :param options: migration options
    :param virsh_options: virsh options
    :param extra: extra options for migration
    :param action_during_mig: list or single function to run during migration
    :param extra_args: arguments for test
    """
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
        test.error("Invalid parameter, only support tls/tcp/ssh/unix_socket/rdma")
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


def poweroff_src_vm(params):
    """
    Poweroff guest on source host

    :param params: dict, get vm session
    """
    vm_session = params.get("vm_session")
    vm_session.cmd("poweroff", ignore_all_errors=True)


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
    disks = vmxml.get_disk_all_by_expr('type==%s' % disk_type, 'device==disk')
    logging.debug("disks: %s", disks)
    debug_kargs = {'ignore_status': False, 'debug': True}
    for disk in list(disks.values()):
        disk_source = disk.find('source').get('dev')
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

    :param params: dict, get expected hard limit
    :raise: test fail if hard limit is not expected
    """
    expect_hard_limit = params.get("expect_hard_limit")
    output = libvirt_memory.get_qemu_process_memlock_hard_limit()
    if int(output) != int(expect_hard_limit) * 1024:
        raise exceptions.TestFail("'%s' is not matched expect hard limit '%s'" % (output, expect_hard_limit))


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
