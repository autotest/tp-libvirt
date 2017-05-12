import datetime
import logging
import re
import time

from autotest.client.shared import error
from autotest.client.shared import utils
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


@error.context_aware
def run(test, params, env):
    """
    This test virsh domtime command and its options.

    1) Start a guest with/without guest agent configured;
    2) Record guest times;
    3) Do some operation to stop VM;
    4) Run virsh domtime command with different options;
    5) Check the command result;
    6) Check the guest times against expectation;
    7) Cleanup test environment.
    """
    epoch = datetime.datetime(1970, 1, 1, 0, 0, 0)
    # Max time can be set with domtime successfully in newer qemu-ga
    time_max_1 = 3155731199
    # Max time can be set with domtime successfully in older qemu-ga
    time_max_2 = 3155759999
    # Max time can be set with domtime bug failed to set RTC in older qemu-ga
    time_max_3 = 9223372035

    def init_time(session):
        """
        Initialize guest RTC time to epoch + 1234567890 and system time
        one day latter.

        :param session: Session from which to access guest
        """
        res = virsh.domtime(vm_name, time=1234567890)
        if res.exit_status:
            logging.debug("Failed to init time to 1234567890:\n%s", res)
        status, output = session.cmd_status_output('date -s "1 day"')
        if status:
            raise error.TestError("Failed to set guest time:\n%s" % output)

    def get_host_utc_time():
        """
        Get host UTC time from date command.
        """
        res = utils.run("date -u")
        # Strip timezone info from output
        # e.g. 'Sun Feb 15 07:31:40 CST 2009' -> 'Sun Feb 15 07:31:40 2009'
        time_str = re.sub(r'\S+ (?=\S+$)', '', res.stdout.strip())
        return datetime.datetime.strptime(time_str,
                                          r"%a %b %d %H:%M:%S %Y")

    def run_cmd(session, cmd):
        """
        Run a command in a session and record duration of call.
        """
        start = time.time()
        output = session.cmd_output(cmd)
        duration = time.time() - start
        logging.info('Result of command "%s". Duration: %s. Output:%s',
                     cmd, duration, output.strip())
        return output, duration

    def get_guest_times(session):
        """
        Retrieve different guest time as a dict for checking.
        Keys:
            local_hw: Guest RTC time in local timezone
            local_sys: Guest system time in local timezone
            utc_sys: Guest system time in UTC
            domtime: Guest system time in UTC got from virsh domtime command

        :param session: Session from which to access guest
        """
        times = {}
        get_begin = time.time()
        # Guest RTC local timezone time
        output, _ = run_cmd(session, 'hwclock')
        time_str, _ = re.search(r"(.+)  (\S+ seconds)", output).groups()

        try:
            # output format 1: Tue 01 Mar 2016 01:53:46 PM CST
            # Remove timezone info from output
            new_str = re.sub(r'\s+\S+$', '', time_str)
            times['local_hw'] = datetime.datetime.strptime(
                new_str, r"%a %d %b %Y %I:%M:%S %p")
        except ValueError:
            # There are two possible output format for `hwclock`
            # output format 2: Sat Feb 14 07:31:33 2009
            times['local_hw'] = datetime.datetime.strptime(
                time_str, r"%a %b %d %H:%M:%S %Y")
        delta = time.time() - get_begin
        times['local_hw'] -= datetime.timedelta(seconds=delta)

        # Guest system local timezone time
        output, _ = run_cmd(session, 'date')
        # Strip timezone info from output
        # e.g. 'Sun Feb 15 07:31:40 CST 2009' -> 'Sun Feb 15 07:31:40 2009'
        time_str = re.sub(r'\S+ (?=\S+$)', '', output.strip())
        times['local_sys'] = datetime.datetime.strptime(
            time_str, r"%a %b %d %H:%M:%S %Y")
        delta = time.time() - get_begin
        times['local_sys'] -= datetime.timedelta(seconds=delta)

        # Guest system UTC timezone time
        output, _ = run_cmd(session, 'date -u')
        # Strip timezone info from output
        # e.g. 'Sun Feb 15 07:31:40 CST 2009' -> 'Sun Feb 15 07:31:40 2009'
        time_str = re.sub(r'\S+ (?=\S+$)', '', output.strip())
        times['utc_sys'] = datetime.datetime.strptime(
            time_str, r"%a %b %d %H:%M:%S %Y")
        delta = time.time() - get_begin
        times['utc_sys'] -= datetime.timedelta(seconds=delta)

        # Guest UTC time from virsh domtime
        res = virsh.domtime(vm_name, pretty=True, ignore_status=True)
        if not res.exit_status:
            logging.info('Result of "domtime". Duration: %s. Output:%s',
                         res.duration, res.stdout.strip())
            _, time_str = res.stdout.split(" ", 1)
            times['domtime'] = datetime.datetime.strptime(
                time_str.strip(), r"%Y-%m-%d %H:%M:%S")
            delta = time.time() - get_begin
            times['domtime'] -= datetime.timedelta(seconds=delta)
        else:
            logging.debug("Unable to get domain time:\n%s", res)
            times['domtime'] = None

        return times, time.time() - get_begin

    def check_get_success(expected_times):
        """
        Check virsh command get result against expected times

        :param expected_times: Expected time for checking
        """
        _, time_str = res.stdout.split(" ", 1)
        if pretty:
            # Time: 2015-01-13 06:29:18
            domtime = datetime.datetime.strptime(time_str.strip(),
                                                 r"%Y-%m-%d %H:%M:%S")
        else:
            # Time: 1421130740
            domtime = epoch + datetime.timedelta(seconds=int(time_str))
        time_shift = time.time() - start
        logging.debug("Time shift is %s", time_shift)
        result_diff = (domtime - expected_times['domtime']).total_seconds()
        if abs(result_diff) > 2.0:
            raise error.TestFail("Expect get time %s, but got %s, time "
                                 "diff: %s" % (org_times['domtime'],
                                               domtime, result_diff))

    def check_guest_times(expected_times, cur_times):
        """
        Check guest times after test against expected times

        :param expected_times: Expected time for checking
        """
        time_shift = time.time() - start
        logging.debug("Time shift is %s", time_shift)

        error_msgs = []
        for key in cur_times:
            if cur_times[key] is not None:
                cur = cur_times[key]
                expect = expected_times[key]

                diff = (cur - expect).total_seconds()
                msg = "For %s, expect get time %s, got %s, time diff: %s" % (
                    key, expect, cur, diff)
                logging.debug(msg)
                if abs(diff) > 2.0:
                    error_msgs.append(msg)
        if error_msgs:
            raise error.TestFail('\n'.join(error_msgs))

    def check_time(result, org_times, cur_times):
        """
        Check whether domain time has been change accordingly.

        :param result: virsh domtime CmdResult instance
        :param org_times: Original guest times
        """
        action = "get"
        if now or sync or (set_time is not None):
            action = "set"

        tz_diff = org_times['local_sys'] - org_times['utc_sys']
        logging.debug("Timezone diff on guest is %d hours.",
                      (tz_diff.total_seconds() / 3600))

        # Hardware time will never stop
        logging.info('Add %ss to expected guest time', interval)
        if action == 'get':
            expected_times = org_times
        elif action == 'set':
            if result.exit_status:
                # Time not change if domtime fails
                expected_times = org_times
            else:
                # Time change accordingly if succeed.
                if now:
                    utc_time = org_host_time
                    local_time = utc_time + tz_diff
                elif sync:
                    local_time = org_times["local_hw"]
                    utc_time = local_time - tz_diff
                elif set_time is not None:
                    utc_time = epoch + datetime.timedelta(
                        seconds=(int(set_time) - guest_duration))
                    local_time = utc_time + tz_diff
                expected_times = {}
                expected_times['local_hw'] = local_time
                expected_times['local_sys'] = local_time
                expected_times["utc_sys"] = utc_time
                expected_times["domtime"] = utc_time

        # Add interval between two checks of guest time
        for key in expected_times:
            if expected_times[key] is not None:
                expected_times[key] += interval

        # Hardware time will never stop
        # Software time will stop if suspended or managed-saved
        if suspend or managedsave:
            logging.info('Remove %ss from expected guest software time',
                         stop_time)
            expected_times["domtime"] -= stop_time
            expected_times["local_sys"] -= stop_time
            expected_times["utc_sys"] -= stop_time

        # Check guest time if domtime succeeded
        check_guest_times(expected_times, cur_times)

        # Check if output of domtime is correct
        if action == 'get' and not result.exit_status:
            check_get_success(expected_times)

    def prepare_fail_patts():
        """
        Predict fail pattern from test parameters.
        """
        fail_patts = []
        if not channel:
            fail_patts.append(r"QEMU guest agent is not configured")
        if not agent:
            # For older version
            fail_patts.append(r"Guest agent not available for now")
            # For newer version
            fail_patts.append(r"Guest agent is not responding")
        if int(now) + int(sync) + int(bool(set_time)) > 1:
            fail_patts.append(r"Options \S+ and \S+ are mutually exclusive")
        if shutdown:
            fail_patts.append(r"domain is not running")

        if set_time is not None:
            if int(set_time) < 0:
                fail_patts.append(r"Invalid argument")
            elif time_max_1 < int(set_time) <= time_max_2:
                fail_patts.append(r"Invalid time")
            elif time_max_2 < int(set_time) <= time_max_3:
                fail_patts.append(r"Invalid time")
            elif time_max_3 < int(set_time):
                fail_patts.append(r"too big for guest agent")
        return fail_patts

    def stop_vm():
        """
        Suspend, managedsave, pmsuspend or shutdown a VM for a period of time
        """
        stop_start = time.time()
        if suspend:
            vm.pause()
            time.sleep(10)
            vm.resume()
        elif managedsave:
            vm.managedsave()
            time.sleep(10)
            vm.start()
            vm.wait_for_login()
        elif pmsuspend:
            vm.pmsuspend()
            time.sleep(10)
            vm.pmwakeup()
            vm.wait_for_login()
        elif shutdown:
            vm.destroy()

        # Check real guest stop time
        stop_seconds = time.time() - stop_start
        stop_time = datetime.timedelta(seconds=stop_seconds)
        logging.debug("Guest stopped: %s", stop_time)
        return stop_time

    # Check availability of virsh command domtime
    if not virsh.has_help_command('domtime'):
        raise error.TestNAError("This version of libvirt does not support "
                                "the domtime test")

    channel = (params.get("prepare_channel", "yes") == 'yes')
    agent = (params.get("start_agent", "yes") == 'yes')
    pretty = (params.get("domtime_pretty", "no") == 'yes')
    now = (params.get("domtime_now", "no") == 'yes')
    sync = (params.get("domtime_sync", "no") == 'yes')
    set_time = params.get("domtime_time", None)

    shutdown = (params.get("shutdown_vm", "no") == 'yes')
    suspend = (params.get("suspend_vm", "no") == 'yes')
    managedsave = (params.get("managedsave_vm", "no") == 'yes')
    pmsuspend = (params.get("pmsuspend_vm", "no") == 'yes')

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Backup domain XML
    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        if pmsuspend:
            vm_xml.VMXML.set_pm_suspend(vm_name)
        # Add or remove qemu-agent from guest before test
        vm.prepare_guest_agent(channel=channel, start=agent)
        session = vm.wait_for_login()
        try:
            if channel and agent:
                init_time(session)

            # Expected fail message patterns
            fail_patts = prepare_fail_patts()

            # Message patterns test should skip when met
            skip_patts = [
                r'The command \S+ has not been found',
            ]

            # Record start time
            start = time.time()

            # Record host time before testing
            org_host_time = get_host_utc_time()
            # Get original guest times
            org_times, guest_duration = get_guest_times(session)

            # Run some operations to stop guest system
            stop_time = stop_vm()

            # Run command with specified options.
            res = virsh.domtime(vm_name, now=now, pretty=pretty, sync=sync,
                                time=set_time)
            libvirt.check_result(res, fail_patts, skip_patts)

            # Check interval between two check of guest time
            interval = datetime.timedelta(
                seconds=(time.time() - start))
            logging.debug("Interval between guest checking: %s", interval)

            if not shutdown:
                # Get current guest times
                cur_times, _ = get_guest_times(session)

                check_time(res, org_times, cur_times)
        finally:
            # Sync guest time with host
            if channel and agent and not shutdown:
                res = virsh.domtime(vm_name, now=True)
                if res.exit_status:
                    session.close()
                    raise error.TestError("Failed to recover guest time:\n%s"
                                          % res)
            session.close()
    finally:
        # Restore VM XML
        xml_backup.sync()
