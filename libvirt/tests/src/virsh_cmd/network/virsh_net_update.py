import os
import logging
import re
import tempfile
import time


from virttest import data_dir, utils_libvirtd
from virttest import virsh
from virttest import utils_net
from virttest import remote
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import xcepts
from virttest.libvirt_xml import vm_xml
from avocado.utils import process


def run(test, params, env):

    global flag_list, flag_list_abs, update_sec, without_sec
    global check_sec, check_without_sec, newxml

    # Basic network params
    net_name = params.get("net_update_net_name", "updatenet")
    net_section = params.get("network_section")
    update_command = params.get("update_command", "add-last")
    options = params.get("cmd_options", "")
    net_state = params.get("net_state")
    use_in_guest = params.get("use_in_guest")
    iface_type = params.get("iface_type", "network")
    ip_version = params.get("ip_version", "ipv4")
    new_start_ip = params.get("start_ip")
    new_end_ip = params.get("end_ip")
    parent_index = params.get("parent_index")
    status_error = params.get("status_error", "no")

    # dhcp host test
    new_dhcp_host_ip = params.get("new_dhcp_host_ip")
    new_dhcp_host_id = params.get("new_dhcp_host_id")
    new_dhcp_host_name = params.get("new_dhcp_host_name")
    new_dhcp_host_mac = params.get("new_dhcp_host_mac")

    # ipv4 range/host test
    ipv4_range_start = params.get("ori_ipv4_range_start")
    ipv4_range_end = params.get("ori_ipv4_range_end")
    ipv4_host_mac = params.get("ori_ipv4_host_mac")
    ipv4_host_ip = params.get("ori_ipv4_host_ip")
    ipv4_host_name = params.get("ori_ipv4_host_name")

    # ipv6 range/host test
    ipv6_range_start = params.get("ori_ipv6_range_start")
    ipv6_range_end = params.get("ori_ipv6_range_end")
    ipv6_host_id = params.get("ori_ipv6_host_id")
    ipv6_host_name = params.get("ori_ipv6_host_name")
    ipv6_host_ip = params.get("ori_ipv6_host_ip")

    # dns test
    dns_name = params.get("ori_dns_name")
    dns_value = params.get("ori_dns_value")
    new_dns_name = params.get("new_dns_name")
    new_dns_value = params.get("new_dns_value")

    # srv test
    srv_service = params.get("ori_srv_service")
    srv_protocol = params.get("ori_srv_protocol")
    srv_domain = params.get("ori_srv_domain")
    srv_target = params.get("ori_srv_target")
    srv_port = params.get("ori_srv_port")
    srv_priority = params.get("ori_srv_priority")
    srv_weight = params.get("ori_srv_weight")
    new_srv_service = params.get("new_srv_service")
    new_srv_protocol = params.get("new_srv_protocol")
    new_srv_domain = params.get("new_srv_domain")
    new_srv_target = params.get("new_srv_target")
    new_srv_port = params.get("new_srv_port")
    new_srv_priority = params.get("new_srv_priority")
    new_srv_weight = params.get("new_srv_weight")

    # dns host test
    dns_hostip = params.get("ori_dns_hostip")
    dns_hostname = params.get("ori_dns_hostname")
    dns_hostname2 = params.get("ori_dns_hostname2")

    # setting for without part
    without_ip_dhcp = params.get("without_ip_dhcp", "no")
    without_dns = params.get("without_dns", "no")
    without_dns_host = params.get("without_dns_host", "no")
    without_dns_txt = params.get("without_dns_txt", "no")
    without_dns_srv = params.get("without_dns_srv", "no")
    without_dns_forwarder = params.get("without_dns_forwarder", "no")

    # setting for update/check/without section
    update_sec = params.get("update_sec")
    check_sec = params.get("check_sec")
    without_sec = params.get("without_sec")
    check_without_sec = params.get("check_without_sec")

    # forward test
    forward_mode = params.get("forward_mode")
    forward_iface = params.get("forward_iface", "eth2")

    # other params
    error_type = params.get("error_type", "")
    vm_name = params.get("main_vm")
    loop_time = int(params.get("loop_time", 1))
    check_config_round = params.get("check_config_round", -1)
    ipv4_host_id = ipv6_host_mac = ""
    dns_enable = params.get("dns_enable", "yes")
    guest_iface_num = int(params.get("guest_iface_num", 1))
    newxml = ""

    def get_hostfile():
        """
        Get the content of hostfile
        """
        logging.info("Checking network hostfile...")
        hostfile = "/var/lib/libvirt/dnsmasq/%s.hostsfile" % net_name
        with open(hostfile) as hostfile_d:
            hostfile = hostfile_d.readlines()
        return hostfile

    def find_config(check_file, need_find):
        """
        Find configure in check_file

        :param check_file: The file that will check
        :param need_find: If need to find the item in check_file, Boolean
        """
        def _find_config(check_func):
            def __find_config(*args, **kwargs):
                logging.info("Checking content of %s", check_file)
                ret = False
                item = None
                with open(check_file) as checkfile_d:
                    while True:
                        check_line = checkfile_d.readline()
                        if not check_line:
                            break
                        (ret, item) = check_func(check_line, *args, **kwargs)
                        if ret:
                            break
                if not ret:
                    if need_find:
                        test.fail("Fail to find %s in %s" % (item, check_file))
                    else:
                        logging.info("Can not find %s in %s as expected" % (item, check_file))
            return __find_config
        return _find_config

    conf_file = "/var/lib/libvirt/dnsmasq/%s.conf" % net_name

    @find_config(conf_file, True)
    def check_item(check_line, item):
        """
        Check if the item in config file
        """
        if item in check_line:
            logging.info("Find %s in %s", item, conf_file)
            return (True, item)
        else:
            return (False, item)

    host_file = "/var/lib/libvirt/dnsmasq/%s.addnhosts" % net_name

    @find_config(host_file, True)
    def check_host(check_line, item):
        """
        Check if the item in host_file
        """
        if re.search(item, check_line):
            logging.info("Find %s in %s", item, host_file)
            return (True, item)
        else:
            return (False, item)

    @find_config(conf_file, False)
    def check_item_absent(check_line, item):
        """
        Check if the item not in config file
        """
        if item in check_line:
            test.fail("Find %s in %s" % (item, conf_file))
        else:
            return (False, item)

    @find_config(host_file, False)
    def check_host_absent(check_line, item):
        """
        Check if the item not in host_file
        """
        if re.search(item, check_line):
            test.fail("Find %s in %s" % (item, host_file))
        else:
            return (False, item)

    def section_update(ori_pre, new_pre):
        """
        Deal with update section and without section in func

        :param ori_pre: prefix of original section parameter name
        :param new_pre: prefix of new section parameter name
        """
        global flag_list, flag_list_abs, update_sec, without_sec
        global check_sec, check_without_sec, newxml

        if update_sec:
            for sec in update_sec.split(","):
                newxml = newxml.replace(names[ori_pre+sec],
                                        names[new_pre+sec])
            if update_command != "delete":
                check_sec = update_sec
        if without_sec:
            for sec_no in without_sec.split(","):
                newxml = re.sub(sec_no+"=\".*?\"", "", newxml)
            if update_command == "modify":
                check_without_sec = without_sec
        if check_sec:
            for c_sec in check_sec.split(","):
                flag_list.append(names[new_pre+c_sec])
        if check_without_sec:
            for c_sec_no in check_without_sec.split(","):
                flag_list_abs.append(names[ori_pre+c_sec_no])

    dns_host_xml = """
<host ip='%s'>
 <hostname>%s</hostname>
 <hostname>%s</hostname>
</host>
""" % (dns_hostip, dns_hostname, dns_hostname2)

    virtual_net = """
<network>
  <name>%s</name>
  <forward mode='nat'/>
  <bridge name='%s' stp='on' delay='0' />
  <mac address='52:54:00:03:78:6c'/>
  <domain name="example.com" localOnly="no"/>
  <mtu size="9000"/>
  <dns enable='%s'>
     <forwarder domain='example.com' addr="8.8.4.4"/>
      <txt name='%s' value='%s'/>
      <srv service='%s' protocol='%s' domain='%s' target='%s' port='%s' priority='%s' weight='%s'/>
      %s
  </dns>
  <ip address='192.168.100.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='%s' end='%s' />
      <host mac='%s' ip='%s' name='%s' />
    </dhcp>
  </ip>
  <ip family='ipv6' address='2001:db8:ca2:2::1' prefix='64'>
    <dhcp>
      <range start='%s' end='%s'/>
      <host id='%s' name='%s' ip='%s'/>
    </dhcp>
  </ip>
  <route family='ipv6' address='2001:db8:ca2:2::' prefix='64' gateway='2001:db8:ca2:2::4'/>
  <ip address='192.168.101.1' netmask='255.255.255.0'/>
  <ip family='ipv6' address='2001:db8:ca2:3::1' prefix='64' />
</network>
""" % (net_name, net_name, dns_enable, dns_name, dns_value, srv_service, srv_protocol,
       srv_domain, srv_target, srv_port, srv_priority, srv_weight, dns_host_xml,
       ipv4_range_start, ipv4_range_end, ipv4_host_mac, ipv4_host_ip, ipv4_host_name,
       ipv6_range_start, ipv6_range_end, ipv6_host_id, ipv6_host_name, ipv6_host_ip)

    port_group = """
<portgroup name='engineering' default='no'>
  <virtualport type='802.1Qbh'>
    <parameters profileid='test'/>
  </virtualport>
  <bandwidth>
    <inbound average='1000' peak='5000' burst='5120'/>
    <outbound average='1000' peak='5000' burst='5120'/>
  </bandwidth>
</portgroup>
"""

    if use_in_guest == "yes":
        vm = env.get_vm(vm_name)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()

    backup_files = []
    try:
        for loop in range(loop_time):
            if loop_time > 1:
                logging.info("Round %s:", loop+1)
                update_command = params.get("update_command").split(",")[loop]
                if net_section == "ip-dhcp-host":
                    new_dhcp_host_ip = params.get("new_dhcp_host_ip").split(",")[loop]
                    new_dhcp_host_name = params.get("new_dhcp_host_name").split(",")[loop]
                    new_dhcp_host_mac = params.get("new_dhcp_host_mac").split(",")[loop]
                    status_error = params.get("status_error").split(",")[loop]
                elif net_section == "dns-txt":
                    net_state = params.get("net_state").split(",")[loop]

            # Get a tmp_dir.
            tmp_dir = data_dir.get_tmp_dir()

            # Write new xml into a tempfile
            tmp_file = tempfile.NamedTemporaryFile(prefix=("new_xml_"),
                                                   dir=tmp_dir)
            xmlfile = tmp_file.name
            tmp_file.close()

            # Generate testxml
            test_xml = network_xml.NetworkXML(network_name=net_name)
            test_xml.xml = virtual_net

            names = locals()

            if net_section == "portgroup":
                portgroup_xml = network_xml.PortgroupXML()
                portgroup_xml.xml = port_group
                test_xml.portgroup = portgroup_xml
            elif net_section == "forward-interface":
                if forward_mode == "bridge":
                    forward_iface = utils_net.get_net_if(state="UP")[0]
                test_xml.forward = {'mode': forward_mode}
                test_xml.forward_interface = [{'dev': forward_iface}]
                del test_xml.bridge
                del test_xml.mac
                for ip_num in range(4):
                    del test_xml.ip
                del test_xml.routes
                del test_xml.dns
                del test_xml.mtu
                del test_xml.domain_name

            section_index = 0
            if ip_version == "ipv6":
                section_index = 1
            element = "/%s" % net_section.replace('-', '/')
            try:
                section_xml = test_xml.get_section_string(xpath=element, index=section_index)
            except xcepts.LibvirtXMLNotFoundError:
                newxml = section_xml = test_xml.get_section_string(xpath="/ip/dhcp/range")

            newxml = section_xml
            logging.debug("section xml is %s", newxml)

            flag_list = []
            flag_list_abs = []

            if (update_command == "delete" and
                    error_type not in ["host-mismatch", "range-mismatch"] and
                    not without_sec and not update_sec):
                logging.info("The delete xml is %s", newxml)
            else:
                if net_section == "bridge":
                    new_bridge_name = net_name + "_new"
                    flag_list.append(new_bridge_name)
                    newxml = section_xml.replace(net_name, new_bridge_name)
                    logging.info("The new bridge xml is %s", newxml)
                elif net_section == "forward":
                    new_mode = "route"
                    flag_list.append(new_mode)
                    newxml = section_xml.replace("nat", new_mode)
                    logging.info("The new forward xml is %s", newxml)
                elif net_section == "ip":
                    new_netmask = "255.255.0.0"
                    flag_list.append(new_netmask)
                    newxml = section_xml.replace("255.255.255.0", new_netmask)
                elif net_section == "ip-dhcp-range":
                    newxml = section_xml.replace(names[ip_version+"_range_start"], new_start_ip)
                    newxml = newxml.replace(names[ip_version+"_range_end"], new_end_ip)
                    for location in ["end", "start"]:
                        if names["new_"+location+"_ip"] == "":
                            newxml = newxml.replace(location+"=\"\"", "")
                        else:
                            flag_list.append(names["new_"+location+"_ip"])
                elif net_section == "portgroup":
                    new_inbound_average = "2000"
                    flag_list.append(new_inbound_average)
                    newxml = section_xml.replace("1000", new_inbound_average)
                    newxml = newxml.replace("default=\"no\"", "default=\"yes\"")
                    flag_list.append("default=('|\")yes")
                    if update_command in ['add-first', 'add-last', 'add']:
                        newxml = newxml.replace("engineering", "sales")
                        flag_list.append("sales")
                    if update_command == "modify" and status_error == "yes":
                        newxml = newxml.replace("engineering", "")
                elif net_section == "forward-interface":
                    new_forward_iface = params.get("new_forward_iface")
                    if error_type != "interface-duplicate":
                        find_iface = 0
                        if not new_forward_iface and forward_mode == "bridge":
                            new_iface_list = utils_net.get_net_if(qdisc="(mq|pfifo_fast)",
                                                                  state="(UP|DOWN)",
                                                                  optional="MULTICAST,UP")
                            logging.info("new_iface_list is %s", new_iface_list)
                            for iface in new_iface_list:
                                if iface[0] != forward_iface:
                                    new_forward_iface = iface[0]
                                    find_iface = 1
                                    break
                            if not find_iface:
                                test.cancel("Can not find another physical interface to attach")
                    else:
                        new_forward_iface = forward_iface
                    flag_list.append(new_forward_iface)
                    newxml = section_xml.replace(forward_iface, new_forward_iface)
                elif net_section == "ip-dhcp-host":
                    if (update_sec is None and
                            update_command not in ['modify', 'delete']):
                        if ip_version == "ipv4":
                            update_sec = "ip,mac,name"
                        elif ip_version == "ipv6":
                            update_sec = "ip,id,name"
                    section_update(ip_version+"_host_", "new_dhcp_host_")
                elif net_section == "dns-txt":
                    section_update("dns_", "new_dns_")
                elif net_section == "dns-srv":
                    section_update("srv_", "new_srv_")
                elif net_section == "dns-host":
                    if without_sec == "hostname":
                        newxml = re.sub("<hostname>.*</hostname>\n", "", newxml)
                    if status_error == 'no':
                        flag_list.append("ip=.*?"+dns_hostip)
                        flag_list.append(dns_hostname)
                        flag_list.append(dns_hostname2)
                # for negative test may have net_section do not match issues
                elif status_error == "no":
                    test.fail("Unknown network section")
                logging.info("The new xml of %s is %s", net_section, newxml)

            with open(xmlfile, 'w') as xmlfile_d:
                xmlfile_d.write(newxml)

            if without_ip_dhcp == "yes":
                test_xml.del_element(element="/ip/dhcp")
                test_xml.del_element(element="/ip/dhcp")

            if without_dns == "yes":
                test_xml.del_element(element='/dns')
            if without_dns_host == "yes":
                test_xml.del_element(element='/dns/host')
            if without_dns_txt == "yes":
                test_xml.del_element(element='/dns/txt')
            if without_dns_srv == "yes":
                test_xml.del_element(element='/dns/srv')
            if without_dns_forwarder == "yes":
                test_xml.del_element(element='/dns/forwarder')
            # Only do net define/start in first loop

            if (net_section == "ip-dhcp-range" and use_in_guest == "yes" and
                    without_ip_dhcp == "no"):
                test_xml.del_element(element="/ip/dhcp", index=section_index)

            if loop == 0:
                try:
                    # Define and start network
                    test_xml.debug_xml()
                    test_xml.define()
                    ori_net_xml = virsh.net_dumpxml(net_name).stdout.strip()
                    if "ipv6" in ori_net_xml:
                        host_ifaces = utils_net.get_net_if(state="UP")
                        backup_files.append(
                            remote.RemoteFile(address='127.0.0.1',
                                              client='scp',
                                              username=params.get('username'),
                                              password=params.get('password'),
                                              port='22',
                                              remote_path='/proc/sys/net/ipv6/'
                                                          'conf/all/accept_ra'))
                        process.run("echo 2 > /proc/sys/net/ipv6/conf/all/accept_ra",
                                    shell=True)
                        for host_iface in host_ifaces:
                            backup_files.append(
                                remote.RemoteFile(address='127.0.0.1',
                                                  client='scp',
                                                  username=params.get('username'),
                                                  password=params.get('password'),
                                                  port='22',
                                                  remote_path='/proc/sys/net/ipv6/'
                                                              'conf/%s/accept_ra'
                                                              % host_iface))
                            process.run("echo 2 > /proc/sys/net/ipv6/conf/%s/accept_ra"
                                        % host_iface, shell=True)
                            process.run("cat /proc/sys/net/ipv6/conf/%s/accept_ra"
                                        % host_iface)
                    if net_state == "active" or net_state == "transient":
                        test_xml.start()
                    if net_state == "transient":
                        test_xml.del_defined()
                    list_result = virsh.net_list("--all --name").stdout.strip()
                    if net_name not in list_result:
                        test.fail("Can not find %s in net-list" % net_name)
                except xcepts.LibvirtXMLError as detail:
                    test.error("Failed to define a test network.\n"
                               "Detail: %s." % detail)
            else:
                # setting net status for following loops
                if net_state == "active":
                    test_xml.set_active(True)
                elif net_state == "inactive":
                    test_xml.set_active(False)

            # get hostfile before update
            if without_ip_dhcp == "yes" and net_state == "active":
                hostfile_before = get_hostfile()
                if hostfile_before != []:
                    test.fail("hostfile is not empty before update: %s"
                              % hostfile_before)
                logging.info("hostfile is empty before update")

            # Get dnsmasq pid before update
            check_dnsmasq = ((update_command == "add" or update_command == "delete") and
                             net_section == "ip-dhcp-range" and status_error == "no" and
                             net_state == "active" and options != "--config")
            if check_dnsmasq:
                cmd = "ps aux|grep dnsmasq|grep -v grep|grep %s|awk '{print $2}'" % net_name
                pid_list_bef = process.run(cmd, shell=True).stdout_text.strip().split('\n')

            if parent_index:
                options += " --parent-index %s" % parent_index

            # Check config before update
            if net_state == "active":
                dns_txt = dns_srv = dns_host_str = None
                try:
                    dns_txt = test_xml.get_section_string(xpath="/dns/txt")
                    dns_srv = test_xml.get_section_string(xpath="/dns/srv")
                    dns_host_str = test_xml.get_section_string(xpath="/dns/host")
                except xcepts.LibvirtXMLNotFoundError:
                    pass

                txt_record = "txt-record=%s,%s" % (dns_name, dns_value)
                srv_host = "srv-host=_%s._%s.%s,%s,%s,%s,%s" % (srv_service, srv_protocol,
                                                                srv_domain, srv_target, srv_port,
                                                                srv_priority, srv_weight)
                hostline = "%s.*%s.*%s" % (dns_hostip, dns_hostname, dns_hostname2)
                if dns_txt:
                    check_item(txt_record)
                if dns_srv:
                    check_item(srv_host)
                if dns_host_str:
                    check_host(hostline)

            # check libvirtd should not crash during the net-update
            ori_pid_libvirtd = process.getoutput("pidof libvirtd")
            # Do net-update operation
            cmd_result = virsh.net_update(net_name,
                                          update_command,
                                          net_section,
                                          xmlfile, options, debug=True)
            aft_pid_libvirtd = process.getoutput("pidof libvirtd")
            if not utils_libvirtd.libvirtd_is_running() or ori_pid_libvirtd != aft_pid_libvirtd:
                test.fail("Libvirtd crash after net-update operation")

            if cmd_result.exit_status:
                err = cmd_result.stderr.strip()
                if status_error == "yes":
                    # index-mismatch error info and judgement
                    index_err1 = "the address family of a host entry IP must match the" + \
                                 " address family of the dhcp element's parent"
                    index_err2 = "XML error: Invalid to specify MAC address.* in network" + \
                                 ".* IPv6 static host definition"
                    index_err3 = "mismatch of address family in range.* for network"
                    mismatch_expect = (error_type == "index-mismatch" and
                                       (re.search(index_err1, err) or
                                        re.search(index_err2, err) or
                                        re.search(index_err3, err)))
                    err_dic = {}
                    # multi-host error info
                    err_dic["multi-hosts"] = "dhcp is supported only for a single.*" + \
                                             " address on each network"
                    # range-mismatch error info
                    err_dic["range-mismatch"] = "couldn't locate a matching dhcp " + \
                                                "range entry in network "
                    # host-mismatch error info
                    err_dic["host-mismatch"] = "couldn't locate a matching dhcp " + \
                                               "host entry in network "
                    # dns-mismatch error info
                    err_dic["dns-mismatch"] = "couldn't locate a matching DNS TXT " + \
                                              "record in network "
                    # range-duplicate error info
                    err_dic["range-duplicate"] = "there is an existing dhcp range" + \
                                                 " entry in network.* that matches"
                    # host-duplicate error info
                    err_dic["host-duplicate"] = "there is an existing dhcp host" + \
                                                " entry in network.* that matches"
                    # out_of_range error info
                    err_dic["out-of-range"] = "range.* is not entirely within network"
                    # no end for range error
                    err_dic["range-no-end"] = "Missing 'end' attribute in dhcp " + \
                                              "range for network"
                    # no match item for host modify err
                    err_dic["no-match-item"] = "couldn't locate an existing dhcp" + \
                                               " host entry with.* in network"
                    # no host name and mac for modify err
                    err_dic["host-no-name-mac"] = "Static host definition in IPv4 network" + \
                                                  ".* must have mac or name attribute"
                    # no host ip for modify err
                    err_dic["host-no-ip"] = "Missing IP address in static host " + \
                                            "definition for network"
                    # wrong command name
                    err_dic["wrong-command-name"] = "unrecognized command name"
                    # wrong section name
                    err_dic["wrong-section-name"] = "unrecognized section name"
                    # delete with only id
                    err_dic["only-id"] = "At least one of name, mac, or ip attribute must be" + \
                                         " specified for static host definition in network"
                    # options exclusive
                    err_dic["opt-exclusive"] = "Options --current and.* are mutually exclusive"
                    # range_reverse error info
                    if ip_version == "ipv4":
                        err_dic["range-reverse"] = "range.* is reversed"
                    elif ip_version == "ipv6":
                        err_dic["range-reverse"] = "range.*start larger than end"
                    # --live with inactive net
                    err_dic["invalid-state"] = "network is not running"
                    err_dic["transient"] = "cannot change persistent config of a transient network"
                    err_dic["dns-disable"] = "Extra data in disabled network"
                    err_dic["modify"] = "cannot be modified, only added or deleted"
                    err_dic["not-support"] = "can't update.*section of network"
                    err_dic["unrecognized"] = "unrecognized section name"
                    err_dic["interface-duplicate"] = "there is an existing interface entry " + \
                                                     "in network.* that matches"
                    err_dic["XML error"] = "XML error: Missing required name attribute in portgroup"

                    if (error_type in list(err_dic.keys()) and
                            re.search(err_dic[error_type], err) or mismatch_expect):
                        logging.debug("Get expect error: %s", err)
                    else:
                        test.fail("Do not get expect err msg: %s, %s"
                                  % (error_type, err_dic))
                else:
                    test.fail("Failed to execute net-update command")
            elif status_error == "yes":
                test.fail("Expect fail, but succeed")

            # Get dnsmasq pid after update
            if check_dnsmasq:
                pid_list_aft = process.run(cmd, shell=True).stdout_text.strip().split('\n')
                for pid in pid_list_aft:
                    if pid in pid_list_bef:
                        test.fail("dnsmasq do not updated")

            # Check the actual xml
            cmd_result = virsh.net_dumpxml(net_name)
            actual_net_xml = cmd_result.stdout.strip()
            new_xml_obj = network_xml.NetworkXML.new_from_net_dumpxml(net_name)
            logging.info("After net-update, the actual net xml is %s",
                         actual_net_xml)

            if "ip-dhcp" in net_section:
                if ip_version == "ipv6":
                    new_xml_obj.del_element(element="/ip", index=0)
                if ip_version == "ipv4":
                    new_xml_obj.del_element(element="/ip", index=1)

            config_not_work = ("--config" in options and net_state == "active" and
                               "--live" not in options)
            if config_not_work:
                if update_command != "delete":
                    flag_list_abs = flag_list
                    flag_list = []
                else:
                    flag_list = flag_list_abs
                    flag_list_abs = []

            logging.info("The check list is %s, absent list is %s", flag_list, flag_list_abs)

            if (update_command == "delete" and status_error == "no" and
                    not config_not_work and loop_time == 1):
                try:
                    section_str = new_xml_obj.get_section_string(xpath=element)
                except xcepts.LibvirtXMLNotFoundError:
                    section_str = None
                    logging.info("Can not find section %s in xml after delete", element)
                if section_str is not None:
                    test.fail("The actual net xml is not expected,"
                              "element still exists")
            elif ("duplicate" not in error_type and error_type != "range-mismatch" and
                    error_type != "host-mismatch" and error_type != "not-support"):
                # check xml should exists
                for flag_string in flag_list:
                    logging.info("checking %s should in xml in positive test,"
                                 "and absent in negative test", flag_string)
                    if not re.search(flag_string, actual_net_xml):
                        if ((status_error == "no" and update_command != "delete") or
                                (update_command == "delete" and status_error == "yes")):
                            test.fail("The actual net xml failed to update,"
                                      "or expect delete fail but xml missing"
                                      ":%s" % flag_string)
                    else:
                        if ((status_error == "yes" and update_command != "delete") or
                                (status_error == "no" and update_command == "delete" and
                                 not config_not_work)):
                            test.fail("Expect test fail, but find xml %s"
                                      " actual, or expect delete succeed,"
                                      "but fail in fact" % flag_string)
                # check xml should not exists
                for flag_string_abs in flag_list_abs:
                    logging.info("checking %s should NOT in xml in positive test,"
                                 "and exist in negative test", flag_string_abs)
                    if re.search(flag_string_abs, actual_net_xml):
                        if status_error == "no":
                            test.fail("Expect absent in net xml, but exists"
                                      "in fact: %s" % flag_string_abs)
                    else:
                        if status_error == "yes":
                            test.fail("Should exists in net xml, but it "
                                      "disappeared: %s" % flag_string_abs)

                # Check if add-last, add-fist works well
                if (status_error == "no" and not config_not_work and
                        update_command in ["add-last", "add", "add-first"]):
                    if update_command == "add-first":
                        find_index = 0
                    else:
                        find_index = -1
                    section_str_aft = new_xml_obj.get_section_string(xpath=element,
                                                                     index=find_index)
                    logging.info("xpath is %s, find_index is %s, section_str_aft is %s", element, find_index, section_str_aft)
                    for flag_string in flag_list:
                        logging.info("flag_string is %s", flag_string)
                        if not re.search(flag_string, section_str_aft):
                            test.fail("Can not find %s in %s" %
                                      (flag_string, section_str_aft))
                    logging.info("%s %s in right place", update_command, section_str_aft)

            # Check the positive test result
            if status_error == "no":
                # Check the network conf file
                # live update
                if ("--live" in options or "--config" not in options) and net_state == "active":
                    if net_section == "ip-dhcp-range" and update_command == "add-first":
                        ip_range = "%s,%s" % (new_start_ip, new_end_ip)
                        if ip_version == "ipv6":
                            ip_range = ip_range + ",64"
                        check_item(ip_range)
                    if "dns" in net_section:
                        if update_command == "delete" and loop == 0:
                            if net_section == "dns-srv":
                                check_item_absent(srv_host)
                            if net_section == "dns-txt":
                                check_item_absent(txt_record)
                            if net_section == "dns-host":
                                check_host_absent(dns_host_str)
                        elif "add" in update_command:
                            if net_section == "dns-srv":
                                for sec in update_sec.split(","):
                                    srv_host = srv_host.replace(names["srv_"+sec],
                                                                names["new_srv_"+sec])
                                check_item(srv_host)
                            if net_section == "dns-txt":
                                for sec in update_sec.split(","):
                                    txt_record = txt_record.replace(names["dns_"+sec],
                                                                    names["new_dns_"+sec])
                                check_item(txt_record)
                            if net_section == "dns-host":
                                check_host(hostline)

                    # Check the hostfile
                    if (net_section == "ip-dhcp-host" and update_command != "modify" and
                            update_command != "delete"):
                        dic_hostfile = {}
                        for sec in ["ip", "mac", "name", "id"]:
                            if update_sec is not None and sec in update_sec.split(","):
                                dic_hostfile[sec] = names["new_dhcp_host_"+sec]+","
                            else:
                                dic_hostfile[sec] = names[ip_version+"_host_"+sec]+","

                            if sec == "ip" and ip_version == "ipv6":
                                dic_hostfile[sec] = "[" + dic_hostfile[sec].strip(",") + "]"

                            if without_sec is not None and sec in without_sec.split(","):
                                dic_hostfile[sec] = ""

                        if ip_version == "ipv4":
                            host_info = (dic_hostfile["mac"] + dic_hostfile["ip"] +
                                         dic_hostfile["name"])
                            if dic_hostfile["mac"] == "":
                                host_info = dic_hostfile["name"] + dic_hostfile["ip"]
                        if ip_version == "ipv6":
                            host_info = ("id:" + dic_hostfile["id"] + dic_hostfile["name"] +
                                         dic_hostfile["ip"])

                        hostfile = get_hostfile()
                        host_info_patten = host_info.strip(",") + "\n"
                        if host_info_patten in hostfile:
                            logging.info("host info %s is in hostfile %s", host_info, hostfile)
                        else:
                            test.fail("Can not find %s in host file: %s"
                                      % (host_info, hostfile))

                # Check the net in guest
                if use_in_guest == "yes" and loop == 0:
                    # Detach all interfaces of vm first
                    iface_index = 0
                    mac_list = vm_xml.VMXML.get_iface_dev(vm_name)
                    for mac in mac_list:
                        iface_dict = vm_xml.VMXML.get_iface_by_mac(vm_name, mac)
                        virsh.detach_interface(vm_name,
                                               "--type %s --mac %s --config"
                                               % (iface_dict.get('type'), mac))
                        vm.free_mac_address(iface_index)
                        iface_index += 1

                    # attach new interface to guest
                    for j in range(guest_iface_num):
                        if net_section == "ip-dhcp-host" and new_dhcp_host_mac:
                            mac = new_dhcp_host_mac
                        else:
                            mac = utils_net.generate_mac_address_simple()
                        ret = virsh.attach_interface(vm_name,
                                                     "--type %s --source %s --mac %s --config"
                                                     % (iface_type, net_name, mac))
                        if ret.exit_status:
                            test.fail("Fail to attach new interface to guest: %s" %
                                      ret.stderr.strip())

                    # The sleep here is to make sure the update make effect
                    time.sleep(2)

                    # Start guest and check ip/mac/hostname...
                    vm.start()
                    logging.debug("vm xml is %s", vm.get_xml())
                    session = vm.wait_for_serial_login(internal_timeout=60)
                    if "ip-dhcp" in net_section:
                        dhclient_cmd = "(if pgrep dhclient;" \
                                       "then pkill dhclient; sleep 3; fi) " \
                                       "&& dhclient -%s -lf /dev/stdout" % ip_version[-1]
                        leases = session.cmd_output(dhclient_cmd)
                        iface_ip = utils_net.get_guest_ip_addr(session, mac,
                                                               ip_version=ip_version,
                                                               timeout=10)
                    if net_section == "ip-dhcp-range":
                        if new_start_ip <= iface_ip <= new_end_ip:
                            logging.info("getting ip %s is in range [%s ~ %s]",
                                         iface_ip, new_start_ip, new_end_ip)
                        else:
                            test.fail("getting ip %s not in range [%s ~ %s]" %
                                      (iface_ip, new_start_ip, new_end_ip))
                    if net_section == "ip-dhcp-host":
                        if iface_ip == new_dhcp_host_ip:
                            logging.info("getting ip is same with set: %s", iface_ip)
                        else:
                            test.fail("getting ip %s is not same with setting %s"
                                      % (iface_ip, new_dhcp_host_ip))
                        hostname = session.cmd_output("hostname -s").strip('\n')
                        # option host-name "redhatipv4-2"
                        dhcp_hostname = "option host-name \"%s\"" % new_dhcp_host_name.split('.')[0]
                        if hostname == new_dhcp_host_name.split('.')[0] or dhcp_hostname in leases:
                            logging.info("getting hostname same with setting: %s", new_dhcp_host_name.split('.')[0])
                        else:
                            test.fail("getting hostname %s is not same with "
                                      "setting: %s" % (hostname, new_dhcp_host_name))

                    session.close()

                # Check network connection for macvtap
                if (use_in_guest == "yes" and net_section == "forward-interface" and
                        forward_mode == "bridge"):
                    xml_obj_use = network_xml.NetworkXML.new_from_net_dumpxml(net_name)
                    net_conn = int(xml_obj_use.connection)
                    iface_conn = xml_obj_use.get_interface_connection()
                    conn_count = 0
                    for k in iface_conn:
                        conn_count = conn_count + int(k)
                    logging.info("net_conn=%s, conn_count=%s, guest_iface_num=%s"
                                 % (net_conn, conn_count, guest_iface_num))
                    if (net_conn != conn_count or
                            (loop == 1 and guest_iface_num != net_conn)):
                        test.fail("Can not get expected connection num: "
                                  "net_conn = %s, iface_conn = %s"
                                  % (net_conn, conn_count))

                #Check --config option after net destroyed
                if (int(check_config_round) == loop or
                        (loop_time == 1 and "--current" in options and net_state == "active")):
                    test_xml.set_active(False)
                    logging.info("Checking xml after net destroyed")
                    cmd_result = virsh.net_dumpxml(net_name)
                    inactive_xml = cmd_result.stdout.strip()
                    logging.info("inactive_xml is %s", inactive_xml)
                    #Check the inactive xml
                    for check_str in flag_list:
                        if update_command != "delete":
                            if "--config" in options:
                                if not re.search(check_str, inactive_xml):
                                    test.fail("Can not find xml after net destroyed")
                            if "--current" in options:
                                if re.search(check_str, inactive_xml):
                                    test.fail("XML still exists after net destroyed")
                        else:
                            if "--config" in options:
                                if re.search(check_str, inactive_xml):
                                    test.fail("XML still exists after net destroyed")
                            if "--current" in options:
                                if not re.search(check_str, inactive_xml):
                                    test.fail("Can not find xml after net destroyed")

                    logging.info("Check for net inactive PASS")

    finally:
        if test_xml.get_active():
            test_xml.del_active()
        if test_xml.get_defined():
            test_xml.del_defined()

        if os.path.exists(xmlfile):
            os.remove(xmlfile)

        if use_in_guest == "yes":
            vm = env.get_vm(vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml_backup.sync()
        for config_file in backup_files:
            config_file._reset_file()
