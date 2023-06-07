#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Chunfu Wen<chwen@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import logging
import os
import re

from avocado.utils import memory
from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

LOG = logging.getLogger('avocado.' + __name__)
cleanup_vms = []
cleanup_files = []


def test_create_domain_same_name_with_existed_guest(test, params, env):
    """
    Test create domain with same existed guest name

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    xml_dump_file = update_domain_attrs_in_vm_xml(params)
    result = virsh.create(xml_dump_file)
    expected_error_msg = params.get("error_msg")
    libvirt.check_result(result, expected_error_msg)


def test_create_domain_248_characters_name(test, params, env):
    """
    Test create domain with 248 characters length name

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    # compose 248 characters length string
    dom_new_name = params.get("dom_new_name") * 31

    params.update({"dom_new_name": dom_new_name})
    xml_dump_file = update_domain_attrs_in_vm_xml(params)

    # define one domain successfully
    cleanup_vms.append(dom_new_name)
    result = virsh.create(xml_dump_file, debug=True)
    libvirt.check_result(result, params.get("error_msg"))


def test_create_domain_not_existed_network(test, params, env):
    """
    Test create domain with not existed network

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    xml_dump_file = update_domain_attrs_in_vm_xml(params)
    result = virsh.create(xml_dump_file)
    libvirt.check_result(result, params.get("error_msg"))


def test_create_domain_overwritten_domain_xml(test, params, env):
    """
    Test create domain with overwritten domain xml

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    xml_dump_file = update_domain_attrs_in_vm_xml(params)
    dom_new_name = params.get("dom_new_name")

    # define one domain successfully
    cleanup_vms.append(dom_new_name)
    virsh.define(xml_dump_file, ignore_status=False, debug=True)

    # Validate VM exist and state is shut off
    vm_state = virsh.domstate(dom_new_name).stdout_text.strip()

    if vm_state != 'shut off':
        test.fail("vm:%s is not in shut off state" % dom_new_name)

    # remove interface(network) part
    process.run(r"sed -i '/<interface type=\"network\">/,/<\/interface>/d' %s" % xml_dump_file, ignore_status=False, shell=True, verbose=True)

    result = virsh.create(xml_dump_file, debug=True)
    libvirt.check_result(result, params.get("error_msg"))


def add_fake_disk(dump_xml):
    """
    One method is to add fake disk in VM xml

    :param dump_xml: one VM xml
    """
    # Prepare disk source xml
    source_attrs_dict = {"protocol": "nbd"}
    disk_src_dict = {}
    disk_src_dict.update({"attrs": source_attrs_dict})
    disk_src_dict.update({"hosts": [{"name": "10.73.75.59", "port": "10809"}]})
    customized_disk = libvirt_disk.create_primitive_disk_xml(
        "network", "disk",
        "vdc", 'virtio',
        'raw', disk_src_dict, None)
    dump_xml.add_device(customized_disk)


def update_domain_attrs_in_vm_xml(params):
    """
    One method is to help update domain attributes in VM xml

    :param params: dict wrapped with params
    """
    vm_name = params.get("main_vm")
    xml_dump = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    fake_disk = ("yes" == params.get("fake_disk"))
    if fake_disk:
        add_fake_disk(xml_dump)
    xml_dump_file = xml_dump.xmltreefile.name
    # remove uuid
    xml_dump.del_uuid()
    # change domain name
    dom_new_name = params.get("dom_new_name")
    if dom_new_name:
        xml_dump.vm_name = dom_new_name
    # change network name
    dom_new_network = params.get("dom_new_network")
    if dom_new_network:
        replace_net_cmd = r"sed -i 's/<source network=\"default\"/<source network=\"%s\"/g' %s" \
                            % (dom_new_network, xml_dump_file)
        process.run(replace_net_cmd, ignore_status=False, shell=True, verbose=True)
    return xml_dump_file


def test_define_domain_not_existed_network(test, params, env):
    """
    Test define domain with not existed network

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    dom_new_name = params.get("dom_new_name")
    xml_dump_file = update_domain_attrs_in_vm_xml(params)
    # define one domain successfully
    cleanup_vms.append(dom_new_name)
    virsh.define(xml_dump_file, ignore_status=False, debug=True)


def test_define_domain_248_characters_name(test, params, env):
    """
    Test define domain with 248 characters long name

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    dom_new_name = params.get("dom_new_name")
    # compose 248 characters length string
    params.update({"dom_new_name": dom_new_name * 31})
    xml_dump_file = update_domain_attrs_in_vm_xml(params)

    # define one domain successfully
    cleanup_vms.append(dom_new_name)
    result = virsh.define(xml_dump_file, debug=True)
    libvirt.check_result(result, params.get("error_msg"))


def add_wiping_storage_disk(params):
    """
    One method is to add wiping disk in VM xml

    :param params: dict wrapped with params
    """
    # Prepare disk source xml
    vm_name = params.get("main_vm")
    xml_dump = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    source_file_path = params.get("source_file_path")
    libvirt.create_local_disk("file", source_file_path, 1,
                              disk_format="qcow2")
    virsh.pool_refresh("images")
    cleanup_files.append(source_file_path)

    disk_src_dict = {"attrs": {"file": source_file_path}}
    target_device = params.get("target_device")

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        "file", "disk",
        target_device, 'virtio',
        'qcow2', disk_src_dict, None)

    xml_dump.add_device(customized_disk)
    xml_dump.sync()


