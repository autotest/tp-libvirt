import time
import logging

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_vm
from virttest.remote import LoginTimeoutError
from virttest.remote import LoginProcessTerminatedError


class TimeoutError(Exception):

    """
    Simple custom exception raised when host down time exceeds timeout.
    """

    def __init__(self, msg):
        super(TimeoutError, self).__init__(self)
        self.msg = msg

    def __str__(self):
        return ("TimeoutError: %s" % self.msg)


def check_host_down_time(remote_ip, timeout=300):
    """
    Test for how long a target host went down.

    :param remote_ip: IP address or hostname of target host.
    :param timeout: For how long will return a timeout expection
                    if host is not recovered.
    :return: Time elapsed before target host is pingable.
    :raise TimeoutExpection: :
    """
    start_time = time.time()
    end_time = time.time() + timeout

    ping_cmd = 'ping -c 1 -W 1 ' + remote_ip
    logging.debug('Wait for host shutting down.')
    while True:
        if time.time() > end_time:
            raise TimeoutError(
                'Downtime %s exceeds maximum allowed %s' %
                (time.time() - start_time, timeout))
        res = process.run(ping_cmd, ignore_status=True, verbose=False, shell=True)
        if res.exit_status:
            logging.debug('Host %s is down.', remote_ip)
            break
        else:
            logging.debug('Host %s is up.', remote_ip)
            time.sleep(1)
    logging.debug('Time elapsed before host down: %.2fs',
                  (time.time() - start_time))

    logging.debug('Wait for host recover from sleep.')
    while True:
        if time.time() > end_time:
            raise TimeoutError(
                'Downtime %s exceeds maximum allowed %s' %
                (time.time() - start_time, timeout))
        res = process.run(ping_cmd, ignore_status=True, verbose=False, shell=True)
        if res.exit_status:
            logging.debug('Host %s is down.', remote_ip)
        else:
            logging.debug('Host %s is up.', remote_ip)
            break

    down_time = time.time() - start_time
    logging.debug('Time elapsed before host up: %.2fs', down_time)
    return down_time


def run(test, params, env):
    """
    Test command: virsh nodesuspend <target> <duration>

    This command will make host suspend or hibernate, running tests on testing
    grid may cause unexpected behavior.

    This tests only work when test runner setup a remote host (physical or
    virtual) with testing version of libvirt daemon running. After that change
    the remote_xxx parameters in configuration file to corresponding value.
    """
    # Retrive parameters
    remote_ip = params.get('nodesuspend_remote_ip',
                           'ENTER.YOUR.REMOTE.EXAMPLE.COM')
    remote_user = params.get('nodesuspend_remote_user', 'root')
    remote_pwd = params.get('nodesuspend_remote_pwd', 'EXAMPLE.PWD')
    suspend_target = params.get('suspend_target', 'mem')
    suspend_time = int(params.get('suspend_time', '60'))
    upper_tolerance = int(params.get('upper_tolerance', '8'))
    lower_tolerance = int(params.get('lower_tolerance', '0'))
    expect_succeed = params.get('expect_succeed', 'yes')

    # Check if remote_ip is set
    if 'EXAMPLE' in remote_ip:
        msg = ('Configuration parameter `nodesuspend_remote_ip` need to be '
               'changed to the ip of host to be tested')
        test.cancel(msg)

    # Create remote virsh session
    remote_uri = libvirt_vm.get_uri_with_transport(
        transport="ssh", dest_ip=remote_ip)
    virsh_dargs = {
        'remote_user': remote_user,
        'remote_pwd': remote_pwd,
        'uri': remote_uri}
    try:
        vrsh = virsh.VirshPersistent(**virsh_dargs)
    except (LoginTimeoutError, LoginProcessTerminatedError):
        test.cancel('Cannot login to remote host, Skipping')

    # Run test
    result = vrsh.nodesuspend(suspend_target, suspend_time, ignore_status=True)
    logging.debug(result)

    # Check real suspend time if command successfully run
    if result.exit_status == 0:
        try:
            down_time = check_host_down_time(
                remote_ip,
                timeout=suspend_time + upper_tolerance)

            # Wait for PM to return completely
            time.sleep(5)

            # Check if host down time within tolerance
            if not (suspend_time - lower_tolerance <
                    down_time <
                    suspend_time + upper_tolerance):
                test.fail('Down time (%.2fs) not in range (%ds)'
                          '+ (%ds) - (%ds).'
                          % (down_time, suspend_time,
                             upper_tolerance, lower_tolerance))
        except TimeoutError as e:
            # Mark test FAIL if down time exceeds expectation
            logging.debug(e)
            vrsh.close_session()
            test.fail('Timeout when checking host down time.')

    # Check whether exit code match expectation.
    if (result.exit_status == 0) != (expect_succeed == 'yes'):
        test.fail(
            'Result do not meet expect_succeed (%s). Result:\n %s' %
            (expect_succeed, result))
