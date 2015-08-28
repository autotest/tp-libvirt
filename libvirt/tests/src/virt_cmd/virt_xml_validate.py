import os
import time
import re

from autotest.client import os_dep, utils
from autotest.client.shared import error

from virttest import common, virsh, data_dir
from virttest.utils_test import libvirt


def domainsnapshot_validate(vm_name, file=None, **virsh_dargs):
    """
    Test for schema domainsnapshot
    """
    snapshot_name = "snap-%s-%s" % (vm_name, time.time())
    cmd_result = virsh.snapshot_create_as(vm_name, snapshot_name)
    libvirt.check_exit_status(cmd_result)

    def check_info(s1, s2, errorstr="Values differ"):
        if s1 != s2:
            error.TestFail("%s (%s != %s)" % (errorstr, s1, s2))
    try:
        ss_info = virsh.snapshot_info(vm_name, snapshot_name)
        check_info(ss_info["Name"], snapshot_name, "Incorrect snapshot name")
        check_info(ss_info["Domain"], vm_name, "Incorrect domain name")
    except error.CmdError, e:
        error.TestFail(str(e))
    except error.TestFail, e:
        error.TestFail(str(e))

    cmd_result = virsh.snapshot_dumpxml(vm_name, snapshot_name, to_file=file)
    libvirt.check_exit_status(cmd_result)


def network_validate(net_name, file=None, **virsh_dargs):
    """
    Test for schema network
    """
    if net_name is None:
        raise error.TestNAError("None network is specified.")

    # Confirm the network exists.
    output = virsh.net_list("--all").stdout.strip()
    if not re.search(net_name, output):
        raise error.TestNAError("Make sure the network exists!!")

    cmd_result = virsh.net_dumpxml(net_name, to_file=file)
    libvirt.check_exit_status(cmd_result)


def storagepool_validate(pool_name, file=None, **virsh_dargs):
    """
    Test for schema storagepool
    """
    if pool_name is None:
        raise error.TestNAError("None pool is specified.")

    # Confirm the storagepool exists.
    found = False
    result = virsh.pool_list(ignore_status=True)
    output = re.findall(r"(\S+)\ +(\S+)\ +(\S+)[\ +\n]", str(result.stdout))
    for item in output[1:]:
        if pool_name == item[0]:
            found = True
            break
    if not found:
        raise error.TestNAError("Make sure the storagepool %s exists!" % pool_name)

    try:
        virsh.pool_dumpxml(pool_name, to_file=file)
    except error.CmdError, e:
        error.TestFail(str(e))


def storagevol_validate(pool_name, file=None, **virsh_dargs):
    """
    Test for schema storagevol
    """
    if pool_name is None:
        raise error.TestNAError("None pool is specified.")

    # Confirm the storagepool exists.
    found = False
    result = virsh.pool_list(ignore_status=True)
    output = re.findall(r"(\S+)\ +(\S+)\ +(\S+)[\ +\n]", str(result.stdout))
    for item in output[1:]:
        if pool_name == item[0]:
            found = True
            break
    if not found:
        raise error.TestNAError("Make sure the storagepool %s exists!" % pool_name)

    # Get volume name
    cmd_result = virsh.vol_list(pool_name, **virsh_dargs)
    libvirt.check_exit_status(cmd_result)
    try:
        vol_name = re.findall(r"(\S+)\ +(\S+)[\ +\n]", str(cmd_result.stdout))[1][0]
    except IndexError:
        raise error.TestError("Fail to get volume name")

    if vol_name is not None:
        cmd_result = virsh.vol_dumpxml(vol_name, pool_name, to_file=file)
        libvirt.check_exit_status(cmd_result)


def nodedev_validate(file=None, **virsh_dargs):
    """
    Test for schema nodedev
    """
    # Get dev name
    cmd_result = virsh.nodedev_list()
    libvirt.check_exit_status(cmd_result)

    dev_name = cmd_result.stdout.strip().splitlines()[1]
    if dev_name:
        cmd_result = virsh.nodedev_dumpxml(dev_name, to_file=file)
        libvirt.check_exit_status(cmd_result)


def capability_validate(file=None, **virsh_dargs):
    """
    Test for schema capability
    """
    cmd_result = virsh.capabilities(to_file=file, **virsh_dargs)


