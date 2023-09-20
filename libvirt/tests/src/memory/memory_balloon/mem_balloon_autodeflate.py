# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices import memballoon
from virttest.staging import utils_memory


def run(test, params, env):
    """
    Verify the autodeflate takes effect with various memory balloon models.

    Scenario:
    1.memory balloon models: virtio, virtio-transitional, virtio-non-transitional.
    2.autodeflate: on, off, default.
    3.mem config: mem, current mem.
    """
    def get_memtotal_vm_internal():
        """
        Get memTotal value

        :return: mem_total, the value of 'cat /proc/meminfo | grep MemTotal'
        """
        session = vm.wait_for_login()
        mem_total = utils_memory.memtotal(session)
        session.close()

        return mem_total

    def run_test():
        """
        Define and start guest
        Check memTotal and memory allocation
        """
        test.log.info("TEST_STEP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

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

        test.log.info("TEST_STEP3: Login guest and get the total guest memory")
        init_mem_total = get_memtotal_vm_internal()

        test.log.info("TEST_STEP4: Change guest current memory allocation")
        result = virsh.setmem(domain=vm_name, size=set_mem, debug=True)
        libvirt.check_exit_status(result)

        test.log.info("TEST_STEP5: Login guest and get the total guest memory")
        second_mem_total = get_memtotal_vm_internal()

        if autodeflate == "on":
            if second_mem_total != init_mem_total:
                test.fail("Total guest memory should not changed from '%d' to "
                          "'%d'" % (init_mem_total, second_mem_total))
        else:
            if init_mem_total - second_mem_total != mem_value - set_mem:
                test.fail("The difference between two memTotal values: '%d-%d'"
                          " should be '%d'" % (init_mem_total, second_mem_total,
                                               mem_value - set_mem))
        test.log.debug("Get twice correct memTotal:'%d' and '%d'",
                       init_mem_total, second_mem_total)

        test.log.info("TEST_STEP6: Check the memory allocation")
        dominfo = virsh.dominfo(vm_name, ignore_status=True, debug=True)
        libvirt.check_result(dominfo, expected_match=dominfo_check)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    mem_attrs = eval(params.get("mem_attrs", "{}"))
    device_dict = eval(params.get("device_dict", "{}"))
    autodeflate = params.get("autodeflate", '')
    mem_value = int(params.get("mem_value"))
    set_mem = int(params.get("set_mem"))
    dominfo_check = params.get("dominfo_check")

    try:
        run_test()

    finally:
        teardown_test()
