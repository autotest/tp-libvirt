import logging
import aexpect

from avocado.utils import process
from virttest import utils_test
from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test virsh create command including parameters except --pass-fds
    because it is used in lxc.

    Basic test scenarios:
    1. --console with all combination(with other options)
    2. --autodestroy with left combination
    3. --paused itself
    """

    def xmlfile_with_extra_attibute(xml):
        """
        Prepare an invalid xml using the disk element
        """
        # For example, <disk type='file' type='disk' foot='3'/>
        some_device_xml = xml.xmltreefile.find('/devices/disk')
        some_device_xml.set("foot", "3")
        logging.debug("The new xml is %s", xml)
        xml_file = xml.xmltreefile.name
        return xml_file

    def change_xml_name(xml, name):
        """
            Change name attribute in the xml with <name> and return updated xml
        """
        xml.vm_name = name
        xml_file = xml.xmltreefile.name
        return xml_file

    vm_name = params.get("main_vm")
    options = params.get("create_options", "")
    status_error = ("yes" == params.get("status_error", "no"))
    c_user = params.get("create_login_user", "root")
    readonly = params.get("readonly", False)
    if c_user == "root":
        c_passwd = params.get("password")
    else:
        c_passwd = params.get("create_login_password_nonroot")
    vm = env.get_vm(vm_name)

    if vm.exists():
        if vm.is_alive():
            vm.destroy()
        xmlfile = vm.backup_xml()
        if not options or "--validate" in options:
            xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vm.undefine()
            if "new_name" in params:
                # Take existing VM XML and change a name there to one from config file
                new_name = params.get("new_name")
                xmlfile = change_xml_name(xml_backup, new_name)
                vm.name = new_name
            else:
                xmlfile = xmlfile_with_extra_attibute(xml_backup)
        else:
            vm.undefine()

    else:
        xmlfile = params.get("create_domain_xmlfile")
        if xmlfile is None:
            test.fail("Please provide domain xml file for create or"
                      " existing domain name with main_vm = xx")
        # get vm name from xml file
        xml_cut = process.getoutput("grep '<name>.*</name>' %s" % xmlfile, shell=True)
        vm_name = xml_cut.strip(' <>').strip("name").strip("<>/")
        logging.debug("vm_name is %s", vm_name)
        vm = env.get_vm(vm_name)

    try:
        def create_status_check(vm):
            """
            check guest status
            1. if have options --paused: check status and resume
            2. check if guest is running after 1
            """
            if "--paused" in options:
                # make sure guest is paused
                ret = utils_misc.wait_for(lambda: vm.is_paused(), 30)
                if not ret:
                    test.fail("Guest status is not paused with"
                              "options %s, state is %s" %
                              (options, vm.state()))
                else:
                    logging.info("Guest status is paused.")
                vm.resume()

            # make sure guest is running
            ret = utils_misc.wait_for(lambda: vm.state() == "running", 30)
            if ret:
                logging.info("Guest is running now.")
            else:
                test.fail("Fail to create guest, guest state is %s"
                          % vm.state())

        def create_autodestroy_check(vm):
            """
            check if guest will disappear with --autodestroy
            """
            # make sure guest is auto destroyed
            ret = utils_misc.wait_for(lambda: not vm.exists(), 30)
            if not ret:
                test.fail("Guest still exist with options %s" %
                          options)
            else:
                logging.info("Guest does not exist after session closed.")

        try:
            if status_error:
                output = virsh.create(xmlfile, options, readonly=readonly,
                                      debug=True, ignore_status=True)
                if output.exit_status:
                    logging.info("Fail to create guest as expect:%s",
                                 output.stderr)
                if vm.state() == "running":
                    test.fail("Expect fail, but succeed indeed")
            elif "--console" in options:
                # Use session for console
                command = "virsh create %s %s" % (xmlfile, options)
                session = aexpect.ShellSession(command)
                # check domain status including paused and running
                create_status_check(vm)
                status = utils_test.libvirt.verify_virsh_console(
                    session, c_user, c_passwd, timeout=90, debug=True)
                if not status:
                    test.fail("Fail to verify console")

                session.close()
                # check if domain exist after session closed
                if "--autodestroy" in options:
                    create_autodestroy_check(vm)

            elif "--autodestroy" in options:
                # Use session for virsh interactive mode because
                # guest will be destroyed after virsh exit
                command = "virsh"
                session = aexpect.ShellSession(command)
                while True:
                    match, text = session.read_until_any_line_matches(
                        [r"Domain \S+ created from %s" % xmlfile, r"virsh # "],
                        timeout=10, internal_timeout=1)
                    if match == -1:
                        logging.info("Run create %s %s", xmlfile, options)
                        command = "create %s %s" % (xmlfile, options)
                        session.sendline(command)
                    elif match == -2:
                        logging.info("Domain created from %s", xmlfile)
                        break
                create_status_check(vm)
                logging.info("Close session!")
                session.close()
                # check if domain exist after session closed
                create_autodestroy_check(vm)
            else:
                # have --paused option or none options
                output = virsh.create(xmlfile, options,
                                      debug=True, ignore_status=True)
                if output.exit_status:
                    test.fail("Fail to create domain:%s" %
                              output.stderr)
                create_status_check(vm)

        except (aexpect.ShellError, aexpect.ExpectError) as detail:
            log = session.get_output()
            session.close()
            vm.define(xmlfile)
            test.fail("Verify create failed:\n%s\n%s" %
                      (detail, log))
    finally:
        if "new_name" in params:
            # Destroy vm with new name and replace name back
            vm.destroy()
            xmlfile = change_xml_name(xml_backup, vm_name)
            vm.name = vm_name
        # Guest recovery
        vm.define(xmlfile)
