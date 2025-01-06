import logging as log

from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import utils_misc


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test capabilities with host numa node topology
    """
    missing_cpu_topology_key = params.get("missing_cpu_topology_key")
    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd.start()
    try:
        new_cap = libvirt_xml.CapabilityXML()
        if not libvirtd.is_running():
            test.fail("Libvirtd is not running")
        topo = new_cap.cells_topology
        logging.debug("topo xml is %s", topo.xmltreefile)
        cell_list = topo.get_cell(withmem=True)
        numa_info = utils_misc.NumaInfo()
        node_list = numa_info.online_nodes_withmem
        if len(node_list) < 2:
            test.cancel('Online NUMA nodes less than 2')

        for cell_num in range(len(cell_list)):
            # check node distances
            node_num = node_list[cell_num]
            cell_distance = cell_list[cell_num].sibling
            if cell_distance:
                logging.debug("cell %s distance is %s", node_num,
                              cell_distance)
                node_distance = numa_info.distances[node_num]
                for j in range(len(cell_list)):
                    if cell_distance[j]['value'] != node_distance[j]:
                        test.fail("cell distance value not "
                                  "expected.")
            # check node cell cpu
            cell_xml = cell_list[cell_num]
            cpu_list_from_xml = cell_xml.cpu
            node_ = numa_info.nodes[node_num]
            cpu_list = node_.cpus
            logging.debug("cell %s cpu list is %s", node_num, cpu_list)
            cpu_topo_list = []
            for cpu_id in cpu_list:
                cpu_dict = node_.get_cpu_topology(cpu_id)
                # if specific cpu topology file from sysfs doesn't exist, default 0
                # would be used in virsh capabilities
                if missing_cpu_topology_key and cpu_dict[missing_cpu_topology_key] is None:
                    cpu_dict[missing_cpu_topology_key] = '0'
                cpu_topo_list.append(cpu_dict)
            logging.debug("cpu topology list from capabilities xml is %s",
                          cpu_list_from_xml)
            for i, cpu_dict in enumerate(cpu_list_from_xml):
                if not set(cpu_dict.items()).issubset(set(cpu_topo_list[i].items())):
                    test.fail("cpu list %s from capabilities xml is not subset from "
                              "system %s" % (cpu_list_from_xml, cpu_topo_list))
    finally:
        libvirtd.restart()
