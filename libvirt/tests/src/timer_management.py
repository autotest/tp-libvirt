"""
Test module for timer management.
"""

import logging
import time
from autotest.client import utils
from autotest.client.shared import error
from virttest.libvirt_xml import vm_xml, xcepts


def set_xml_clock(vms, params):
    """
    Config libvirt XML for clock.
    """
    offset = params.get("clock_offset", "utc")
    adjustment = params.get("clock_adjustment")
    timezone = params.get("clock_timezone")
    for vm in vms:
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
        logging.debug("New vm config:\n%s", vmclockxml)
        vmclockxml.sync()


def get_avail_clksrc(vm):
    """Get available clocksources in vm"""
    session = vm.wait_for_login()
    cd_cmd = "cd /sys/devices/system/clocksource/clocksource0/"
    get_cmd = "cat available_clocksource"
    # Get into path first
    session.cmd(cd_cmd)
    # Get available sources
    gets, geto = session.cmd_status_output(get_cmd)
    if gets:
        logging.debug("Get available clocksources failed:\n%s", geto)
        return []
    else:
        return geto.strip().split()


def get_current_clksrc(vm):
    """Get current clocksource in vm"""
    session = vm.wait_for_login()
    cd_cmd = "cd /sys/devices/system/clocksource/clocksource0/"
    get_cmd = "cat current_clocksource"
    # Get into path first
    session.cmd(cd_cmd)
    # Get current source
    gets, geto = session.cmd_status_output(get_cmd)
    if gets:
        logging.debug("Get current clocksource failed:\n%s", geto)
        return None
    else:
        return geto.strip()


def set_current_clksrc(vm, clocksource):
    """Set current clocksource in vm"""
    session = vm.wait_for_login()
    cd_cmd = "cd /sys/devices/system/clocksource/clocksource0/"
    set_cmd = "echo %s > current_clocksource" % clocksource
    # Get into path first
    session.cmd(cd_cmd)
    # Set current source
    sets, seto = session.cmd_status_output(set_cmd)
    if sets:
        logging.debug("Set current clocksource failed:\n%s", seto)
        return False
    return True


def recover_vm_xml(vms):
    """Recover to utc clock"""
    for vm in vms:
        logging.debug("Recover xml for %s", vm.name)
        vmclockxml = vm_xml.VMClockXML()
        vmclockxml.from_dumpxml(vm.name)
        del vmclockxml.adjustment
        del vmclockxml.timezone
        vmclockxml.offset = "utc"
        vmclockxml.timers = []
        try:
            vmclockxml.sync()
        except xcepts.LibvirtXMLError, detail:
            logging.error(detail)


def get_vm_time(vm, time_type=None):
    """
    Return epoch time.

    :param time_type: UTC or timezone time
    """
    if time_type == "utc":
        cmd = "date -u +%s"
    else:
        cmd = "date +%Y/%m/%d/%H/%M/%S"
    session = vm.wait_for_login()
    ts, timestr = session.cmd_status_output(cmd)
    session.close()
    if ts:
        logging.error("Get time in vm failed:%s", timestr)
        return -1
    if time_type == "utc":
        return int(timestr)
    else:
        return int(time.mktime(time.strptime(timestr.strip(),
                                             '%Y/%m/%d/%H/%M/%S')))


def set_host_timezone(timezone="America/New_York"):
    """Set host timezone to what we want"""
    timezone_file = "/usr/share/zoneinfo/%s" % timezone
    if utils.run("ls %s" % timezone_file, ignore_status=True).exit_status:
        raise error.TestError("Not correct timezone:%s", timezone_file)
    else:
        utils.run("unlink /etc/localtime", ignore_status=True)
        result = utils.run("ln -s %s /etc/localtime" % timezone_file,
                           ignore_status=True)
        if result.exit_status:
            raise error.TestError("Set timezone failed:%s", result)


def set_vm_timezone(vm, timezone="America/New_York"):
    """Set vm timezone to what we want"""
    timezone_file = "/usr/share/zoneinfo/%s" % timezone
    session = vm.wait_for_login()
    if session.cmd_status("ls %s" % timezone_file):
        session.close()
        raise error.TestError("Not correct timezone:%s", timezone_file)
    else:
        session.cmd("unlink /etc/localtime")
        ts, to = session.cmd_status_output("ln -s %s /etc/localtime"
                                           % timezone_file)
        if ts:
            session.close()
            raise error.TestError("Set timezone failed:%s", to)
    session.close()


def convert_tz_to_vector(tz_name="Europe/London"):
    """
    Convert string of city to a vector with utc time(hours).
    """
    # TODO: inspect timezone automatically
    zoneinfo = {'0': ["Europe/London"],
                '8': ["Asia/HongKong", "Asia/Shanghai"],
                '9': ["Asia/Tokyo"],
                '-4': ["America/New_York"]}
    for key in zoneinfo:
        if tz_name in zoneinfo[key]:
            return int(key)
    logging.error("Not supported timezone:%s", tz_name)
    return None


