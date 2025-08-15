import os

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml.domcapability_xml import DomCapabilityXML
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def get_migratable_cpu_baseline(cpuxml):
    """

    """
    virsh_option = {'debug': True, 'ignore_status': False}
    migratable_cpu_xml = virsh.hypervisor_cpu_baseline(cpuxml,
                                                       options='--migratable',
                                                       **virsh_option).stdout_text.strip()
    return migratable_cpu_xml


def update_vm_xml(migratable_cpu_xml, params):

    vm_local = params.get("vm_location") == 'local'
    if vm_local:
        vm_name = params.get("migrate_main_vm")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    else:
        remote_virsh_session = params.get('remote_virsh_session')
        remote_vm_name = params.get('remote_vm_name')
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(remote_vm_name,
                                                       virsh_instance=remote_virsh_session)
        params['remote_vm_disk_source_file'] = vmxml.get_devices("disk")[0].fetch_attrs()['source']['attrs']['file']
        # update boot disk path
        disk_attrs = {'source': {'attrs': {'file': '/var/lib/avocado/data/avocado-vt/images/jeos-27-x86_641.qcow2'}}}


        libvirt_vmxml.modify_vm_device(vmxml, 'disk', dev_dict={''})
    if vmxml.cpu:
        del vmxml.cpu
    new_cpu = vm_xml.VMCPUXML()
    new_cpu.xml = migratable_cpu_xml
    cpu_match = params.get('cpu_match')
    new_cpu.match = cpu_match
    vmxml.cpu = new_cpu
    vmxml.sync()


def compare_cpu(domcap_xml, test):
    """

    :return:  1 - CPU described in cpu-compare.xml is identical to host CPU
              2 - Host CPU is a superset of CPU described in cpu-compare.xml
              3 - CPU described in cpu-compare.xml is incompatible with host CPU
    """
    virsh_options = {'debug': True, 'ignore_status': True}
    ret = virsh.hypervisor_cpu_compare(domcap_xml, **virsh_options)

    key1 = 'CPU provided by hypervisor on the host is a superset'
    key2 = 'incompatible with the CPU provided by hypervisor on the host'
    key3 = 'identical to the CPU provided by hypervisor on the host'
    if ret.stdout_text.strip().count(key1) or ret.stderr_text.strip().count(key1):
        return "local_superset"
    elif ret.stdout_text.strip().count(key2) or ret.stderr_text.strip().count(key2):
        return "incompatible"
    elif ret.stdout_text.strip().count(key3) or ret.stderr_text.strip().count(key3):
        return "identical"
    else:
        test.error("cpu compare return {} unexpectedly".format(ret))


def get_cpu_from_domcapabilities(params):

    # remote_ip = params.get("migrate_dest_host")
    # remote_user = params.get("migrate_dest_user", "root")
    # remote_pwd = params.get("migrate_dest_pwd")
    #
    # virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
    #                'remote_pwd': remote_pwd, 'unprivileged_user': None,
    #                'ssh_remote_auth': True}
    # remote_virsh_session = virsh.VirshPersistent(**virsh_dargs)
    remote_virsh_session = params.get('remote_virsh_session')
    #remote_virsh_session.capabilities()
    remote_domcap = DomCapabilityXML(virsh_instance=remote_virsh_session)
    local_domcap = DomCapabilityXML()
    #local_cpu_node = local_domcap.xmltreefile.find('/cpu')
    #remote_cpu_node = remote_domcap.xmltreefile.find('/cpu')
    return local_domcap, remote_domcap


def create_cap_file(local_cpu, remote_cpu, domcap_file_path):

    with open(local_cpu.xml) as fp:
        local_cap_cpu_content = fp.read()
    with open(remote_cpu.xml) as fp:
        remote_cap_cpu_content = fp.read()
    with open(domcap_file_path, 'w') as fp:
        fp.write(local_cap_cpu_content)
    with open(domcap_file_path, 'a') as fp:
        fp.write(remote_cap_cpu_content)


def setup_migratable_cpu_features(params, vm, migration_obj, test):
    """
    Setup for migration with graphics whose listening type is socket

    :param params: dict, test parameters
    :param vm: VM object
    :param migration_obj: Migration object
    :param test: test object
    """

    domcap_file = params.get('both_cap_file')
    domcap_file = os.path.join(data_dir.get_tmp_dir(), domcap_file)
    local_cpu_domcap, remote_cpu_domcap = get_cpu_from_domcapabilities(params)
    ret = compare_cpu(remote_cpu_domcap.xml, test)
    create_cap_file(local_cpu_domcap, remote_cpu_domcap, domcap_file)
    params['cpu_relation_on_two_hosts'] = ret
    migratable_cpu_xml = get_migratable_cpu_baseline(domcap_file)
    update_vm_xml(migratable_cpu_xml, params)
    set_expected_migration_result(params)
    if params.get('vm_location') == 'remote':
        migration_obj.setup_connection(setup_default=False)
    else:
        migration_obj.setup_connection()


def set_expected_migration_result(params):
    cpu_relation = params.get('cpu_relation_on_two_hosts')
    cpu_match = params.get('cpu_match')

    if cpu_match == 'minimum':
        if cpu_relation == "incompatible":
            params['status_error'] = 'yes'
        elif cpu_relation == 'local_superset':
            if params.get('vm_location') == 'local':
                params['status_error'] = 'yes'


def verify_test_back_default(vm, params, test):
    """
    Verify steps for migration back by default

    :param vm:  VM object
    :param params: dict, test parameters
    :param test: test object
    """
    pass


def run(test, params, env):
    """
    Test live migration with graphic device

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    test_case = params.get('test_case', '')
    vm_location = params.get("vm_location")
    vm_name = params.get("migrate_main_vm")
    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")

    vm = env.get_vm(vm_name)

    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("migrate_dest_user", "root")
    remote_pwd = params.get("migrate_dest_pwd")
    virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                   'remote_pwd': remote_pwd, 'unprivileged_user': None,
                   'ssh_remote_auth': True}
    remote_virsh_session = virsh.VirshPersistent(**virsh_dargs)
    params['remote_virsh_session'] = remote_virsh_session
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        globals() else migration_obj.setup_connection
    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        globals() else migration_obj.verify_default
    # Verify checkpoints after migration back
    verify_test_back = eval('verify_test_back_%s' % test_case) \
        if 'verify_test_back_%s' % test_case in globals() else verify_test_back_default

    try:
        setup_test(params, vm, migration_obj, test)
        if vm_location == 'local':
            migration_obj.run_migration()
            verify_test()
            if migrate_vm_back:
                migration_obj.run_migration_back()
                verify_test_back(vm, params, test)
        else:
            params['migrate_main_vm'] = params.get('remote_vm_name')
            migration_obj.run_migration_back()
            verify_test_back(vm, params, test)
            if migrate_vm_back:
                migration_obj.run_migration()
                verify_test()
    finally:
        migration_obj.cleanup_connection()
