"""
Test module for timer management.
"""

import os
import logging
import time

from avocado.core import exceptions
from avocado.utils import process

from virttest import utils_test
from virttest import virsh
from virttest import data_dir
from virttest import virt_vm
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts

from virttest import libvirt_version

CLOCK_SOURCE_PATH = '/sys/devices/system/clocksource/clocksource0/'


def set_clock_xml(test, vm, params):
    """
    Config VM clock XML.

    :param vm: VM instance
    :param params: Test parameters
    """
    timer_elems = []
    if "yes" == params.get("specific_timer", "no"):
        timers = params.get("timer_name", "").split(',')
        timer_present = params.get("timer_present", "no")
        if len(timers):
            new_present = timer_present
            for timer in timers:
                if timer_present == 'mix':
                    if new_present == "yes":
                        new_present = "no"
                    else:
                        new_present = "yes"
                timer_attrs = {'name': timer, 'present': new_present}
                timer_elems.append(timer_attrs)
        else:
            test.error("No timer provided")

    offset = params.get("clock_offset", "utc")
    adjustment = params.get("clock_adjustment")
    timezone = params.get("clock_timezone")

    vmclockxml = vm_xml.VMClockXML()
    vmclockxml.from_dumpxml(vm.name)
    vmclockxml.offset = offset
    del vmclockxml.adjustment
    del vmclockxml.timezone
    if adjustment is not None:
        vmclockxml.adjustment = adjustment
    if timezone is not None:
        vmclockxml.timezone = timezone
    # Clear timers for re-creating
    vmclockxml.timers = []
    newtimers = []
    for element in timer_elems:
        newtimer = vm_xml.VMClockXML.TimerXML()
        newtimer.update(element)
        newtimers.append(newtimer)
    vmclockxml.timers = newtimers
    logging.debug("New vm XML:\n%s", vmclockxml)
    vmclockxml.sync()
    # Return timer elements for test verify
    return timer_elems


def vm_clock_source(vm, target, value=''):
    """
    Get/Set available/current clocksource in vm

    :param vm: VM instance
    :param target: Available or current clocksource
    :param value: target clocksource value
    :return: Return clocksource if value is empty
    """
    if target == 'available':
        clock_file = 'available_clocksource'
    elif target == 'current':
        clock_file = 'current_clocksource'
    else:
        exceptions.TestError("Clock source target must be 'available' or 'current'")
    session = vm.wait_for_login()
    session.cmd("cd %s" % CLOCK_SOURCE_PATH)
    set_clock = False
    if value:
        set_clock = True
        cmd = "echo %s > %s" % (value, clock_file)
    else:
        cmd = "cat %s" % clock_file
    cmd_s, cmd_o = session.cmd_status_output(cmd)
    session.close()
    result = ''
    if cmd_s:
        logging.error("Run command %s in VM fail: %s", cmd, cmd_o)
    else:
        result = cmd_o.strip()
    if set_clock:
        result = cmd_s == 0
    return result


def get_time(vm=None, time_type=None, windows=False):
    """
    Return epoch time. Windows will return timezone only.

    :param time_type: UTC or timezone time
    :param windows: If the vm is a windows guest
    :return: Epoch time or timezone
    """
    if vm:
        session = vm.wait_for_login()
        if time_type == "utc":
            cmd = "date -u +%Y/%m/%d/%H/%M/%S"
            ts, timestr = session.cmd_status_output(cmd)
        elif windows is True:
            # Date in this format: Sun 09/14/2014 or 2014/09/14 Sun
            # So deal with it after getting date
            date_cmd = (r"echo %date%")
            time_cmd = (r"echo %time:~0,2%/%time:~3,2%/%time:~6,2%")
            ts1, output1 = session.cmd_status_output(date_cmd)
            ts2, output2 = session.cmd_status_output(time_cmd)
            ts = ts1 or ts2
            if output1.split()[0].split('/') == 3:
                datestr = output1.split()[0]
            else:
                datestr = output1.split()[1]
            date_elems = datestr.split('/')
            if int(date_elems[-1]) > 30:   # This is mm/dd/yyyy
                timestr = "%s/%s/%s" % (date_elems[-1], date_elems[0],
                                        date_elems[1])
            else:
                timestr = datestr
            timestr += "/%s" % output2
        else:
            cmd = "date +%Y/%m/%d/%H/%M/%S"
            ts, timestr = session.cmd_status_output(cmd)
        session.close()
        if ts:
            logging.error("Get time in vm failed: %s", timestr)
            return -1
        # To avoid some unexpected space, strip it manually
        timestr = timestr.replace(" ", "").strip()
        struct_time = time.strptime(timestr, "%Y/%m/%d/%H/%M/%S")
    else:
        if time_type == 'utc':
            struct_time = time.gmtime()
        else:
            struct_time = time.localtime()
    try:
        return int(time.mktime(struct_time))
    except TypeError as e:
        logging.error("Translate time to seconds error: %s", e)
        return -1


