import os
import re
import logging
from virttest import aexpect
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_selinux
from virttest.staging import service
from autotest.client.shared import error
from autotest.client.shared import utils


class LibvirtdSession(aexpect.Tail):

    """
    Class to generate a libvirtd process and handler all the logging info.
    """

    def _output_handler(self, line):
        """
        Output handler function triggered when new log line outputted.

        This handler separate handlers for both warnings and errors.

        :param line: Newly added logging line.
        """
        # Regex pattern to Match log time string like:
        # '2014-04-08 06:04:22.443+0000: 15122: '
        time_pattern = r'[-\d]+ [.:+\d]+ [:\d]+ '

        # Call `debug_func` if it's a debug log
        debug_pattern = time_pattern + 'debug :'
        result = re.match(debug_pattern, line)
        params = self.debug_params + (line,)
        if self.debug_func and result:
            self.debug_func(*params)

        # Call `info_func` if it's an info log
        info_pattern = time_pattern + 'info :'
        result = re.match(info_pattern, line)
        params = self.info_params + (line,)
        if self.info_func and result:
            self.info_func(*params)

        # Call `warning_func` if it's a warning log
        warning_pattern = time_pattern + 'warning :'
        result = re.match(warning_pattern, line)
        params = self.warning_params + (line,)
        if self.warning_func and result:
            self.warning_func(*params)

        # Call `error_func` if it's an error log
        error_pattern = time_pattern + 'error :'
        result = re.match(error_pattern, line)
        params = self.error_params + (line,)
        if self.error_func and result:
            self.error_func(*params)

    def _termination_handler(self, status):
        """
        Termination handler function triggered when libvirtd exited.

        This handler recover libvirtd service status.

        :param status: Return code of exited libvirtd session.
        """
        if self.was_running:
            logging.debug('Restarting libvirtd service')
            self.libvirtd.start()

    def _wait_for_start(self, timeout=60):
        """
        Wait 'timeout' seconds for libvirt to start.

        :param timeout: Maxinum time for the waiting.
        """
        def _check_start():
            """
            Check if libvirtd is start by return status of 'virsh list'
            """
            virsh_cmd = "virsh list"
            try:
                utils.run(virsh_cmd, timeout=2)
                return True
            except:
                return False
        return utils_misc.wait_for(_check_start, timeout=timeout)

    def __init__(self,
                 debug_func=None, debug_params=(),
                 info_func=None, info_params=(),
                 warning_func=None, warning_params=(),
                 error_func=None, error_params=(),
                 ):
        """
        Initialize a libvirt daemon process and monitor all the logging info.

        The corresponding callback function will be called if a logging line
        is found. The status of libvirtd service will be backed up and
        recovered after termination of this process.

        :param debug_func    : Callback function which will be called if a
                               debug message if found in libvirtd logging.
        :param debug_params  : Additional parameters to be passed to
                               'debug_func'.
        :param info_func     : Callback function which will be called if a
                               info message if found in libvirtd logging.
        :param info_params   : Additional parameters to be passed to
                               'info_func'.
        :param warning_func  : Callback function which will be called if a
                               warning message if found in libvirtd logging.
        :param warning_params: Additional parameters to be passed to
                               'warning_func'.
        :param error_func    : Callback function which will be called if a
                               error message if found in libvirtd logging.
        :param error_params  : Additional parameters to be passed to
                               'error_func'.
        """
        self.debug_func = debug_func
        self.debug_params = debug_params
        self.info_func = info_func
        self.info_params = info_params
        self.warning_func = warning_func
        self.warning_params = warning_params
        self.error_func = error_func
        self.error_params = error_params

        # Libvirtd service status will be backed up at first and
        # recovered after.
        self.libvirtd = utils_libvirtd.Libvirtd()
        self.was_running = self.libvirtd.is_running()
        if self.was_running:
            logging.debug('Stopping libvirtd service')
            self.libvirtd.stop()
        aexpect.Tail.__init__(
            self, "LIBVIRT_DEBUG=1 /usr/sbin/libvirtd",
            output_func=self._output_handler,
            termination_func=self._termination_handler)
        self._wait_for_start()


