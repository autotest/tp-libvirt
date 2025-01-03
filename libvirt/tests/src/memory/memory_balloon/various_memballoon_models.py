from virttest import virsh

from avocado.utils import process

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memballoon
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify no error msg prompts without guest memory balloon driver.
    Scenario:
    1.memory balloon models: virtio, virtio-transitional, virtio-non-transitional.
    """

    def setup_vm_memory(vmxml, params):
        """
        Setup vm memory and current memory tags  according params

        :params vmxml: vmxml object
        :params params: Dictionary with the test parameters
        """

        # TODO do we need to check if the memory is available on host?
        # setup memory according fixed settings
        vm_memory_attrs = {}
        try:
            mem_unit = params.get("mem_unit", "")
            mem_value = params.get("mem_value","")
            if mem_unit == "" or mem_value == "":
                test.log.debug("Memory unit and/or memory value not provided, xml NOT changed")
            else: 
                vm_memory_attrs['memory'] = mem_value
                vm_memory_attrs['memory_unit'] = mem_unit
            current_mem_unit = params.get("current_mem_unit", "")
            current_mem = params.get("current_mem", "")
            if current_mem_unit == "" or current_mem == "":
                test.log.debug("Current Memory unitm and/or value not provided, xml NOT changed")
            else:
                vm_memory_attrs['current_mem'] = current_mem
                vm_memory_attrs['current_mem_unit'] = current_mem_unit
        except Exception as e:
            test.log.error(f"Wrong test configuration: {(str(e))}")
        test.log.debug(f"setting memory if provided({(len(vm_memory_attrs))}) (mem: {mem_unit}, {mem_value}, " + 
                       f"cur: {current_mem_unit}, {current_mem}")
        if len(vm_memory_attrs)>0:
            vmxml.setup_attrs(**vm_memory_attrs)

    def setup_vm_memballoon(vmxml, params):
        """
        Setup vm memballoon according params
        ! be aware, that vmxml.sync is part of update_memballoon_xml 

        :params vmxml: vmxml object
        :params params: Dictionary with the test parameters
        """
        # not sure if it can be empty, but assuming not ... we will use the "" as not set
        # TODO question: in other tests there is first the memballoon device removed
        membal_model = params.get("membal_model", "")
        membal_alias_name = params.get("membal_alias_name", "")
        if membal_model == "" or membal_alias_name =="":
            test.error(f"Wrong test configuration: memballoon model and alias are mandatory. Provided values: model ({membal_model}), alias ({membal_alias_name}).")

        membal_stats_period = params.get("membal_stats_period")
        test.log.debug(f"Setting add memballoon ({membal_model}, {membal_alias_name}).")
        balloon_dict = {
            'membal_model': membal_model,
            'membal_alias_name': membal_alias_name,
            'membal_stats_period': membal_stats_period
        }

        libvirt.update_memballoon_xml(vmxml, balloon_dict)

    def check_qemu_cmd_line():
        # org_umask = process.run('umask', verbose=True).stdout_text.strip()
        try:
            qemu_cmd_output = process.run('ps -ef | grep qemu-kvm | grep -v grep').stdout_text.strip()
            test.log.info(f"Result of qemu cmd output {qemu_cmd_output}")
        except Exception as e:
            test.error(f"Failed with: {str(e)}")


    def run_test():
        """
        Setup vm memballoon device model and aliase according params

        :params vm_name: VM name (for debug log).
        :params vmxml: vmxml object
        :params params: Dictionary with the test parameters
        """

        # setup memory and current memory according fixed settings
        test.log.info("TEST_STEP 1. define VM")
        setup_vm_memory(vmxml, params)

        # setup memballoon according provided parameters
        setup_vm_memballoon(vmxml, params)

        test.log.info("TEST_STEP 2. start VM")
        vm.start() # 

        test.log.info("TEST STEP 3. Check the qemu cmd line")
        check_qemu_cmd_line()

        test.log.debug(virsh.dumpxml(vm_name).stdout_text)

    def run_test2():
        """
        Define and start guest
        Check No error msg prompts without guest memory balloon driver.
        """
        test.log.info("TEST_STEP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        vmxml.del_device('memballoon', by_tag=True)
        mem_balloon = memballoon.Memballoon()
        mem_balloon.setup_attrs(**device_dict)
        vmxml.devices = vmxml.devices.append(mem_balloon)

        vmxml.setup_attrs(**mem_attrs)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start guest ")
        if not vm.is_alive():
            vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("After define vm, get vmxml is:\n%s", vmxml)

        test.log.info("TEST_STEP3: Remove virtio_balloon module in guest")
        remove_module(module)

        test.log.info("TEST_STEP4: Change guest current memory allocation")
        result = virsh.setmem(domain=vm_name, size=set_mem, debug=True)
        libvirt.check_exit_status(result)

        test.log.info("TEST_STEP5: Check memory allocation is not changed")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expect_xpath)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    test.log.info("TEST_SETUP: backup and define: guest ")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()



    try:
        run_test()

    finally:
        teardown_test()


