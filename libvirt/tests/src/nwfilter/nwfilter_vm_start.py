import re
import logging
from autotest.client import os_dep
from autotest.client.shared import utils, error
from virttest import virsh
from virttest import virt_vm
from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices import interface


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
    try:
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
            utils.run(cmd)

        if ipset_command:
            try:
                os_dep.command("ipset")
            except ValueError:
                ret = utils.run("yum install ipset -y")
                if ret.exit_status:
                    raise error.TestNAError("Can't install ipset on host")
            utils.run(ipset_command)

        # Run command
        try:
            vm.start()
            vm.wait_for_serial_login()
            vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
            iface_xml = vmxml.get_devices('interface')[0]
            iface_target = iface_xml.target['dev']
            logging.debug("iface target dev name is %s", iface_target)

            # Check iptables or ebtables on host
            if check_cmd:
                if "DEVNAME" in check_cmd:
                    check_cmd = check_cmd.replace("DEVNAME", iface_target)
                wait_func = lambda: not utils.system(check_cmd,
                                                     ignore_status=True)
                ret = utils.wait_for(wait_func, timeout=30)
                if not ret:
                    raise error.TestFail("Rum command '%s' failed" % check_cmd)
                out = utils.system_output(check_cmd, ignore_status=False)
                if expect_match and not re.search(expect_match, out):
                    raise error.TestFail("'%s' not found in output: %s"
                                         % (expect_match, out))

        except virt_vm.VMStartError, e:
            # Starting VM failed.
            if not status_error:
                raise error.TestFail("Test failed in positive case.\n error:"
                                     " %s\n%s" % (e, bug_url))

        if kill_libvirtd:
            cmd = "kill -SIGTERM `pidof libvirtd`"
            utils.run(cmd)
            ret = utils.wait_for(lambda: not libvirtd.is_running(),
                                 timeout=30)
            if not ret:
                raise error.TestFail("Failed to kill libvirtd. %s" % bug_url)

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
            utils.run("umount -l %s" % device_name)
            utlv.setup_or_cleanup_iscsi(is_setup=False)
        if ipset_command:
            utils.run("ipset destroy blacklist")
