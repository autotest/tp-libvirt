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
from virttest.utils_libvirt import libvirt_nwfilter
from virttest.libvirt_xml.devices import interface
from virttest import utils_libguestfs
from virttest import utils_net
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
    status_error = "yes" == params.get("status_error", "no")
    mount_noexec_tmp = "yes" == params.get("mount_noexec_tmp", "no")
    kill_libvirtd = "yes" == params.get("kill_libvirtd", "no")
    bug_url = params.get("bug_url", "")
    ipset_command = params.get("ipset_command")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    username = params.get("username")
    password = params.get("password")
    need_vm2 = "yes" == params.get("need_vm2", "no")
    add_vm_name = params.get("add_vm_name", "vm2")
    vms = [vm]
    dst_outside = params.get("dst_outside", "www.google.com")
    ping_timeout = int(params.get("ping_timeout", "10"))

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
        if params_dict['value'] == "MAC_of_virbr0":
            virbr0_info = process.run("ip a | grep virbr0: -A1", shell=True).stdout_text.strip()
            virbr0_mac = re.search(r'link/ether\s+(\w{2}:\w{2}:\w{2}:\w{2}:\w{2}:\w{2})',
                                   virbr0_info, re.M | re.I).group(1)
            params_dict['value'] = virbr0_mac
            logging.debug("params_dict['value'] is %s " % params_dict['value'])
        filter_param_list.append(params_dict)
    filterref_dict = {}
    filterref_dict['name'] = filter_name
    filterref_dict['parameters'] = filter_param_list
    params['filter_uuid'] = process.run("uuidgen", ignore_status=True, shell=True).stdout_text.strip()

    # get all the check commands and corresponding expected results form config file and make a dictionary
    cmd_list_ = params.get('check_cmd', '')
    if cmd_list_:
        cmd_list = cmd_list_.split(',')
        expect_res = params.get('expect_match', '').split(',')
        logging.debug("cmd_list is %s" % cmd_list)
        logging.debug("expect_res is %s" % expect_res)
        cmd_result_dict = dict(zip(cmd_list, expect_res))
        logging.debug("the check dict is %s" % cmd_result_dict)
    # backup vm xml
    vmxml_backup = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    libvirtd = utils_libvirtd.Libvirtd("virtqemud")
    device_name = None

    def check_nwfilter_rules(check_cmd, expect_match):
        """"check the nwfilter corresponding rule is added by iptables commands"""
        ret = utils_misc.wait_for(lambda: not process.system(check_cmd, ignore_status=True, shell=True), timeout=30)
        if not ret:
            test.fail("Rum command '%s' failed" % check_cmd)
        # This change anchors nwfilter_vm_start.possitive_test.new_filter.variable_notation case
        # The matched destination could be ip address or hostname
        if "iptables -L" in check_cmd and expect_match and 'ACCEPT' in expect_match:
            # ip address that need to be replaced
            replace_param = params.get("parameter_value_2")
            # Get hostname by ip address.
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
            # Add pre-check whether nwfilter exists or not since
            # utlv.create_nwfilter_xml will fail if there is no any nwfilter exists
            nwfilter_list = libvirt_nwfilter.get_nwfilter_list()
            if not nwfilter_list:
                test.error("There is no any nwfilter existed on the host")
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
            iface_mac = iface_xml.mac_address
            logging.debug("iface target dev name is %s", iface_target)

            # Check iptables or ebtables on host
            if need_vm2:
                # Clone more vm for testing
                result = virsh.dom_list('--inactive').stdout_text
                if add_vm_name in result:
                    logging.debug("%s is already exists!" % add_vm_name)
                    vms.append(env.get_vm(add_vm_name))
                else:
                    vm.destroy()
                    ret_clone = utils_libguestfs.virt_clone_cmd(vm_name, add_vm_name,
                                                                True, timeout=360)
                    if ret_clone.exit_status:
                        test.fail("Error when clone a second vm!")
                    vms.append(vm.clone(add_vm_name))
                    vm.start()
                vm2 = vms[1]
                logging.debug("Now the vms is: %s", [dom.name for dom in vms])
                # update the vm2 interface with the nwfilter
                logging.debug("filter_params_list is %s" % filter_param_list)
                iface_dict = {"filter": filter_name, "filter_parameters": filter_param_list, "del_mac": True}
                if vm2.is_alive():
                    vm2.destroy()
                utlv.modify_vm_iface(vm2.name, "update_iface", iface_dict)
                vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm2.name)
                iface_xml = vmxml.get_devices('interface')[0]
                logging.debug("iface_xml for vm2 is %s" % iface_xml)
                vm2.start()
                vm2_session = vm2.wait_for_serial_login()
                vm2_mac = vm2.get_mac_address()
                vm2_ip = utils_net.get_guest_ip_addr(vm2_session, vm2_mac)
                vm.session = vm.wait_for_serial_login()
                # test network functions, the 2 vms can not access to each other
                gateway_ip = utils_net.get_ip_address_by_interface("virbr0")
                status1, output1 = utils_net.ping(dest=vm2_ip, count='3', timeout=ping_timeout, session=vm.session,
                                                  force_ipv4=True)
                status2, output2 = utils_net.ping(dest=gateway_ip, count='3', timeout=ping_timeout, session=vm.session,
                                                  force_ipv4=True)
                status3, output3 = utils_net.ping(dest=dst_outside, count='3', timeout=ping_timeout, session=vm.session,
                                                  force_ipv4=True)
                if not status1:
                    test.fail("vm with clean-traffic-gateway ping succeed to %s %s, but it is not expected!" %
                              (vm2.name, vm2_ip))
                if status2 or status3:
                    test.fail("vm ping failed! check %s \n %s" % (output2, output3))
            if cmd_list_:
                loop = 0
                for check_cmd_, expect_match_ in cmd_result_dict.items():
                    check_cmd = check_cmd_.strip()
                    expect_match = expect_match_.strip()
                    if "DEVNAME" in check_cmd:
                        check_cmd = check_cmd.replace("DEVNAME", iface_target)
                    if "VMMAC" in expect_match:
                        expect_match = expect_match.replace("VMMAC", iface_mac)
                    logging.debug("the check_cmd is %s, and expected result is %s" % (check_cmd, expect_match))
                    check_nwfilter_rules(check_cmd, expect_match)
                    loop += 1
        except virt_vm.VMStartError as e:
            # Starting VM failed.
            if not status_error:
                test.fail("Test failed in positive case.\n error:"
                          " %s\n%s" % (e, bug_url))

        if kill_libvirtd:
            daemon_name = libvirtd.service_name
            pid = process.run('pidof %s' % daemon_name, shell=True).stdout_text.strip()
            cmd = "kill -s TERM %s" % pid
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
        # Undefine created filter except clean-traffic as it is built-in nwfilter
        if filter_name != exist_filter and filter_name != 'clean-traffic':
            virsh.nwfilter_undefine(filter_name, debug=True)
        if mount_noexec_tmp:
            if device_name:
                process.run("umount -l %s" % device_name, ignore_status=True, shell=True)
            utlv.setup_or_cleanup_iscsi(is_setup=False)
        if ipset_command:
            process.run("ipset destroy blacklist", shell=True)
        # Remove additional vms
        if need_vm2:
            result = virsh.dom_list("--all").stdout_text
            if add_vm_name in result:
                virsh.remove_domain(add_vm_name, "--remove-all-storage")
