import logging

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import capability_xml
VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def parse_cpu_topology(output_string):
    """
    Parse CPU topology output into a list of dictionaries.

    :param output_string: The CPU topology output string
    :return: List of dictionaries containing CPU information
    """
    lines = output_string.strip().split('\n')
    data_lines = lines[1:]
    result = []
    for line in data_lines:
        fields = line.split('\t')
        # Skip empty lines
        if len(fields) < 7:
            continue

        cpu_info = {
            'cpuid': fields[0].strip(),
            'phyid': int(fields[1].strip()),
            'phycpus': fields[2].strip(),
            'phylist': fields[3].strip(),
            'dieid': int(fields[4].strip()),
            'diecpus': fields[5].strip(),
            'dielist': fields[6].strip()
        }

        result.append(cpu_info)

    return result


def check_cpu_topology(output_data, die_count):
    """
    Check if CPU topology output matches expected pattern based on die count.

    :param output_data: List of dictionaries containing CPU topology data
    :param die_count: Number of dies (input parameter)
    :return: Results of validation checks
    """
    results = {
        'valid': True,
        'errors': [],
        'summary': {}
    }

    expected_total_cpus = die_count * 8
    expected_cpus_per_phy = die_count * 4
    expected_last_cpu = expected_total_cpus - 1

    # Check total CPU count
    if len(output_data) != expected_total_cpus:
        results['valid'] = False
        results['errors'].append(
            f"Expected {expected_total_cpus} CPUs, got {len(output_data)}")

    # Check phyid
    halves = [
        (output_data[:expected_cpus_per_phy], 0, 0),
        (output_data[expected_cpus_per_phy:], 1, expected_cpus_per_phy)
    ]

    for half_data, expected_phyid, offset in halves:
        for i, cpu in enumerate(half_data):
            cpu_num = i + offset
            if cpu['phyid'] != expected_phyid:
                results['valid'] = False
                results['errors'].append(
                    f"CPU{cpu_num} should have phyid={expected_phyid}, "
                    f"got {cpu['phyid']}")

    # Check phylist
    phylist_patterns = [
        (output_data[:expected_cpus_per_phy],
         f"0-{expected_cpus_per_phy - 1}"),
        (output_data[expected_cpus_per_phy:],
         f"{expected_cpus_per_phy}-{expected_last_cpu}")
    ]

    for half_data, expected_phylist in phylist_patterns:
        for cpu in half_data:
            if cpu['phylist'] != expected_phylist:
                results['valid'] = False
                results['errors'].append(
                    f"CPU{cpu['cpuid']} should have "
                    f"phylist='{expected_phylist}', got '{cpu['phylist']}'")
                break

    # Check dielist
    for i, cpu in enumerate(output_data):
        die_start = (i // 4) * 4
        die_end = die_start + 3
        expected_dielist = f"{die_start}-{die_end}"

        if cpu['dielist'] != expected_dielist:
            results['valid'] = False
            results['errors'].append(
                f"CPU{i} should have dielist='{expected_dielist}', "
                f"got '{cpu['dielist']}'")

    # Check dieid
    for phyid in [0, 1]:
        phyid_cpus = [cpu for cpu in output_data if cpu['phyid'] == phyid]

        if phyid_cpus:
            base_dieid = phyid_cpus[0]['dieid']  # Get the starting dieid value
            for i, cpu in enumerate(phyid_cpus):
                expected_dieid = base_dieid + (i // 4)
                if cpu['dieid'] != expected_dieid:
                    results['valid'] = False
                    results['errors'].append(
                        f"CPU{cpu['cpuid']} (phyid={phyid}) should have "
                        f"dieid={expected_dieid}, got {cpu['dieid']}")

    # Generate summary
    results['summary'] = {
        'total_cpus': len(output_data),
        'expected_cpus': expected_total_cpus,
        'phyid_0_count': sum(1 for cpu in output_data if cpu['phyid'] == 0),
        'phyid_1_count': sum(1 for cpu in output_data if cpu['phyid'] == 1),
        'last_cpu_number': expected_last_cpu,
        'die_count': die_count
    }

    return results


def run(test, params, env):
    """
    Test start VM with hyper-v related features
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vm_attrs = eval(params.get('vm_attrs', '{}'))
    cpu_attrs = eval(params.get('cpu_attrs', '{}'))
    case = params.get('case')

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if case == 'host_capabilities':
            capa = capability_xml.CapabilityXML()
            cells = capa.cells_topology.get_cell()
            die_id_dict = {cell.cell_id: [cpu['die_id']
                                          for cpu in cell.cpu] for cell in cells}
            die_ids = [set(v) for v in die_id_dict]
            if not all([len(x) == 1 for x in die_ids]):
                test.error(
                    'The values of die_id of the same cell should be the same')
            die_id_set = set([int(x) for y in die_ids for x in y])
            if die_id_set != {0, 1}:
                test.error(
                    f'The values of die_id should be 0 or 1, not {die_id_set}')
        elif case == 'on_vm':
            vcpu = vm_attrs['vcpu']
            vmxml.setup_attrs(**vm_attrs)
            vmxml.sync()
            vm.start()
            session = vm.wait_for_login()
            shell_str = '''printf cpuid"\\t"phyid"\\t"phycpus"\\t"phylist"\\t"dieid"\\t"diecpus"\\t"dielist"\n"
for i in {0..%r}
do
printf CPU$i"\\t"
for j in physical_package_id package_cpus package_cpus_list die_id die_cpus die_cpus_list
do
printf `cat /sys/devices/system/cpu/cpu$i/topology/$j`"\\t"
done
printf "\n"
done''' % (vcpu - 1)
            shell_file = 'check_cpu_topo_die_id.sh'
            session.cmd(f"echo '{shell_str}' > {shell_file}")
            LOG.debug(session.cmd(f'cat {shell_file}'))
            check_die_output = session.cmd_output(f'sh {shell_file}')
            LOG.debug(check_die_output)
            session.cmd(f'rm {shell_file} -f')

            die_info = parse_cpu_topology(check_die_output)
            LOG.debug(die_info)

            dies = int(params.get('dies'))
            result = check_cpu_topology(die_info, dies)
            if result['valid'] is False:
                test.fail(result['errors'])

    finally:
        bkxml.sync()
