import re
import logging
import socket

from avocado.utils import process
from avocado.utils import astring

from virttest import virsh
from virttest import virt_vm
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_package
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices import interface

from virttest import libvirt_version


def run(test, params, env):
    """
    Test start domain with nwfilter rules.

    1) Prepare parameters.
    2) Prepare nwfilter rule and update domain interface to apply.
    3) Start domain and check rule.
    4) Clean env
    """
    # Prepare parameters
    filter_name = params.get("filter_name", "testcase")
    exist_filter = params.get("exist_filter", "no-mac-spoofing")
    check_cmd = params.get("check_cmd")
    expect_match = params.get("expect_match")
    status_error = "yes" == params.get("status_error", "no")
    mount_noexec_tmp = "yes" == params.get("mount_noexec_tmp", "no")
    kill_libvirtd = "yes" == params.get("kill_libvirtd", "no")
    bug_url = params.get("bug_url", "")
    ipset_command = params.get("ipset_command")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    username = params.get("username")
    password = params.get("password")

    # Prepare vm filterref parameters dict list
    filter_param_list = []
    params_key = []
    for i in params.keys():
        if 'parameter_name_' in i:
            params_key.append(i)
    params_key.sort()
    for i in range(len(params_key)):
        params_dict = {}
        params_dict['name'] = params[params_key[i]]
        params_dict['value'] = params['parameter_value_%s' % i]
        filter_param_list.append(params_dict)
    filterref_dict = {}
    filterref_dict['name'] = filter_name
    filterref_dict['parameters'] = filter_param_list

    # backup vm xml
    vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    libvirtd = utils_libvirtd.Libvirtd()
    device_name = None

    def clean_up_dirty_nwfilter_binding():
        cmd_result = virsh.nwfilter_binding_list(debug=True)
        binding_list = cmd_result.stdout_text.strip().splitlines()
        binding_list = binding_list[2:]
        result = []
        # If binding list is not empty.
        if binding_list:
            for line in binding_list:
                # Split on whitespace, assume 1 column
                linesplit = line.split(None, 1)
                result.append(linesplit[0])
        logging.info("nwfilter binding list is: %s", result)
        for binding_uuid in result:
            try:
                virsh.nwfilter_binding_delete(binding_uuid)
            except Exception as e:
                logging.error("Exception thrown while undefining nwfilter-binding: %s", str(e))
                raise

    try:
        # Clean up dirty nwfilter binding if there are.
        clean_up_dirty_nwfilter_binding()
        rule = params.get("rule")
        if rule:
            # Create new filter xml
            filterxml = utlv.create_nwfilter_xml(params)
            # Define filter xml
            virsh.nwfilter_define(filterxml.xml, debug=True)

        # Update first vm interface with filter
        vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        vmxml.del_device(iface_xml)
        new_iface = interface.Interface('network')
        new_iface.xml = iface_xml.xml
        new_filterref = new_iface.new_filterref(**filterref_dict)
        new_iface.filterref = new_filterref
        logging.debug("new interface xml is: %s" % new_iface)
        vmxml.add_device(new_iface)
        vmxml.sync()

        if mount_noexec_tmp:
            device_name = utlv.setup_or_cleanup_iscsi(is_setup=True)
            utlv.mkfs(device_name, 'ext4')
            cmd = "mount %s /tmp -o noexec,nosuid" % device_name
            process.run(cmd, shell=True)

        if ipset_command:
            pkg = "ipset"
            if not utils_package.package_install(pkg):
                test.cancel("Can't install ipset on host")
            process.run(ipset_command, shell=True)

        # Run command
        try:
            vm.start()
            if not mount_noexec_tmp:
                vm.wait_for_serial_login(username=username, password=password)
            vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
            iface_xml = vmxml.get_devices('interface')[0]
            iface_target = iface_xml.target['dev']
            logging.debug("iface target dev name is %s", iface_target)

            # Check iptables or ebtables on host
            if check_cmd:
                if "DEVNAME" in check_cmd:
                    check_cmd = check_cmd.replace("DEVNAME", iface_target)
                ret = utils_misc.wait_for(lambda: not
                                          process.system(check_cmd,
                                                         ignore_status=True,
                                                         shell=True),
                                          timeout=30)
                if not ret:
                    test.fail("Rum command '%s' failed" % check_cmd)
                # This change anchors nwfilter_vm_start.possitive_test.new_filter.variable_notation case
                # The matched destination could be ip address or hostname
                if "iptables -L" in check_cmd and expect_match and 'ACCEPT' in expect_match:
                    # ip address that need to be replaced
                    replace_param = params.get("parameter_value_2")
                    #Get hostname by ip address.
                    hostname_info = None
                    try:
                        hostname_info = socket.gethostbyaddr(replace_param)
                    except socket.error as e:
                        logging.info("Failed to get hostname from ip address with error: %s", str(e))
                    if hostname_info:
                        # String is used to replace ip address
                        replace_with = "%s|%s" % (replace_param, hostname_info[0])
                        expect_match = r"%s" % expect_match.replace(replace_param, replace_with)
                        logging.debug("final iptables match string:%s", expect_match)
                out = astring.to_text(process.system_output(check_cmd, ignore_status=False, shell=True))
                if expect_match and not re.search(expect_match, out):
                    test.fail("'%s' not found in output: %s"
                              % (expect_match, out))

        except virt_vm.VMStartError as e:
            # Starting VM failed.
            if not status_error:
                test.fail("Test failed in positive case.\n error:"
                          " %s\n%s" % (e, bug_url))

        if kill_libvirtd:
            cmd = "kill -s TERM `pidof libvirtd`"
            process.run(cmd, shell=True)
            ret = utils_misc.wait_for(lambda: not libvirtd.is_running(),
                                      timeout=30)
            # After libvirt 5.6.0, libvirtd is using systemd socket activation by default
            if not ret and not libvirt_version.version_compare(5, 6, 0):
                test.fail("Failed to kill libvirtd. %s" % bug_url)

    finally:
        if kill_libvirtd:
            libvirtd.restart()
        # Clean env
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync()
        # Undefine created filter
        if filter_name != exist_filter:
            virsh.nwfilter_undefine(filter_name, debug=True)
        if mount_noexec_tmp:
            if device_name:
                process.run("umount -l %s" % device_name, ignore_status=True, shell=True)
            utlv.setup_or_cleanup_iscsi(is_setup=False)
        if ipset_command:
            process.run("ipset destroy blacklist", shell=True)