def test_all_timers(vms, params):
    """
    Test all available timers in vm.
    """
    host_tz = params.get("host_timezone", "Asia/Tokyo")
    vm_tz = params.get("vm_timezone", "America/New_York")
    clock_tz = params.get("clock_timezone", "Asia/Shanghai")
    host_tz_vector = convert_tz_to_vector(host_tz)
    vm_tz_vector = convert_tz_to_vector(vm_tz)
    set_tz_vector = convert_tz_to_vector(clock_tz)
    if ((host_tz_vector is None) or (vm_tz_vector is None)
       or (set_tz_vector is None)):
        raise error.TestError("Not supported timezone to convert.")
    delta = int(params.get("allowd_delta", "300"))

    # Confirm vm is down for editing
    for vm in vms:
        if vm.is_alive():
            vm.destroy()

    # Config clock in VMXML
    set_xml_clock(vms, params)

    try:
        # Logging vm to set time
        for vm in vms:
            vm.start()
            vm.wait_for_login()
            set_vm_timezone(vm, params.get("vm_timezone"))

        # Set host timezone
        set_host_timezone(params.get("host_timezone"))
    except error.TestError:
        # Cleanup for setting failure
        for vm in vms:
            vm.destroy()
        recover_vm_xml(vms)

    # Get expected utc distance between host and vms
    # with different offset(seconds)
    offset = params.get("clock_offset", "utc")
    # No matter what utc is, the timezone distance
    vm_tz_span = vm_tz_vector * 3600
    host_tz_span = host_tz_vector * 3600
    if offset == "utc":
        utc_span = 0
    elif offset == "localtime":
        utc_span = host_tz_vector * 3600
    elif offset == "timezone":
        utc_span = set_tz_vector * 3600
    elif offset == "variable":
        utc_span = int(params.get("clock_adjustment", 3600))

    # TODO: It seems that actual timezone time in vm is only based on
    # timezone on host. I need to confirm whether it is normal(or bug)
    vm_tz_span = vm_tz_span - host_tz_span

    # To track failed information
    fail_info = []

    # Set vms' clocksource(different vm may have different sources)
    # (kvm-clock tsc hpet acpi_pm...)
    for vm in vms:
        # Get available clocksources
        avail_srcs = get_avail_clksrc(vm)
        if not avail_srcs:
            fail_info.append("Get available clocksources of %s "
                             "failed." % vm.name)
            continue
        logging.debug("Available clocksources of %s:%s", vm.name, avail_srcs)

        for clocksource in avail_srcs:
            if not set_current_clksrc(vm, clocksource):
                fail_info.append("Set clocksource to %s in %s failed."
                                 % (clocksource, vm.name))
                continue

            # Wait 2s to let new clocksource stable
            time.sleep(2)

            newclksrc = get_current_clksrc(vm)
            logging.debug("\nExpected clocksource:%s\n"
                          "Actual clocksource:%s", clocksource, newclksrc)
            if newclksrc.strip() != clocksource.strip():
                fail_info.append("Set clocksource passed, but current "
                                 "clocksource is not set one.")
                continue

            # Get vm's utc time and timezone time
            vm_utc_tm = get_vm_time(vm, "utc")
            vm_tz_tm = get_vm_time(vm, "tz")
            # Get host's utc time and timezone time
            host_utc_tm = int(time.time())

            logging.debug("\nUTC time in vm:%s\n"
                          "TimeZone time in vm:%s\n"
                          "UTC time on host:%s\n",
                          vm_utc_tm, vm_tz_tm, host_utc_tm)

            # Check got time
            # Distance between vm and host
            utc_distance = vm_utc_tm - host_utc_tm
            # Distance between utc and timezone time in vm
            vm_tz_distance = vm_tz_tm - vm_utc_tm
            logging.debug("\nUTC distance:%s\n"
                          "VM timezone distance:%s\n"
                          "Expected UTC distance:%s\n"
                          "Expected VM timezone distance:%s",
                          utc_distance, vm_tz_distance,
                          utc_span, vm_tz_span)
            # Check UTC time
            if abs(utc_distance - utc_span) > delta:
                fail_info.append("UTC time between host and %s do not match."
                                 % vm.name)
            # Check timezone time of vm
            if abs(vm_tz_distance - vm_tz_span) > delta:
                fail_info.append("Timezone time of %s is not right." % vm.name)

        # Useless, so shutdown for cleanup
        vm.destroy()

    if len(fail_info):
        raise error.TestFail(fail_info)


def run(test, params, env):
    """
    Test vms' time according timer management of XML configuration.
    """
    timer_type = params.get("timer_type", "all_timers")
    vms = env.get_all_vms()
    if not len(vms):
        raise error.TestNAError("No available vms")

    testcase = globals()["test_%s" % timer_type]
    try:
        testcase(vms, params)
    finally:
        for vm in vms:
            vm.destroy()
        # Reset all vms to utc time
        recover_vm_xml(vms)
