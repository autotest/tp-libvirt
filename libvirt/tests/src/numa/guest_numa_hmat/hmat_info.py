#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Dan Zheng <dzheng@redhat.com>
#

import re

from virttest import utils_libvirtd
from virttest import utils_split_daemons
from virttest import virsh
from virttest import test_setup
from virttest.libvirt_xml import capability_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_nested

from provider.numa import numa_base


def setup_default(numatest_obj):
    """
    Default setup function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.setup()
    numatest_obj.test.log.debug("Step: setup is done")


def prepare_vm_xml(numatest_obj):
    """
    Customize the vm xml

    :param numatest_obj: NumaTest object
    :return: VMXML object updated
    """
    vmxml = numatest_obj.prepare_vm_xml()

    numatest_obj.test.log.debug("Step: vm xml before defining:\n%s", vmxml)
    return vmxml


def verify_guest_dmesg(numatest_obj, vm_session):

    search_list = ['Flags:00 Type:Access Latency Initiator', 'Initiator-Target[0-1]:10 nsec',
                   'Flags:00 Type:Read Latency Initiator', 'Initiator-Target[0-0]:6 nsec',
                   'Initiator-Target[1-1]:11 nsec', 'Flags:00 Type:Write Latency Initiator',
                   'Initiator-Target[0-0]:7 nsec', 'Initiator-Target[1-1]:12 nsec',
                   'Flags:00 Type:Access Bandwidth Initiator', 'Initiator-Target[0-1]:100 MB/s',
                   'Flags:00 Type:Read Bandwidth Initiator', 'Initiator-Target[0-0]:201 MB/s',
                   'Initiator-Target[1-1]:101 MB/s', 'Flags:00 Type:Write Bandwidth Initiator',
                   'Initiator-Target[0-0]:202 MB/s', 'Initiator-Target[1-1]:102 MB/s',
                   'Flags:01 Type:Read Latency Initiator', 'Initiator-Target[0-0]:5 nsec',
                   'Flags:01 Type:Access Bandwidth Initiator', 'Initiator-Target[0-0]:200 MB/s']
    cmd_dmesg = 'dmesg | grep hmat'
    status, output = vm_session.cmd_status_output(cmd_dmesg)
    if status:
        numatest_obj.test.error("Can not find any message with command '%s'" % cmd_dmesg)

    for search_item in search_list:
        if not output.count(search_item):
            numatest_obj.test.fail("Expect '%s' in guest dmesg, but not found" % search_item)
    numatest_obj.test.log.debug("Verify guest dmesg - PASS")


def verify_hmat_cache_by_virsh_capabilities(numatest_obj, vm_session, capa_xml):
    def _compare_cache(expected_cache, actual_cache):
        if actual_cache != expected_cache:
            numatest_obj.test.fail("Expect cache should "
                                   "be '%s', but found '%s'" % (expected_cache,
                                                                actual_cache))

    topo_xml = capa_xml.cells_topology
    cells = topo_xml.get_cell(withmem=True)
    numa_cell0_cache0 = eval(numatest_obj.params.get('numa_cell0_cache0'))
    numa_cell0_cache1 = eval(numatest_obj.params.get('numa_cell0_cache1'))
    numa_cell1_cache0 = eval(numatest_obj.params.get('numa_cell1_cache0'))
    for one_cell in cells:
        cache_list = one_cell.cache
        numatest_obj.test.log.debug("cache_list:%s", cache_list)
        if one_cell.cell_id == '0':
            _compare_cache(numa_cell0_cache0, cache_list[0])
            _compare_cache(numa_cell0_cache1, cache_list[1])
        else:
            _compare_cache(numa_cell1_cache0, cache_list[0])

    numatest_obj.test.log.debug("Verify cache in virsh capabilities - PASS")


def verify_hmat_interconnects_by_virsh_capabilities(numatest_obj, vm_session, capa_xml):
    topo_xml = capa_xml.cells_topology
    interconnects = topo_xml.interconnects
    latency_list = interconnects.latency
    bandwidth_list = interconnects.bandwidth
    numatest_obj.test.log.debug(latency_list)
    numatest_obj.test.log.debug(bandwidth_list)
"""     for counter in range(0, 6):
        latency = numatest_obj.params.get('latency%s' % counter)
        bandwidth = numatest_obj.params.get('bandwidth%s' % counter)
        if latency_list[] """



def run_default(numatest_obj):
    """
    Default run function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.test.log.debug("Step: prepare vm xml")
    vmxml = prepare_vm_xml(numatest_obj)
    numatest_obj.test.log.debug("Step: define vm")
    virsh.define(vmxml.xml, **numatest_obj.virsh_dargs)
    numatest_obj.test.log.debug("Step: start vm")
    virsh.start(numatest_obj.vm.name, **numatest_obj.virsh_dargs)
    numatest_obj.test.log.debug("After vm is started, vm xml:\n"
                            "%s", vm_xml.VMXML.new_from_dumpxml(numatest_obj.vm.name))
    vm_session = numatest_obj.vm.wait_for_login()
    verify_guest_dmesg(numatest_obj, vm_session)

    libvirt_nested.install_virt_pkgs(vm_session)
    if utils_split_daemons.is_modular_daemon(vm_session):
        utils_libvirtd.Libvirtd(session=vm_session).start()
        utils_libvirtd.Libvirtd(session=vm_session, service_name='virtproxyd.socket').start()

    vm_ip = numatest_obj.vm.get_address()
    virsh_dargs = {'remote_ip': vm_ip,
                   'remote_user': numatest_obj.params.get('username'),
                   'remote_pwd': numatest_obj.params.get('password'),
                   'ssh_remote_auth': True}
    virsh_instance = virsh.VirshPersistent(**virsh_dargs)
    capa_xml = capability_xml.CapabilityXML(virsh_instance=virsh_instance)
    numatest_obj.test.log.debug("capa_xml:%s", capa_xml)
    verify_hmat_cache_by_virsh_capabilities(numatest_obj, vm_session, capa_xml)
    verify_hmat_interconnects_by_virsh_capabilities(numatest_obj, vm_session, capa_xml)


def teardown_default(numatest_obj):
    """
    Default teardown function for the test

    :param numatest_obj: NumaTest object
    """
    numatest_obj.teardown()
    numatest_obj.test.log.debug("Step: teardown is done")


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
