import re
import time

from avocado.utils import process

from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt
from virttest import virsh
from virttest import utils_libvirtd

from provider.virsh_cmd_check import virsh_cmd_check_base


def run(test, params, env):
    """
    Verify no error msg prompts without guest memory balloon driver.
    Scenario:
    1.memory balloon models: virtio, virtio-transitional, virtio-non-transitional.
    """

    def setup_vm_memory():
        """
        Setup general vm memory and current memory tags  according params - doesn't change the VM if not set
        """

        # QUESTION do we need to check if the memory is available on host?
        # setup memory according provided settings
        vm_memory_attrs = {}
        try:
            if mem_unit == "" or mem_value == "":
                test.log.debug("Memory unit and/or memory value not provided, xml NOT changed")
            else:
                vm_memory_attrs['memory_unit'] = mem_unit
                vm_memory_attrs['memory'] = int(mem_value)
            # current_mem_unit & current_mem got from params earlier
            if current_mem_unit == "" or current_mem == "":
                test.log.debug("Current Memory unitm and/or value not provided, xml NOT changed")
            else:
                vm_memory_attrs['current_mem_unit'] = current_mem_unit
                vm_memory_attrs['current_mem'] = int(current_mem)
            if len(vm_memory_attrs) > 0:
                vmxml.setup_attrs(**vm_memory_attrs)
        except Exception as e:
            test.log.error(f"Wrong test configuration: {(str(e))}")
        test.log.debug(f"setting memory if provided(count: {(len(vm_memory_attrs))}) (mem: {mem_unit}, {mem_value}, " +
                       f"current: {current_mem_unit}, {current_mem}")

    def setup_vm_memballoon():
        """
        Setup vm memballoon inside the vm according params
        consuming memballoon_model, membal_alias_name, we have from provided test params
        """
        # not sure if it can be empty, but assuming not ... we will use the "" as not set
        # QUESTION: in other tests there is first the memballoon device removed
        if memballoon_model == "" or memballoon_alias_name == "":
            test.error(f"Wrong test configuration: memballoonloon model and alias are mandatory. Provided values: model ({memballoon_model}), alias ({memballoon_alias_name}).")

        memballoon_stats_period = params.get("memballoon_stats_period", "10")
        test.log.debug(f"Setting add memballoonloon ({memballoon_model}, {memballoon_alias_name}).")
        balloon_dict = {
            'membal_model': memballoon_model,
            'membal_alias_name': memballoon_alias_name,
            'membal_stats_period': memballoon_stats_period
        }

        libvirt.update_memballoon_xml(vmxml, balloon_dict)

    def check_device_on_qemu_cmd_line():
        """
        Checks the QEMU command line for the presence of a specific memballoon device.
        This function retrieves the QEMU command line arguments using `ps -ef`,
        then searches for a memballoon device using a regular expression. It verifies
        whether the detected device matches the expected `memballoon_driver` and `memballoon_alias_name`.
        """
        test.log.info("TEST_STEP 4. Check the qemu cmd line")
        try:
            qemu_cmd = "ps -ef | grep qemu-kvm | grep -v grep"
            qemu_cmd_output = process.run(qemu_cmd, shell=True).stdout_text.strip()

            memballoon_driver = params.get("memballoon_driver", "")
            pattern = fr'-device\s+{{"driver":"({memballoon_driver})".*?"id":"([^"]+)".*?}}'
            matches = re.findall(pattern, qemu_cmd_output)
            test.log.debug(f"model: {memballoon_model}, matches: {len(matches)}")

            device_found = False
            if memballoon_model != "none":
                for _, device_id in matches:
                    if device_id == memballoon_alias_name:
                        device_found = True
                        test.log.info(f"Found correct id: {device_id} with expected memballoon_driver")
                    else:
                        test.log.debug(f"Found device with expected memballoon_driver ({memballoon_driver}) but different ID {device_id}")
                if not device_found:
                    test.fail(f"There is no device for driver ({memballoon_driver}) and id ({device_id})")
                else:
                    return

            if len(matches) > 0:
                driver, device_id = matches[0]
                test.fail(f"For memballoon_model == none, there shouldn't be device with balloon related driver ({driver}) and id ({device_id})")

        except Exception as e:
            test.error(f"Failed with: {str(e)}")

    def check_device_on_guest():
        """
        This function logs into the guest, runs the `lspci` command to check for
        a memory balloon device
        If `memballoon_model` is set to "none", check no such device is detected.
        """
        test.log.info("TEST_STEP 5. Check device exists on guest")
        guest_cmd = "lspci -vvv | grep balloon"
        guest_session = vm.wait_for_login()

        status, stdout = guest_session.cmd_status_output(guest_cmd)
        guest_session.close()
        test.log.debug(f"guest cmd result: {status}, stdout: {stdout}")

        if memballoon_model == "none":
            if status != 1 or stdout != "":
                test.fail(f"There shouldn't be any device returned from guest lscpi command with memballoon model none.")
        else:
            if status != 0:
                test.fail("Failed to run lscpi command on guest and get device info: ")

            # check returned stdout contains memory balloon string
            # the searched string can be set in configuration, or it is by default memory balloon
            memballoon_device_str = params.get("memballoon_device_str", "memory balloon")
            if not re.search(memballoon_device_str, stdout):
                test.fail(f"Expected string {memballoon_device_str} was not returned from guest lspci command. Returned: ")

    def setup_and_start_vm():
        # setup memory and current memory according fixed settings
        test.log.info("TEST_STEP 1. define VM")
        setup_vm_memory()
        # setup memballoon according provided parameters
        setup_vm_memballoon()

        test.log.info("TEST_STEP 2. start VM")
        vm.start()

    def check_vm_xml(expect_xpath, xpath_exists=True):
        """
        Check if the given XPath exists in the VM XML.

        :param expect_xpath: The expected XPath to check for.
        :type expect_xpath: str
        :param xpath_exists: Whether the XPath is expected to exist, default True.
        :type xpath_exists: bool, optional
        """
        if not libvirt_vmxml.check_guest_xml_by_xpaths(
                vmxml, expect_xpath, ignore_status=True) == xpath_exists:
            test.fail(f"Expects to get '{expect_xpath}' in xml")
        test.log.info('Correct element found in XML')

    def wait_for_dominfo(changed_mem_value, timeout=20):
        """
        Wait for the dominant output to match the expected memory usage pattern.

        This function checks the DOMINFO command output periodically until it matches
        the expected pattern of Max and Used memory values. If the expected state is not reached within
        the specified timeout period, an exception is raised.

        Args:
            changed_mem_value (int): The expected used memory value in KiB.
            timeout (int, optional): The maximum time to wait for the condition to be met, default is 20 seconds.

        Raises:
            Exception: If the dominant output does not match the expected pattern within the specified timeout.
        """
        start_time = time.time()
        dominfo_check = rf"Max memory:\s+{mem_value} KiB\nUsed memory:\s+{changed_mem_value} KiB"
        last_exception = None
        while time.time() - start_time < timeout:
            dominfo = virsh.dominfo(vm_name, ignore_status=True, debug=True)
            test.log.debug(f"check {dominfo}, {start_time}, {time.time()}")
            try:
                libvirt.check_result(dominfo, expected_match=dominfo_check)
                return
            except Exception as e:
                last_exception = e
                time.sleep(0.5)  # polling interval
        raise last_exception

    @virsh.EventTracker.wait_event
    def try_setmem(vm_name, size,
                   expected_status=0,
                   expected_error='',
                   event_type='',
                   wait_for_event=False,
                   event_timeout=1, **dargs):
        """
        Check events when setting guest agent status

        :param name: Name of domain.
        :param event_type: type of the event
        :param event_timeout: timeout for virsh event command
        :param dargs: standardized function keywords
        """
        result = virsh.setmem(vm_name, size)
        test.log.debug(f"inner try_setmem({result.exit_status}={expected_status}, \n{result.stderr}\n{expected_error}\n): {result}")
        if result.exit_status != int(expected_status) or result.stderr.strip() != expected_error:
            test.fail(f"Unexpected result of virsh setmem {size} command: got status={result.exit_status}, error={result.stderr}  ")

        return result

    def consume_guest_memory():
        guest_session = vm.wait_for_login()
        free_mem = utils_memory.freememtotal(guest_session)
        test.log.debug('Free mem on vm: %d kB', free_mem)

        result = guest_session.cmd('swapoff -a')
        test.log.debug(f"swapoff result: {result}")
        if result != '':
            test.fail(f"Command swapoff -a on guest failed with error {result}")
        memory_to_consume = free_mem - 100000
        result = guest_session.cmd(f"memhog {memory_to_consume}K")
        if not re.fullmatch(r"\.+", result.strip()):
            test.fail(f"Command memhog {memory_to_consume}k failed with error {result}")
        guest_session.close()

    def run_test():
        """
        Setup vm memballoon device model and alias according params

        :params vm_name: VM name (for debug log).
        :params vmxml: vmxml object
        :params params: Dictionary with the test parameters
        """

        test.log.debug('TEST_STEP 3. check VM XML for memballoon element')
        expected_xpath = [{'element_attrs': [f".//memballoon[@model='{memballoon_model}']/alias[@name='{memballoon_alias_name}']"]}]
        check_vm_xml(expected_xpath)  # TEST_STEP 3. check VM XML for memballoon element
        check_device_on_qemu_cmd_line()  # TEST_STEP 4. Check the qemu cmd for device & id)
        check_device_on_guest()  # TEST_STEP 5. Check device exists on guest

        save_file_name = "/tmp/avocado.save"
        test.log.debug('TEST_STEP 6. save and restore VM ')
        virsh_helper.check_save_restore(save_file_name)

        test.log.debug('TEST_STEP 7. restart virtqemud')
        if not libvirtd.restart():
            test.fail("fail to restart libvirtd")

        not_none_model = (memballoon_model != "none")

        if not_none_model:
            test.log.debug('TEST_STEP 8. check disk/memory cache ')
            virsh_helper.check_disk_caches()

        test.log.debug('TEST_STEP 9. set memory to lower_mem_size')
        lower_mem_size = params.get("lower_mem_value", "1843200")
        higher_mem_size = params.get("higher_mem_value", "3145728")
        sized_event_type = params.get("sized_event_type", "no")
        if sized_event_type == "yes":
            sized_event_type_str = rf"event 'balloon-change' for domain '{vm_name}': \d+KiB.*\nevent 'balloon-change' for domain '{vm_name}': {lower_mem_size}KiB"
        else:
            sized_event_type_str = ""

        lower_error_status = params.get("lower_error_status", 0)
        lower_error_msg = params.get("lower_error_msg", "")
        try_setmem(vm_name, lower_mem_size, event_type=sized_event_type_str, event_timeout=10, wait_for_event=True, expected_status=lower_error_status, expected_error=lower_error_msg)

        if not_none_model:
            test.log.debug('TEST_STEP 10. send SIGSTOP signal')
            kill_cmd = "kill -19 `pidof qemu-kvm`"
            process.run(kill_cmd, shell=True).stdout_text.strip()

            test.log.debug(f"TEST_STEP 11: verify dominfo values for {lower_mem_size}")
            wait_for_dominfo(lower_mem_size)

            test.log.debug('TEST_STEP 12. check memory allocation')
            expected_xpath = [{'element_attrs': [f".//memory[@unit='{mem_unit}']"], 'text': f"{mem_value}"},
                              {'element_attrs': [f".//currentMemory[@unit='{current_mem_unit}']"], 'text': f"{current_mem}"}]
            check_vm_xml(expected_xpath)

            test.log.debug('TEST_STEP 13. send CONT signal')
            kill_cmd = "kill -18 `pidof qemu-kvm`"
            process.run(kill_cmd, shell=True).stdout_text.strip()

            test.log.debug(f"TEST_STEP 14. set memory back to original_mem_size {current_mem}")
            try_setmem(vm_name, current_mem)

            test.log.debug(f"TEST_STEP 15. check memory allocation by dominfo for {current_mem}")
            wait_for_dominfo(current_mem)

            test.log.debug('TEST_STEP 16. set memory to higher memory ')
            higher_error_msg = params.get("higher_error_msg", "")
            try_setmem(vm_name, higher_mem_size, expected_status=1, expected_error=higher_error_msg)

        test.log.debug('TEST_STEP 17. consume the guest memory (in guest)')
        consume_guest_memory()
        test.log.debug('TEST_STEP 18. destroy the guest.')
        vm.destroy()
        test.log.debug('TEST_STEP 19. set memory to any other memory ')
        not_running_msg = params.get("not_running_msg", "")
        try_setmem(vm_name, higher_mem_size, expected_status=1, expected_error=not_running_msg)
        test.log.debug('TEST_STEP END.')

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        virsh_helper.teardown()
        bkxml.sync()

    test.log.info("TEST_SETUP: backup and define: guest ")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    virsh_helper = virsh_cmd_check_base.VirshCmdCheck(vm, vm_name, params, test)
    libvirtd = utils_libvirtd.Libvirtd("virtqemud")
    bkxml = vmxml.copy()

    memballoon_model = params.get("memballoon_model", "")
    memballoon_alias_name = params.get("memballoon_alias_name", "")

    mem_unit = params.get("mem_unit", "")
    mem_value = params.get("mem_value", "")

    current_mem = params.get("current_mem", "")
    current_mem_unit = params.get("current_mem_unit", "")

    try:
        setup_and_start_vm()
        run_test()

    finally:
        teardown_test()
