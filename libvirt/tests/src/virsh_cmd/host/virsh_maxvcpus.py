import logging

from avocado.core import exceptions

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_conn
from virttest.libvirt_xml import domcapability_xml as domcap
from virttest.libvirt_xml import capability_xml
from virttest import libvirt_version
import platform


def run(test, params, env):
    """
    Test the command virsh maxvcpus

    (1) Call virsh maxvcpus
    (2) Call virsh -c remote_uri maxvcpus
    (3) Call virsh maxvcpus with an unexpected option
    """

    # get the params from subtests.
    # params for general.
    option = params.get("virsh_maxvcpus_options")
    status_error = params.get("status_error")
    connect_arg = params.get("connect_arg", "")

    # params for transport connect.
    local_ip = params.get("local_ip", "ENTER.YOUR.LOCAL.IP")
    local_pwd = params.get("local_pwd", "ENTER.YOUR.LOCAL.ROOT.PASSWORD")
    server_ip = params.get("remote_ip", local_ip)
    server_pwd = params.get("remote_pwd", local_pwd)
    transport_type = params.get("connect_transport_type", "local")
    transport = params.get("connect_transport", "ssh")
    connect_uri = None
    # check the config
    if (connect_arg == "transport" and
            transport_type == "remote" and
            local_ip.count("ENTER")):
        raise exceptions.TestSkipError("Parameter local_ip is not configured "
                                       "in remote test.")
    if (connect_arg == "transport" and
            transport_type == "remote" and
            local_pwd.count("ENTER")):
        raise exceptions.TestSkipError("Parameter local_pwd is not configured "
                                       "in remote test.")

    if connect_arg == "transport":
        canonical_uri_type = virsh.driver()

        if transport == "ssh":
            ssh_connection = utils_conn.SSHConnection(server_ip=server_ip,
                                                      server_pwd=server_pwd,
                                                      client_ip=local_ip,
                                                      client_pwd=local_pwd)
            try:
                ssh_connection.conn_check()
            except utils_conn.ConnectionError:
                ssh_connection.conn_setup()
                ssh_connection.conn_check()

            connect_uri = libvirt_vm.get_uri_with_transport(
                uri_type=canonical_uri_type,
                transport=transport, dest_ip=server_ip)
            virsh_dargs = {'remote_ip': server_ip, 'remote_user': 'root',
                           'remote_pwd': server_pwd,
                           'ssh_remote_auth': True}
            virsh_instance = virsh.VirshPersistent(**virsh_dargs)
    else:
        connect_uri = connect_arg
        virsh_instance = virsh

    if libvirt_version.version_compare(2, 3, 0):
        try:
            maxvcpus = None
            maxvcpus_cap = None
            dom_capabilities = None
            # make sure we take maxvcpus from right host, helps incase remote
            try:
                dom_capabilities = domcap.DomCapabilityXML(virsh_instance=virsh_instance)
                maxvcpus = dom_capabilities.max
                logging.debug("maxvcpus calculate from domcapabilities "
                              "is %s", maxvcpus)
            except Exception as details:
                raise exceptions.TestFail("Failed to get maxvcpus from "
                                          "domcapabilities xml:\n%s"
                                          % dom_capabilities)
            try:
                cap_xml = capability_xml.CapabilityXML()
                maxvcpus_cap = cap_xml.get_guest_capabilities()['hvm'][platform.machine()]['maxcpus']
                logging.debug('maxvcpus_cap is %s', maxvcpus_cap)
            except Exception as details:
                logging.debug("Failed to get maxvcpu from virsh "
                              "capabilities: %s", details)
                # Let's fall back incase of failure
                maxvcpus_cap = maxvcpus
            if not maxvcpus:
                raise exceptions.TestFail("Failed to get max value for vcpu"
                                          "from domcapabilities "
                                          "xml:\n%s" % dom_capabilities)
        except Exception as details:
            raise exceptions.TestFail("Failed get the virsh instance with uri: "
                                      "%s\n Details: %s" % (connect_uri, details))

    is_arm = "aarch" in platform.machine()
    gic_version = ''
    if is_arm:
        for gic_enum in domcap.DomCapabilityXML()['features']['gic_enums']:
            if gic_enum['name'] == "version":
                gic_version = gic_enum['values'][0].get_value()

    # Run test case
    result = virsh.maxvcpus(option, uri=connect_uri, ignore_status=True,
                            debug=True)

    maxvcpus_test = result.stdout.strip()
    status = result.exit_status

    # Check status_error
    if status_error == "yes":
        if status == 0:
            raise exceptions.TestFail("Run successed with unsupported option!")
        else:
            logging.info("Run failed with unsupported option %s " % option)
    elif status_error == "no":
        if status == 0:
            if not libvirt_version.version_compare(2, 3, 0):
                if "kqemu" in option:
                    if not maxvcpus_test == '1':
                        raise exceptions.TestFail("Command output %s is not "
                                                  "expected for %s " % (maxvcpus_test, option))
                elif option in ['qemu', '--type qemu', '']:
                    if not maxvcpus_test == '16':
                        raise exceptions.TestFail("Command output %s is not "
                                                  "expected for %s " % (maxvcpus_test, option))
                else:
                    # No check with other types
                    pass
            else:
                # It covers all possible combinations
                if option in ['qemu', 'kvm', '--type qemu', '--type kvm', 'kqemu', '--type kqemu', '']:
                    if (is_arm and gic_version == '2' and option in ['kvm', '']):
                        if not maxvcpus_test == '8':
                            raise exceptions.TestFail("Command output %s is not "
                                                      "expected for %s " % (maxvcpus_test, option))
                    elif not (maxvcpus_test == maxvcpus or maxvcpus_test == maxvcpus_cap):
                        raise exceptions.TestFail("Command output %s is not "
                                                  "expected for %s " % (maxvcpus_test, option))
                else:
                    # No check with other types
                    pass
        else:
            raise exceptions.TestFail("Run command failed")