def set_host_timezone(test, timezone="America/New_York"):
    """
    Set host timezone

    :param timezone: New timezone
    """
    timezone_file = "/usr/share/zoneinfo/%s" % timezone
    if process.run("ls %s" % timezone_file, ignore_status=True, shell=True).exit_status:
        test.error("Invalid timezone file: %s" % timezone_file)
    else:
        process.run("unlink /etc/localtime", ignore_status=True, shell=True)
        result = process.run("ln -s %s /etc/localtime" % timezone_file,
                             ignore_status=True, shell=True)
        if result.exit_status:
            test.error("Set timezone failed: %s" % result)
        else:
            logging.debug("Set host timezone to %s", timezone)


def set_vm_timezone(test, vm, timezone="America/New_York", windows=False):
    """
    Set vm timezone

    :param vm: VM instance
    :param timezone: Timezone name
    :param windows_vm: If the vm is a windows guest
    """
    cmd_s = 0
    cmd_o = ''
    if not windows:
        timezone_file = "/usr/share/zoneinfo/%s" % timezone
        session = vm.wait_for_login()
        if session.cmd_status("ls %s" % timezone_file):
            session.close()
            test.error("Not correct timezone:%s" % timezone_file)
        else:
            session.cmd("unlink /etc/localtime")
            cmd_s, cmd_o = session.cmd_status_output("ln -s %s /etc/localtime"
                                                     % timezone_file)
            session.close()
    else:
        timezone_codes = {"America/New_York": "Eastern Standard Time",
                          "Europe/London": "UTC",
                          "Asia/Shanghai": "China Standard Time",
                          "Asia/Tokyo": "Tokyo Standard Time"}
        if timezone not in list(timezone_codes.keys()):
            test.error("Not supported timezone, please add it.")
        cmd = "tzutil /s \"%s\"" % timezone_codes[timezone]
        session = vm.wait_for_login()
        cmd_s, cmd_o = session.cmd_status_output(cmd)
        session.close()
    if cmd_s:
        test.error("Set vm timezone failed: %s" % cmd_o)
    else:
        logging.debug("Set vm timezone to %s", timezone)


def numeric_timezone(tz_name="Europe/London"):
    """
    Return numeric(in hour) timezone

    :param tz_name: Timezone name
    """
    numeric_tz = None
    try:
        cmd = "TZ='%s'" % tz_name
        cmd += " date +%z"
        z = process.run(cmd, shell=True).stdout_text.strip()
        numeric_tz = float(z[0:-2]) + float(z[0] + z[-2:]) / 60
    except Exception as e:
        logging.error("Convert timezone error: %s", e)
    return numeric_tz


def manipulate_vm(vm, operation, params=None):
    """
    Manipulate the VM.

    :param vm: VM instance
    :param operation: stress_in_vms, inject_nmi, dump, suspend_resume
                      or save_restore
    :param params: Test parameters
    """
    err_msg = ''
    # Special operations for test
    if operation == "stress":
        logging.debug("Load stress in VM")
        err_msg = utils_test.load_stress(operation, params=params, vms=[vm])[0]
    elif operation == "inject_nmi":
        inject_times = int(params.get("inject_times", 10))
        logging.info("Trying to inject nmi %s times", inject_times)
        while inject_times > 0:
            try:
                inject_times -= 1
                virsh.inject_nmi(vm.name, debug=True, ignore_status=False)
            except process.CmdError as detail:
                err_msg = "Inject nmi failed: %s" % detail
    elif operation == "dump":
        dump_times = int(params.get("dump_times", 10))
        logging.info("Trying to dump vm %s times", dump_times)
        while dump_times > 0:
            dump_times -= 1
            dump_path = os.path.join(data_dir.get_tmp_dir(), "dump.file")
            try:
                virsh.dump(vm.name, dump_path, debug=True, ignore_status=False)
            except (process.CmdError, OSError) as detail:
                err_msg = "Dump %s failed: %s" % (vm.name, detail)
            try:
                os.remove(dump_path)
            except OSError:
                pass
    elif operation == "suspend_resume":
        paused_times = int(params.get("paused_times", 10))
        logging.info("Trying to suspend/resume vm %s times", paused_times)
        while paused_times > 0:
            paused_times -= 1
            try:
                virsh.suspend(vm.name, debug=True, ignore_status=False)
                virsh.resume(vm.name, debug=True, ignore_status=False)
            except process.CmdError as detail:
                err_msg = "Suspend-Resume %s failed: %s" % (vm.name, detail)
    elif operation == "save_restore":
        save_times = int(params.get("save_times", 10))
        logging.info("Trying to save/restore vm %s times", save_times)
        while save_times > 0:
            save_times -= 1
            save_path = os.path.join(data_dir.get_tmp_dir(), "save.file")
            try:
                virsh.save(vm.name, save_path, debug=True,
                           ignore_status=False)
                virsh.restore(save_path, debug=True, ignore_status=False)
            except process.CmdError as detail:
                err_msg = "Save-Restore %s failed: %s" % (vm.name, detail)
            try:
                os.remove(save_path)
            except OSError:
                pass
    else:
        err_msg = "Unsupport operation in this function: %s" % operation
    return err_msg


