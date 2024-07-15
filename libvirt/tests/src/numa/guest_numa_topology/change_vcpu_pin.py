#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

import re
import time

from avocado.utils import cpu as cpuutils

from virttest import virsh
from virttest.libvirt_xml import vm_xml

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_service

from provider.numa import numa_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    numad_active = test_obj.params.get('numad_active') == 'yes'
    old_status = libvirt_service.ensure_service_status('numad',
                                                       expect_active=numad_active)
    test_obj.params['backup_numad_status'] = old_status
    test_obj.test.log.info("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object
    :return: VMXML object updated
    """
    vmxml = test_obj.prepare_vm_xml()
    test_obj.test.log.debug("Customized vm xml "
                            "before defining:\n%s", vmxml)
    return vmxml


def produce_expected_error(test_obj):
    """
    produce the expected error message

    :param test_obj: NumaTest object
    :return: str, error message to be checked
    """
    return test_obj.produce_expected_error()


def verify_vcpupin(test_obj, online_cpus, host_online_cpu_num):
    """
    Verify virsh.vcpupin output

    :param test_obj: NumaTest object
    :param online_cpus: list, online cpus
    :param host_online_cpu_num: str, number of host online cpus
    """
    single_cpu_pin = test_obj.params.get('single_cpu_pin') == 'yes'
    vcpupin_result = virsh.vcpupin(test_obj.vm.name,
                                   **test_obj.virsh_dargs).stdout_text.strip()
    vcpupin_dict_ret = libvirt_misc.convert_to_dict(vcpupin_result)

    for vcpu_id, cpu_id in vcpupin_dict_ret.items():
        test_obj.test.log.debug("vcpu_id, cpu_id = %s, %s", vcpu_id, cpu_id)
        if single_cpu_pin:
            if cpu_id != '0':
                test_obj.test.fail("CPU ids are expected to be '0', "
                                   "but found %s" % cpu_id)
        else:
            cpu_id = numa_base.convert_to_list_of_int(cpu_id,
                                                      host_online_cpu_num)
            if cpu_id != online_cpus:
                test_obj.test.fail("CPU ids are expected to be '%s', "
                                   "but found %s" % (online_cpus, cpu_id))
    test_obj.test.log.debug("Verify vcpupin output: PASS")
    test_obj.virsh_dargs.update({'debug': False})
    for one_online_cpu in online_cpus:
        virsh.vcpupin(test_obj.vm.name, '0',
                      one_online_cpu, **test_obj.virsh_dargs)
        time.sleep(1)
        virsh.vcpuinfo(test_obj.vm.name, **test_obj.virsh_dargs)
    test_obj.test.log.debug("Verify VM vcpu 0 is able to be pined to "
                            "any online host cpu: PASS")


def verify_vcpuinfo(test_obj, online_cpus, host_online_cpu_num):
    """
    Verify virsh.vcpuinfo output

    :param test_obj: NumaTest object
    :param online_cpus: list, online cpus
    :param host_online_cpu_num: str, number of host online cpus
    """
    single_cpu_pin = test_obj.params.get('single_cpu_pin') == 'yes'
    vcpuinfo_result = virsh.vcpuinfo(test_obj.vm.name, options='--pretty',
                                     **test_obj.virsh_dargs).stdout_text.strip()
    cpu_ids = re.findall(r"\s+CPU:\s+(\d+)", vcpuinfo_result)
    cpu_affinity = re.findall(r'CPU Affinity:\s*(.*) .*out of', vcpuinfo_result)

    if not single_cpu_pin:
        for one_cpu in cpu_ids:
            if int(one_cpu) not in online_cpus:
                test_obj.test.fail("The pined host CPU %s should "
                                   "be in online cpu list: %s" % (one_cpu,
                                                                  online_cpus))
        for one_affinity in cpu_affinity:
            one_affinity = numa_base.convert_to_list_of_int(one_affinity,
                                                            host_online_cpu_num)
            if one_affinity != online_cpus:
                test_obj.test.fail("The cpu affinity %s should "
                                   "be in online cpu list: %s" % (one_affinity,
                                                                  online_cpus))
    else:
        for one_cpu in cpu_ids:
            if one_cpu != '0':
                test_obj.test.fail("The pined host CPU should be '0', "
                                   "but found '%s'" % one_cpu)
        for one_affinity in cpu_affinity:
            if one_affinity != '0':
                test_obj.test.fail("The cpu affinity should "
                                   "be '0', but found: %s" % one_affinity)
    test_obj.test.log.debug("Verify VM vcpu info: PASS")


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    test_obj.test.log.info("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.info("Step: define vm")
    ret = virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    test_obj.virsh_dargs.update({'ignore_status': True})
    test_obj.test.log.info("Step: start vm and check result")
    ret = virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    err_msg_expected = produce_expected_error(test_obj)
    libvirt.check_result(ret, expected_fails=err_msg_expected)
    #  Some negative cases will fail to start the vm, so they will return
    #  to skip following steps
    if err_msg_expected:
        return
    test_obj.virsh_dargs.update({'ignore_status': False})
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    test_obj.vm.wait_for_login().close()
    online_cpus = cpuutils.online_list()
    host_online_cpu_num = len(online_cpus)
    # For vcpuinfo would be impacted by numad changing cpu affinity of guest process,
    # so we skip testing the numad impact.
    if test_obj.params.get('numad_active') == 'no':
        verify_vcpuinfo(test_obj, online_cpus, host_online_cpu_num)
    verify_vcpupin(test_obj, online_cpus, host_online_cpu_num)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    backup_numad_status = test_obj.params['backup_numad_status']
    libvirt_service.ensure_service_status('numad',
                                          expect_active=backup_numad_status)
    test_obj.test.log.info("Step: teardown is done")


def run(test, params, env):
    """
    This is to test vcpupin can pin to different host numa nodes
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
