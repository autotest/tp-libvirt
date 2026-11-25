import os
import time
import threading
import logging as log

from virttest import utils_kdump
from virttest import utils_misc
from virttest import utils_test
from virttest import virsh
from virttest.libvirt_xml import vm_xml


logging = log.getLogger("avocado." + __name__)


def run(test, params, env):
    """
    Test: Firmware-assisted dump (fadump) of guest

    param test:   kvm test object
    param params: Dictionary with the test parameters
    param env:    Dictionary with test environment.

    This script is used to test the fadump functionality of the guest(s).
    It is supported only on ppc64le guests. The kdump service is
    configured first, as fadump relies on it to register and to save
    the vmcore.
    1. Enable fadump in the guest(s) and check it is enabled and registered
    2. Unregister and register fadump again if specified
    3. Get the vm-cores present in the guest
    4. Load the stress app if specified
    5. Trigger crash simultaneously in all guests
    6. Check fadump is registered again and a new vm-core is generated
    7. Debug vm-core using crash utility if specified
    """
    # This test always runs with fadump, regardless of the cfg
    params["dump_type"] = "fadump"
    vms = env.get_all_vms()
    guest_stress = params.get("guest_stress", "no") == "yes"
    crash_utility = params.get("crash_utility", "no") == "yes"
    setup_kdump = params.get("setup_kdump", "yes") == "yes"
    toggle_registration = params.get("toggle_registration", "no") == "yes"
    stress_time = params.get("stress_time", "30")
    registration_timeout = int(params.get("registration_timeout", "300"))
    debug_dir = params.get("debug_dir", "/home/")
    dump_options = params.get("dump_options", "--memory-only --bypass-cache")

    def get_distro_info(vm):
        """
        Get the distro info of the vm, cancel the test if not supported
        Current supported Distros: rhel, fedora, ubuntu, sles, suse

        param vm: vm object
        returns: distro info dictionary
        """
        session = vm.wait_for_login(timeout=240)
        distro_info = utils_kdump.get_distro_info(session)
        session.close()
        if distro_info["name"] == "unknown":
            test.cancel("Guest distro not supported")
        return distro_info

    def toggle_fadump_registration(vm):
        """
        Unregister and register fadump again using sysfs

        param vm: vm object
        returns: None
        """
        distro_info = get_distro_info(vm)
        session = vm.wait_for_login(timeout=240)
        try:
            logging.info("Unregistering fadump in %s", vm.name)
            if not utils_kdump.set_fadump_registration(
                    session, distro_info, register=False):
                test.fail("Unable to unregister fadump in %s" % vm.name)
            logging.info("Registering fadump in %s", vm.name)
            if not utils_kdump.set_fadump_registration(
                    session, distro_info, register=True):
                test.fail("Unable to register fadump in %s" % vm.name)
        finally:
            session.close()

    def wait_fadump_registered(vm):
        """
        Wait for fadump to be registered, the kdump service registers it
        again on boot after the crash

        param vm: vm object
        returns: True if fadump got registered, False on timeout
        """
        distro_info = get_distro_info(vm)
        session = vm.wait_for_login(timeout=240)
        try:
            return utils_misc.wait_for(
                lambda: utils_kdump.is_fadump_registered(session, distro_info),
                registration_timeout, 0, 10)
        finally:
            session.close()

    def load_guest_stress(vms):
        """
        Load stress app in all the vms

        param vms: all vm objects
        returns: None
        """
        logging.info("Starting stress app in guest(s)")
        try:
            utils_test.load_stress("stress_in_vms", params=params, vms=vms)
        except Exception as err:
            test.fail("Error running stress in vms: %s" % str(err))

    def unload_guest_stress(vms):
        """
        Unload stress app in all the vms

        param vms: all vm objects
        returns: None
        """
        logging.info("Stopping stress app in guest(s)")
        utils_test.unload_stress("stress_in_vms", params=params, vms=vms)

    def virsh_dump(failed_vms):
        """
        Take virsh dump of guest in case of guest failure

        param failed_vms: vm objects which are failed/broken
        returns: None
        """
        logging.info("Dumping failed vms to directory %s", debug_dir)
        for vm in failed_vms:
            if vm.state() != "shut off":
                logging.debug("Dumping %s to debug_dir %s", vm.name, debug_dir)
                virsh.dump(vm.name, os.path.join(debug_dir, vm.name + "-core"),
                           dump_options, ignore_status=False,
                           debug=True)
                logging.debug("Successfully dumped %s as %s-core", vm.name, vm.name)
            else:
                logging.debug("Cannot dump %s as it is in shut off state", vm.name)

    def trigger_crash(vm):
        """
        Trigger crash in the vm, failures are recorded as an exception
        raised in a thread does not fail the test

        param vm: vm object
        returns: None
        """
        try:
            kdump_managers[vm.name].trigger_crash(wait_time=120)
        except Exception as err:
            crash_errors[vm.name] = str(err)

    # Declaring variables before starting test
    failed_vms = set()
    virsh_dump_vms = set()
    crash_errors = {}
    kdump_managers = {
        vm.name: utils_kdump.KdumpManager(vm, params, test) for vm in vms
    }

    # Set on_crash value to restart in guests
    for vm in vms:
        logging.info("Setting on_crash to restart in %s", vm.name)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml.on_crash = "restart"
        vmxml.sync()
        vm.start()

    # Disable bracketed paste in guests, its escape codes in the serial
    for vm in vms:
        session = vm.wait_for_login(timeout=240)
        session.cmd(
            "grep -qs '^set enable-bracketed-paste off' /etc/inputrc || "
            "printf '\\nset enable-bracketed-paste off\\n' >> /etc/inputrc"
        )
        session.close()

    # Record the initial fadump state to restore it after the test
    initial_fadump = {}
    for vm in vms:
        session = vm.wait_for_login(timeout=240)
        initial_fadump[vm.name] = utils_kdump.is_fadump_param_enabled(session)
        session.close()

    try:
        # Setup kdump packages/configuration before validation
        if setup_kdump:
            for vm in vms:
                kdump_managers[vm.name].setup()

        # Enable fadump regardless of setup_kdump. This is a no-op, without
        # reboot, if fadump is already enabled in the guest.
        for vm in vms:
            utils_kdump.configure_fadump(
                vm, get_distro_info(vm), enable=True, test=test
            )

        # Check for kdump service and fadump if they are operational
        for vm in vms:
            kdump_managers[vm.name].check_service()

        # Unregister and register fadump again
        if toggle_registration:
            for vm in vms:
                toggle_fadump_registration(vm)

        # Check for the present vm-cores in guests
        for vm in vms:
            kdump_managers[vm.name].get_vmcores_before_crash()

        # Load the stress app
        if guest_stress:
            load_guest_stress(vms)
            logging.info("Started running stress app")
            logging.info("Sleeping for %s seconds", stress_time)
            time.sleep(int(stress_time))

            for vm in vms:
                if utils_kdump.check_guest_status(vm):
                    failed_vms.add(vm.name)
                    virsh_dump_vms.add(vm)
            if failed_vms:
                virsh_dump(virsh_dump_vms)
                test.fail("Guest %s not running after running stress" % failed_vms)

        # Trigger crash in guests in parallel
        fadump_threads = []
        for vm in vms:
            fadump_threads.append(
                threading.Thread(target=trigger_crash, args=(vm,))
            )
        time.sleep(20)
        for fadump_thread in fadump_threads:
            fadump_thread.start()
        for fadump_thread in fadump_threads:
            fadump_thread.join()
        if crash_errors:
            virsh_dump([vm for vm in vms if vm.name in crash_errors])
            test.fail("Unable to trigger crash: %s" % crash_errors)

        # Check guest status after crash
        for vm in vms:
            try:
                if utils_kdump.check_guest_status(vm):
                    raise Exception("Guest %s not running after triggering crash" % vm.name)
                session = vm.wait_for_login(timeout=240)
                logging.info("Able to login into %s", vm.name)
                session.close()
            except Exception as err:
                logging.debug("Error occured %s", str(err))
                failed_vms.add(vm.name)
                virsh_dump_vms.add(vm)
        if failed_vms:
            virsh_dump(virsh_dump_vms)
            test.fail("Unable to login into %s after triggering crash" % failed_vms)

        # Check for the vm-cores in guests after crash
        for vm in vms:
            kdump_managers[vm.name].get_vmcores_after_crash()

        # Check if vm-core got generated after crash in guests
        for vm in vms:
            if not kdump_managers[vm.name].get_new_vmcores():
                failed_vms.add(vm.name)
        if failed_vms:
            test.fail("vmcore not generated in %s" % failed_vms)

        # Check fadump is registered again after the crash
        for vm in vms:
            if not wait_fadump_registered(vm):
                failed_vms.add(vm.name)
        if failed_vms:
            test.fail("fadump not registered in %s after crash" % failed_vms)

        # Debug vm-core using crash utility tool
        if crash_utility:
            for vm in vms:
                kdump_managers[vm.name].setup_crash_utility()
                kdump_managers[vm.name].analyze_vmcore()

        # Unload the stress app in guests
        if guest_stress:
            unload_guest_stress(vms)
    finally:
        # Restore the initial fadump state, a failure here must not hide the
        # result of the test
        for vm in vms:
            try:
                utils_kdump.configure_fadump(
                    vm, get_distro_info(vm), enable=initial_fadump[vm.name], test=test
                )
            except Exception as err:
                logging.warning("Unable to restore fadump in %s: %s", vm.name, err)
