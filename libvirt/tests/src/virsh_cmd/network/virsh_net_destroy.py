import re
import logging
import os
from virttest import data_dir
from avocado.utils import process
from virttest import virsh
from virttest import libvirt_version
from virttest import utils_test
from virttest.libvirt_xml import vm_xml
from virttest import utils_libvirtd
from virttest import utils_misc


def run(test, params, env):
    """
    Test command: virsh net-destroy.

    The command can forcefully stop a given network.
    1.Make sure the network exists.
    2.Prepare network status.
    3.Perform virsh net-destroy operation.
    4.Check if the network has been destroied.
    5.Recover network environment.
    6.Confirm the test result.
    """

    net_ref = params.get("net_destroy_net_ref")
    extra = params.get("net_destroy_extra", "")
    network_name = params.get("net_destroy_network", "default")
    network_status = params.get("net_destroy_status", "active")
    status_error = params.get("status_error", "no")
    net_persistent = "yes" == params.get("net_persistent", "yes")
    net_cfg_file = params.get("net_cfg_file", "/usr/share/libvirt/networks/default.xml")
    check_libvirtd = "yes" == params.get("check_libvirtd")
    vm_defined = "yes" == params.get("vm_defined")
    check_vm = "yes" == params.get("check_vm")

    # libvirt acl polkit related params
    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    output_all = virsh.net_list("--all").stdout.strip()
    # prepare the network status: active, persistent
    if not re.search(network_name, output_all):
        if net_persistent:
            virsh.net_define(net_cfg_file, ignore_status=False)
            virsh.net_start(network_name, ignore_status=False)
        else:
            virsh.create(net_cfg_file, ignore_status=False)
    # Backup the current network xml
    net_xml_bk = os.path.join(data_dir.get_tmp_dir(), "%s.xml" % network_name)
    virsh.net_dumpxml(network_name, to_file=net_xml_bk)
    if net_persistent:
        if not virsh.net_state_dict()[network_name]['persistent']:
            logging.debug("make the network persistent...")
            virsh.net_define(net_xml_bk)
    else:
        if virsh.net_state_dict()[network_name]['persistent']:
            virsh.net_undefine(network_name, ignore_status=False)
    if not virsh.net_state_dict()[network_name]['active']:
        if network_status == "active":
            virsh.net_start(network_name, ignore_status=False)
    else:
        if network_status == "inactive":
            logging.debug("destroy network as we need to test inactive network...")
            virsh.net_destroy(network_name, ignore_status=False)
    logging.debug("After prepare: %s" % virsh.net_state_dict())

    # Run test case
    if net_ref == "uuid":
        net_ref = virsh.net_uuid(network_name).stdout.strip()
    elif net_ref == "name":
        net_ref = network_name

    if check_libvirtd or check_vm:
        vm_name = params.get("main_vm")
        if virsh.is_alive(vm_name):
            virsh.destroy(vm_name)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml
        # make sure there is interface with source network as default
        iface_devices = vmxml.get_devices(device_type="interface")
        has_default_net = False
        for iface in iface_devices:
            source = iface.get_source()
            if 'network' in source.keys() and source['network'] == 'default':
                has_default_net = True
                break
            elif 'bridge' in source.keys() and source['bridge'] == 'virbr0':
                has_default_net = True
                break
        if not has_default_net:
            options = "network default --current"
            virsh.attach_interface(vm_name, options, ignore_status=False)
        try:
            if vm_defined:
                ret = virsh.start(vm_name)
            else:
                logging.debug("undefine the vm, then create the vm...")
                vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                virsh.undefine(vm_name)
                ret = virsh.create(vmxml.xml)
                logging.debug(ret.stdout)
            # check the create or start cmd status
            utils_test.libvirt.check_exit_status(ret, expect_error=(network_status != 'active'))
            status = 1

            if status_error != 'yes':
                libvirtd = utils_libvirtd.Libvirtd("virtqemud")
                daemon_name = libvirtd.service_name
                pid_before_run = utils_misc.get_pid(daemon_name)
                ret = virsh.net_destroy(net_ref, extra, uri=uri, debug=True,
                                        unprivileged_user=unprivileged_user,
                                        ignore_status=True)
                utils_test.libvirt.check_exit_status(ret, expect_error=False)
                # check_libvirtd pid no change
                pid_after_run = utils_misc.get_pid(daemon_name)
                if pid_after_run != pid_before_run:
                    test.fail("libvirtd crash after destroy network!")
                    status = 1
                else:
                    logging.debug("libvirtd do not crash after destroy network!")
                    status = 0
                if check_libvirtd:
                    # destroy vm, check libvirtd pid no change
                    ret = virsh.destroy(vm_name)
                    utils_test.libvirt.check_exit_status(ret, expect_error=False)
                    pid_after_run2 = utils_misc.get_pid(daemon_name)
                    if pid_after_run2 != pid_before_run:
                        test.fail("libvirtd crash after destroy vm!")
                        status = 1
                    else:
                        logging.debug("libvirtd do not crash after destroy vm!")
                        status = 0
                elif check_vm:
                    # restart libvirtd and check vm is running
                    libvirtd = utils_libvirtd.Libvirtd()
                    libvirtd.restart()
                    if not virsh.is_alive(vm_name):
                        test.fail("vm shutdown when transient network destroyed then libvirtd restart")
                    else:
                        status = 0

        finally:
            if not vm_defined:
                vmxml_backup.define()
            vmxml_backup.sync()

    else:
        readonly = (params.get("net_destroy_readonly", "no") == "yes")
        status = virsh.net_destroy(net_ref, extra, uri=uri, readonly=readonly,
                                   debug=True, unprivileged_user=unprivileged_user,
                                   ignore_status=True).exit_status
        # Confirm the network has been destroyed.
        if net_persistent:
            if virsh.net_state_dict()[network_name]['active']:
                status = 1
        else:
            output_all = virsh.net_list("--all").stdout.strip()
            if re.search(network_name, output_all):
                status = 1
                logging.debug("transient network should not exists after destroy")

    # Recover network status to system default status
    try:
        if network_name not in virsh.net_state_dict():
            virsh.net_define(net_xml_bk, ignore_status=False)
        if not virsh.net_state_dict()[network_name]['active']:
            virsh.net_start(network_name, ignore_status=False)
        if not virsh.net_state_dict()[network_name]['persistent']:
            virsh.net_define(net_xml_bk, ignore_status=False)
        if not virsh.net_state_dict()[network_name]['autostart']:
            virsh.net_autostart(network_name, ignore_status=False)
    except process.CmdError:
        test.error("Recover network status failed!")

    # Clean up the backup network xml file
    if os.path.isfile(net_xml_bk):
        data_dir.clean_tmp_files()
        logging.debug("Cleaning up the network backup xml")

    # Check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
    else:
        test.error("The status_error must be 'yes' or 'no'!")
