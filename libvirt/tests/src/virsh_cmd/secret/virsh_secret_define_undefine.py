import os
import re
import logging as log

from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_split_daemons
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml.devices import disk
from virttest.libvirt_xml.secret_xml import SecretXML
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

SECRET_DIR = "/etc/libvirt/secrets/"
SECRET_BASE64 = "cmVkaGF0"


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh secret-define <file>
                  secret-undefine <secret>
    The testcase is to define or modify a secret
    from an XML file, then undefine it
    """

    # MAIN TEST CODE ###
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    secret_ref = params.get("secret_ref")
    ephemeral = params.get("ephemeral", "no")
    private = params.get("private_value", "no")
    modify_volume = ("yes" == params.get("secret_modify_volume", "no"))
    remove_uuid = ("yes" == params.get("secret_remove_uuid", "no"))
    libvirtd_timeout = ("yes" == params.get("libvirtd_timeout", "no"))
    libvirt_version.is_libvirt_feature_supported(params)
    start_vm = ("yes" == params.get("start_vm", "no"))
    extra_luks_parameter = params.get("extra_luks_parameter")
    luks_size = params.get("luks_size")
    luks_image = os.path.join(data_dir.get_data_dir(), "test.qcow2")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    if secret_ref == "secret_valid_uuid":
        # Generate valid uuid
        cmd = "uuidgen"
        status, uuid = process.getstatusoutput(cmd)
        if status:
            test.cancel("Failed to generate valid uuid")

    elif secret_ref == "secret_invalid_uuid":
        uuid = params.get(secret_ref)

    # libvirt acl related params
    uri = params.get("virsh_uri")
    if uri and not utils_split_daemons.is_modular_daemon():
        uri = "qemu:///system"
    unprivileged_user = params.get('unprivileged_user')
    define_acl = "yes" == params.get("define_acl", "no")
    undefine_acl = "yes" == params.get("undefine_acl", "no")
    get_value_acl = "yes" == params.get("get_value_acl", "no")
    define_error = "yes" == params.get("define_error", "no")
    undefine_error = "yes" == params.get("undefine_error", "no")
    get_value_error = "yes" == params.get("get_value_error", "no")
    define_readonly = "yes" == params.get("secret_define_readonly", "no")
    undefine_readonly = "yes" == params.get("secret_undefine_readonly", "no")
    expect_msg = params.get("secret_err_msg", "")

    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    acl_dargs = {'uri': uri, 'unprivileged_user': unprivileged_user,
                 'debug': True}

    # Get a full path of tmpfile, the tmpfile need not exist
    tmp_dir = data_dir.get_tmp_dir()
    volume_path = os.path.join(tmp_dir, "secret_volume")

    secret_xml_obj = SecretXML(ephemeral, private)
    secret_xml_obj.uuid = uuid
    secret_xml_obj.volume = volume_path
    secret_xml_obj.usage = "volume"

    secret_obj_xmlfile = os.path.join(SECRET_DIR, uuid + ".xml")

    # Run the test
    try:
        if define_acl:
            process.run("chmod 666 %s" % secret_xml_obj.xml, shell=True)
            cmd_result = virsh.secret_define(secret_xml_obj.xml, **acl_dargs)
        else:
            cmd_result = virsh.secret_define(secret_xml_obj.xml, debug=True,
                                             readonly=define_readonly)
        libvirt.check_exit_status(cmd_result, define_error)
        if cmd_result.exit_status:
            if define_readonly:
                if not re.search(expect_msg, cmd_result.stderr.strip()):
                    test.fail("Fail to get expect err msg: %s" % expect_msg)
                else:
                    logging.info("Get expect err msg: %s", expect_msg)
            return

        # Check ephemeral attribute
        exist = os.path.exists(secret_obj_xmlfile)
        if (ephemeral == "yes" and exist) or \
           (ephemeral == "no" and not exist):
            test.fail("The ephemeral attribute worked not expected")

        # Check ephemeral secret in list after libvirtd timeout
        if libvirtd_timeout:
            secret_list = utils_misc.wait_for(lambda: virsh.secret_list(),
                                              timeout=130, first=125, step=1)
            if uuid not in secret_list.stdout_text:
                test.fail("The ephemeral secret has been disappeared unexpectedly.")

        # Check private attribute
        virsh.secret_set_value(uuid, SECRET_BASE64, debug=True)
        if get_value_acl:
            cmd_result = virsh.secret_get_value(uuid, **acl_dargs)
        else:
            cmd_result = virsh.secret_get_value(uuid, debug=True)
        libvirt.check_exit_status(cmd_result, get_value_error)
        status = cmd_result.exit_status
        err_msg = "The private attribute worked not expected"
        if private == "yes" and not status:
            test.fail(err_msg)
        if private == "no" and status:
            if not get_value_error:
                test.fail(err_msg)

        if modify_volume:
            volume_path = os.path.join(tmp_dir, "secret_volume_modify")
            secret_xml_obj.volume = volume_path
            cmd_result = virsh.secret_define(secret_xml_obj.xml, debug=True)
            if cmd_result.exit_status == 0:
                test.fail("Expect fail on redefine after modify "
                          "volume, but success indeed")
        if remove_uuid:
            secret_xml_obj2 = SecretXML(ephemeral, private)
            secret_xml_obj2.volume = volume_path
            secret_xml_obj2.usage = "volume"
            cmd_result = virsh.secret_define(secret_xml_obj2.xml, debug=True)
            if cmd_result.exit_status == 0:
                test.fail("Expect fail on redefine after remove "
                          "uuid, but success indeed")
        if start_vm:
            secret_disk_dict = eval(params.get("secret_disk_dict", "{}") % (luks_image, uuid))
            vm.wait_for_login().close()
            # Prepare a running guest and a luks disk
            libvirt.create_local_disk(disk_type="file", extra=extra_luks_parameter,
                                      path=luks_image, size=luks_size,
                                      disk_format="qcow2")
            # Prepare a disk xml and attach/detach.
            diskxml = disk.Disk()
            diskxml.setup_attrs(**secret_disk_dict)
            virsh.attach_device(vm_name, diskxml.xml,
                                ignore_status=False, debug=True)
            virsh.detach_device(vm_name, diskxml.xml,
                                ignore_status=False, debug=True)

        if undefine_acl:
            cmd_result = virsh.secret_undefine(uuid, **acl_dargs)
        else:
            cmd_result = virsh.secret_undefine(uuid, debug=True, readonly=undefine_readonly)
            libvirt.check_exit_status(cmd_result, undefine_error)
            if undefine_readonly:
                if not re.search(expect_msg, cmd_result.stderr.strip()):
                    test.fail("Fail to get expect err msg: %s" % expect_msg)
                else:
                    logging.info("Get expect err msg: %s", expect_msg)

    finally:
        # cleanup
        bkxml.sync()
        virsh.secret_undefine(uuid, ignore_status=True)
        if os.path.exists(volume_path):
            os.unlink(volume_path)
        if os.path.exists(secret_obj_xmlfile):
            os.unlink(secret_obj_xmlfile)
        if os.path.exists(luks_image):
            os.unlink(luks_image)