def nwfilter_validate(file=None, **virsh_dargs):
    """
    Test for schema nwfilter
    """
    cmd_result = virsh.nwfilter_list(**virsh_dargs)
    libvirt.check_exit_status(cmd_result)
    try:
        uuid = re.findall(r"(\S+)\ +(\S+)[\ +\n]", str(cmd_result.stdout))[1][0]
    except IndexError:
        raise error.TestError("Fail to get nwfilter uuid")

    if uuid:
        cmd_result = virsh.nwfilter_dumpxml(uuid, to_file=file, **virsh_dargs)
        libvirt.check_exit_status(cmd_result)


def secret_validate(file=None, **virsh_dargs):
    """
    Test for schema secret
    """
    cmd_result = virsh.secret_list(**virsh_dargs)
    libvirt.check_exit_status(cmd_result)
    try:
        uuid = re.findall(r"(\S+)\ +(\S+)[\ +\n]", str(cmd_result.stdout))[1][0]
    except IndexError:
        raise error.TestError("Fail to get secret uuid")

    if uuid:
        try:
            virsh.secret_dumpxml(uuid, to_file=file, **virsh_dargs)
        except error.CmdError, e:
            raise error.TestError(str(e))


def interface_validate(file=None, **virsh_dargs):
    """
    Test for schema interface
    """
    cmd_result = virsh.iface_list(**virsh_dargs)
    libvirt.check_exit_status(cmd_result)
    try:
        iface_name = re.findall(r"(\S+)\ +(\S+)\ +(\S+)[\ +\n]",
                                str(cmd_result.stdout))[1][0]
    except IndexError:
        raise error.TestError("Fail to get iface name")

    if iface_name is None:
        raise error.TestNAError("None iface is specified.")

    try:
        virsh.iface_dumpxml(iface_name, to_file=file, **virsh_dargs)
    except error.CmdError, e:
        raise error.TestError(str(e))


def run(test, params, env):
    """
    Test for virt-xml-validate
    """
    # Get the full path of virt-xml-validate command.
    try:
        VIRT_XML_VALIDATE = os_dep.command("virt-xml-validate")
    except ValueError:
        raise error.TestNAError("Not find virt-xml-validate command on host.")

    vm_name = params.get("main_vm", "virt-tests-vm1")
    net_name = params.get("net_dumpxml_name", "default")
    pool_name = params.get("pool_dumpxml_name", "default")
    schema = params.get("schema", "domain")
    output = params.get("output_file", "output")
    output_path = os.path.join(data_dir.get_tmp_dir(), output)

    valid_schemas = ['domain', 'domainsnapshot', 'network', 'storagepool',
                     'storagevol', 'nodedev', 'capability',
                     'nwfilter', 'secret', 'interface']
    if schema not in valid_schemas:
        raise error.TestFail("invalid %s specified" % schema)

    virsh_dargs = {'ignore_status': True, 'debug': True}
    if schema == "domainsnapshot":
        domainsnapshot_validate(vm_name, file=output_path, **virsh_dargs)
    elif schema == "network":
        network_validate(net_name, file=output_path, **virsh_dargs)
    elif schema == "storagepool":
        storagepool_validate(pool_name, file=output_path, **virsh_dargs)
    elif schema == "storagevol":
        storagevol_validate(pool_name, file=output_path, **virsh_dargs)
    elif schema == "nodedev":
        nodedev_validate(file=output_path, **virsh_dargs)
    elif schema == "capability":
        capability_validate(file=output_path, **virsh_dargs)
    elif schema == "nwfilter":
        nwfilter_validate(file=output_path, **virsh_dargs)
    elif schema == "secret":
        secret_validate(file=output_path, **virsh_dargs)
    elif schema == "interface":
        interface_validate(file=output_path, **virsh_dargs)
    else:
        # domain
        virsh.dumpxml(vm_name, to_file=output_path)

    cmd = "%s %s %s" % (VIRT_XML_VALIDATE, output_path, schema)
    cmd_result = utils.run(cmd, ignore_status=True)
    if cmd_result.exit_status:
        raise error.TestFail("virt-xml-validate command failed.\n"
                             "Detail: %s." % cmd_result)

    if cmd_result.stdout.count("fail"):
        raise error.TestFail("xml fails to validate\n"
                             "Detail: %s." % cmd_result)
