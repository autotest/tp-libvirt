import logging
import os
import re
import time

from avocado.utils import process
from avocado.utils import path
from avocado.core import exceptions

from virttest import nfs
from virttest import remote
from virttest import defaults
from virttest import utils_test
from virttest import virsh
from virttest import utils_libvirtd
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError
from virttest import utils_misc
from virttest.utils_misc import SELinuxBoolean
from virttest.qemu_storage import QemuImg
from virttest.utils_test import libvirt
from virttest import test_setup
from virttest.staging import utils_memory

from provider import libvirt_version
from autotest.client.shared import error


def run(test, params, env):
    """
    Test virsh migrate command.
    """

    def check_vm_state(vm, state):
        """
        Return True if vm is in the correct state.
        """
        actual_state = vm.state()
        if cmp(actual_state, state) == 0:
            return True
        else:
            return False

    def cleanup_dest(vm, src_uri=""):
        """
        Clean up the destination host environment
        when doing the uni-direction migration.
        """
        logging.info("Cleaning up VMs on %s" % vm.connect_uri)
        try:
            if virsh.domain_exists(vm.name, uri=vm.connect_uri):
                vm_state = vm.state()
                if vm_state == "paused":
                    vm.resume()
                elif vm_state == "shut off":
                    vm.start()
                vm.destroy(gracefully=False)

                if vm.is_persistent():
                    vm.undefine()

        except Exception, detail:
            logging.error("Cleaning up destination failed.\n%s" % detail)

        if src_uri:
            vm.connect_uri = src_uri

    def do_migration(delay, vm, dest_uri, options, extra):
        logging.info("Sleeping %d seconds before migration" % delay)
        time.sleep(delay)
        # Migrate the guest.
        successful = vm.migrate(
            dest_uri, options, extra, True, True).exit_status
        logging.info("successful: %d", successful)
        if int(successful) != 0:
            logging.error("Migration failed for %s." % vm_name)
            return False

        if options.count("dname") or extra.count("dname"):
            vm.name = extra.split()[1].strip()

        if vm.is_alive():  # vm.connect_uri was updated
            logging.info("Alive guest found on destination %s." % dest_uri)
        else:
            if not options.count("offline"):
                logging.error("VM not alive on destination %s" % dest_uri)
                return False

        # Throws exception if console shows panic message
        vm.verify_kernel_crash()
        return True

    def numa_pin(memory_mode, memnode_mode, numa_dict_list, host_numa_node):
        """
        creates dictionary numatune memory
        creates list of dictionaries for numatune memnode

        :param memory_mode: memory mode of guest numa
        :param memnode_mode: memory mode list for each specific node
        :param numa_dict_list: list of guest numa
        :param host_numa_node: list of host numa
        :return: list of memnode dictionaries
        :return: memory dictionary
        """
        memory_placement = params.get("memory_placement", "static")
        memnode_list = []
        memory = {}
        memory['mode'] = str(memory_mode)
        memory['placement'] = str(memory_placement)

        if (len(numa_dict_list) == 1):
            # 1 guest numa available due to 1 vcpu then pin it
            # with one host numa
            memnode_dict = {}
            memory['nodeset'] = str(host_numa_node[0])
            memnode_dict['cellid'] = "0"
            memnode_dict['mode'] = str(memnode_mode[0])
            memnode_dict['nodeset'] = str(memory['nodeset'])
            memnode_list.append(memnode_dict)

        else:
            for index in range(2):
                memnode_dict = {}
                memnode_dict['cellid'] = str(index)
                memnode_dict['mode'] = str(memnode_mode[index])
                if (len(host_numa_node) == 1):
                    # Both guest numa pinned to same host numa as 1 hostnuma
                    # available
                    memory['nodeset'] = str(host_numa_node[0])
                    memnode_dict['nodeset'] = str(memory['nodeset'])
                else:
                    # Both guest numa pinned to different host numa
                    memory['nodeset'] = "%s,%s" % (str(host_numa_node[0]),
                                                   str(host_numa_node[1]))
                    memnode_dict['nodeset'] = str(host_numa_node[index])
                memnode_list.append(memnode_dict)
        return memory, memnode_list

    def create_numa(vcpu, max_mem, max_mem_unit):
        """
        creates list of dictionaries of numa

        :param vcpu: vcpus of existing guest
        :param max_mem: max_memory of existing guest
        :param max_mem_unit: unit of max_memory
        :return: numa dictionary list
        """
        numa_dict = {}
        numa_dict_list = []
        if vcpu == 1:
            numa_dict['id'] = '0'
            numa_dict['cpus'] = '0'
            numa_dict['memory'] = str(max_mem)
            numa_dict['unit'] = str(max_mem_unit)
            numa_dict_list.append(numa_dict)
        else:
            for index in range(2):
                numa_dict['id'] = str(index)
                numa_dict['memory'] = str(max_mem / 2)
                numa_dict['unit'] = str(max_mem_unit)
                if vcpu == 2:
                    numa_dict['cpus'] = "%s" % str(index)
                else:
                    if index == 0:
                        if vcpu == 3:
                            numa_dict['cpus'] = "%s" % str(index)
                        if vcpu > 3:
                            numa_dict['cpus'] = "%s-%s" % (str(index),
                                                           str(vcpu / 2 - 1))
                    else:
                        numa_dict['cpus'] = "%s-%s" % (str(vcpu / 2),
                                                       str(vcpu - 1))
                numa_dict_list.append(numa_dict)
                numa_dict = {}
        return numa_dict_list

    def enable_hugepage(vmname, no_of_HPs, hp_unit='', hp_node='', pin=False,
                        node_list=[], host_hp_size=0, numa_pin=False):
        """
        creates list of dictionaries of page tag for HP

        :param vmname: name of the guest
        :param no_of_HPs: Number of hugepages
        :param hp_unit: unit of HP size
        :param hp_node: number of numa nodes to be HP pinned
        :param pin: flag to pin HP with guest numa or not
        :param node_list: Numa node list
        :param host_hp_size: size of the HP to pin with guest numa
        :param numa_pin: flag to numa pin
        :return: list of page tag dictionary for HP pin
        """
        dest_machine = params.get("migrate_dest_host")
        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")
        command = "cat /proc/meminfo | grep HugePages_Free"
        server_session = remote.wait_for_login('ssh', dest_machine, '22',
                                               server_user, server_pwd,
                                               r"[\#\$]\s*$")
        cmd_output = server_session.cmd_status_output(command)
        server_session.close()
        if (cmd_output[0] == 0):
            dest_HP_free = cmd_output[1].strip('HugePages_Free:').strip()
        else:
            raise error.TestNAError("HP not supported/configured")
        hp_list = []

        # setting hugepages in destination machine here as remote ssh
        # configuration is done
        hugepage_assign(str(no_of_HPs), target_ip=dest_machine,
                        user=server_user, password=server_pwd)
        logging.debug("Remote host hugepage config done")
        if numa_pin:
            for each_node in node_list:
                if (each_node['mode'] == 'strict'):
                    # reset source host hugepages
                    if int(utils_memory.get_num_huge_pages() > 0):
                        logging.debug("reset source host hugepages")
                        hugepage_assign("0")
                    # reset dest host hugepages
                    if (int(dest_HP_free) > 0):
                        logging.debug("reset dest host hugepages")
                        hugepage_assign("0", target_ip=dest_machine,
                                        user=server_user, password=server_pwd)
                    # set source host hugepages for the specific node
                    logging.debug("set src host hugepages for specific node")
                    hugepage_assign(str(no_of_HPs), node=each_node['nodeset'],
                                    hp_size=str(host_hp_size))
                    # set dest host hugepages for specific node
                    logging.debug("set dest host hugepages for specific node")
                    hugepage_assign(str(no_of_HPs), target_ip=dest_machine,
                                    node=each_node['nodeset'], hp_size=str(
                                    host_hp_size), user=server_user,
                                    password=server_pwd)
        if not pin:
            vm_xml.VMXML.set_memoryBacking_tag(vmname)
            logging.debug("Hugepage without pin")
        else:
            hp_dict = {}
            hp_dict['size'] = str(host_hp_size)
            hp_dict['unit'] = str(hp_unit)
            if int(hp_node) == 1:
                hp_dict['nodeset'] = "0"
                logging.debug("Hugepage with pin to 1 node")
            else:
                hp_dict['nodeset'] = "0-1"
                logging.debug("Hugepage with pin to both nodes")
            hp_list.append(hp_dict)
            logging.debug(hp_list)
        return hp_list

    def hugepage_assign(hp_num, target_ip='', node='', hp_size='', user='',
                        password=''):
        """
        Allocates hugepages for src and dst machines

        :param hp_num: number of hugepages
        :param target_ip: ip address of destination machine
        :param node: numa node to which HP have to be allocated
        :param hp_size: hugepage size
        :param user: remote machine's username
        :param password: remote machine's password
        """
        command = ""
        if node == '':
            if target_ip == '':
                utils_memory.set_num_huge_pages(int(hp_num))
            else:
                command = "echo %s > /proc/sys/vm/nr_hugepages" % (hp_num)
        else:
            command = "echo %s > /sys/devices/system/node/node" % (hp_num)
            command += "%s/hugepages/hugepages-%skB/" % (str(node), hp_size)
            command += "nr_hugepages"
        if command != "":
            if target_ip != "":
                server_session = remote.wait_for_login('ssh', target_ip, '22',
                                                       user, password,
                                                       r"[\#\$]\s*$")
                cmd_output = server_session.cmd_status_output(command)
                server_session.close()
                if (cmd_output[0] != 0):
                    raise error.TestNAError("HP not supported/configured")
            else:
                process.system_output(command, verbose=True, shell=True)

    def create_mem_hotplug_xml(mem_size, mem_unit, numa_node='',
                               mem_model='dimm'):
        """
        Forms and return memory device xml for hotplugging

        :param mem_size: memory to be hotplugged
        :param mem_unit: unit for memory size
        :param numa_node: numa node to which memory is hotplugged
        :param mem_model: memory model to be hotplugged
        :return: xml with memory device
        """
        mem_xml = memory.Memory()
        mem_xml.mem_model = mem_model
        target_xml = memory.Memory.Target()
        target_xml.size = mem_size
        target_xml.size_unit = mem_unit
        if numa_node:
            target_xml.node = int(numa_node)
        mem_xml.target = target_xml
        logging.debug(mem_xml)
        mem_xml_file = os.path.join(data_dir.get_tmp_dir(),
                                    'memory_hotplug.xml')
        try:
            fp = open(mem_xml_file, 'w')
        except Exception, info:
            raise exceptions.TestError(info)
        fp.write(str(mem_xml))
        fp.close()
        return mem_xml_file

    def check_migration_timeout_suspend(params):
        """
        Handle option '--timeout --timeout-suspend'.
        As the migration thread begins to execute, this function is executed
        at same time almostly. It will sleep the specified seconds and check
        the VM state on both hosts. Both should be 'paused'.

        :param params: The parameters used

        :raise: exceptions.TestFail if the VM state is not as expected
        """
        timeout = int(params.get("timeout_before_suspend", 5))
        server_ip = params.get("server_ip")
        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")
        vm_name = params.get("migrate_main_vm")
        vm = params.get("vm_migration")
        logging.debug("Wait for %s seconds as specified by --timeout", timeout)
        # --timeout <seconds> --timeout-suspend means the vm state will change
        # to paused when live migration exceeds <seconds>. Here migration
        # command is executed on a separate thread asynchronously, so there
        # may need seconds to run the thread and other helper function logic
        # before virsh migrate command is executed. So a buffer is suggested
        # to be added to avoid of timing gap. '1' second is a usable choice.
        time.sleep(timeout + 1)
        logging.debug("Check vm state on source host after timeout")
        vm_state = vm.state()
        if vm_state != "paused":
            raise exceptions.TestFail("After timeout '%s' seconds, "
                                      "the vm state on source host should "
                                      "be 'paused', but %s found",
                                      timeout, vm_state)
        logging.debug("Check vm state on target host after timeout")
        virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                       'remote_pwd': server_pwd, 'unprivileged_user': None,
                       'ssh_remote_auth': True}
        new_session = virsh.VirshPersistent(**virsh_dargs)
        vm_state = new_session.domstate(vm_name).stdout.strip()
        if vm_state != "paused":
            raise exceptions.TestFail("After timeout '%s' seconds, "
                                      "the vm state on target host should "
                                      "be 'paused', but %s found",
                                      timeout, vm_state)
        new_session.close_session()

    for v in params.itervalues():
        if isinstance(v, str) and v.count("EXAMPLE"):
            raise exceptions.TestSkipError("Please set real value for %s" % v)

    # Check the required parameters
    extra = params.get("virsh_migrate_extra")
    migrate_uri = params.get("virsh_migrate_migrateuri", None)
    # Add migrateuri if exists and check for default example
    if migrate_uri:
        extra = ("%s --migrateuri=%s" % (extra, migrate_uri))

    graphics_uri = params.get("virsh_migrate_graphics_uri", "")
    if graphics_uri:
        extra = "--graphicsuri %s" % graphics_uri

    shared_storage = params.get("migrate_shared_storage", "")
    # use default image jeos-23-64
    if shared_storage == "":
        default_guest_asset = defaults.get_default_guest_os_info()['asset']
        shared_storage = params.get("nfs_mount_dir")
        shared_storage += ('/' + default_guest_asset + '.qcow2')

    options = params.get("virsh_migrate_options")
    # Direct migration is supported only for Xen in libvirt
    if options.count("direct") or extra.count("direct"):
        if params.get("driver_type") is not "xen":
            raise error.TestNAError("Direct migration is supported only for "
                                    "Xen in libvirt.")

    if (options.count("compressed") and not
            virsh.has_command_help_match("migrate", "--compressed")):
        raise error.TestNAError("Do not support compressed option "
                                "on this version.")

    if (options.count("graphicsuri") and not
            virsh.has_command_help_match("migrate", "--graphicsuri")):
        raise error.TestNAError("Do not support 'graphicsuri' option"
                                "on this version.")

    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")

    graphics_server = params.get("graphics_server")
    if graphics_server:
        try:
            remote_viewer_executable = path.find_command('remote-viewer')
        except path.CmdNotFoundError:
            raise error.TestNAError("No 'remote-viewer' command found.")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # For safety reasons, we'd better back up  xmlfile.
    orig_config_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if not orig_config_xml:
        raise exceptions.TestError("Backing up xmlfile failed.")

    vmxml = orig_config_xml.copy()
    graphic = vmxml.get_device_class('graphics')()

    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = params.get("nfs_mount_dir")

    # Params for NFS and SSH setup
    params["server_ip"] = params.get("migrate_dest_host")
    params["server_user"] = "root"
    params["server_pwd"] = params.get("migrate_dest_pwd")
    params["client_ip"] = params.get("migrate_source_host")
    params["client_user"] = "root"
    params["client_pwd"] = params.get("migrate_source_pwd")
    params["nfs_client_ip"] = params.get("migrate_dest_host")
    params["nfs_server_ip"] = params.get("migrate_source_host")

    # Params to enable SELinux boolean on remote host
    params["remote_boolean_varible"] = "virt_use_nfs"
    params["remote_boolean_value"] = "on"
    params["set_sebool_remote"] = "yes"

    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")

    graphics_type = params.get("graphics_type")
    graphics_port = params.get("graphics_port")
    graphics_listen = params.get("graphics_listen")
    graphics_autoport = params.get("graphics_autoport", "yes")
    graphics_listen_type = params.get("graphics_listen_type")
    graphics_listen_addr = params.get("graphics_listen_addr")

    # Update graphic XML
    if graphics_type and graphic.get_type() != graphics_type:
        graphic.set_type(graphics_type)
    if graphics_port:
        graphic.port = graphics_port
    if graphics_autoport:
        graphic.autoport = graphics_autoport
    if graphics_listen:
        graphic.listen = graphics_listen
    if graphics_listen_type:
        graphic.listen_type = graphics_listen_type
    if graphics_listen_addr:
        graphic.listen_addr = graphics_listen_addr

    vm_ref = params.get("vm_ref", vm.name)
    delay = int(params.get("virsh_migrate_delay", 10))
    ping_count = int(params.get("ping_count", 5))
    ping_timeout = int(params.get("ping_timeout", 10))
    status_error = params.get("status_error", 'no')
    libvirtd_state = params.get("virsh_migrate_libvirtd_state", 'on')
    src_state = params.get("virsh_migrate_src_state", "running")
    enable_numa = "yes" == params.get("virsh_migrate_with_numa", "no")
    enable_numa_pin = "yes" == params.get("virsh_migrate_with_numa_pin", "no")
    enable_HP = "yes" == params.get("virsh_migrate_with_HP", "no")
    enable_HP_pin = "yes" == params.get("virsh_migrate_with_HP_pin", "no")
    postcopy_cmd = params.get("virsh_postcopy_cmd", "")
    postcopy_timeout = int(params.get("postcopy_migration_timeout", "180"))
    mem_hotplug = "yes" == params.get("virsh_migrate_mem_hotplug", "no")
    # min memory that can be hotplugged 256 MiB - 256 * 1024 = 262144
    mem_hotplug_size = int(params.get("virsh_migrate_hotplug_mem", "262144"))
    mem_hotplug_count = int(params.get("virsh_migrate_mem_hotplug_count", "1"))
    mem_size_unit = params.get("virsh_migrate_hotplug_mem_unit", "KiB")

    # To check Unsupported conditions for Numa scenarios
    if enable_numa_pin:
        host_numa_node = utils_misc.NumaInfo()
        host_numa_node_list = host_numa_node.online_nodes
        memory_mode = params.get("memory_mode", 'strict')
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        vcpu = vmxml.vcpu

        # To check if Host numa node available
        if (len(host_numa_node_list) == 0):
            raise error.TestNAError("No host numa node available to pin")

        # To check preferred memory mode not used for 2 numa nodes
        # if vcpu > 1, two guest Numa nodes are created in create_numa()
        if (int(vcpu) > 1) and (memory_mode == "preferred"):
            raise error.TestNAError("NUMA memory tuning in preferred mode only"
                                    " supports single node")

    # To check if Hugepage supported and configure
    if enable_HP or enable_HP_pin:
        try:
            hp_obj = test_setup.HugePageConfig(params)
            host_hp_size = hp_obj.get_hugepage_size()
            # libvirt xml takes HP sizes in KiB
            default_hp_unit = "KiB"
            hp_pin_nodes = int(params.get("HP_pin_node_count", "2"))
            vm_max_mem = vmxml.max_mem
            no_of_HPs = int(vm_max_mem / host_hp_size) + 1
            # setting hugepages in source machine
            if (int(utils_memory.get_num_huge_pages_free()) < no_of_HPs):
                hugepage_assign(str(no_of_HPs))
            logging.debug("Hugepage support check done on host")
        except:
            raise error.TestNAError("HP not supported/configured")

    # To check mem hotplug should not exceed maxmem
    if mem_hotplug:
        # To check memory hotplug is supported by libvirt, memory hotplug
        # support QEMU/KVM driver was added in 1.2.14 version of libvirt
        if not libvirt_version.version_compare(1, 2, 14):
            raise exceptions.TestSkipError("Memory Hotplug is not supported")

        # hotplug memory in KiB
        vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_max_dimm_slots = int(params.get("virsh_migrate_max_dimm_slots",
                                           "32"))
        vm_hotplug_mem = mem_hotplug_size * mem_hotplug_count
        vm_current_mem = int(vmxml_backup.current_mem)
        vm_max_mem = int(vmxml_backup.max_mem)
        # 256 MiB(min mem that can be hotplugged) * Max no of dimm slots
        # that can be hotplugged
        vm_max_mem_rt_limit = 256 * 1024 * vm_max_dimm_slots
        # configure Maxmem in guest xml for memory hotplug to work
        try:
            vm_max_mem_rt = int(vmxml_backup.max_mem_rt)
            if(vm_max_mem_rt <= vm_max_mem_rt_limit):
                vmxml_backup.max_mem_rt = (vm_max_mem_rt_limit +
                                           vm_max_mem)
                vmxml_backup.max_mem_rt_slots = vm_max_dimm_slots
                vmxml_backup.max_mem_rt_unit = mem_size_unit
                vmxml_backup.sync()
                vm_max_mem_rt = int(vmxml_backup.max_mem_rt)
        except LibvirtXMLNotFoundError:
            vmxml_backup.max_mem_rt = (vm_max_mem_rt_limit +
                                       vm_max_mem)
            vmxml_backup.max_mem_rt_slots = vm_max_dimm_slots
            vmxml_backup.max_mem_rt_unit = mem_size_unit
            vmxml_backup.sync()
            vm_max_mem_rt = int(vmxml_backup.max_mem_rt)
        logging.debug("Hotplug mem = %d %s" % (mem_hotplug_size,
                                               mem_size_unit))
        logging.debug("Hotplug count = %d" % mem_hotplug_count)
        logging.debug("Current mem = %d" % vm_current_mem)
        logging.debug("VM maxmem = %d" % vm_max_mem_rt)
        if((vm_current_mem + vm_hotplug_mem) > vm_max_mem_rt):
            raise exceptions.TestSkipError("Cannot hotplug memory more than"
                                           "max dimm slots supported")
        if mem_hotplug_count > vm_max_dimm_slots:
            raise exceptions.TestSkipError("Cannot hotplug memory more than"
                                           " %d times" % vm_max_dimm_slots)

    # Get expected cache state for test
    attach_scsi_disk = "yes" == params.get("attach_scsi_disk", "no")
    disk_cache = params.get("virsh_migrate_disk_cache", "none")
    params["driver_cache"] = disk_cache
    unsafe_test = False
    if options.count("unsafe") and disk_cache != "none":
        unsafe_test = True

    nfs_client = None
    seLinuxBool = None
    exception = False
    remote_viewer_pid = None
    asynch_migration = False
    ret_migrate = True

    try:
        # Change the disk of the vm to shared disk
        libvirt.set_vm_disk(vm, params)
        # Backup the SELinux status on local host for recovering
        local_selinux_bak = params.get("selinux_status_bak")

        # Configure NFS client on remote host
        nfs_client = nfs.NFSClient(params)
        nfs_client.setup()

        logging.info("Enable virt NFS SELinux boolean on target host.")
        seLinuxBool = SELinuxBoolean(params)
        seLinuxBool.setup()

        subdriver = utils_test.get_image_info(shared_storage)['format']
        extra_attach = ("--config --driver qemu --subdriver %s --cache %s"
                        % (subdriver, disk_cache))

        # Attach a scsi device for special testcases
        if attach_scsi_disk:
            shared_dir = os.path.dirname(shared_storage)
            # This is a workaround. It does not take effect to specify
            # this parameter in config file
            params["image_name"] = "scsi_test"
            scsi_qemuImg = QemuImg(params, shared_dir, '')
            scsi_disk, _ = scsi_qemuImg.create(params)
            s_attach = virsh.attach_disk(vm_name, scsi_disk, "sdb",
                                         extra_attach, debug=True)
            if s_attach.exit_status != 0:
                logging.error("Attach another scsi disk failed.")

        # Get vcpu and memory info of guest for numa related tests
        if enable_numa:
            numa_dict_list = []
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
            vcpu = vmxml.vcpu
            max_mem = vmxml.max_mem
            max_mem_unit = vmxml.max_mem_unit
            if vcpu < 1:
                raise error.TestError("%s not having even 1 vcpu"
                                      % vm.name)
            else:
                numa_dict_list = create_numa(vcpu, max_mem, max_mem_unit)
            vmxml_cpu = vm_xml.VMCPUXML()
            vmxml_cpu.xml = "<cpu><numa/></cpu>"
            logging.debug(vmxml_cpu.numa_cell)
            vmxml_cpu.numa_cell = numa_dict_list
            logging.debug(vmxml_cpu.numa_cell)
            vmxml.cpu = vmxml_cpu
            if enable_numa_pin:
                memnode_mode = []
                memnode_mode.append(params.get("memnode_mode_1", 'preferred'))
                memnode_mode.append(params.get("memnode_mode_2", 'preferred'))
                memory_dict, memnode_list = numa_pin(memory_mode, memnode_mode,
                                                     numa_dict_list,
                                                     host_numa_node_list)
                logging.debug(memory_dict)
                logging.debug(memnode_list)
                if memory_dict:
                    vmxml.numa_memory = memory_dict
                if memnode_list:
                    vmxml.numa_memnode = memnode_list

            # Hugepage enabled guest by pinning to node
            if enable_HP_pin:
                # if only 1 numanode created based on vcpu available
                # check param needs to pin HP to 2 nodes
                if len(numa_dict_list) == 1:
                    if (hp_pin_nodes == 2):
                        hp_pin_nodes = 1
                if enable_numa_pin:
                    HP_page_list = enable_hugepage(vm_name, no_of_HPs,
                                                   hp_unit=default_hp_unit,
                                                   hp_node=hp_pin_nodes,
                                                   pin=True,
                                                   node_list=memnode_list,
                                                   host_hp_size=host_hp_size,
                                                   numa_pin=True)
                else:
                    HP_page_list = enable_hugepage(vm_name, no_of_HPs,
                                                   hp_unit=default_hp_unit,
                                                   hp_node=hp_pin_nodes,
                                                   host_hp_size=host_hp_size,
                                                   pin=True)
                vmxml_mem = vm_xml.VMMemBackingXML()
                vmxml_hp = vm_xml.VMHugepagesXML()
                pagexml_list = []
                for page in range(len(HP_page_list)):
                    pagexml = vmxml_hp.PageXML()
                    pagexml.update(HP_page_list[page])
                    pagexml_list.append(pagexml)
                vmxml_hp.pages = pagexml_list
                vmxml_mem.hugepages = vmxml_hp
                vmxml.mb = vmxml_mem
            vmxml.sync()

        # Hugepage enabled guest without pinning to node
        if enable_HP:
            if enable_numa_pin:
                # HP with Numa pin
                HP_page_list = enable_hugepage(vm_name, no_of_HPs, pin=False,
                                               node_list=memnode_list,
                                               host_hp_size=host_hp_size,
                                               numa_pin=True)
            else:
                # HP without Numa pin
                HP_page_list = enable_hugepage(vm_name, no_of_HPs)
        if not vm.is_alive():
            vm.start()

        vm.wait_for_login()

        # Perform memory hotplug after VM is up
        if mem_hotplug:
            if enable_numa:
                numa_node = '0'
                if mem_hotplug_count == 1:
                    mem_xml = create_mem_hotplug_xml(mem_hotplug_size,
                                                     mem_size_unit, numa_node)
                    logging.info("Trying to hotplug memory")
                    ret_attach = virsh.attach_device(vm_name, mem_xml,
                                                     flagstr="--live",
                                                     debug=True)
                    if ret_attach.exit_status != 0:
                        logging.error("Hotplugging memory failed")
                elif mem_hotplug_count > 1:
                    for each_count in range(mem_hotplug_count):
                        mem_xml = create_mem_hotplug_xml(mem_hotplug_size,
                                                         mem_size_unit,
                                                         numa_node)
                        logging.info("Trying to hotplug memory")
                        ret_attach = virsh.attach_device(vm_name, mem_xml,
                                                         flagstr="--live",
                                                         debug=True)
                        if ret_attach.exit_status != 0:
                            logging.error("Hotplugging memory failed")
                        # Hotplug memory to numa node alternatively if
                        # there are 2 nodes
                        if len(numa_dict_list) == 2:
                            if numa_node == '0':
                                numa_node = '1'
                            else:
                                numa_node = '0'
                # check hotplugged memory is reflected
                vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
                vm_new_current_mem = int(vmxml_backup.current_mem)
                logging.debug("Old current memory %d" % vm_current_mem)
                logging.debug("Hot plug mem %d" % vm_hotplug_mem)
                logging.debug("New current memory %d" % vm_new_current_mem)
                logging.debug("old mem + hotplug = %d" % (vm_current_mem +
                                                          vm_hotplug_mem))
                if not (vm_new_current_mem == (vm_current_mem +
                                               vm_hotplug_mem)):
                    raise exceptions.TestFail("Memory hotplug failed")
                else:
                    logging.debug("Memory hotplugged successfully !!!")

        # Confirm VM can be accessed through network.
        time.sleep(delay)
        vm_ip = vm.get_address()
        logging.info("To check VM network connectivity before migrating")
        s_ping, o_ping = utils_test.ping(vm_ip, count=ping_count,
                                         timeout=ping_timeout)
        logging.info(o_ping)
        if s_ping != 0:
            raise error.TestError("%s did not respond after %d sec."
                                  % (vm.name, ping_timeout))

        # Prepare for --dname dest_exist_vm
        if extra.count("dest_exist_vm"):
            logging.debug("Preparing a new vm on destination for exist dname")
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
            vmxml.vm_name = extra.split()[1].strip()
            del vmxml.uuid
            # Define a new vm on destination for --dname
            virsh.define(vmxml.xml, uri=dest_uri)

        # Prepare for --xml.
        xml_option = params.get("xml_option", "no")
        if xml_option == "yes":
            if not extra.count("--dname") and not extra.count("--xml"):
                logging.debug("Preparing new xml file for --xml option.")
                ret_attach = vm.attach_interface("--type bridge --source "
                                                 "virbr0 --target tmp-vnet",
                                                 True, True)
                if not ret_attach:
                    exception = True
                    raise error.TestError("Attaching nic to %s failed."
                                          % vm.name)
                ifaces = vm_xml.VMXML.get_net_dev(vm.name)
                new_nic_mac = vm.get_virsh_mac_address(
                    ifaces.index("tmp-vnet"))
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
                logging.debug("Xml file on source:\n%s" % vm.get_xml())
                extra = ("%s --xml=%s" % (extra, vmxml.xml))
            elif extra.count("--dname"):
                vm_new_name = params.get("vm_new_name")
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
                if vm_new_name:
                    logging.debug("Preparing change VM XML with a new name")
                    vmxml.vm_name = vm_new_name
                extra = ("%s --xml=%s" % (extra, vmxml.xml))

        # Turn VM into certain state.
        logging.debug("Turning %s into certain state." % vm.name)
        if src_state == "paused":
            if vm.is_alive():
                vm.pause()
        elif src_state == "shut off":
            if vm.is_alive():
                if not vm.shutdown():
                    vm.destroy()

        # Turn libvirtd into certain state.
        logging.debug("Turning libvirtd into certain status.")
        if libvirtd_state == "off":
            utils_libvirtd.libvirtd_stop()

        # Test uni-direction migration.
        logging.debug("Doing migration test.")
        if vm_ref != vm_name:
            vm.name = vm_ref    # For vm name error testing.
        if unsafe_test:
            options = "--live"

        if graphics_server:
            cmd = "%s %s" % (remote_viewer_executable, graphics_server)
            logging.info("Execute command: %s", cmd)
            ps = process.SubProcess(cmd, shell=True)
            remote_viewer_pid = ps.start()
            logging.debug("PID for process '%s': %s",
                          remote_viewer_executable, remote_viewer_pid)

        # Case for option '--timeout --timeout-suspend'
        # 1. Start the guest
        # 2. Set migration speed to a small value. Ensure the migration
        #    duration is much larger than the timeout value
        # 3. Start the migration
        # 4. When the eclipse time reaches the timeout value, check the guest
        #    state to be paused on both source host and target host
        # 5. Wait for the migration done. Check the guest state to be shutoff
        #    on source host and running on target host
        if extra.count("--timeout-suspend"):
            asynch_migration = True
            speed = int(params.get("migrate_speed", 1))
            timeout = int(params.get("timeout_before_suspend", 5))
            logging.debug("Set migration speed to %sM", speed)
            virsh.migrate_setspeed(vm_name, speed, debug=True)
            migration_test = libvirt.MigrationTest()
            migrate_options = "%s %s" % (options, extra)
            vms = [vm]
            params["vm_migration"] = vm
            migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                        migrate_options, thread_timeout=900,
                                        ignore_status=True,
                                        func=check_migration_timeout_suspend,
                                        func_params=params)
            ret_migrate = migration_test.RET_MIGRATION
        if postcopy_cmd != "":
            asynch_migration = True
            vms = []
            vms.append(vm)
            obj_migration = libvirt.MigrationTest()
            migrate_options = "%s %s" % (options, extra)
            cmd = "sleep 5 && virsh %s %s" % (postcopy_cmd, vm_name)
            logging.info("Starting migration in thread")
            try:
                obj_migration.do_migration(vms, src_uri, dest_uri, "orderly",
                                           options=migrate_options,
                                           thread_timeout=postcopy_timeout,
                                           ignore_status=False,
                                           func=process.run,
                                           func_params=cmd,
                                           shell=True)
            except Exception, info:
                raise exceptions.TestFail(info)
            if obj_migration.RET_MIGRATION:
                utils_test.check_dest_vm_network(vm, vm.get_address(),
                                                 server_ip, server_user,
                                                 server_pwd)
                ret_migrate = True
            else:
                ret_migrate = False
        if not asynch_migration:
            ret_migrate = do_migration(delay, vm, dest_uri, options, extra)

        dest_state = params.get("virsh_migrate_dest_state", "running")
        if ret_migrate and dest_state == "running":
            server_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            logging.info("Check VM network connectivity after migrating")
            s_ping, o_ping = utils_test.ping(vm_ip, count=ping_count,
                                             timeout=ping_timeout,
                                             output_func=logging.debug,
                                             session=server_session)
            logging.info(o_ping)
            if s_ping != 0:
                server_session.close()
                raise error.TestError("%s did not respond after %d sec."
                                      % (vm.name, ping_timeout))
            server_session.close()

        if graphics_server:
            logging.info("To check the process running '%s'.",
                         remote_viewer_executable)
            if process.pid_exists(int(remote_viewer_pid)) is False:
                raise error.TestFail("PID '%s' for process '%s'"
                                     " does not exist"
                                     % (remote_viewer_pid,
                                        remote_viewer_executable))
            else:
                logging.info("PID '%s' for process '%s' still exists"
                             " as expected.",
                             remote_viewer_pid,
                             remote_viewer_executable)
            logging.debug("Kill the PID '%s' running '%s'",
                          remote_viewer_pid,
                          remote_viewer_executable)
            process.kill_process_tree(int(remote_viewer_pid))

        # Check unsafe result and may do migration again in right mode
        check_unsafe_result = True
        if ret_migrate is False and unsafe_test:
            options = params.get("virsh_migrate_options")
            ret_migrate = do_migration(delay, vm, dest_uri, options, extra)
        elif ret_migrate and unsafe_test:
            check_unsafe_result = False
        if vm_ref != vm_name:
            vm.name = vm_name

        # Recover libvirtd state.
        logging.debug("Recovering libvirtd status.")
        if libvirtd_state == "off":
            utils_libvirtd.libvirtd_start()

        # Check vm state on destination.
        logging.debug("Checking %s state on target %s.", vm.name,
                      vm.connect_uri)
        if (options.count("dname") or
                extra.count("dname") and status_error != 'yes'):
            vm.name = extra.split()[1].strip()
        check_dest_state = True
        check_dest_state = check_vm_state(vm, dest_state)
        logging.info("Supposed state: %s" % dest_state)
        logging.info("Actual state: %s" % vm.state())

        # Check vm state on source.
        if extra.count("--timeout-suspend"):
            logging.debug("Checking '%s' state on source '%s'", vm.name,
                          src_uri)
            vm_state = virsh.domstate(vm.name, uri=src_uri).stdout.strip()
            if vm_state != "shut off":
                raise exceptions.TestFail("Local vm state should be 'shut off'"
                                          ", but found '%s'" % vm_state)

        # Recover VM state.
        logging.debug("Recovering %s state." % vm.name)
        if src_state == "paused":
            vm.resume()
        elif src_state == "shut off":
            vm.start()

        # Checking for --persistent.
        check_dest_persistent = True
        if options.count("persistent") or extra.count("persistent"):
            logging.debug("Checking for --persistent option.")
            if not vm.is_persistent():
                check_dest_persistent = False

        # Checking for --undefinesource.
        check_src_undefine = True
        if options.count("undefinesource") or extra.count("undefinesource"):
            logging.debug("Checking for --undefinesource option.")
            logging.info("Verifying <virsh domstate> DOES return an error."
                         "%s should not exist on %s." % (vm_name, src_uri))
            if virsh.domain_exists(vm_name, uri=src_uri):
                check_src_undefine = False

        # Checking for --dname.
        check_dest_dname = True
        if (options.count("dname") or extra.count("dname") and
                status_error != 'yes'):
            logging.debug("Checking for --dname option.")
            dname = extra.split()[1].strip()
            if not virsh.domain_exists(dname, uri=dest_uri):
                check_dest_dname = False

        # Checking for --xml.
        check_dest_xml = True
        if (xml_option == "yes" and not extra.count("--dname") and
                not extra.count("--xml")):
            logging.debug("Checking for --xml option.")
            vm_dest_xml = vm.get_xml()
            logging.info("Xml file on destination: %s" % vm_dest_xml)
            if not re.search(new_nic_mac, vm_dest_xml):
                check_dest_xml = False

    except Exception, detail:
        exception = True
        logging.error("%s: %s" % (detail.__class__, detail))

    # Whatever error occurs, we have to clean up all environment.
    # Make sure vm.connect_uri is the destination uri.
    vm.connect_uri = dest_uri
    if (options.count("dname") or extra.count("dname") and
            status_error != 'yes'):
        # Use the VM object to remove
        vm.name = extra.split()[1].strip()
        cleanup_dest(vm, src_uri)
        vm.name = vm_name
    else:
        cleanup_dest(vm, src_uri)

    # Recover source (just in case).
    # Simple sync cannot be used here, because the vm may not exists and
    # it cause the sync to fail during the internal backup.
    vm.destroy()
    vm.undefine()
    orig_config_xml.define()

    # cleanup xml created during memory hotplug test
    if mem_hotplug:
        if os.path.isfile(mem_xml):
            data_dir.clean_tmp_files()
            logging.debug("Cleanup mem hotplug xml")

    # cleanup hugepages
    if enable_HP or enable_HP_pin:
        logging.info("Cleanup Hugepages")
        # cleaning source hugepages
        hugepage_assign("0")
        # cleaning destination hugepages
        hugepage_assign(
            "0", target_ip=server_ip, user=server_user, password=server_pwd)

    if attach_scsi_disk:
        libvirt.delete_local_disk("file", path=scsi_disk)

    if seLinuxBool:
        logging.info("Recover virt NFS SELinux boolean on target host...")
        # keep .ssh/authorized_keys for NFS cleanup later
        seLinuxBool.cleanup(True)

    if nfs_client:
        logging.info("Cleanup NFS client environment...")
        nfs_client.cleanup()

    logging.info("Remove the NFS image...")
    source_file = params.get("source_file")
    libvirt.delete_local_disk("file", path=source_file)

    logging.info("Cleanup NFS server environment...")
    exp_dir = params.get("export_dir")
    mount_dir = params.get("mnt_path_name")
    libvirt.setup_or_cleanup_nfs(False, export_dir=exp_dir,
                                 mount_dir=mount_dir,
                                 restore_selinux=local_selinux_bak)
    if exception:
        raise error.TestError(
            "Error occurred. \n%s: %s" % (detail.__class__, detail))

    # Check test result.
    if status_error == 'yes':
        if ret_migrate:
            raise error.TestFail("Migration finished with unexpected status.")
    else:
        if not ret_migrate:
            raise error.TestFail("Migration finished with unexpected status.")
        if not check_dest_state:
            raise error.TestFail("Wrong VM state on destination.")
        if not check_dest_persistent:
            raise error.TestFail("VM is not persistent on destination.")
        if not check_src_undefine:
            raise error.TestFail("VM is not undefined on source.")
        if not check_dest_dname:
            raise error.TestFail("Wrong VM name %s on destination." % dname)
        if not check_dest_xml:
            raise error.TestFail("Wrong xml configuration on destination.")
        if not check_unsafe_result:
            raise error.TestFail("Migration finished in unsafe mode.")