def translate_timer_name(timer_name):
    """
    Translate timer name in XML to clock source name in VM.

    :param timer_name: Timer name in VM XML
    :return: Clock source name in VM
    """
    clock_name = ""
    if timer_name in ['hpet', 'pit', 'rtc', 'tsc']:
        clock_name = timer_name
    elif timer_name == "kvmclock":
        clock_name = "kvm-clock"
    elif timer_name == "platform":
        clock_name = "acpi_pm"
    else:
        logging.warn('Unrecognized timer name %s', timer_name)
    return clock_name


def test_timers_in_vm(test, vm, params):
    """
    Test all available timers in VM.

    :param vm: VM instance
    :param params: Test parameters
    """
    host_tz = params.get("host_timezone", "Asia/Tokyo")
    vm_tz = params.get("vm_timezone", "America/New_York")
    clock_tz = params.get("clock_timezone", "Asia/Shanghai")
    host_tz_vector = numeric_timezone(host_tz)
    vm_tz_vector = numeric_timezone(vm_tz)
    set_tz_vector = numeric_timezone(clock_tz)
    if not any([host_tz_vector, vm_tz_vector, set_tz_vector]):
        test.error("Not supported timezone to convert.")
    delta = int(params.get("allowd_delta", "300"))
    windows_test = "yes" == params.get("windows_test", "no")

    # Set host timezone
    set_host_timezone(test, host_tz)

    # Confirm vm is down for editing
    if vm.is_alive():
        vm.destroy()

    # Config clock in VMXML
    set_clock_xml(test, vm, params)

    # Logging vm to set time
    vm.start()
    vm.wait_for_login()
    set_vm_timezone(test, vm, vm_tz, windows_test)

    # manipulate vm if necessary, linux guest only
    operation = params.get("stress_type")
    if operation is not None:
        err_msg = manipulate_vm(vm, operation, params)
        if err_msg:
            logging.error(err_msg)

    # Get expected utc distance between host and vm
    # with different offset(seconds)
    offset = params.get("clock_offset", "utc")
    vm_tz_span = vm_tz_vector * 3600
    host_tz_span = host_tz_vector * 3600
    if offset == "utc":
        expect_utc_gap = 0
    elif offset == "localtime":
        expect_utc_gap = host_tz_span
    elif offset == "timezone":
        expect_utc_gap = set_tz_vector * 3600
    elif offset == "variable":
        expect_utc_gap = int(params.get("clock_adjustment", 3600))

    logging.debug("Expected UTC time gap between vm and host: %s",
                  abs(expect_utc_gap))
    expect_tz_gap = abs(vm_tz_span)
    logging.debug("Expected vm timezone time gap: %s", expect_tz_gap)

    host_utc_time = get_time(time_type='utc')
    logging.debug("UTC time on host: %s", host_utc_time)
    if windows_test:
        # Get windows vm's time(timezone)
        vm_tz_time = get_time(vm, "tz", windows=True)
        logging.debug("TimeZone time in vm: %s", vm_tz_time)
        # Gap between vm timezone time and host utc time
        actual_tz_gap = abs(vm_tz_time - host_utc_time)
        logging.debug("Actual vm timezone time gap: %s", actual_tz_gap)
        if abs(actual_tz_gap - expect_utc_gap) > delta:
            test.fail("Timezone time of %s is not expected" % vm.name)
    else:
        # Get available clocksources
        avail_clock = vm_clock_source(vm, 'available').split()
        if not avail_clock:
            test.error("Get available clock sources of %s failed"
                       % vm.name)
        logging.debug("Available clock sources of %s: %s", vm.name, avail_clock)
        for clock in avail_clock:
            logging.debug("Trying to set vm clock source to %s", clock)
            if not vm_clock_source(vm, 'current', clock):
                test.fail("Set clock source to %s in %s failed."
                          % (clock, vm.name))
            # Wait 2s to let new clocksource stable
            time.sleep(2)

            new_clock = vm_clock_source(vm, 'current')
            logging.debug("New clock source in VM: %s", new_clock)
            if new_clock.strip() != clock.strip():
                test.fail("New clock source is not expected")

            vm_utc_time = get_time(vm, "utc")
            logging.debug("UTC time in vm: %s", vm_utc_time)
            vm_tz_time = get_time(vm, "tz")
            logging.debug("TimeZone time in vm: %s", vm_tz_time)

            # Gap between vm utc time and host utc time
            actual_utc_gap = abs(vm_utc_time - host_utc_time)
            logging.debug("Actual UTC time gap between vm and host: %s",
                          actual_utc_gap)
            if abs(actual_utc_gap - expect_utc_gap) > delta:
                test.fail("UTC time between host and %s do not match"
                          % vm.name)
            # Gap between timezone and utc time in vm
            actual_tz_gap = abs(vm_tz_time - vm_utc_time)
            logging.debug("Actual time gap between timezone and UTC time in"
                          " vm: %s", actual_tz_gap)
            if abs(actual_tz_gap - expect_tz_gap) > delta:
                test.fail("Timezone time of %s is not expected"
                          % vm.name)


