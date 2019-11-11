import logging
import platform
from six import itervalues, iteritems

from avocado.utils import path
from avocado.utils import process

from virttest import libvirt_vm
from virttest import virsh
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
        logging.debug("Host UUID (capabilities_xml): %s", xml_uuid)
        if xml_uuid == "":
            test.fail("The host uuid in capabilities_xml is none!")

        # Check the host arch.
        xml_arch = cap_xml.arch
        logging.debug("Host arch (capabilities_xml): %s", xml_arch)
        exp_arch = process.run("arch", shell=True).stdout_text.strip()
        if xml_arch != exp_arch:
            test.fail("The host arch in capabilities_xml is "
                      "expected to be %s, but get %s" %
                      (exp_arch, xml_arch))

        # Check the host cpu count.
        xml_cpu_count = cap_xml.cpu_count
        logging.debug("Host cpus count (capabilities_xml): %s", xml_cpu_count)
        search_str = 'processor'
        if platform.machine() == 's390x':
            search_str = 'cpu number'
        cmd = "grep '%s' /proc/cpuinfo | wc -l" % search_str
        exp_cpu_count = int(process.run(cmd, shell=True).stdout_text.strip())
        if xml_cpu_count != exp_cpu_count:
            test.fail("Host cpus count is expected to be %s, "
                      "but get %s" %
                      (exp_cpu_count, xml_cpu_count))

        # Check the arch of guest supported.
        guest_capa = cap_xml.get_guest_capabilities()
        logging.debug(guest_capa)

        # libvirt track wordsize in hardcode struct virArchData
        wordsize = {}
        wordsize['64'] = ['alpha', 'aarch64', 'ia64', 'mips64', 'mips64el',
                          'parisc64', 'ppc64', 'ppc64le', 's390x', 'sh4eb',
                          'sparc64', 'x86_64']
        wordsize['32'] = ['armv6l', 'armv7l', 'armv7b', 'cris', 'i686', 'lm32',
                          'm68k', 'microblaze', 'microblazeel', 'mips',
                          'mipsel', 'openrisc', 'parisc', 'ppc', 'ppcle',
                          'ppcemb', 's390', 'sh4', 'sparc', 'unicore32',
                          'xtensa', 'xtensaeb']
        uri_type = process.run("virsh uri", shell=True).stdout_text.split(':')[0]
        domain_type = "domain_" + uri_type
        for arch_dict in list(itervalues(guest_capa)):
            for arch, val_dict in list(iteritems(arch_dict)):
                # Check wordsize
                if arch not in wordsize[val_dict['wordsize']]:
                    test.fail("'%s' wordsize '%s' in "
                              "capabilities_xml not expected" %
                              (arch, val_dict['wordsize']))
                # Check the type of hypervisor
                if domain_type not in list(val_dict.keys()):
                    test.fail("domain type '%s' is not matched"
                              " under arch '%s' in "
                              "capabilities_xml" %
                              (uri_type, arch))

        # check power management support.
        try:
            pm_cmd = path.find_command('pm-is-supported')
            pm_cap_map = {'suspend': 'suspend_mem',
                          'hibernate': 'suspend_disk',
                          'suspend-hybrid': 'suspend_hybrid'}
            exp_pms = []
            for opt in pm_cap_map:
                cmd = '%s --%s' % (pm_cmd, opt)
                res = process.run(cmd, ignore_status=True, shell=True)
                if res.exit_status == 0:
                    exp_pms.append(pm_cap_map[opt])
            pms = cap_xml.power_management_list
            if set(exp_pms) != set(pms):
                test.fail("Expected supported PMs are %s, got %s "
                          "instead." % (exp_pms, pms))
        except path.CmdNotFoundError:
            logging.debug('Power management checking is skipped, since command'
                          ' pm-is-supported is not found.')

    connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                              "default"))
    # Run test case
    option = params.get("virsh_cap_options")
    try:
        output = virsh.capabilities(option, uri=connect_uri,
                                    ignore_status=False, debug=True)
        status = 0  # good
    except process.CmdError:
        status = 1  # bad
        output = ''

    # Check status_error
    status_error = params.get("status_error")
    if status_error == "yes":
        if status == 0:
            test.fail("Command virsh capabilities %s succeeded (incorrect command)" % option)
    elif status_error == "no":
        compare_capabilities_xml(output)
        if status != 0:
            test.fail("Command 'virsh capabilities %s' failed "
                      "(correct command)" % option)
