import os
import re
import logging
import time

from avocado.core import exceptions
from avocado.utils import process
from avocado.utils import software_manager
from avocado.utils import service

from virttest import utils_libvirtd
from virttest import utils_selinux


def run(test, params, env):
    """
    This case check error messages in libvirtd logging.

    Implemented test cases:
    with_iptables:  Start libvirtd when using iptables service as firewall.
    with_firewalld: Start libvirtd when using firewalld service as firewall.
    no_firewall:    Start libvirtd With both firewall services shut off.
    """
    def _error_handler(line, errors):
        """
        A callback function called when new error lines appares in libvirtd
        log, then this line is appended to list 'errors'

        :param errors: A list to contain all error lines.
        :param line: Newly found error line in libvirtd log.
        """
        errors.append(line)

    def _check_errors():
        """
        Check for unexpected error messages in libvirtd log.
        """
        logging.info('Checking errors in libvirtd log')
        accepted_error_patterns = [
            'Cannot access storage file',
            'Failed to autostart storage pool',
            'cannot open directory',
        ]

        if (not iptables_service and not firewalld_service and
                'virt_t' not in libvirt_context):
            logging.info("virt_t is not in libvirtd process context. "
                         "Failures for setting iptables rules will be ignored")
            # libvirtd process started without virt_t will failed to set
            # iptables rules which is expected here
            accepted_error_patterns.append(
                '/sbin/iptables .* unexpected exit status 1')

        logging.debug("Accepted errors are: %s", accepted_error_patterns)

        if errors:
            logging.debug("Found errors in libvirt log:")
            for line in errors:
                logging.debug(line)

            unexpected_errors = []
            for line in errors:
                if any([re.search(p, line)
                        for p in accepted_error_patterns]):
                    logging.debug('Error "%s" is acceptable', line)
                else:
                    unexpected_errors.append(line)
            if unexpected_errors:
                raise exceptions.TestFail(
                    "Found unexpected errors in libvirt log:\n%s" %
                    '\n'.join(unexpected_errors))

    iptables_service = params.get('iptables_service', 'off') == 'on'
    firewalld_service = params.get('firewalld_service', 'off') == 'on'

    # In RHEL7 iptables service is provided by a separated package
    # In RHEL6 iptables-services and firewalld is not supported
    # So try to install all required packages but ignore failures
    logging.info('Preparing firewall related packages')
    software_mgr = software_manager.SoftwareManager()
    for pkg in ['iptables', 'iptables-services', 'firewalld']:
        if not software_mgr.check_installed(pkg):
            software_mgr.install(pkg)

    # Backup services status
    service_mgr = service.ServiceManager()
    logging.info('Backing up firewall services status')
    backup_iptables_status = service_mgr.status('iptables')
    backup_firewalld_status = service_mgr.status('firewalld')

    # iptables-service got deprecated in newer distros
    if iptables_service and backup_iptables_status is None:
        raise exceptions.TestSkipError('iptables service not found')
    # firewalld service could not exists on many distros
    if firewalld_service and backup_firewalld_status is None:
        raise exceptions.TestSkipError('firewalld service not found')
    try:
        if iptables_service and firewalld_service:
            raise exceptions.TestError(
                'iptables service and firewalld service can not be started at '
                'the same time')

        # We should stop services first then start the other after.
        # Directly start one service will force the other service stop,
        # which will not be easy to handle.
        # Backup status should be compared with None to make sure that
        # service exists before action.
        logging.info('Changing firewall services status')
        if not iptables_service and backup_iptables_status is not None:
            process.run('iptables-save > /tmp/iptables.save', shell=True)
            service_mgr.stop('iptables')
        if not firewalld_service and backup_firewalld_status is not None:
            service_mgr.stop('firewalld')

        if iptables_service and backup_iptables_status is not None:
            service_mgr.start('iptables')
        if firewalld_service and backup_firewalld_status is not None:
            service_mgr.start('firewalld')
        errors = []
        # Run libvirt session and collect errors in log.
        libvirtd_session = utils_libvirtd.LibvirtdSession(
            logging_handler=_error_handler,
            logging_params=(errors,),
            logging_pattern=r'[-\d]+ [.:+\d]+ [:\d]+ error :',
        )
        try:
            logging.info('Starting libvirtd session')
            libvirtd_session.start()
            time.sleep(3)

            libvirt_pid = libvirtd_session.tail.get_pid()
            sestatus = utils_selinux.get_status()
            if sestatus == "disabled":
                raise exceptions.TestSkipError("SELinux is in Disabled mode."
                                               "It must be in enforcing mode "
                                               "for test execution")
            libvirt_context = utils_selinux.get_context_of_process(libvirt_pid)
            logging.debug(
                "The libvirtd process context is: %s", libvirt_context)

        finally:
            libvirtd_session.exit()
        _check_errors()
    finally:
        logging.info('Recovering services status')
        # If service do not exists, then backup status and current status
        # will all be none and nothing will be done
        if service_mgr.status('iptables') != backup_iptables_status:
            if backup_iptables_status:
                service_mgr.start('iptables')
                process.run('iptables-restore < /tmp/iptables.save',
                            shell=True)
            else:
                service_mgr.stop('iptables')
        if service_mgr.status('firewalld') != backup_firewalld_status:
            if backup_firewalld_status:
                service_mgr.start('firewalld')
            else:
                service_mgr.stop('firewalld')

        logging.info('Removing backup iptables')
        if os.path.exists("/tmp/iptables.save"):
            os.remove("/tmp/iptables.save")
