import logging
import re
from xml.dom.minidom import parseString
from autotest.client.shared import utils, error
from autotest.client import os_dep
from virttest import libvirt_vm, virsh, utils_libvirtd, utils_misc
from virttest.libvirt_xml import capability_xml


def run(test, params, env):
    """
    Test the command virsh capabilities

    (1) Call virsh capabilities
    (2) Call virsh capabilities with an unexpected option
    (3) Call virsh capabilities with libvirtd service stop
    """
    def compare_capabilities_xml(source):
        cap_xml = capability_xml.CapabilityXML()
        cap_xml.xml = source

        # Check that host has a non-empty UUID tag.
        xml_uuid = cap_xml.uuid
        logging.debug("Host UUID (capabilities_xml): %s" % xml_uuid)
        if xml_uuid == "":
            raise error.TestFail("The host uuid in capabilities_xml is none!")

        # Check the host arch.
        xml_arch = cap_xml.arch
        logging.debug("Host arch (capabilities_xml): %s", xml_arch)
        exp_arch = utils.run("arch", ignore_status=True).stdout.strip()
        if cmp(xml_arch, exp_arch) != 0:
            raise error.TestFail("The host arch in capabilities_xml is expected"
                                 " to be %s, but get %s" % (exp_arch, xml_arch))

        # Check the host cpu count.
        xml_cpu_count = cap_xml.cpu_count
        logging.debug("Host cpus count (capabilities_xml): %s", xml_cpu_count)
        cmd = "grep processor /proc/cpuinfo | wc -l"
        exp_cpu_count = int(utils.run(cmd, ignore_status=True).stdout.strip())
        if xml_cpu_count != exp_cpu_count:
            raise error.TestFail("Host cpus count is expected to be %s, but get "
                                 "%s" % (exp_cpu_count, xml_cpu_count))

        # Check the arch of guest supported.
        xmltreefile = cap_xml.__dict_get__('xml')
        xml_os_arch_machine_map = cap_xml.os_arch_machine_map
        logging.debug(xml_os_arch_machine_map['hvm'])
        try:
            img = utils_misc.find_command("qemu-kvm")
        except ValueError:
            raise error.TestNAError("Cannot find qemu-kvm")
        if re.search("ppc", utils.run("arch").stdout):
            cmd = img + " --cpu ? | grep ppc"
        else:
            cmd = img + " --cpu ? | grep qemu"
        cmd_result = utils.run(cmd, ignore_status=True)
        for guest in xmltreefile.findall('guest'):
            guest_wordsize = guest.find('arch').find('wordsize').text
            logging.debug("Arch of guest supported (capabilities_xml):%s",
                          guest_wordsize)
            if not re.search(guest_wordsize, cmd_result.stdout.strip()):
                raise error.TestFail("The capabilities_xml gives an extra arch "
                                     "of guest to support!")

        # Check the type of hypervisor.
        first_guest = xmltreefile.findall('guest')[0]
        first_domain = first_guest.find('arch').findall('domain')[0]
        guest_domain_type = first_domain.get('type')
        logging.debug("Hypervisor (capabilities_xml):%s", guest_domain_type)
        cmd_result = utils.run("virsh uri", ignore_status=True)
        if not re.search(guest_domain_type, cmd_result.stdout.strip()):
            raise error.TestFail("The capabilities_xml gives an different "
                                 "hypervisor")

        # check power management support.
        try:
            pm_cmd = os_dep.command('pm-is-supported')
            pm_cap_map = {'suspend': 'suspend_mem',
                          'hibernate': 'suspend_disk',
                          'suspend-hybrid': 'suspend_hybrid',
                          }
            exp_pms = []
            for opt in pm_cap_map:
                cmd = '%s --%s' % (pm_cmd, opt)
                res = utils.run(cmd, ignore_status=True)
                if res.exit_status == 0:
                    exp_pms.append(pm_cap_map[opt])
            pms = cap_xml.power_management_list
            if set(exp_pms) != set(pms):
                raise error.TestFail("Expected supported PMs are %s, got %s "
                                     "instead." % (exp_pms, pms))
        except ValueError:
            logging.debug('Power management checking is skipped, since command '
                          'pm-is-supported is not found.')

    connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                              "default"))

    # Prepare libvirtd service
    if "libvirtd" in params:
        libvirtd = params.get("libvirtd")
        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

    # Run test case
    option = params.get("virsh_cap_options")
    try:
        output = virsh.capabilities(option, uri=connect_uri,
                                    ignore_status=False, debug=True)
        status = 0  # good
    except error.CmdError:
        status = 1  # bad
        output = ''

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check status_error
    status_error = params.get("status_error")
    if status_error == "yes":
        if status == 0:
            if libvirtd == "off":
                raise error.TestFail("Command 'virsh capabilities' succeeded "
                                     "with libvirtd service stopped, incorrect")
            else:
                raise error.TestFail("Command 'virsh capabilities %s' succeeded "
                                     "(incorrect command)" % option)
    elif status_error == "no":
        compare_capabilities_xml(output)
        if status != 0:
            raise error.TestFail("Command 'virsh capabilities %s' failed "
                                 "(correct command)" % option)