def test_undefine_domain_wipe_storage(test, params, env):
    """
    Test undefine domain with wiping storage

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # add additional disk
    add_wiping_storage_disk(params)

    target_device = params.get("target_device")
    source_file = vm.get_blk_devices()[target_device].get('source')
    if source_file != params.get("source_file_path"):
        test.fail("VM actual target path: %s is not the same with expected one: %s"
                  % (source_file, params.get("source_file_path")))

    LOG.debug("Dump out Vm xml with wipe storage:")
    LOG.debug(vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

    virsh.undefine(vm_name, " --storage %s --wipe-storage" % target_device, ignore_status=False, debug=True)
    if os.path.exists(source_file):
        test.fail("Fail to remove source file: %s when undefine domain with --wipe-storage" % source_file)


def test_undefine_domain_convert_persistent_to_transient(test, params, env):
    """
    Test undefining running domain will convert persistent VM to_transient VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # start VM
    virsh.start(vm_name, ignore_status=False)
    vm.wait_for_login().close()

    # undefine running VM
    virsh.undefine(vm_name, " --nvram")

    # check VM is transient state
    result = virsh.dominfo(vm_name, ignore_status=True, debug=True)
    item_matched = "Persistent:.*no"
    if not re.search(r'%s' % item_matched, result.stdout.strip()):
        test.fail("VM is not persistent state with output: %s" % result.stdout.strip())


def test_reset_domain_shut_off_state_guest(test, params, env):
    """
    Test reset one shut off domain

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    dom_new_name = params.get("dom_new_name")
    xml_dump_file = update_domain_attrs_in_vm_xml(params)
    # define one domain successfully
    cleanup_vms.append(dom_new_name)
    virsh.define(xml_dump_file, ignore_status=False, debug=True)

    result = virsh.reset(dom_new_name, debug=True)
    libvirt.check_result(result, params.get("error_msg"))


def test_start_domain_memory_bigger_than_allocated(test, params, env):
    """
    Test start domain with memory bigger than allocated

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")

    # memory in M unit
    memory_factor = params.get("memory_factor", 4)
    # max memory is 20G multiplied
    usermaxmem = 20480 * int(memory_factor)
    host_memory = int(memory.rounded_memtotal()) / 1024

    if 2.5*host_memory > usermaxmem:
        test.cancel("Skip this case since host memory exceeds %s" % usermaxmem/2.5)

    # update VM memory
    current_vm_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    current_vm_xml.current_mem_unit = 'M'
    current_vm_xml.max_mem_unit = 'M'
    current_vm_xml.current_mem = usermaxmem
    current_vm_xml.max_mem = usermaxmem
    current_vm_xml.sync()

    # start VM
    result = virsh.start(vm_name)
    libvirt.check_result(result, params.get("error_msg"))


def test_destroy_domain_paused_state_guest(test, params, env):
    """
    Test destroy domain with paused state guest

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    dom_new_name = params.get("dom_new_name")
    xml_dump_file = update_domain_attrs_in_vm_xml(params)
    # define one domain successfully
    cleanup_vms.append(dom_new_name)
    virsh.define(xml_dump_file, ignore_status=False, debug=True)

    # create interactive virsh session to allow to not block code execution
    start_vm_cmd = "start %s" % dom_new_name
    virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                       auto_close=True)
    virsh_session.sendline(start_vm_cmd)

    virsh.start(dom_new_name, debug=True)
    vm_state = virsh.domstate(dom_new_name).stdout_text.strip()
    if vm_state != 'paused':
        test.fail("vm:%s is not in paused state" % dom_new_name)

    virsh.destroy(dom_new_name, ignore_status=False)
    vm_state = virsh.domstate(dom_new_name).stdout_text.strip()
    if vm_state != 'shut off':
        test.fail("vm:%s can not be shut off" % dom_new_name)


def test_shutdown_domain_paused_state_guest(test, params, env):
    """
    Test shutdown paused state guest

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    virsh.start(vm_name, ignore_status=False, debug=True)
    virsh.suspend(vm_name, ignore_status=False, debug=True)

    result = virsh.shutdown(vm_name, debug=True)
    libvirt.check_result(result, params.get("error_msg"))

    virsh.resume(vm_name)
    vm.wait_for_login().close()


def run(test, params, env):
    """
    Test command: virsh lifecycle

    The command can gracefully control domain life cycle.

    1.Prepare test environment.
    2.Perform domain life cycle operation
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    test_operation = params.get("test_operation")
    test_scenario = params.get("test_scenario")
    run_test_case = eval("test_%s_%s" % (test_operation, test_scenario))

    try:
        run_test_case(test, params, env)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        LOG.info("Restoring vm...")
        xml_backup.sync()
        for vm_name in cleanup_vms:
            if virsh.domain_exists(vm_name):
                if virsh.is_alive(vm_name):
                    virsh.destroy(vm_name)
                virsh.undefine(vm_name, "--nvram")
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
