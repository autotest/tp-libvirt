import math

from virttest import utils_misc
from virttest import virsh
from virttest import xml_utils

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base


def run(test, params, env):
    """
    Verify libvirt recognizes the right memory unit.
    Verity libvirt makes the memory value round up
    The unit defaults to "KiB"

    KB=10**3 bytes, KiB=2**10 bytes
    MB=10**6 bytes, MiB=2**20 bytes
    GB=10**9 bytes, GiB=2**30 bytes

    Scenario:
    1:byte/KB/KiB/MB/MiB/GB/GiB/invalid
    2:with numa/without numa
    """

    def run_positive_test():
        """
        Start guest
        Check the qemu cmd line
        Check the memory size with virsh dominfo cmd
        """
        test.log.info("TEST_STEP1: Define vm.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**eval(params.get("vm_attrs")))
        result = virsh.define(vmxml.xml, debug=True)
        libvirt.check_exit_status(result)

        test.log.info("TEST_STEP2: Check domain memory configuration")
        check_mem_config("KiB")

        test.log.info("TEST_STEP3: Start guest and Check domain xml"
                      " for memory configuration?")
        vm.start()
        check_mem_after_operation('start')

        test.log.info("TEST_STEP4: Check guest memory value")
        session = vm.wait_for_login()
        guest_mem = utils_misc.get_mem_info(session)
        session.close()
        test.log.debug("Get memory value in guest: %s", guest_mem)

        test.log.info("TEST_STEP5: Check currentmemory change to "
                      "the value and round up to a multiple of default pagesize")
        check_mem_after_operation('login')
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        current_mem_new = vmxml.get_current_mem()

        test.log.info("TEST_STEP6: Check guest memory less than currentmemory")
        if guest_mem >= current_mem_new:
            test.fail("The guest memory: %s should be less than:%s" % (
                guest_mem, current_mem_new))
        else:
            test.log.debug("Get memory value:%s in guest, get current mem "
                           "value:%s in xml" % (guest_mem, current_mem_new))

    def run_negative_test():
        """
        Define vm and check result.
        """
        test.log.info("TEST_STEP1: Define vm with invalid config")
        set_negative_memory(mem_config)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    def check_mem_config(dest_unit):
        """
        Check current mem device config

        :param dest_unit: dest unit, eg: KB ,MB..
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        test.log.debug("Current xml is: %s", vmxml)

        current_mem_value = mem = memory_base.convert_data_size(
            mem_value + mem_unit, dest_unit)
        pattern = ''
        if mem_config == "without_numa":
            pattern = xpaths % (int(mem), int(current_mem_value))
        elif mem_config == "with_numa":
            max_mem_value = memory_base.convert_data_size(
                max_mem + max_mem_unit, dest_unit)
            numa_value = memory_base.convert_data_size(
                mem_value + mem_unit, dest_unit)
            pattern = xpaths % (int(mem), int(current_mem_value),
                                math.ceil(max_mem_value), int(numa_value))

        test.log.debug('Got xml pattern:%s', pattern)
        for item in eval(pattern):
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, item)

    def check_mem_after_operation(operation="start"):
        """
        Check mem after operation

        :param operation: guest operation
        """
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        result = get_expected_result(operation)
        for attr in eval(params.get("vm_attrs")).keys():
            if attr == "cpu":
                numa_memory = new_vmxml.cpu.numa_cell[0]['memory']
                numa_unit = new_vmxml.cpu.numa_cell[0]['unit']

                if str(numa_memory) != str(result["numa_memory"]):
                    test.fail('numa memory should be %s instead of %s ' %
                              (result["numa_memory"], numa_memory))
                if numa_unit != result["numa_unit"]:
                    test.fail('numa unit should be %s instead of %s ' %
                              (result["numa_unit"], numa_memory))

            else:
                get_func = eval('new_vmxml.get_%s' % attr)
                new_value = get_func()
                if str(new_value) != str(result[attr]):
                    test.fail('%s should be %s instead of %s ' %
                              (attr, result[attr], new_value))
                else:
                    test.log.debug("Get correct %s=%s in xml", (attr, result[attr]))

    def set_negative_memory(case):
        """
        Set negative value for memory and max memory value.

        :param case: Test scenario.
        """
        vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.xmltreefile.remove_by_xpath('/memory')
        vmxml.xmltreefile.remove_by_xpath('/currentMemory')

        xmltreefile = vmxml.__dict_get__('xml')
        xml_utils.ElementTree.SubElement(xmltreefile.getroot(),
                                         "memory").text = mem_value
        xml_utils.ElementTree.SubElement(xmltreefile.getroot(),
                                         "currentMemory").text = mem_value
        if mem_config == "with_numa":
            xml_utils.ElementTree.SubElement(xmltreefile.getroot(),
                                             'maxMemory').text = max_mem
            xml_utils.ElementTree.SubElement(xmltreefile.getroot(),
                                             'numa', numa_dict)
        vmxml.xmltreefile.write()
        test.log.debug("The invalid xml to be defined is :%s", vmxml)
        cmd_result = virsh.define(vmxml.xml, debug=True)
        libvirt.check_result(cmd_result, error_msg)

    def get_dest_size(source_size, operation="start", check_current_mem=False):
        """
        Round up the source size and convert dest size
        according to the operation
        according to the operation

        :param source_size: The size needs to be converted
        :param operation: The guest operation.
        :param check_current_mem: The checking mem is current mem or not.

        :return: dest_size: The dest size.

        Note:

        1) When the operation is guest login, Verify the current memory
        value would change to the value set in defined xml and round up to a
        multiple of 4KiB(default pagesize), other memory value same as 2):

            2000000KB = 1953125 KiB, and divide default_pagesize = 488281.25
            rounds up to 488282, than 488282 * default_page = 1953128 KiB

        2) When the operation is guest start or login and non-current memory,
         Verify the memory value
        would round up to unit of MiB:

            2000000KB = 1907.35 MiB rounds up to 1908MiB = 1953792KiB

        """
        dest_size = 0
        if operation == "login" and check_current_mem:
            dest_unit = 'KiB'
            convert_size = memory_base.convert_data_size(source_size,
                                                         dest_unit)
            round_up_size = math.ceil(convert_size/default_pagesize)
            dest_size = round_up_size * default_pagesize

        else:
            dest_unit = 'MiB'
            convert_size = memory_base.convert_data_size(source_size,
                                                         dest_unit)
            round_up_size = math.ceil(convert_size)
            dest_size = memory_base.convert_data_size(str(round_up_size)+dest_unit, 'KiB')

        test.log.debug("Convert '%s' to '%s%s' round up to '%s' than convert to"
                       " '%sKiB' " % (source_size, convert_size, dest_unit,
                                      round_up_size, dest_size))

        return int(dest_size)

    def get_expected_result(operation='start'):
        """
        Get expected memory result.

        :param operation: The operation before we need to check memory.
        :return: result_dict: Get the expected memory result.
        """
        result_dict.update({
            'memory': get_dest_size(mem_value+mem_unit, operation),
            'current_mem': get_dest_size(current_mem+current_mem_unit,
                                         operation, check_current_mem=True),
            "max_mem_rt": get_dest_size(max_mem+max_mem_unit, operation),
            "numa_memory": get_dest_size(mem_value+mem_unit, operation),
        })
        return result_dict

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    case = params.get("case")
    error_msg = params.get("error_msg")
    default_pagesize = int(params.get("default_pagesize"))
    mem_value = params.get("mem_value")
    mem_unit = params.get("mem_unit")
    current_mem = params.get("current_mem")
    current_mem_unit = params.get("current_mem_unit")
    max_mem = params.get("max_mem", 0)
    max_mem_unit = params.get("max_mem_unit", 'KiB')
    mem_config = params.get("mem_config")
    xpaths = params.get("xpaths")
    numa_dict = eval(params.get("numa_dict", "{}"))
    result_dict = eval(params.get("result_dict"))

    run_test = eval("run_%s" % case)

    try:
        run_test()

    finally:
        teardown_test()
