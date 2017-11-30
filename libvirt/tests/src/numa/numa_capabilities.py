import logging

from virttest import libvirt_xml
from virttest import utils_libvirtd
from virttest import utils_misc


def run(test, params, env):
    """
    Test capabilities with host numa node topology
    """
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
                cpu_topo_list.append(cpu_dict)
            logging.debug("cpu topology list from capabilities xml is %s",
                          cpu_list_from_xml)
            if cpu_list_from_xml != cpu_topo_list:
                test.fail("cpu list %s from capabilities xml not "
                          "expected.")
    finally:
        libvirtd.restart()
