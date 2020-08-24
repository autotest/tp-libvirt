import os
import time
import re

from avocado.utils import process
from avocado.utils import astring
from avocado.core import exceptions

from virttest import virsh
from virttest import data_dir
from virttest import utils_secret
from virttest.utils_test import libvirt


def domainsnapshot_validate(test, vm_name, file=None, **virsh_dargs):
    """
    Test for schema domainsnapshot
    """
    snapshot_name = "snap-%s-%s" % (vm_name, time.time())
    cmd_result = virsh.snapshot_create_as(vm_name, snapshot_name)
    libvirt.check_exit_status(cmd_result)

    def check_info(s1, s2, errorstr="Values differ"):
        if s1 != s2:
            test.fail("%s (%s != %s)" % (errorstr, s1, s2))
    try:
        ss_info = virsh.snapshot_info(vm_name, snapshot_name)
        check_info(ss_info["Name"], snapshot_name, "Incorrect snapshot name")
        check_info(ss_info["Domain"], vm_name, "Incorrect domain name")
    except process.CmdError as e:
        test.fail(str(e))
    except exceptions.TestFail as e:
        test.fail(str(e))

    cmd_result = virsh.snapshot_dumpxml(vm_name, snapshot_name, to_file=file)
    libvirt.check_exit_status(cmd_result)


def network_validate(test, net_name, file=None, **virsh_dargs):
    """
    Test for schema network
    """
    if net_name is None:
        test.cancel("None network is specified.")

    # Confirm the network exists.
    output = virsh.net_list("--all").stdout.strip()
    if not re.search(net_name, output):
        test.cancel("Make sure the network exists!!")

    cmd_result = virsh.net_dumpxml(net_name, to_file=file)
    libvirt.check_exit_status(cmd_result)


def storagepool_validate(test, pool_name, file=None, **virsh_dargs):
    """
    Test for schema storagepool
    """
    if pool_name is None:
        test.cancel("None pool is specified.")

    # Confirm the storagepool exists.
    found = False
    result = virsh.pool_list(ignore_status=True)
    output = re.findall(r"(\S+)\ +(\S+)\ +(\S+)", str(result.stdout.strip()))
    for item in output[1:]:
        if pool_name == item[0]:
            found = True
            break
    if not found:
        test.cancel("Make sure the storagepool %s exists!" % pool_name)

    try:
        virsh.pool_dumpxml(pool_name, to_file=file)
    except process.CmdError as e:
        test.fail(str(e))


def storagevol_validate(test, pool_name, file=None, **virsh_dargs):
    """
    Test for schema storagevol
    """
    if pool_name is None:
        test.cancel("None pool is specified.")

    # Confirm the storagepool exists.
    found = False
    result = virsh.pool_list(ignore_status=True)
    output = re.findall(r"(\S+)\ +(\S+)\ +(\S+)", str(result.stdout.strip()))
    for item in output[1:]:
        if pool_name == item[0]:
            found = True
            break
    if not found:
        test.cancel("Make sure the storagepool %s exists!" % pool_name)

    # Get volume name
    cmd_result = virsh.vol_list(pool_name, **virsh_dargs)
    libvirt.check_exit_status(cmd_result)
    try:
        vol_name = re.findall(r"(\S+)\ +(\S+)", str(cmd_result.stdout.strip()))[1][0]
    except IndexError:
        test.error("Fail to get volume name")

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


def nwfilter_validate(test, file=None, **virsh_dargs):
    """
    Test for schema nwfilter
    """
    cmd_result = virsh.nwfilter_list(**virsh_dargs)
    libvirt.check_exit_status(cmd_result)
    try:
        uuid = re.findall(r"(\S+)\ +(\S+)", str(cmd_result.stdout.strip()))[1][0]
    except IndexError:
        test.error("Fail to get nwfilter uuid")

    if uuid:
        cmd_result = virsh.nwfilter_dumpxml(uuid, to_file=file, **virsh_dargs)
        libvirt.check_exit_status(cmd_result)


