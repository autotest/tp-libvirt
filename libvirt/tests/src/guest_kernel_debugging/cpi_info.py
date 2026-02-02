import logging as log
import re
import time

from virttest import utils_misc, virsh
from virttest import utils_package
from virttest.utils_cpi import (
    CPIChecker,
    get_cpi_config,
    restore_cpi_config,
    set_cpi_config,
)

logging = log.getLogger("avocado." + __name__)


def run(test, params, env):
    """
    Test CPI (Control Program Information) functionality for s390 guests.

    This test verifies CPI functionality in various scenarios:
    - Basic CPI configuration and field retrieval for both regular and Secure Execution guests
    - Managedsave timestamp behavior (regular guests only)
    - System name length validation (regular guests only)

    For Secure Execution guests, the test automatically detects the guest type and
    verifies that CPI fields are empty/zero without CPI_PERMIT_ON_PVGUEST=1, then
    enables the permit flag and confirms fields are properly populated.

    For regular guests, the test directly verifies CPI fields match expected values
    and performs additional tests for managedsave/restore and system name validation.

    :param test: Test object
    :param params: Dictionary with test parameters
    :param env: Test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    test_case_name = params.get("test_case_name")
    serial = "yes" == params.get("use_serial", "no")
    test_system_type = params.get("test_system_type", "LINUX")
    test_system_name = params.get("test_system_name", "TESTVM")
    test_sysplex_name = params.get("test_sysplex_name", "TESTPLEX")
    expected_max_timestamp_age = float(params.get("expected_max_timestamp_age", "120"))

    utils_misc.is_qemu_function_supported(params)

    def is_secure_execution_guest():
        """
        Check if the guest is a Secure Execution guest by looking for UV indicators
        """
        session = None
        try:
            session = vm.wait_for_login(timeout=60)
            uv_folder_exists = session.cmd_status("test -d /sys/firmware/uv") == 0
            if uv_folder_exists:
                result = session.cmd_output(
                    "cat /sys/firmware/uv/prot_virt_guest 2>/dev/null || echo '0'"
                )
                prot_virt_guest = result.strip() == "1"
                return prot_virt_guest
            return False
        except Exception as e:
            logging.warning(f"Failed to check SE guest status: {e}")
            return False
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as e:
                    logging.warning(f"Failed to close session: {e}")

    def test_cpi_functionality():
        """
        Test case: CPI functionality with automatic SE guest detection and handling

        This test:
        a) Sets CPI configuration
        b) If Secure Execution guest, checks CPI fields are empty/zero, then enables
           permit_on_pvguest='1' and confirms fields are shown
        c) If regular guest, directly verifies CPI fields match expected values
        """
        logging.info("=== Test Case 1: CPI Functionality ===")

        is_se_guest = is_secure_execution_guest()
        logging.info(f"Secure Execution guest detected: {is_se_guest}")

        if is_se_guest:
            logging.info("Handling Secure Execution guest workflow")

            try:
                current_config = get_cpi_config(vm, serial=serial)
                if current_config.get("CPI_PERMIT_ON_PVGUEST") == "1":
                    test.fail(
                        "CPI_PERMIT_ON_PVGUEST is already set to '1'."
                        " This must not happen because this leaks guest"
                        " internal information to the hypervisor."
                    )
            except Exception as e:
                test.error(f"Failed to get CPI configuration: {e}")

            logging.info(
                f"Setting CPI config: system_type={test_system_type}, "
                f"system_name={test_system_name}, sysplex_name={test_sysplex_name}"
            )

            set_cpi_config(
                vm,
                system_type=test_system_type,
                system_name=test_system_name,
                sysplex_name=test_sysplex_name,
                reboot=True,
                serial=serial,
            )

            # Verify that CPI fields are empty/zero without permit_on_pvguest
            checker = CPIChecker(vm, serial=serial)
            cpi_data = checker.get_all_cpi_fields()

            if cpi_data.get("system_type") != "":
                test.fail(
                    f"Expected empty system_type for SE guest without permit, "
                    f"got: {cpi_data.get('system_type')}"
                )

            if cpi_data.get("system_name") != "":
                test.fail(
                    f"Expected empty system_name for SE guest without permit, "
                    f"got: {cpi_data.get('system_name')}"
                )

            if cpi_data.get("sysplex_name") != "":
                test.fail(
                    f"Expected empty sysplex_name for SE guest without permit, "
                    f"got: {cpi_data.get('sysplex_name')}"
                )

            if cpi_data.get("system_level") != 0:
                test.fail(
                    f"Expected system_level=0 for SE guest without permit, "
                    f"got: {cpi_data.get('system_level')}"
                )

            logging.info("SE guest without permit shows expected empty/zero values")

            logging.info("Enabling CPI_PERMIT_ON_PVGUEST=1")
            set_cpi_config(
                vm,
                system_type=test_system_type,
                system_name=test_system_name,
                sysplex_name=test_sysplex_name,
                permit_on_pvguest="1",
                reboot=True,
                serial=serial,
            )

        else:
            logging.info("Handling regular guest workflow")

            logging.info(
                f"Setting CPI config: system_type={test_system_type}, "
                f"system_name={test_system_name}, sysplex_name={test_sysplex_name}"
            )

            set_cpi_config(
                vm,
                system_type=test_system_type,
                system_name=test_system_name,
                sysplex_name=test_sysplex_name,
                reboot=True,
                serial=serial,
            )

        checker = CPIChecker(vm, serial=serial)
        results = checker.run_all_checks(expected_max_timestamp_age)

        if results["status"] != "PASS":
            test.fail(f"CPI checks failed: {results['errors']}")

        logging.info("CPI functionality test passed")

    def test_managedsave():
        """
        Test case: Test managedsave behavior

        Gets initial data and compares with final data after managedsave and start.
        """
        logging.info("=== Test Case 2: Managedsave ===")

        if is_secure_execution_guest():
            test.error("Managedsave test is not supported on Secure Execution guests")

        set_cpi_config(
            vm,
            system_type=test_system_type,
            system_name=test_system_name,
            sysplex_name=test_sysplex_name,
            reboot=True,
            serial=serial,
        )

        checker = CPIChecker(vm, serial=serial)

        initial_cpi_data = checker.get_all_cpi_fields()
        initial_timestamp = initial_cpi_data["timestamp"]

        logging.info(f"Initial CPI data: {initial_cpi_data}")
        logging.info(f"Initial timestamp: {initial_timestamp}")

        logging.info("Performing managedsave...")
        virsh.managedsave(vm_name, debug=True, ignore_status=False)
        virsh.start(vm_name, debug=True, ignore_status=False)

        # Clean up and recreate serial console after managedsave/start
        # to avoid stale console issues
        if serial:
            vm.cleanup_serial_console()
            vm.create_serial_console()

        vm.wait_for_login(timeout=60).close()

        final_cpi_data = checker.get_all_cpi_fields()

        logging.info(f"Final CPI data: {final_cpi_data}")

        # Verify all fields remain the same
        for field in [
            "system_type",
            "system_name",
            "sysplex_name",
            "system_level",
            "timestamp",
        ]:
            if initial_cpi_data.get(field) != final_cpi_data.get(field):
                test.fail(
                    f"CPI field {field} changed after managedsave: "
                    f"initial={initial_cpi_data.get(field)}, "
                    f"final={final_cpi_data.get(field)}"
                )

        results = checker.run_all_checks(expected_max_timestamp_age)

        if results["status"] != "PASS":
            test.fail(f"CPI checks failed after managedsave: {results['errors']}")

        logging.info("Managedsave test passed")

    def test_long_system_name(long_system_name):
        """
        Test case: Test system_name length validation (>8 characters)

        :param long_system_name: the system name that should be set
        """
        logging.info("=== Test Case 3: Long System Name Validation ===")

        if len(long_system_name) <= 8:
            test.error(
                "The system name for the test is not long enough."
            )

        if is_secure_execution_guest():
            test.error(
                "Long system name validation test doesn't apply to Secure Execution guests per default"
            )

        logging.info(
            f"Testing with system_name: {long_system_name} (length: {len(long_system_name)})"
        )

        set_cpi_config(vm, system_name=long_system_name, reboot=True, serial=serial)

        session = None
        try:
            session = vm.wait_for_login(timeout=60)

            result = session.cmd_output("systemctl status cpi.service", timeout=30)
            logging.info(f"CPI service status:\n{result}")

            if "failed" not in result.lower() and "inactive" not in result.lower():
                test.fail("CPI service should have failed with long system name")

            journal_output = session.cmd_output(
                "journalctl -u cpi.service --no-pager", timeout=30
            )
            logging.info(f"Journal output: {journal_output}")

            if not re.search(r"cpictl.*too long", journal_output, re.IGNORECASE):
                test.fail(
                    "Expected error message containing 'cpictl' and 'too long' not found in journal"
                )

        except Exception as e:
            test.fail(f"Failed to check CPI service status: {e}")
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as e:
                    logging.warning(f"Failed to close session: {e}")

        logging.info("Long system name validation test passed")

    def test_nested_kvm_cpi():
        """
        Test case: Test CPI system_level behavior in nested KVM environment

        Steps:
        1. L2 guest: Check /sys/firmware/cpi/system_level and verify hypervisor_bit is '0'
        2. L2 guest: enable nested and Boot L3 guest
        3. L2 guest: Check /sys/firmware/cpi/system_level and verify hypervisor_bit is '1'
        """
        logging.info("=== Test Case 4: Nested KVM CPI ===")

        def check_and_enable_nested(session):
            current_nested = None
            try:
                # Check on L2 guest
                current_nested = (session.cmd_output(
                    "cat /sys/module/kvm/parameters/nested",
                    timeout=30).strip())
                logging.info(f"Current nested status: {current_nested}")
            except Exception as e:
                logging.warning(
                    f"Failed to check current nested status: {e}")

            # Reload kvm module with nested=1 if not already enabled
            if current_nested != "1":
                logging.info("Reloading kvm module with nested=1")
                try:
                    # Reload kvm modules with nested enabled on L2 guest
                    session.cmd_output(
                        "modprobe -r kvm ; modprobe kvm nested=1",
                        timeout=30)
                    # Verify nested is enabled
                    new_status = session.cmd_output(
                        "cat /sys/module/kvm/parameters/nested",
                        timeout=30).strip()
                    if new_status != "1":
                        test.fail("Failed to enable nested virtualization")
                    logging.info(
                        "Nested virtualization enabled successfully")
                except Exception as e:
                    test.fail(f"Failed to reload kvm module: {e}")
            else:
                logging.info("Nested virtualization already enabled")

        logging.info("Step 1: Checking CPI system_level on L2 guest")
        session = None
        try:
            # Login L2 guest
            session = vm.wait_for_login(timeout=60)

            checker = CPIChecker(vm, serial=serial)
            system_level = checker.get_cpi_field("system_level")
            parsed = checker._parse_system_level(system_level)
            if parsed['hypervisor_bit'] != 0:
                test.fail(f"L2 hypervisor_bit should be 0, but actual is "
                          f"{parsed['hypervisor_bit']}")

            logging.info("Step 2: Enable nested on L2 guest and Boot L3 guest")
            check_and_enable_nested(session)

            # install qemu-kvm on L2
            logging.info("Installing qemu-kvm")
            utils_package.package_install("qemu-kvm", session=session)

            logging.info("Starting L3 guest")
            start_cmd = \
                "/usr/libexec/qemu-kvm -machine s390-ccw-virtio -no-shutdown &"
            try:
                session.sendline(start_cmd)
            except Exception as e:
                logging.error(f"Failed to start L3 guest: {e}")
                test.fail(f"Failed to start L3 guest: {e}")

            # wait L3 guest to boot up
            logging.info("waiting for L3 guest to boot")
            time.sleep(5)
            # Verify qemu-kvm process is running
            qemu_status = session.cmd_status("pgrep -f qemu-kvm")
            if qemu_status != 0:
                test.fail("L3 guest (qemu-kvm) process not found")

            logging.info("Step 3: Checking CPI system_level on L2 guest")
            system_level = checker.get_cpi_field("system_level")
            parsed = checker._parse_system_level(system_level)
            if parsed['hypervisor_bit'] != 1:
                test.fail(f"L2 hypervisor_bit should be 1, but actual is "
                          f"{parsed['hypervisor_bit']}")
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as e:
                    logging.warning(f"Failed to close session: {e}")

    def cleanup_cpi_config():
        """
        Clean up CPI configuration by restoring from backup
        """
        try:
            restore_cpi_config(vm, reboot=False, serial=serial)
            logging.info("CPI configuration restored from backup")
        except Exception as e:
            logging.warning(f"Failed to restore CPI config: {e}")

    try:
        if test_case_name == "cpi_functionality":
            test_cpi_functionality()
        elif test_case_name == "managedsave":
            test_managedsave()
        elif test_case_name == "long_system_name":
            test_long_system_name(test_system_name)
        elif test_case_name == "nested_kvm_cpi":
            test_nested_kvm_cpi()
        else:
            test.error(f"Unknown test case: {test_case_name}")

    finally:
        cleanup_cpi_config()
