import logging as log

from avocado.utils import cpu

from virttest import cpu as cpuutil
from virttest.libvirt_xml import vm_xml


LOG = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test that the vm can start with vcpus which is equal to host online cpu number
    and vm topology is consistent to those configured.

    Steps:
    1. Configure the vm topology with specified number of sockets, cores, and clusters
    2. Start configured vm with guest agent
    3. Check that the vm setup is consistent with the topology configured
    3a. Check the lscpu output
    3b. Check the kernel file for core id for each vcpu
    3c. Check the vcpu cluster number
    3d. Check the cluster cpu list
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    memory = params.get("memory", "4194304")
    vcpus_placement = params.get("vcpus_placement", "static")
    sockets_param = params.get("sockets", "")
    cores_param = params.get("cores", "")
    clusters_param = params.get("clusters", "")

    vcpus_num = 0
    sockets_list = []
    cores_list = []
    clusters_list = []

    # Back up domain XML
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    try:
        # Modify vm
        if vm.is_alive():
            vm.destroy()
        vmxml.placement = vcpus_placement
        vmxml.memory = int(memory)
        vmxml.current_mem = int(memory)
        vmxml.sync()

        # Set vcpus_num to the host online cpu number
        vcpus_num = cpu.online_count()
        LOG.debug("Host online CPU number: %s", str(vcpus_num))

        # Setting number of sockets, cores, and clusters
        # one_socket case
        # cores = vcpus_num // number of clusters
        if sockets_param == "one":
            sockets_list = [1]

            # many_clusters case
            if clusters_param == "many":
                # Ensure that vcpus_num is evenly divisible by the number of clusters
                clusters_list = [clusters for clusters in [2, 4, 6] if vcpus_num % clusters == 0]
                cores_list = [vcpus_num // clusters for clusters in clusters_list]
            # default_clusters case
            else:
                cores_list = [vcpus_num]

        # one_core_per_socket case
        elif sockets_param == "many" and cores_param == "one":
            sockets_list = [vcpus_num]
            cores_list = [1]

        # many_cores_per_socket
        # Ensure that vcpus_num is evenly divisible by the number of cores
        else:
            # many_clusters case
            # sockets = vcpus_num // number of cores // number of clusters
            if clusters_param == "many":
                # defaulting to either 2 or 3 cores
                cores = 2 if vcpus_num % 2 == 0 else 3
                clusters_list = [clusters for clusters in [2, 4, 6] if (vcpus_num / cores) % clusters == 0]
                cores_list = [cores] * (len(clusters_list))
                sockets_list = [(vcpus_num // cores) // clusters for clusters in clusters_list]
            # default_clusters case
            # sockets * cores = vcpus_num
            else:
                cores_list = [cores for cores in [2, 4, 6] if vcpus_num % cores == 0]
                sockets_list = [vcpus_num // cores for cores in cores_list]

        if not sockets_list or not cores_list:
            test.error("The number of sockets or cores is not valid")
        elif (len(cores_list) == 1):
            # len(sockets_list) will also be 1
            set_and_check_topology(test, vm, vcpus_num, sockets_list[0], cores_list[0])
        else:
            for i, cores in enumerate(cores_list):
                if (cores == 0):
                    continue
                if (len(sockets_list) == 1):
                    if clusters_list:
                        set_and_check_topology(test, vm, vcpus_num, sockets_list[0], cores, clusters_list[i])
                    else:
                        set_and_check_topology(test, vm, vcpus_num, sockets_list[0], cores)
                else:
                    if (sockets_list[i] == 0):
                        continue
                    if clusters_list:
                        set_and_check_topology(test, vm, vcpus_num, sockets_list[i], cores, clusters_list[i])
                    else:
                        set_and_check_topology(test, vm, vcpus_num, sockets_list[i], cores)

    finally:
        # Recover VM
        if vm.is_alive():
            vm.destroy(gracefully=False)
        LOG.info("Restoring vm...")
        vmxml_backup.sync()


def set_and_check_topology(test, vm, vcpus_num, sockets, cores, clusters=1):
    '''
    Perform steps 2-3 for each vm topology configuration

    :param test: test object
    :param vm: defined vm
    :param vcpus_num: number of vcpus to set
    :param sockets: number of sockets to set
    :param cores: number of cores to set
    :param clusters: number of clusters to set, default 1
    '''
    vm_xml.VMXML.new_from_dumpxml(vm.name).set_vm_vcpus(
        vm.name,
        vcpus_num,
        sockets=sockets,
        cores=cores,
        threads=1,
        clusters=clusters,
        add_topology=True
    )
    LOG.debug("Defined guest with '%s' vcpu(s), '%s' socket(s), and '%s' core(s), and '%s' cluster(s)",
              str(vcpus_num), str(sockets), str(cores), str(clusters))

    # Start guest agent in vm and wait for login
    vm.prepare_guest_agent()
    session = vm.wait_for_login()

    # Check kernel file for core id for each vcpu in the vm
    for vcpu in range(vcpus_num):
        cmd_coreid = f'cat /sys/devices/system/cpu/cpu{vcpu}/topology/core_id'
        ret_coreid = session.cmd_output(cmd_coreid).strip()
        if (str(vcpu) != ret_coreid):
            test.fail("In the vm kernel file, the core id for vcpu %s should not be %s" % (vcpu, ret_coreid))

    # Check vcpu cluster number
    cmd_clusterid = 'cat /sys/devices/system/cpu/cpu*/topology/cluster_id | sort | uniq -c | wc -l'
    ret_clusterid = session.cmd_output(cmd_clusterid).strip()
    # The result should be equal to sockets * clusters
    if (str(sockets * clusters) != ret_clusterid):
        test.fail("In the vm kernel file, the vcpu cluster number should be %s, not %s" % (str(sockets * clusters), ret_clusterid))

    # Check cluster cpu list
    cmd_cluster_cpu_list = 'cat /sys/devices/system/cpu/cpu*/topology/cluster_cpus_list | sort | uniq -c | wc -l'
    ret_cluster_cpu_list = session.cmd_output(cmd_cluster_cpu_list).strip()
    # The result should be equal to sockets * clusters
    if (str(sockets * clusters) != ret_cluster_cpu_list):
        test.fail("In the vm kernel file, the cluster cpu list should be %s, not %s" % (str(sockets * clusters), ret_cluster_cpu_list))

    # Check lscpu output within the vm is consistent with the topology configured
    lscpu_output = cpuutil.get_cpu_info(session)
    # get_cpu_info() should close the session
    session.close()
    lscpu_check_fail = "The configured topology is not consistent with the lscpu output within the vm for "
    if (str(vcpus_num) != lscpu_output["CPU(s)"]):
        test.fail(lscpu_check_fail + "CPU(s)")
    elif (('0' + '-' + str(vcpus_num - 1)) != lscpu_output["On-line CPU(s) list"]):
        test.fail(lscpu_check_fail + "on-line CPU(s) list")
    elif ("1" != lscpu_output["Thread(s) per core"]):
        test.fail(lscpu_check_fail + "thread(s) per core")
    elif (str(sockets) != lscpu_output["Socket(s)"]):
        test.fail(lscpu_check_fail + "socket(s)")
    elif (str(cores * clusters) != lscpu_output["Core(s) per socket"]):
        test.fail(lscpu_check_fail + "core(s) per socket")
