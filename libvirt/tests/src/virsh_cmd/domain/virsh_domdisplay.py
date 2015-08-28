import re
import os
import shutil
import logging
from autotest.client.shared import error
from virttest import utils_misc, utils_libvirtd, virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.graphics import Graphics


def run(test, params, env):
    """
    Test virsh domdisplay command, return the graphic url
    This test covered vnc and spice type, also readonly and readwrite mode
    If have --include-passwd option, also need to check passwd list in result
    """

    if not virsh.has_help_command('domdisplay'):
        raise error.TestNAError("This version of libvirt doesn't support "
                                "domdisplay test")

    vm_name = params.get("main_vm", "virt-tests-vm1")
    status_error = ("yes" == params.get("status_error", "no"))
    options = params.get("domdisplay_options", "")
    graphic = params.get("domdisplay_graphic", "vnc")
    readonly = ("yes" == params.get("readonly", "no"))
    passwd = params.get("domdisplay_passwd")
    is_ssl = ("yes" == params.get("domdisplay_ssl", "no"))
    is_domid = ("yes" == params.get("domdisplay_domid", "no"))
    is_domuuid = ("yes" == params.get("domdisplay_domuuid", "no"))
    qemu_conf = params.get("qemu_conf_file", "/etc/libvirt/qemu.conf")

    # Do xml backup for final recovery
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    tmp_file = os.path.join(test.tmpdir, "qemu.conf.bk")

    def prepare_ssl_env():
        """
        Do prepare for ssl spice connection
        """
        # modify qemu.conf
        f_obj = open(qemu_conf, "r")
        cont = f_obj.read()

        # remove the existing setting
        left_cont = re.sub(r'\s*spice_tls\s*=.*', '', cont)
        left_cont = re.sub(r'\s*spice_tls_x509_cert_dir\s*=.*', '',
                           left_cont)

        # write back to origin file with cut left content
        f_obj = open(qemu_conf, "w")
        f_obj.write(left_cont)
        f_obj.write("spice_tls = 1\n")
        f_obj.write("spice_tls_x509_cert_dir = \"/etc/pki/libvirt-spice\"")
        f_obj.close()

        # make modification effect
        utils_libvirtd.libvirtd_restart()

        # Generate CA cert
        utils_misc.create_x509_dir("/etc/pki/libvirt-spice",
                                   "/C=IL/L=Raanana/O=Red Hat/CN=my CA",
                                   "/C=IL/L=Raanana/O=Red Hat/CN=my server",
                                   passwd)

    try:
        graphic_count = len(vmxml_backup.get_graphics_devices())
        if is_ssl:
            # Do backup for qemu.conf in tmp_file
            shutil.copyfile(qemu_conf, tmp_file)
            prepare_ssl_env()
            if graphic_count:
                Graphics.del_graphic(vm_name)
            Graphics.add_graphic(vm_name, passwd, "spice", True)
        else:
            if not graphic_count:
                Graphics.add_graphic(vm_name, passwd, graphic)
            # Only change graphic type and passwd
            Graphics.change_graphic_type_passwd(vm_name, graphic, passwd)

        vm = env.get_vm(vm_name)
        if not vm.is_alive():
            vm.start()

        dom_id = virsh.domid(vm_name).stdout.strip()
        dom_uuid = virsh.domuuid(vm_name).stdout.strip()

        if is_domid:
            vm_name = dom_id
        if is_domuuid:
            vm_name = dom_uuid

        # Do test
        result = virsh.domdisplay(vm_name, options, readonly=readonly,
                                  debug=True)
        logging.debug("result is %s", result)
        if result.exit_status:
            if not status_error:
                raise error.TestFail("Fail to get domain display info. Error:"
                                     "%s." % result.stderr.strip())
            else:
                logging.info("Get domain display info failed as expected. "
                             "Error:%s.", result.stderr.strip())
                return
        elif status_error:
            raise error.TestFail("Expect fail, but succeed indeed!")

        output = result.stdout.strip()
        # Different result depends on the domain xml listen address
        if output.find("localhost:") >= 0:
            expect_addr = "localhost"
        else:
            expect_addr = "127.0.0.1"

        # Get active domain xml info
        vmxml_act = vm_xml.VMXML.new_from_dumpxml(vm_name, "--security-info")
        logging.debug("xml is %s", vmxml_act.get_xmltreefile())
        graphic_act = vmxml_act.devices.by_device_tag('graphics')[0]
        port = graphic_act.port

        # Do judgement for result
        if graphic == "vnc":
            expect = "vnc://%s:%s" % (expect_addr, str(int(port)-5900))
        elif graphic == "spice" and is_ssl:
            tlsport = graphic_act.tlsPort
            expect = "spice://%s:%s?tls-port=%s" % \
                     (expect_addr, port, tlsport)
        elif graphic == "spice":
            expect = "spice://%s:%s" % (expect_addr, port)

        if options != "" and passwd is not None:
            # have --include-passwd and have passwd in xml
            if graphic == "vnc":
                expect = "vnc://:%s@%s:%s" % \
                         (passwd, expect_addr, str(int(port)-5900))
            elif graphic == "spice" and is_ssl:
                expect = expect + "&password=" + passwd
            elif graphic == "spice":
                expect = expect + "?password=" + passwd

        # Do judge for all situations
        if output == expect:
            logging.info("Get correct display:%s", output)
        else:
            raise error.TestFail("Expect %s, but get %s"
                                 % (expect, output))

    finally:
        # Domain xml recovery
        vmxml_backup.sync()
        if is_ssl:
            # qemu.conf recovery
            shutil.move(tmp_file, qemu_conf)
            utils_libvirtd.libvirtd_restart()
