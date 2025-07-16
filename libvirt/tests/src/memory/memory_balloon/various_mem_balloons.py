import re
import time

from avocado.utils import process

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml, libvirt_memory
from virttest.utils_test import libvirt
from virttest import virsh
from virttest import utils_libvirtd

from provider.virsh_cmd_check import virsh_cmd_check_base


def run(test, params, env):
    """
    Verify no error msg prompts without guest memory balloon driver.
    Scenario:
    1.memory balloon models: virtio, virtio-non-transitional, none.
    """

    def check_device_on_qemu_cmd_line():
        memballoon_driver = params.get("memballoon_driver", "")
        pattern = (
            r'-device\s+\{'
            fr'(?=[^{{}}]*?"id":"{memballoon_alias_name}")'
            fr'(?=[^{{}}]*?"driver":"{memballoon_driver}")'
            r'[^}]*\}'
            )

        expect_exist = (memballoon_model != "none")
        libvirt.check_qemu_cmd_line(pattern, expect_exist=expect_exist)

    def check_device_on_guest(guest_session=None, close_session=False):
        """
        Check if memory balloon device exists on guest.

        :param guest_session (optional): Guest session object.
        :param close_session (bool, optional): Close the guest session after running command. Defaults to False.

        :returns guest_session: Updated guest session object.
        """

        test.log.info("TEST_STEP: 5. Check device exists on guest")
        guest_cmd = "lspci -vvv"
        if guest_session is None:
            guest_session = vm.wait_for_login()

        status, stdout = guest_session.cmd_status_output(guest_cmd)
        if close_session:
            guest_session.close()
        test.log.debug(f"guest cmd result: {status}, stdout: {stdout}")

        # check returned stdout contains memory balloon string
        memballoon_device_str = params.get("memballoon_device_str", "memory balloon")
        if not re.search(memballoon_device_str, stdout):
            if model_defined:
                test.fail(f"Expected string {memballoon_device_str} was not returned from guest lspci command. Returned: {stdout}")
        elif not model_defined:
            test.fail("Unexpected devices returned with 'memballoon_model' set as 'none'.")

        return guest_session

    def setup_and_start_vm():
        """
        Setup configured memory and memory balloons and start a VM
        """
        # setup memory and current memory according fixed settings
        test.log.info("TEST_STEP: define VM - add memory and current memory according test configuration")
        mem_attrs = eval(params.get("mem_attrs", "{}"))
        vmxml.setup_attrs(**mem_attrs)

        test.log.info("TEST_STEP: define VM - add memory balloon to XML")
        memballoon_stats_period = params.get("memballoon_stats_period", "10")
        test.log.debug(f"Settings to add memballoon ({memballoon_model}, {memballoon_alias_name}).")
        balloon_dict = {
            'membal_model': memballoon_model,
            'membal_alias_name': memballoon_alias_name,
            'membal_stats_period': memballoon_stats_period
        }

        libvirt.update_memballoon_xml(vmxml, balloon_dict)

        test.log.info("TEST_STEP: start VM")
        vm.start()

    def check_vm_xml(expect_xpath, xpath_exists=True):
        """
        Check if the given XPath exists in the VM XML.

        :param expect_xpath (str): The expected XPath to check for.
        :param xpath_exists (bool, optional): Whether the XPath is expected to exist, default True.
        """
        test.log.debug(f"TEST_STEP: check if XPath exists in XML: {expect_xpath}")
        if not libvirt_vmxml.check_guest_xml_by_xpaths(
                vmxml, expect_xpath, ignore_status=True) == xpath_exists:
            test.fail(f"Expects to get '{expect_xpath}' in xml")
        test.log.info('Correct element found in XML')

    def wait_for_dominfo(changed_mem_value, timeout=20):
        """
        Wait for the dominant output to match the expected memory usage pattern.

        Checks the DOMINFO command output periodically until it matches
        the expected pattern of Max and Used memory values. If the expected state is not reached within
        the specified timeout period, an exception is raised.

        :param changed_mem_value (int): The expected used memory value in KiB.
        :param timeout (int, optional): The maximum time to wait for the condition to be met, default is 20 seconds.

        Raises:
            Exception: If the dominant output does not match the expected pattern within the specified timeout.
        """
        test.log.debug(f"TEST_STEP: verify dominfo values for {changed_mem_value}")

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
        test.log.debug(f"TEST_STEP. set memory to {size}")

        result = virsh.setmem(vm_name, size)
        test.log.debug(f"inner try_setmem({result.exit_status}={expected_status}, \n{result.stderr}\n{expected_error}\n): {result}")
        if result.exit_status != int(expected_status) or (expected_error not in result.stderr):
            test.fail(f"Unexpected result of virsh setmem {size} command: got status={result.exit_status}, error={result.stderr}  ")

        return result

    def consume_guest_memory(guest_session=None, close_session=False):
        """
        Consumes free memory on the guest VM.

        This function logs in to the guest session, retrieves the available free memory,
        calculates how much memory to consume (free mem minus 100K), and then attempts
        to consume that amount of memory.
        """
        test.log.debug('TEST_STEP: consume the guest memory')
        if guest_session is None:
            guest_session = vm.wait_for_login()
        (status, _) = libvirt_memory.consume_vm_freememory(guest_session)
        if status:
            test.fail(f"Memory consume failed")
        if close_session:
            guest_session.close()
        return guest_session

    def check_kill_sig(lower_mem_size, higher_mem_size):
        """
        Check memory allocation after sending SIGSTOP and CONT signals to the VM.

        :param lower_mem_size: Lower memory size in format <size><unit> e.g., '1024M'.
        :param higher_mem_size: Higher memory size in format <size><unit> e.g., '512M'.
        :type higher_mem_size: str
        """
        test.log.debug('TEST_STEP: 10. send SIGSTOP signal')
        kill_cmd = "kill -19 `pidof qemu-kvm`"
        process.run(kill_cmd, shell=True).stdout_text.strip()

        wait_for_dominfo(lower_mem_size)

        test.log.debug('TEST_STEP: 12. check memory allocation')
        expected_xpath = [{'element_attrs': [f".//memory[@unit='{mem_unit}']"], 'text': f"{mem_value}"},
                          {'element_attrs': [f".//currentMemory[@unit='{current_mem_unit}']"],
                           'text': f"{current_mem}"}]
        check_vm_xml(expected_xpath)

        test.log.debug('TEST_STEP: 13. send CONT signal')
        kill_cmd = "kill -18 `pidof qemu-kvm`"
        process.run(kill_cmd, shell=True).stdout_text.strip()

        try_setmem(vm_name, current_mem)
        wait_for_dominfo(current_mem)

        higher_error_msg = params.get("higher_error_msg", "")
        try_setmem(vm_name, higher_mem_size, expected_status=1, expected_error=higher_error_msg)

    def try_to_setmem_on_destroyed_vm(vm_name, mem_size):
        """
        Test to set memory on a destroyed VM.

        :param vm_name: Name of the VM.
        :param mem_size: Memory size to set.
        """
        test.log.debug('TEST_STEP: 18. destroy the guest.')
        vm.destroy()
        test.log.debug('TEST_STEP: 19. set memory to any other memory ')
        not_running_msg = params.get("not_running_msg", "")
        try_setmem(vm_name, mem_size, expected_status=1, expected_error=not_running_msg)

    def run_test():
        """
        Setup vm memballoon device model and alias according params

        :params vm_name: VM name (for debug log).
        :params vmxml: vmxml object
        :params params: Dictionary with the test parameters
        """

        test.log.debug('TEST_STEP: 3. check VM XML for memballoon element')
        expected_xpath = [{'element_attrs': [f".//memballoon[@model='{memballoon_model}']/alias[@name='{memballoon_alias_name}']"]}]
        check_vm_xml(expected_xpath)
        check_device_on_qemu_cmd_line()  # TEST_STEP: 4. Check the qemu cmd for device & id)
        guest_session = check_device_on_guest()  # TEST_STEP: 5. Check device exists on guest

        virsh_helper.check_save_restore(save_file_name)  # TEST_STEP: 6. save and restore

        test.log.debug('TEST_STEP: 7. restart virtqemud')
        if not libvirtd.restart():
            test.fail("fail to restart libvirtd")

        if model_defined:
            virsh_helper.check_disk_caches()

        test.log.debug('TEST_STEP: 9. set memory to lower_mem_size')
        if sized_event_type == "yes":
            sized_event_type_str = rf"event 'balloon-change' for domain '{vm_name}': \d+KiB.*\nevent 'balloon-change' for domain '{vm_name}': {lower_mem_size}KiB"
        else:
            sized_event_type_str = ""
        try_setmem(vm_name, lower_mem_size, event_type=sized_event_type_str, event_timeout=10,
                   wait_for_event=True, expected_status=lower_error_status, expected_error=lower_error_msg)

        if model_defined:
            check_kill_sig(lower_mem_size, higher_mem_size)

        consume_guest_memory(guest_session, close_session=True)
        try_to_setmem_on_destroyed_vm(vm_name, higher_mem_size)

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
    model_defined = (memballoon_model != "none")

    mem_unit = params.get("mem_unit", "")
    mem_value = params.get("mem_value", "")

    current_mem = params.get("current_mem", "")
    current_mem_unit = params.get("current_mem_unit", "")
    lower_mem_size = params.get("lower_mem_value", "1843200")
    lower_error_status = params.get("lower_error_status", 0)
    lower_error_msg = params.get("lower_error_msg", "")
    higher_mem_size = params.get("higher_mem_value", "3145728")
    sized_event_type = params.get("sized_event_type", "no")
    save_file_name = "/tmp/avocado.save"
    try:
        setup_and_start_vm()
        run_test()

    finally:
        teardown_test()