def test_specific_timer(test, vm, params):
    """
    Test specific timer and optional attributes of it.

    :param vm: VM instance
    :param params: Test parameters
    """
    timers = params.get("timer_name", "").split(',')
    if 'tsc' in timers and not libvirt_version.version_compare(3, 2, 0):
        start_error = True
    else:
        start_error = "yes" == params.get("timer_start_error", "no")
    if vm.is_dead():
        vm.start()
    vm.wait_for_login()
    # Not config VM clock if the timer is unsupport in VM
    config_clock_in_vm = True
    for timer in timers:
        timer = translate_timer_name(timer)
        if timer not in vm_clock_source(vm, 'available').split():
            config_clock_in_vm = False
    vm.destroy()

    try:
        timer_dict_list = set_clock_xml(test, vm, params)
    except xcepts.LibvirtXMLError:
        if libvirt_version.version_compare(6, 0, 0) and start_error:
            return
        else:
            raise

    # Logging vm to verify whether setting is work
    try:
        vm.start()
        vm.wait_for_login()
        if start_error:
            test.fail("Start vm succeed, but expect fail.")
    except virt_vm.VMStartError as detail:
        if start_error:
            logging.debug("Expected failure: %s", detail)
            return
        else:
            test.fail(detail)

    # TODO: Check VM cmdline about different timers
    vm_pid = vm.get_pid()
    with open("/proc/%s/cmdline" % vm_pid) as cmdline_f:
        cmdline_content = cmdline_f.read()
    logging.debug("VM cmdline output:\n%s", cmdline_content.replace('\x00', ' '))
    if not config_clock_in_vm:
        return

    # Get available clocksources
    avail_clock = vm_clock_source(vm, 'available').split()
    if not avail_clock:
        test.fail("Get available clock sources of %s failed"
                  % vm.name)
    logging.debug("Available clock sources of %s: %s", vm.name, avail_clock)
    for timer_dict in timer_dict_list:
        t_name = translate_timer_name(timer_dict['name'])
        t_present = timer_dict['present']
        # Check available clock sources
        if t_present == 'no':
            if t_name in avail_clock:
                test.fail("Timer %s(present=no) is still available"
                          " in vm" % t_name)
        else:
            if t_name not in avail_clock:
                test.fail("Timer %s(present=yes) is not available"
                          " in vm" % t_name)
        # Try to set specific timer
        if not vm_clock_source(vm, 'current', t_name):
            test.error("Set clock source to % in vm failed" % t_name)
        time.sleep(2)
        if t_present == 'no':
            if vm_clock_source(vm, 'current') == t_name:
                test.fail("Set clock source to %s in vm successfully"
                          " while present is no" % t_name)
        else:
            if vm_clock_source(vm, 'current') != t_name:
                test.fail("Set clock source to %s in vm successfully"
                          " while present is yes" % t_name)


def run(test, params, env):
    """
    Test vm time according timer management of XML configuration.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Backup host timezone in the same dir
    process.run("ln --backup /etc/localtime /etc/localtime.bk", shell=True)
    timer_test_type = params.get("timer_test_type")
    testcase = globals()[timer_test_type]
    try:
        # run the test
        testcase(test, vm, params)
    finally:
        vm.destroy()
        vmxml_backup.sync()
        os.rename("/etc/localtime.bk", '/etc/localtime')
        if params.get("operation") == "stress_on_host":
            utils_test.HostStress(params, "stress").unload_stress()
