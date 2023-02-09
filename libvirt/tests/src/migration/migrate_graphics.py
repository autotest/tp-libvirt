from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def verify_socket_file_path(socket_file_path, vm, test):
    """
    Verify the created socket file path in vm xml

    :param socket_file_path: str, the socket file path in vm xml
    :param vm: VM object
    :param test: test object
    """
    expected_socket_file_path = '/var/lib/libvirt/qemu/domain-%s-%s/vnc.sock' % (vm.get_id(),
                                                                                 vm.name)
    if socket_file_path == expected_socket_file_path:
        test.log.info("The socket file path '%s' is as expected" % socket_file_path)
    else:
        test.fail("The socket file path should be '%s', but "
                  "found '%s'" % (expected_socket_file_path,
                                  socket_file_path))


def get_graphic_attrs(vm_name, option, test, virsh_instance=None):
    """
    Get the attributes of graphic device in vm xml

    :param vm_name: the vm name
    :param option: options for new_from_dumpxml()
    :param test: test object
    :param virsh_instance: virsh object for virsh command
    :return: dict, the attributes of graphic device in vm xml
    """
    if virsh_instance:
        vmxml = VMXML.new_from_dumpxml(vm_name,
                                       options=option,
                                       virsh_instance=virsh_instance)
    else:
        vmxml = VMXML.new_from_dumpxml(vm_name, options=option)
    graphic_dev = vmxml.get_devices('graphics')[0]
    graphic_attributes = graphic_dev.fetch_attrs()

    msg = "After migration, " if virsh_instance else ''
    msg = "%sthe attributes of graphics device in vm xml " \
          "(options=%s):\n%s" % (msg, option, graphic_attributes)

    test.log.debug(msg)

    return graphic_attributes


def setup_listen_type_socket(params, vm, migration_obj, test):
    """
    Setup for migration with graphics whose listening type is socket

    :param params: dict, test parameters
    :param vm: VM object
    :param migration_obj: Migration object
    :param test: test object
    """
    graphic_attrs = eval(params.get('graphic_attrs', '{}'))
    vmxml = VMXML.new_from_dumpxml(vm.name)
    libvirt_vmxml.modify_vm_device(vmxml, 'graphics', graphic_attrs)
    migration_obj.setup_connection()

    graphic_attributes = get_graphic_attrs(vm.name, '', test)

    verify_socket_file_path(graphic_attributes['listen_attrs']['socket'], vm, test)

    graphic_attributes = get_graphic_attrs(vm.name, '--migratable', test)

    if (hasattr(graphic_attributes, 'socket')):
        test.fail("The socket file path should not exist in vmxml "
                  "with --migratable")
    else:
        test.log.info("The socket file path does not exist in vmxml "
                      "with --migratable as expected")


def verify_listen_type_socket(params, vm, migration_obj, test):
    """
    Verify for migration with graphics whose listening type is socket

    :param params: dict, test parameters
    :param vm: VM object
    :param migration_obj: Migration object
    :param test: test object
    """
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)

    graphic_attributes = get_graphic_attrs(vm.name, '',
                                           test,
                                           virsh_instance=remote_virsh_session)

    verify_socket_file_path(graphic_attributes['listen_attrs']['socket'], vm, test)

    graphic_attributes = get_graphic_attrs(vm.name, '--inactive',
                                           test,
                                           virsh_instance=remote_virsh_session)

    if graphic_attributes['listen_attrs']['type'] != 'socket':
        test.fail("The listen type of graphics device should be "
                  "socket, but found '%s'" % graphic_attributes['listen_attrs']['type'])
    else:
        test.log.info("The listen type of graphics device is socket as expected")
    remote_virsh_session.close_session()

    migration_obj.verify_default()


def run(test, params, env):
    """
    Test live migration with graphic device

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        globals() else migration_obj.setup_connection
    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        globals() else migration_obj.verify_default

    try:
        setup_test(params, vm, migration_obj, test)
        migration_obj.run_migration()
        verify_test(params, vm, migration_obj, test)
    finally:
        migration_obj.cleanup_connection()
