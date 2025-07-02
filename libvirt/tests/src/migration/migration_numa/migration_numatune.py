from aexpect import remote

from virttest import libvirt_version
from virttest import test_setup
from virttest import utils_libvirtd
from virttest import virsh

from virttest.libvirt_xml import vm_xml

from provider.migration import base_steps
from provider.numa import numa_base


def update_numa_memnode(numatest, node0, node1):
    """
    Update numa_memnode values using available numa nodes

    :param numatest: NumaTest object
    :param node0: str, available numa node
    :param node1: str, available numa node
    """
    mem_mode = numatest.params.get("mem_mode")
    numa_memnode = numatest.params.get("numa_memnode")
    two_nodes = "%d,%d" % (node0, node1)
    two_nodes = numa_base.convert_to_string_with_dash(two_nodes)
    if mem_mode == "strict":
        numa_memnode = numa_memnode % (node0, two_nodes)
    elif mem_mode == "interleave":
        numa_memnode = numa_memnode % (two_nodes, node1)
    elif mem_mode == "preferred":
        numa_memnode = numa_memnode % (two_nodes, node0)
    elif mem_mode == "restrictive":
        numa_memnode = numa_memnode % (node0, node1)

    numatest.params["numa_memnode"] = eval(numa_memnode)


def setup_test(numatest_src, numatest_dst, migration_obj):
    """
    Setup steps

    :param numatest_src: NumaTest object for source host
    :param numatest_dst: NumaTest object for target host
    :param migration_obj: MigrationBase object
    """
    numatest_src.test.log.info("Setup steps.")
    numatest_src.setup(expect_node_free_mem_min=2097152)
    src_numanodes = numatest_src.get_available_numa_nodes(expect_node_free_mem_min=2097152)
    dst_numanodes = numatest_dst.get_available_numa_nodes(expect_node_free_mem_min=2097152)
    same_node_ids = set(src_numanodes).intersection(set(dst_numanodes))
    if len(same_node_ids) < 2:
        numatest_src.test.cancel("The two hosts do not have at least 2 numa nodes with same ID")
    same_node_ids = list(same_node_ids)
    node0 = same_node_ids[0]
    node1 = same_node_ids[1]
    update_numa_memnode(numatest_src, node0, node1)
    vmxml = numatest_src.prepare_vm_xml(required_nodes=same_node_ids)
    virsh.define(vmxml.xml, **numatest_src.virsh_dargs)

    if numatest_src.params.get('memory_backing'):
        for numa_obj in [numatest_src, numatest_dst]:
            session = numatest_dst.session if numa_obj == numatest_dst else None
            numa_base.adjust_parameters(numa_obj.params,
                                        hugepage_mem=int(numa_obj.params.get("hugepage_mem")),
                                        target_nodes=f"{node0} {node1}")
            hpc = test_setup.HugePageConfig(numa_obj.params, session=session)
            hpc.setup()
            numa_obj.params['hpc_list'] = [hpc]
            utils_libvirtd.Libvirtd(session=session).restart()
            numatest_src.test.log.info("Restart libvirt daemon to make hugepage "
                                       "configration take effect.")
    migration_obj.setup_connection()


def verify_test(numatest, migration_obj):
    """
    Verify steps for cases

    :param numatest: NumaTest object for source host
    :param migration_obj: MigrationBase object

    """
    numatest.test.log.info("Verify steps.")
    desturi = migration_obj.params.get("virsh_migrate_desturi")
    backup_uri, migration_obj.vm.connect_uri = migration_obj.vm.connect_uri, desturi
    virsh_remote = virsh.VirshPersistent(uri=migration_obj.vm.connect_uri)
    vmxml_remote = vm_xml.VMXML.new_from_dumpxml(
                    migration_obj.vm.name, virsh_instance=virsh_remote
                    )
    memory_backing = eval(numatest.params.get("memory_backing", "{}"))
    if memory_backing:
        mb_remote = vmxml_remote.mb
        mb_remote_attr = mb_remote.fetch_attrs()
        numatest.test.log.info("Memorybacking on remote vm: %s", mb_remote)
        if memory_backing != mb_remote_attr:
            numatest.test.fail("Expect memory backing to be '%s', "
                               "but found '%s'" % (memory_backing, mb_remote_attr))
        else:
            numatest.test.log.debug("Verify memory backing on remote vm - PASS")

    actual_numa_memnodes = vmxml_remote.numa_memnode
    actual_numa_memory = vmxml_remote.numa_memory
    conf_numa_memory = numatest.params.get("numa_memory")
    conf_numa_memnode = numatest.params.get("numa_memnode")
    if actual_numa_memnodes != conf_numa_memnode:
        numatest.test.fail("Expect numa memnode to be '%s' on remote vm, "
                           "but found '%s'" % (conf_numa_memnode, actual_numa_memnodes))
    else:
        numatest.test.log.debug("Verify numa memnode on remote vm - PASS")
    if actual_numa_memory != conf_numa_memory:
        numatest.test.fail("Expect numa memory to be '%s' on remote vm, "
                           "but found '%s'" % (conf_numa_memory, actual_numa_memory))
    else:
        numatest.test.log.debug("Verify numa memory on remote vm - PASS")
    migration_obj.vm.connect_uri = backup_uri
    migration_obj.verify_default()


def teardown_default(numatest_src, numatest_dst, migration_obj):
    """
    Default teardown function for the test

    :param numatest_src: NumaTest object for source host
    :param numatest_dst: NumaTest object for target host
    :param migration_obj: MigrationBase object
    """
    migration_obj.cleanup_connection()
    for numatest_obj in [numatest_src, numatest_dst]:
        numatest_obj.teardown()
        numatest_obj.test.log.debug("Step: teardown is done")


def run(test, params, env):
    """
    Verify that guest with externally launched virtiofs device can be migrated.

    Check numa nodes on both hosts are available and qualified which must
    have at least same two IDs of numa nodes with memory.
    Check numa memory is enough on both hosts
    Setup hugepage if needed on both hosts
    Do migration
    Check hugepage setting in remote host after migration

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")
    server_ip = params.get("server_ip")
    server_pwd = params.get("server_pwd")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    remote_session = remote.remote_login(
        client="ssh",
        host=server_ip,
        port=22,
        username="root",
        password=server_pwd,
        prompt=r"[$#%]",
    )
    numatest_dst = numa_base.NumaTest(None, params.copy(), test, session=remote_session)
    numatest_src = numa_base.NumaTest(vm, params, test)
    try:
        setup_test(numatest_src, numatest_dst, migration_obj)
        migration_obj.run_migration()
        verify_test(numatest_src, migration_obj)

    finally:
        teardown_default(numatest_src, numatest_dst, migration_obj)
