import os
import re
import shutil
import logging as log

from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest.libvirt_xml.vm_xml import VMXML


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh save.

    The command can save the RAM state of a running domain.
    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Run virsh save command with assigned options.
    4.Recover test environment.(If the libvirtd service is stopped ,start
      the libvirtd service.)
    5.Confirm the test result.

    """
    savefile = params.get("save_file", "save.file")
    if savefile:
        savefile = os.path.join(data_dir.get_tmp_dir(), savefile)
    libvirtd = params.get("libvirtd", "on")
    extra_param = params.get("save_extra_param")
    vm_ref = params.get("save_vm_ref")
    progress = ("yes" == params.get("save_progress", "no"))
    options = params.get("save_option", "")
    status_error = ("yes" == params.get("save_status_error", "yes"))
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    uri = params.get("virsh_uri")
    readonly = ("yes" == params.get("save_readonly", "no"))
    expect_msg = params.get("save_err_msg", "")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    def create_alter_xml(vm_name):
        """
        Prepare an alternative xml with changed image for --xml test.

        :param vm_name: current vm name
        :return: xmlfile to use for --xml option
        """
        xmlfile = os.path.join(data_dir.get_tmp_dir(), '%s.xml' % vm_name)
        virsh.dumpxml(vm_name, extra="--migratable", to_file=xmlfile, ignore_status=False)
        vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
        disk = vmxml.get_devices('disk')[0]
        img_path = disk.source.attrs['file']
        new_img_path = os.path.join(data_dir.get_tmp_dir(), 'test.img')
        shutil.copyfile(img_path, new_img_path)
        with open(xmlfile) as file_xml:
            updated_xml = file_xml.read().replace(img_path, new_img_path)
        with open(xmlfile, 'w') as file_xml:
            file_xml.write(updated_xml)
        return xmlfile

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    # set the option
    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref.count("invalid"):
        vm_ref = params.get(vm_ref)
    elif vm_ref.count("name"):
        vm_ref = vm_name
    vm_ref += (" %s" % extra_param)

    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    if progress:
        options += " --verbose"

    if options == "--xml":
        options += ' %s' % create_alter_xml(vm_name)

    virsh.domstate(vm_name, ignore_status=True, debug=True)
    result = virsh.save(vm_ref, savefile, options, ignore_status=True,
                        unprivileged_user=unprivileged_user,
                        uri=uri, debug=True, readonly=readonly)
    status = result.exit_status
    err_msg = result.stderr.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    if savefile:
        if "--xml" in options:
            xml_after_save = virsh.save_image_dumpxml(savefile).stdout_text
            savefile += ' %s' % options
        virsh.restore(savefile, debug=True)
    virsh.domstate(vm_name, ignore_status=True, debug=True)

    # check status_error
    try:
        if status_error:
            if not status:
                if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                    logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                                 "and command should succeed")
                else:
                    test.fail("virsh run succeeded with an "
                              "incorrect command")
            if readonly:
                if not re.search(expect_msg, err_msg):
                    test.fail("Fail to get expect err msg: %s" % expect_msg)
        else:
            if status:
                test.fail("virsh run failed with a "
                          "correct command: %s" % err_msg)
            if progress and not err_msg.count("Save:"):
                test.fail("No progress information outputted!")
            if "--xml" in options and 'test.img' not in xml_after_save:
                test.fail('Not found "test.img" in vm xml after save --xml.'
                          'Modification to xml did not take effect.')
            if options.count("running"):
                if vm.is_dead() or vm.is_paused():
                    test.fail("Guest state should be"
                              " running after restore"
                              " due to the option --running")
            elif options.count("paused"):
                if not vm.is_paused():
                    test.fail("Guest state should be"
                              " paused after restore"
                              " due to the option --paused")
            else:
                if vm.is_dead():
                    test.fail("Guest state should be"
                              " alive after restore"
                              " since no option was specified")
    finally:
        if vm.is_paused():
            virsh.resume(vm_name)
        if os.path.exists(savefile):
            os.remove(savefile)
