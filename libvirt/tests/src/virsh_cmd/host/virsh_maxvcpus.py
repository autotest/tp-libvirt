import logging

from avocado.core import exceptions

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_conn
from virttest.libvirt_xml import capability_xml


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
    capa = capability_xml.CapabilityXML()
    host_arch = capa.arch
    xmltreefile = capa.__dict_get__('xml')
    maxvcpus = None
    try:
        maxvcpus = capa.get_guest_capabilities()['hvm'][host_arch]['maxcpus']
    except:
        raise exceptions.TestFail("Failed to get maxvcpus from capabilities"
                                  " xml\n%s", xmltreefile)

    if not maxvcpus:
        raise exceptions.TestFail("Failed to get guest section for host arch: %s "
                                  "from capabilities xml\n%s", host_arch, xmltreefile)

    # params for transport connect.
    local_ip = params.get("local_ip", "ENTER.YOUR.LOCAL.IP")
    local_pwd = params.get("local_pwd", "ENTER.YOUR.LOCAL.ROOT.PASSWORD")
    server_ip = params.get("remote_ip", local_ip)
    server_pwd = params.get("remote_pwd", local_pwd)
    transport_type = params.get("connect_transport_type", "local")
    transport = params.get("connect_transport", "ssh")

    # check the config
    if (connect_arg == "transport" and
            transport_type == "remote" and
            local_ip.count("ENTER")):
        raise exceptions.TestNAError("Parameter local_ip is not configured"
                                     "in remote test.")
    if (connect_arg == "transport" and
            transport_type == "remote" and
            local_pwd.count("ENTER")):
        raise exceptions.TestNAError("Parameter local_pwd is not configured"
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
    else:
        connect_uri = connect_arg

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
            if "kqemu" in option:
                if not maxvcpus_test == '1':
                    raise exceptions.TestFail("Command output %s is not expected "
                                              "for %s " % (maxvcpus_test, option))
            elif option == 'qemu' or option == '--type qemu' or option == '':
                if not maxvcpus_test == maxvcpus:
                    raise exceptions.TestFail("Command output %s is not expected "
                                              "for %s " % (maxvcpus_test, option))
            else:
                # No check with other types
                pass
        else:
            raise exceptions.TestFail("Run command failed")