def secret_validate(test, secret_volume, file=None, **virsh_dargs):
    """
    Test for schema secret
    """
    # Clean up dirty secrets in test environments if there are.
    utils_secret.clean_up_secrets()
    sec_params = {"sec_usage": "volume",
                  "sec_volume": secret_volume,
                  "sec_desc": "Test for schema secret."
                  }
    sec_uuid = libvirt.create_secret(sec_params)
    if sec_uuid:
        try:
            virsh.secret_dumpxml(sec_uuid, to_file=file, **virsh_dargs)
        except process.CmdError as e:
            test.error(str(e))


def interface_validate(test, file=None, **virsh_dargs):
    """
    Test for schema interface
    """
    cmd_result = virsh.iface_list(**virsh_dargs)
    libvirt.check_exit_status(cmd_result)
    try:
        iface_name = re.findall(r"(\S+)\ +(\S+)\ +(\S+)",
                                str(cmd_result.stdout.strip()))[1][0]
    except IndexError:
        test.error("Fail to get iface name")

    if iface_name is None:
        test.cancel("None iface is specified.")

    try:
        virsh.iface_dumpxml(iface_name, to_file=file, **virsh_dargs)
    except process.CmdError as e:
        test.error(str(e))


def run(test, params, env):
    """
    Test for virt-xml-validate
    """
    # Get the full path of virt-xml-validate command.
    try:
        VIRT_XML_VALIDATE = astring.to_text(process.system_output("which virt-xml-validate", shell=True))
    except ValueError:
        test.cancel("Not find virt-xml-validate command on host.")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    net_name = params.get("net_dumpxml_name", "default")
    pool_name = params.get("pool_dumpxml_name", "default")
    schema = params.get("schema", "domain")
    output = params.get("output_file", "output")
    output_path = os.path.join(data_dir.get_tmp_dir(), output)
    secret_volume = params.get("secret_volume", None)

    valid_schemas = ['domain', 'domainsnapshot', 'network', 'storagepool',
                     'storagevol', 'nodedev', 'capability',
                     'nwfilter', 'secret', 'interface']
    if schema not in valid_schemas:
        test.fail("invalid %s specified" % schema)

    # If storage volume doesn't exist then create it
    if secret_volume and not os.path.isfile(secret_volume):
        process.run("dd if=/dev/zero of=%s bs=1 count=1 seek=1M" % secret_volume, shell=True)

    virsh_dargs = {'ignore_status': True, 'debug': True}
    if schema == "domainsnapshot":
        domainsnapshot_validate(test, vm_name, file=output_path, **virsh_dargs)
    elif schema == "network":
        network_validate(test, net_name, file=output_path, **virsh_dargs)
    elif schema == "storagepool":
        storagepool_validate(test, pool_name, file=output_path, **virsh_dargs)
    elif schema == "storagevol":
        storagevol_validate(test, pool_name, file=output_path, **virsh_dargs)
    elif schema == "nodedev":
        nodedev_validate(file=output_path, **virsh_dargs)
    elif schema == "capability":
        capability_validate(file=output_path, **virsh_dargs)
    elif schema == "nwfilter":
        nwfilter_validate(test, file=output_path, **virsh_dargs)
    elif schema == "secret":
        secret_validate(test, secret_volume, file=output_path, **virsh_dargs)
    elif schema == "interface":
        interface_validate(test, file=output_path, **virsh_dargs)
    else:
        # domain
        virsh.dumpxml(vm_name, to_file=output_path)

    try:
        cmd = "%s %s %s" % (VIRT_XML_VALIDATE, output_path, schema)
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
        if cmd_result.exit_status:
            test.fail("virt-xml-validate command failed.\n"
                      "Detail: %s." % cmd_result)

        if cmd_result.stdout_text.count("fail"):
            test.fail("xml fails to validate\n"
                      "Detail: %s." % cmd_result)
    finally:
        utils_secret.clean_up_secrets()
        if secret_volume and os.path.isfile(secret_volume):
            os.remove(secret_volume)
