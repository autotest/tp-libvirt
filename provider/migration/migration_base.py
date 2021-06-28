import logging
import types

from virttest import virsh                           # pylint: disable=W0611

from virttest.utils_conn import TLSConnection
from virttest.utils_libvirt import libvirt_network   # pylint: disable=W0611


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

    :param conn_type: str, 'tls' for now
    :param params: dict, used to setup the connection
    :param test: test object
    :return: TLSConnection, the connection object
    """
    logging.debug("Begin to create new {} connection".format(conn_type.upper()))
    conn_obj = None
    if conn_type.upper() == 'TLS':
        conn_obj = TLSConnection(params)
        conn_obj.auto_recover = True
        conn_obj.conn_setup()
    else:
        test.error("Invalid parameter, only support 'tls'")
    return conn_obj


def cleanup_conn_obj(obj_list, test):
    """
    Clean up TLS/SSH/TCP/UNIX connection objects

    :param obj_list: list, object list
    :param test: test object
    """
    if obj_list is None:
        test.error("No connection object needs to be cleaned up")
    for one_conn in obj_list:
        if one_conn:
            logging.debug("Clean up one connection object")
            del one_conn