def _set_iptables_firewalld(iptables_status, firewalld_status):
    """
    Try to set firewalld and iptables services status.

    :param iptables_status: Whether iptables should be set active.
    :param firewalld_status: Whether firewalld should be set active.
    :return: A tuple of two boolean stand for the original status of
             iptables and firewalld.
    """
    # pylint: disable=E1103
    logging.debug("Setting firewalld and iptables services.")

    # Iptables and firewalld are two exclusive services.
    # It's impossible to start both.
    if iptables_status and firewalld_status:
        msg = "Can't active both iptables and firewalld services."
        raise error.TestNAError(msg)

    # Check the availability of both packages.
    try:
        utils_misc.find_command('iptables')
        iptables = service.Factory.create_service('iptables')
    except ValueError:
        msg = "Can't find service iptables."
        raise error.TestNAError(msg)

    try:
        utils_misc.find_command('firewalld')
        firewalld = service.Factory.create_service('firewalld')
    except ValueError:
        msg = "Can't find service firewalld."
        raise error.TestNAError(msg)

    # Back up original services status.
    old_iptables = iptables.status()
    old_firewalld = firewalld.status()

    # We should stop services first then start the other after.
    # Directly start one service will force the other service stop,
    # which will not be easy to handle.
    if not iptables_status and iptables.status():
        utils.run('iptables-save > /tmp/iptables.save')
        if not iptables.stop():
            msg = "Can't stop service iptables"
            raise error.TestError(msg)

    if not firewalld_status and firewalld.status():
        if not firewalld.stop():
            msg = ("Service firewalld can't be stopped. "
                   "Maybe it is masked by default. you can unmask it by "
                   "running 'systemctl unmask firewalld'.")
            raise error.TestNAError(msg)

    if iptables_status and not iptables.status():
        if not iptables.start():
            msg = "Can't start service iptables"
            raise error.TestError(msg)
        utils.run('iptables-restore < /tmp/iptables.save')

    if firewalld_status and not firewalld.status():
        if not firewalld.start():
            msg = ("Service firewalld can't be started. "
                   "Maybe it is masked by default. you can unmask it by "
                   "running 'systemctl unmask firewalld'.")
            raise error.TestNAError(msg)

    return old_iptables, old_firewalld


def run(test, params, env):
    """
    This case check error messages in libvirtd logging.

    Implemetent test cases:
    with_iptables:  Simply start libvirtd when using iptables service
                          as firewall.
    with_firewalld: Simply start libvirtd when using firewalld service
                          as firewall.
    """
    def _error_handler(errors, line):
        """
        A callback function called when new error lines appares in libvirtd
        log, then this line is appended to list 'errors'

        :param errors: A list to contain all error lines.
        :param line: Newly found error line in libvirtd log.
        """
        errors.append(line)

    test_type = params.get('test_type')

    old_iptables = None
    old_firewalld = None
    iptables = None
    try:
        # Setup firewall services according to test type.
        if test_type == 'with_firewalld':
            old_iptables, old_firewalld = _set_iptables_firewalld(False, True)
        elif test_type == 'with_iptables':
            old_iptables, old_firewalld = _set_iptables_firewalld(True, False)
        elif test_type == 'stop_iptables':
            # Use _set_iptables_firewalld(False, False) on rhel6 will got skip
            # as firewalld not on rhel6, but the new case which came from bug
            # 716612 is mainly a rhel6 problem and should be tested, so skip
            # using the  _set_iptables_firewalld function and direct stop
            # iptables.
            try:
                utils_misc.find_command('iptables')
                iptables = service.Factory.create_service('iptables')
            except ValueError:
                msg = "Can't find service iptables."
                raise error.TestNAError(msg)

            utils.run('iptables-save > /tmp/iptables.save')
            if not iptables.stop():
                msg = "Can't stop service iptables"
                raise error.TestError(msg)

        try:
            errors = []
            # Run libvirt session and collect errors in log.
            libvirtd_session = LibvirtdSession(
                error_func=_error_handler,
                error_params=(errors,),
            )

            libvirt_pid = libvirtd_session.get_pid()
            libvirt_context = utils_selinux.get_context_of_process(libvirt_pid)
            logging.debug("The libvirtd pid context is: %s" % libvirt_context)

            # Check errors.
            if errors:
                logging.debug("Found errors in libvirt log:")
                for line in errors:
                    logging.debug(line)
                if test_type == 'stop_iptables':
                    for line in errors:
                        # libvirtd process started without virt_t will failed
                        # to set iptable rules which is expected here
                        if ("/sbin/iptables" and
                                "unexpected exit status 1" not in line):
                            raise error.TestFail("Found errors other than"
                                                 " iptables failure in"
                                                 " libvirt log.")
                else:
                    raise error.TestFail("Found errors in libvirt log.")
        finally:
            libvirtd_session.close()
    finally:
        # Recover services status.
        if test_type in ('with_firewalld', 'with_iptables'):
            _set_iptables_firewalld(old_iptables, old_firewalld)
        elif test_type == "stop_iptables" and iptables:
            iptables.start()
            utils.run('iptables-restore < /tmp/iptables.save')
        if os.path.exists("/tmp/iptables.save"):
            os.remove("/tmp/iptables.save")
