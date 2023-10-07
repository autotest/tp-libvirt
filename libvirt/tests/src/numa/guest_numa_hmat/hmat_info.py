#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Dan Zheng <dzheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import utils_package
from virttest import virsh
from virttest import ssh_key

from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.numa import numa_base
from provider.virtual_network import network_base


def setup_default(test_obj):
    """
    Default setup function for the test

    :param test_obj: NumaTest object
    """
    test_obj.setup()
    test_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(test_obj):
    """
    Customize the vm xml

    :param test_obj: NumaTest object
    :return: VMXML object updated
    """
    vmxml = test_obj.prepare_vm_xml()
    test_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def verify_guest_dmesg(test_obj, vm_session):
    """
    Verify messages in guest dmesg output

    :param test_obj: NumaTest object
    :param vm_session: vm session
    """
    dmesg_pattern = test_obj.params.get("dmesg_pattern")
    search_list = dmesg_pattern.split(",")
    cmd_dmesg = 'dmesg | grep hmat'
    status, output = vm_session.cmd_status_output(cmd_dmesg)
    if status:
        test_obj.test.error("Can not find any message with command '%s'" % cmd_dmesg)

    for search_item in search_list:
        if not output.count(search_item):
            test_obj.test.fail("Expect '%s' in guest dmesg, but not found" % search_item)
        else:
            test_obj.test.log.debug("Verify '%s' in guest dmesg - PASS", search_item)
    test_obj.test.log.debug("Verify guest dmesg - PASS")


def verify_qemu_command_line(test_obj):
    """
    Verify qemu command line

    :param test_obj: NumaTest object
    """
    all_search_keys = [key for key in list(test_obj.params.keys()) if "qemu_check" in key]

    all_search_values = [test_obj.params.get(key) for key in all_search_keys]
    test_obj.test.log.debug("search string:%s", all_search_values)
    libvirt.check_qemu_cmd_line(all_search_values, expect_exist=True)
    test_obj.test.log.debug("Verify qemu command line - PASS")


def verify_hmat_info(test_obj, vm_session):
    """
    Verify HMAT info

    :param test_obj: NumaTest object
    :param vm_session: vm session
    """
    if not utils_package.package_install("libvirt", vm_session):
        test_obj.test.fail("Failed to install libvirt on guest")
    if libvirt_version.version_compare(9, 0, 0):
        for daemon_name in ["virtproxyd.socket", "virtqemud.socket"]:
            daemon = utils_libvirtd.Libvirtd(daemon_name, session=vm_session)
            daemon.start()
            test_obj.test.log.debug("%s is started", daemon_name)

    iface_mac = vm_xml.VMXML.get_first_mac_by_name(test_obj.vm.name)
    vm_ip = network_base.get_vm_ip(vm_session, iface_mac)
    if not vm_ip:
        test_obj.test.error("Can not get vm IP")
    connect_uri = "qemu+ssh://%s/system" % vm_ip
    vm_user = test_obj.params.get("username", "root")
    vm_user_passwd = test_obj.params.get("password")
    ssh_key.setup_ssh_key(vm_ip, vm_user, vm_user_passwd)
    output = virsh.capabilities("", uri=connect_uri,
                                ignore_status=False, debug=True)
    cap_xml = capability_xml.CapabilityXML()
    cap_xml.xml = output

    verify_hmat_cache_by_virsh_capabilities(test_obj, cap_xml)
    verify_hmat_interconnects_by_virsh_capabilities(test_obj, cap_xml)


def verify_hmat_cache_by_virsh_capabilities(test_obj, cap_xml):
    """
    Verify HMAT cache info

    :param test_obj: NumaTest object
    :param cap_xml: CapabilityXML instance
    """
    def _compare(cell_id, cache_id, expect_cache, actual_cache):
        if actual_cache != expect_cache:
            test_obj.test.fail("Expect cache %d in cell %d to be '%s', "
                               "but found '%s'" % (cache_id, cell_id,
                                                   expect_cache,
                                                   actual_cache))

    numa_cell0_cache0 = eval(test_obj.params.get("numa_cell0_cache0"))
    numa_cell0_cache1 = eval(test_obj.params.get("numa_cell0_cache1"))
    numa_cell1_cache0 = eval(test_obj.params.get("numa_cell1_cache0"))
    cells = cap_xml.cells_topology.get_cell()

    actual_cell0_cache0 = cells[0].cache[0].fetch_attrs()
    actual_cell0_cache1 = cells[0].cache[1].fetch_attrs()
    actual_cell1_cache0 = cells[1].cache[0].fetch_attrs()
    _compare(0, 0, numa_cell0_cache0, actual_cell0_cache0)
    _compare(0, 1, numa_cell0_cache1, actual_cell0_cache1)
    _compare(1, 0, numa_cell1_cache0, actual_cell1_cache0)
    test_obj.test.log.debug("Verify HMAT cache information in virsh capability - PASS")


def verify_hmat_interconnects_by_virsh_capabilities(test_obj, cap_xml):
    """
    Verify HMAT interconnects info

    :param test_obj: NumaTest object
    :param cap_xml: CapabilityXML instance
    """
    def _compare(conf_items, actual_items, item_name):
        conf_sublist = []
        for item in conf_items:
            # Ignore checking "access" because kernel does not expose it yet
            if item["type"] == "access" or item.get("cache"):
                continue
            conf_sublist.append(item)
        if len(conf_sublist) != len(actual_items):
            test_obj.test.fail("Expect %s to be '%s', "
                               "but found '%s'" % (item_name,
                                                   conf_sublist,
                                                   actual_items))
        for conf_item in conf_sublist:
            if conf_item not in actual_items:
                test_obj.test.fail("Expect %s to include '%s', "
                                   "but not found in '%s'" % (item_name,
                                                              conf_item,
                                                              actual_items))
            else:
                test_obj.test.log.debug("Verify '%s' in HMAT interconnects of "
                                        "virsh capability in guest - PASS", item_name)
    interconnects = cap_xml.cells_topology.interconnects.fetch_attrs()
    conf_bandwidth = eval(test_obj.params.get("bandwidth"))
    conf_latency = eval(test_obj.params.get("latency"))
    _compare(conf_bandwidth, interconnects["bandwidth"], "bandwidth")
    _compare(conf_latency, interconnects["latency"], "latency")
    test_obj.test.log.debug("Verify HMAT interconnects information of "
                            "virsh capability in guest - PASS")


def run_default(test_obj):
    """
    Default run function for the test

    :param test_obj: NumaTest object
    """
    test_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(test_obj)
    test_obj.test.log.debug("Step: define vm")
    virsh.define(vmxml.xml, **test_obj.virsh_dargs)
    test_obj.test.log.debug("Step: start vm")
    ret = virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    test_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(test_obj.vm.name))
    vm_session = test_obj.vm.wait_for_login()
    verify_qemu_command_line(test_obj)
    verify_guest_dmesg(test_obj, vm_session)
    verify_hmat_info(test_obj, vm_session)


def teardown_default(test_obj):
    """
    Default teardown function for the test

    :param test_obj: NumaTest object
    """
    test_obj.teardown()
    test_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Test for numa memory binding with emulator thread pin
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    numatest_obj = numa_base.NumaTest(vm, params, test)
    try:
        setup_default(numatest_obj)
        run_default(numatest_obj)
    finally:
        teardown_default(numatest_obj)
