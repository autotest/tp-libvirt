import logging as log
import platform
from six import itervalues, iteritems

from avocado.utils import path
from avocado.utils import process
from avocado.utils import cpu

from virttest import libvirt_vm
from virttest import virsh
from virttest.libvirt_xml import capability_xml

# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_host_arch(cap_xml, test):
    """
    Check host arch information in virsh capabilities output

    :param cap_xml: CapabilityXML instance
    :param test: test object
    """
    xml_arch = cap_xml.arch
    logging.debug("Host arch (capabilities_xml): %s", xml_arch)
    exp_arch = platform.machine()
    if xml_arch != exp_arch:
        test.fail("The host arch in capabilities_xml is "
                  "expected to be %s, but get %s" %
                  (exp_arch, xml_arch))


def check_host_cpu_count(cap_xml, test):
    """
    Check host cpu number in virsh capabilities output

    :param cap_xml: CapabilityXML instance
    :param test: test object
    """
    xml_cpu_count = cap_xml.cpu_count
    logging.debug("Host cpus count (capabilities_xml): %s", xml_cpu_count)
    exp_cpu_count = cpu.online_count()
    if xml_cpu_count != exp_cpu_count:
        test.fail("Host cpus count is expected to be %s, "
                  "but get %s" %
                  (exp_cpu_count, xml_cpu_count))


def check_host_cpu_topology(cap_xml, test):
    """
    Check host cpu topology information in virsh capabilities output

    :param cap_xml: CapabilityXML instance
    :param test: test object
    """
    def _compare_value(cmd, value_in_capxml, key_in_cpu):
        """
        Compare a pair of cpu key and value in capabilities to sysfs

        :param cmd: str, command to execute
        :param value_in_capxml: str, key value in <cpu>
        :param key_in_cpu: str, key name in <cpu>
        """
        sys_value = process.run(cmd, shell=True).stdout_text.strip()
        if sys_value != value_in_capxml:
            test.fail("Expect '%s' to be '%s', "
                      "but found '%s'" % (key_in_cpu,
                                          sys_value,
                                          value_in_capxml))

    cell_list = cap_xml.cells_topology.cell
    current_arch = platform.machine()
    is_x8664 = True if current_arch == "x86_64" else False
    is_aarch64 = True if current_arch == "aarch64" else False
    is_s390x = True if current_arch == "s390x" else False
    prefix_cmd = "cat /sys/devices/system/cpu/cpu%s/topology/%s"
    file_name_key_mapping = {"physical_package_id": "socket_id",
                             "cluster_id": "cluster_id",
                             "core_id": "core_id",
                             "thread_siblings_list": "siblings"}
    if is_x8664:
        file_name_key_mapping.update({"die_id": "die_id"})
    if is_aarch64 or is_x8664:
        file_name_key_mapping.update({"cluster_id": "cluster_id"})
    if is_s390x:
        del file_name_key_mapping["cluster_id"]
    for a_cell in cell_list:
        cpu_list = a_cell.cpu
        for a_cpu in cpu_list:
            for file_name, key_in_cap in file_name_key_mapping.items():
                cmd = prefix_cmd % (a_cpu['id'], file_name)
                _compare_value(cmd, a_cpu[key_in_cap], key_in_cap)
            logging.debug("Verify cpu%s with sysfs - PASS", a_cpu['id'])


def check_arch_guest_support(cap_xml, test):
    """
    Check arch guest support information in virsh capabilities output

    :param cap_xml: CapabilityXML instance
    :param test: test object
    """
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
    uri_type = virsh.command('uri').stdout_text.split(':')[0]
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
                if (arch == "ppc64" or arch == "ppc64le"):
                    tcg_check = process.run("qemu-system-ppc64 --accel help",
                                            shell=True).stdout_text.split('\n')
                    if "tcg" not in tcg_check:
                        logging.info("expected to fail as tcg is disabled")
                    else:
                        test.fail("domain type '%s' is not matched"
                                  " under arch '%s' in "
                                  "capabilities_xml" %
                                  (uri_type, arch))
                else:
                    test.fail("domain type '%s' is not matched"
                              " under arch '%s' in "
                              "capabilities_xml" %
                              (uri_type, arch))


def check_power_management_suuport(cap_xml, test):
    """
    Check power management support in virsh capabilities output

    :param cap_xml: CapabilityXML instance
    :param test: test object
    """
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


def compare_capabilities_xml(source, test):
    """
    Check virsh capabilities output

    :param source: CapabilityXML instance
    :param test: test object
    """
    cap_xml = capability_xml.CapabilityXML()
    cap_xml.xml = source

    # Check that host has a non-empty UUID tag.
    xml_uuid = cap_xml.uuid
    logging.debug("Host UUID (capabilities_xml): %s", xml_uuid)
    if xml_uuid == "":
        test.fail("The host uuid in capabilities_xml is none!")

    check_host_arch(cap_xml, test)
    check_host_cpu_count(cap_xml, test)
    check_host_cpu_topology(cap_xml, test)
    check_arch_guest_support(cap_xml, test)
    check_power_management_suuport(cap_xml, test)


def run(test, params, env):
    """
    Test the command virsh capabilities

    (1) Call virsh capabilities
    (2) Call virsh capabilities with an unexpected option
    """
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
            test.fail("Command virsh capabilities %s succeeded (incorrect \
                       command)" % option)
    elif status_error == "no":
        compare_capabilities_xml(output, test)
        if status != 0:
            test.fail("Command 'virsh capabilities %s' failed "
                      "(correct command)" % option)
