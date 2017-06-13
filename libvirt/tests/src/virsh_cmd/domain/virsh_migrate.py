import logging
import os
import re
import time
import platform

from avocado.utils import process
from avocado.utils import path

from virttest import nfs
from virttest import remote
from virttest import defaults
from virttest import utils_test
from virttest import virsh
from virttest import utils_libvirtd
from virttest import data_dir
from virttest import libvirt_vm
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml.devices import memory
from virttest.libvirt_xml.xcepts import LibvirtXMLNotFoundError
from virttest import utils_misc
from virttest.utils_misc import SELinuxBoolean
from virttest.qemu_storage import QemuImg
from virttest.utils_test import libvirt
from virttest import test_setup
from virttest import ssh_key
from virttest.staging import utils_memory
from virttest.utils_iptables import Iptables
from virttest import utils_package
from virttest import utils_config
from virttest import iscsi

from provider import libvirt_version

# secret uuid generated during iscsi hotplug testcases to be cleaned up
# finally
src_uuid = ""
dest_uuid = ""


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
        logging.info("Cleaning up VMs on %s", vm.connect_uri)
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
            logging.error("Cleaning up destination failed.\n%s", detail)

        if src_uri:
            vm.connect_uri = src_uri

    def check_migration_result(migration_res):
        """
        Check the migration result.

        :param migration_res: the CmdResult of migration

        :raise: test.cancel when some known messages found
        """
        logging.debug("Migration result:\n%s", migration_res)
        if migration_res.stderr.find("error: unsupported configuration:") >= 0:
            test.cancel(migration_res.stderr)

    def do_migration(delay, vm, dest_uri, options, extra):
        logging.info("Sleeping %d seconds before migration", delay)
        time.sleep(delay)
        # Migrate the guest.
        migration_res = vm.migrate(dest_uri, options, extra, True, True)
        logging.info("Migration exit status: %d", migration_res.exit_status)
        check_migration_result(migration_res)
        if int(migration_res.exit_status) != 0:
            logging.error("Migration failed for %s.", vm_name)
            return False

        if options.count("dname") or extra.count("dname"):
            vm.name = extra.split()[1].strip()

        if vm.is_alive():  # vm.connect_uri was updated
            logging.info("Alive guest found on destination %s.", dest_uri)
        else:
            if not options.count("offline"):
                logging.error("VM not alive on destination %s", dest_uri)
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
            test.cancel("HP not supported/configured")
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
                                    node=each_node['nodeset'],
                                    hp_size=str(host_hp_size),
                                    user=server_user,
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
                    test.cancel("HP not supported/configured")
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
        xml_to_file(mem_xml, mem_xml_file)
        return mem_xml_file

    def cpu_hotplug_hotunplug(vm, vm_addr, cpu_count, operation,
                              uri=None, params=None):
        """
        Performs CPU Hotplug or Hotunplug based on the cpu_count given.

        :param vm: VM object
        :param vm_addr: IP address of VM
        :param cpu_count: No of CPUs to be hotplugged or hotunplugged
        :param operation: operation to be performed, ie hotplug or hotunplug
        :param uri: virsh connect uri if operation to be performed remotely
        :param params: Test dict params

        :raise test.fail if hotplug or hotunplug doesn't work
        """
        if params:
            session = create_session_with_remote(params)
        else:
            session = vm.wait_for_login()
        status = virsh.setvcpus(vm.name, cpu_count, extra="--live", debug=True,
                                uri=uri)
        if status.exit_status:
            test.fail("CPU Hotplug failed - %s" % status.stderr.strip())
        logging.debug("Checking CPU %s gets reflected in xml", operation)
        try:
            virsh_inst = virsh.Virsh(uri=uri)
            guest_xml = vm_xml.VMXML.new_from_dumpxml(vm.name,
                                                      virsh_instance=virsh_inst)
            vcpu_list = guest_xml.vcpus.vcpu
            enabled_cpus_count = 0
            for each_vcpu in vcpu_list:
                if(str(each_vcpu["enabled"]).strip().lower() == "yes"):
                    enabled_cpus_count += 1
            logging.debug("%s CPUs - %s", operation, cpu_count)
            logging.debug("CPUs present in guest xml- %s", enabled_cpus_count)
            if (enabled_cpus_count != cpu_count):
                test.fail("CPU %s failed as cpus is not "
                          "reflected in guest xml" % operation)

            logging.debug("Checking CPU number gets reflected from inside "
                          "guest")
            cmd = "lscpu | grep \"^CPU(s):\""
            if params:
                guest_user = params.get("username", "root")
                ssh_cmd = "ssh %s@%s" % (guest_user, vm_addr)
                ssh_cmd += " -o StrictHostKeyChecking=no"
                cmd = "%s '%s'" % (ssh_cmd, cmd)
            # vcpu count gets reflected step by step gradually, so we check
            # vcpu and compare with previous count by taking 5 seconds, if
            # there is no change in vpcu count we break the loop.
            prev_output = -1
            while True:
                ret, output = session.cmd_status_output(cmd)
                if ret:
                    test.fail("CPU %s failed - %s" % (operation, output))
                output = output.split(":")[-1].strip()

                if int(prev_output) == int(output):
                    break
                prev_output = output
                time.sleep(5)
            logging.debug("CPUs available from inside guest after %s - %s",
                          operation, output)
            if int(output) != cpu_count:
                test.fail("CPU %s failed as cpus are not "
                          "reflected from inside guest" % operation)
            logging.debug("CPU %s successful !!!", operation)
        except Exception, info:
            test.fail("CPU %s failed - %s" % (operation, info))

    def check_usable_target(vm, bus="virtio"):
        """
        checks the existing target from partition list and returns usable target

        :param vm: VM Object
        :param bus: type of disk device
        """
        if bus.lower() == "virtio":
            target = "vd%s"
        session = vm.wait_for_login()
        targets = [target % num for num in range(97, 123)]
        exist_targets = libvirt.get_parts_list(session)
        session.close()
        for each_target in targets:
            if each_target not in exist_targets:
                return each_target

    # Configure ssh key between destination machine and VM
    # before migration, so that commands can be executed from
    # destination machine to VM after migration for validation
    def pwdless_ssh_remote_host_and_guest(params, vm):
        """
        Configure passwordless ssh between remote host and guest

        :param params: Test dict params
        :param vm: VM object
        """

        config_opt = ["StrictHostKeyChecking=no"]
        guest_user = params.get("username", "root")
        guest_ip = vm.get_address()
        guest_pwd = params.get("password", "password")
        server_ip = params.get("migrate_dest_host")
        server_pwd = params.get("migrate_dest_pwd")
        server_user = params.get("server_user", "root")
        ssh_key.setup_remote_ssh_key(server_ip, server_user,
                                     server_pwd, hostname2=guest_ip,
                                     user2=guest_user,
                                     password2=guest_pwd,
                                     config_options=config_opt,
                                     public_key="rsa")

    def xml_to_file(xml, file_path):
        """
        Writes xml attributes to the file

        :param xml: xml attribute content
        :param file_path: file to which xml content to be written

        :raise: test.error() if fails to open the file_path
        """
        try:
            fp = open(file_path, 'w')
        except Exception, info:
            test.error(info)
        fp.write(str(xml))
        fp.close()

    def create_secret_disk_xml(target_name, usage_type, ephemeral="no",
                               private="yes", passphrase=None, uri=None):
        """
        Creates secret xml, defines it, set secret with uuid returns uuid

        :param target_name: disk target exposed to guest in /dev
        :param usage_type: secret usage type, iscsi, ceph etc
        :param ephemeral: yes, for secret to be stored persistently, no for not
        :param private: yes, to reveal secret to libvirt caller, no for not
        :param passphrase: passphrase to set secret with uuid
        :param uri: connect uri

        :return: uuid generated by defining secret xml
        :raise: test.error() if secret failed to define or failed to set
        """
        secret_attr = secret_xml.SecretXML()
        secret_attr.set_secret_ephemeral(ephemeral)
        secret_attr.set_secret_private(private)
        secret_attr.set_target(target_name)
        secret_attr.set_usage(usage_type)
        xml_file = os.path.join(data_dir.get_data_dir(), "secret.xml")
        xml_to_file(secret_attr, xml_file)
        virsh_output = virsh.secret_define(xml_file, uri=uri)
        if virsh_output.exit_status:
            test.error("secret xml failed to define: %s" % virsh_output.stdout)
        secret_uuid = virsh_output.stdout.split("Secret")[-1]
        secret_uuid = secret_uuid.split("created")[0].strip()
        if passphrase:
            secret = process.system_output("printf %s | base64" % passphrase,
                                           shell=True)
            if virsh.secret_set_value(secret_uuid, secret,
                                      uri=uri).exit_status:
                test.error("Failed to perform secret-set-value with "
                           "passphrase")
        return secret_uuid

    def create_iscsi_disk_xml(disk_target, params, port="3260", uri=None):
        """
        Creates iscsi disk xml from params and returns the xml file

        :param disk_target: disk target exposed to guest in /dev
        :param params: Test dict params
        :param port: iscsi port to communicate
        :param uri: connect uri

        :return: iscsi disk xml file
        """

        global src_uuid
        global dest_uuid

        auth_pass = params.get("iscsi_auth_password", "password")
        secret_target = params.get("secret_usage", "libvirtiscsi")

        # create secret xml and set secret with respective uuid
        if not dest_uuid:
            src_uuid = create_secret_disk_xml(secret_target, "iscsi",
                                              passphrase=auth_pass,
                                              uri=uri)

        # create secret xml and set secret with respective uuid in destination
        if not iscsi_hotplug_after_migrate or not iscsi_hotunplug_after_migrate:
            dest_uri = params.get("virsh_migrate_desturi")
            dest_uuid = create_secret_disk_xml(secret_target, "iscsi",
                                               passphrase=auth_pass,
                                               uri=dest_uri)
        # create iscsi disk xml
        iscsi_disk = libvirt.create_disk_xml(params)
        iscsi_xml_file = os.path.join(data_dir.get_data_dir(), "iscsi_disk.xml")
        xml_to_file(iscsi_disk, iscsi_xml_file)
        return iscsi_xml_file

    def create_session_with_remote(params):
        """
        Creates remote ssh session to remote host and returns the session obj

        :param params: Test dict params

        :return: ssh session object
        """
        remote_ip = params.get("migrate_dest_host")
        remote_pwd = params.get("migrate_dest_pwd")
        remote_user = params.get("server_user", "root")
        return remote.remote_login("ssh", remote_ip, "22",
                                   remote_user, remote_pwd,
                                   r"[\#\$]\s*$")

    # After migration, guest is not accessible from source host with VM
    # object or using ssh as it will have private ip with in remote host,
    # Before migration configure passwordless ssh between remote host and
    # guest to use ssh commands after migration between remote host and
    # guest from source host for our validation
    def check_disk_after_migration(target_dev, created_file, check_sum,
                                   guest_ip=None, vm=None, params=None,
                                   hotunplug=False):
        """
        This method checks the hotplug/hotunplug disk in guest

        :param target_dev: disk to check available for guest in /dev
        :param created_file: path for file created inside guest in disk
        :param check_sum: md5sum of the created file
        :param guest_ip: IPaddress of guest
        :param vm: VM Object of guest if not migrated yet
        :param params: Test dict params if guest migrated
        :param hotunplug: if True checks hotunplugged disk

        :raise: test.fail() if file doesn't exist or corrupted for hotplug
        :raise: test.fail() if disk still exist after hotunplug
        """
        ssh_cmd = "%s"
        cmd = "cat /proc/partitions | awk '{print $4}'"
        if not vm:
            guest_user = params.get("username", "root")
            ssh_cmd = "ssh %s@%s" % (guest_user, guest_ip)
            ssh_cmd += " -o StrictHostKeyChecking=no '%s'"
            session = create_session_with_remote(params)
        else:
            session = vm.wait_for_login()
        cmd = ssh_cmd % (cmd)
        status, output = session.cmd_status_output(cmd)
        if status:
            session.close()
            test.fail("Failed to get disk info from guest: %s" % output)
        exist_parts = output.strip().split()
        if hotunplug:
            if target_dev in exist_parts:
                session.close()
                test.fail("%s exist even after iscsi hotunplug" % target_dev)
        else:
            if target_dev not in exist_parts:
                test.fail("%s doesn't exist after iscsi hotplug" % target_dev)
            cmd = "md5sum %s" % created_file
            cmd = ssh_cmd % (cmd)
            status, output = session.cmd_status_output(cmd)
            if status:
                session.close()
                test.fail("Created file doesn't exist: %s" % output)
            actual_checksum = output.split()[0].strip()
            if actual_checksum != check_sum.strip():
                logging.debug("checksum before migration: %s" % check_sum)
                logging.debug("checksum after migration: %s" %
                              actual_checksum)
                test.fail("checksum failed for created file")
        if session:
            session.close()

    def create_file_with_hotplug_disk(target_dev, params=None, vm=None,
                                      guest_ip=None):
        """
        Makes filesystem on the target disk exposed to guest, mount it,
        creates a file, get checksum of created file for future validation.

        :param target_dev: disk to check available for guest in /dev
        :param params: Test dict params if guest migrated
        :param vm: VM Object of guest if not migrated yet
        :param guest_ip: ipaddress of guest

        :raise: test.error() if failed to create directory inside guest
        :raise: test.fail() if failed to create filesystem, mount, file.

        :return: file path, md5sum of the file created
        """
        ssh_cmd = "%s"
        if not vm:
            guest_user = params.get("username", "root")
            ssh_cmd = "ssh %s@%s" % (guest_user, guest_ip)
            ssh_cmd += " -o StrictHostKeyChecking=no '%s'"
            session = create_session_with_remote(params)
        else:
            session = vm.wait_for_login()
        cmd = "mkfs.ext4 -F /dev/%s" % target_dev
        cmd = ssh_cmd % cmd
        status, output = session.cmd_status_output(cmd)
        if status:
            session.close()
            test.fail("Failed create Filesystem in disk: %s" % output)
        cmd = "mkdir -p /mnt/%s" % (target_dev)
        cmd = ssh_cmd % cmd
        if session.cmd_status(cmd):
            session.close()
            test.error("Failed to create directory in guest")
        cmd = "mount /dev/%s /mnt/%s" % (target_dev, target_dev)
        cmd = ssh_cmd % cmd
        status, output = session.cmd_status_output(cmd)
        if status:
            session.close()
            test.fail("Failed to mount the disk in guest: %s" % output)
        file_path = "/mnt/%s/test.txt" % target_dev
        cmd = "touch %s && echo 'test' > %s" % (file_path, file_path)
        cmd = ssh_cmd % cmd
        status, output = session.cmd_status_output(cmd)
        if status:
            session.close()
            test.fail("Failed to create file in disk: %s" % output)
        cmd = "md5sum %s" % file_path
        cmd = ssh_cmd % cmd
        status, output = session.cmd_status_output(cmd)
        session.close()
        if status:
            test.fail("Failed to create file in disk: %s" % output)
        return file_path, output.split()[0].strip()

    def check_migration_timeout_suspend(params):
        """
        Handle option '--timeout --timeout-suspend'.
        As the migration thread begins to execute, this function is executed
        at same time almostly. It will sleep the specified seconds and check
        the VM state on both hosts. Both should be 'paused'.

        :param params: The parameters used

        :raise: test.fail if the VM state is not as expected
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
            test.fail("After timeout '%s' seconds, the vm state on source "
                      "host should be 'paused', but %s found" %
                      timeout, vm_state)
        logging.debug("Check vm state on target host after timeout")
        virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                       'remote_pwd': server_pwd, 'unprivileged_user': None,
                       'ssh_remote_auth': True}
        new_session = virsh.VirshPersistent(**virsh_dargs)
        vm_state = new_session.domstate(vm_name).stdout.strip()
        if vm_state != "paused":
            test.fail("After timeout '%s' seconds, the vm state on target "
                      "host should be 'paused', but %s found" %
                      timeout, vm_state)
        new_session.close_session()

    # For negative scenarios, there_desturi_nonexist and there_desturi_missing
    # let the test takes desturi from variants in cfg and for other scenarios
    # let us use API to form complete uri
    extra = params.get("virsh_migrate_extra")
    migrate_dest_ip = params.get("migrate_dest_host")
    if "virsh_migrate_desturi" not in params.keys():
        params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(migrate_dest_ip)

    migrate_uri = params.get("virsh_migrate_migrateuri", None)
    # Add migrateuri if exists and check for default example
    if migrate_uri:
        migrate_port = params.get("migrate_port", "49152")
        migrate_proto = params.get("migrate_proto")
        migrate_uri = libvirt_vm.complete_uri(migrate_dest_ip, migrate_proto,
                                              migrate_port)
        extra = ("%s --migrateuri=%s" % (extra, migrate_uri))

    graphics_uri = params.get("virsh_migrate_graphics_uri", None)
    if graphics_uri:
        graphics_port = params.get("graphics_port", "5900")
        graphics_uri = libvirt_vm.complete_uri(migrate_dest_ip, "spice",
                                               graphics_port)
        extra = "--graphicsuri %s" % graphics_uri

    # Check the required parameters
    nfs_mount_path = params.get("nfs_mount_dir")
    shared_storage = params.get("migrate_shared_storage", "")
    if shared_storage == "" or "EXAMPLE" in shared_storage:
        # retrieve shared storage image path from existing params
        try:
            image = params.get("image_name").split("/")[-1]
            image_format = params.get("image_format")
            image = "%s.%s" % (image, image_format)
            params["migrate_shared_storage"] = shared_storage = os.path.join(nfs_mount_path,
                                                                             image)
            logging.debug("shared storage image location: %s", shared_storage)
        except IndexError:
            # use default image jeos-23-64
            default_guest_asset = defaults.get_default_guest_os_info()['asset']
            default_guest_asset = "%s.qcow2" % default_guest_asset
            params["migrate_shared_storage"] = shared_storage = os.path.join(nfs_mount_path,
                                                                             (default_guest_asset))

    for v in params.itervalues():
        if isinstance(v, str) and v.count("EXAMPLE"):
            test.cancel("Please set real value for %s" % v)

    options = params.get("virsh_migrate_options")
    # Direct migration is supported only for Xen in libvirt
    if options.count("direct") or extra.count("direct"):
        if params.get("driver_type") is not "xen":
            test.cancel("Direct migration is supported only for Xen in "
                        "libvirt")

    if (options.count("compressed") and not
            virsh.has_command_help_match("migrate", "--compressed")):
        test.cancel("Do not support compressed option on this version.")

    if (options.count("graphicsuri") and not
            virsh.has_command_help_match("migrate", "--graphicsuri")):
        test.cancel("Do not support 'graphicsuri' option on this version.")

    # For --postcopy enable
    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        options = "%s %s" % (options, postcopy_options)

    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")

    graphics_server = params.get("graphics_server")
    if graphics_server:
        try:
            remote_viewer_executable = path.find_command('remote-viewer')
        except path.CmdNotFoundError:
            test.cancel("No 'remote-viewer' command found.")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    # For safety reasons, we'd better back up  xmlfile.
    orig_config_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if not orig_config_xml:
        test.error("Backing up xmlfile failed.")

    vmxml = orig_config_xml.copy()
    graphic = vmxml.get_device_class('graphics')()

    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = nfs_mount_path

    # Params for NFS and SSH setup
    params["server_ip"] = migrate_dest_ip
    params["server_user"] = "root"
    params["server_pwd"] = params.get("migrate_dest_pwd")
    params["client_ip"] = params.get("migrate_source_host")
    params["client_user"] = "root"
    params["client_pwd"] = params.get("migrate_source_pwd")
    params["nfs_client_ip"] = migrate_dest_ip
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
    migrate_there_and_back = "yes" == params.get("virsh_migrate_back", "no")
    secret_target = params.get("secret_target", "libvirtiscsi")
    secret_usage = params.get("secret_usage", "iscsi")
    # Params required for cpu hotplug/hotunplug
    cpu_hotplug = "yes" == params.get("virsh_migrate_cpu_hotplug", "no")
    cpu_hotunplug = "yes" == params.get("virsh_migrate_cpu_hotunplug", "no")
    hotplug_before_migrate = "yes" == params.get("virsh_hotplug_cpu_before",
                                                 "no")
    hotunplug_before_migrate = "yes" == params.get("virsh_hotunplug_cpu_"
                                                   "before", "no")
    hotplug_after_migrate = "yes" == params.get("virsh_hotplug_cpu_after",
                                                "no")
    hotunplug_after_migrate = "yes" == params.get("virsh_hotunplug_cpu_after",
                                                  "no")
    # Params required for iscsi hotplug/hotunplug
    iscsi_hotplug = "yes" == params.get("virsh_migrate_iscsi_hotplug", "no")
    iscsi_hotunplug = "yes" == params.get("virsh_migrate_iscsi_hotunplug",
                                          "no")
    iscsi_hotplug_before_migrate = "yes" == params.get("virsh_hotplug_iscsi_"
                                                       "before", "no")
    iscsi_hotunplug_before_migrate = "yes" == params.get("virsh_hotunplug_"
                                                         "iscsi_before", "no")
    iscsi_hotplug_after_migrate = "yes" == params.get("virsh_hotplug_iscsi_"
                                                      "after", "no")
    iscsi_hotunplug_after_migrate = "yes" == params.get("virsh_hotunplug_"
                                                        "iscsi_after", "no")
    hostname = process.system_output("hostname -f", shell=True).strip()
    params["source_host_name"] = params.get("source_host_name", hostname)
    params["target_dev"] = check_usable_target(vm)

    # Configurations for iscsi to be setup in host machine
    if iscsi_hotplug:
        iscsi_disk_xml = ""
        disk_target = params.get("target_dev")
        if "ubuntu" in platform.platform().lower():
            iscsi_package = ["tgt", "open-iscsi"]
        else:
            iscsi_package = ["scsi-target-utils", "iscsi-initiator-utils"]

        # Install linux iscsi target and initiator software
        package_mgr = utils_package.package_manager(None, iscsi_package)
        if(not package_mgr.check_installed(iscsi_package) and
           not package_mgr.install()):
            test.cancel("Packages %s required for configuring iscsi target" %
                        iscsi_package)

        # configure username and password for the client to give access
        iscsi_username = params.get("auth_user", "avocadokvm")
        iscsi_password = params.get("iscsi_auth_password", "password")
        iscsid_config = params.get("iscsi_config", "/etc/iscsi/iscsid.conf")
        try:
            iscsi_config = utils_config.SectionlessConfig(iscsid_config)
            iscsi_config["node.session.auth.username"] = iscsi_username
            iscsi_config["node.session.auth.password"] = iscsi_password
            iscsi_config["discovery.sendtargets.auth.username"] = iscsi_username
            iscsi_config["discovery.sendtargets.auth.password"] = iscsi_password
            iscsi.restart_iscsid()
        except Exception, info:
            iscsi_config.restore()
            test.error("iscsi configuration for username/password failed")

        # setup emulated iscsi target in source host
        itarget, iluns = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                        is_login=False,
                                                        chap_user=iscsi_username,
                                                        chap_passwd=iscsi_password,
                                                        restart_tgtd="yes")
        params['target'] = itarget
        logging.debug("emulated iscsi setup successful:\nTarget - %s\n"
                      "Luns - %s", itarget, iluns)

        # enable rule in iptables for iscsi
        iptable_rule = ["INPUT -m state --state NEW -p tcp --dport 3260 -j ACCEPT"]
        Iptables.setup_or_cleanup_iptables_rules(iptable_rule)

    # Configurations for cpu hotplug and cpu hotunplug
    if cpu_hotplug:
        # To check cpu hotplug is supported or not
        if not virsh.has_command_help_match("setvcpus", "--live"):
            test.cancel("The current libvirt doesn't support '--live' option "
                        "for setvcpus")
        # Ensure rtas_errd service runs inside guest for PowerPC
        if "ppc64" in platform.machine().lower():
            session = vm.wait_for_login()
            cmd = "service rtas_errd start"
            ret, output = session.cmd_status_output(cmd)
            if ret:
                test.cancel("cpu hotplug doesn't work: %s" % output)
            else:
                cmd = "service rtas_errd status | grep \"Active:\" | "
                cmd += "awk '{print $3}'"
                ret, output = session.cmd_status_output(cmd)
                if "running" not in output.strip().lower():
                    test.cancel("cpu hotplug can't work if rtas_errd service "
                                "is %s" % output)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        current_vcpus = int(params.get("virsh_migrate_vcpus_current", "8"))
        # if guest topology defined, then configure accordingly
        if vmxml.get_cpu_topology():
            vcpu_sockets = params.get("cpu_topology_sockets", "1")
            vcpu_cores = params.get("cpu_topology_cores", "8")
            vcpu_threads = params.get("cpu_topology_threads", "8")
            max_vcpus = (int(vcpu_sockets) * int(vcpu_cores) *
                         int(vcpu_threads))
            vm_xml.VMXML.set_vm_vcpus(vm_name, max_vcpus, current=current_vcpus,
                                      sockets=vcpu_sockets, cores=vcpu_cores,
                                      threads=vcpu_threads)
        else:
            max_vcpus = int(params.get("virsh_migrate_vcpus", "64"))
            vm_xml.VMXML.set_vm_vcpus(vm_name, max_vcpus, current=current_vcpus)

    # To check Unsupported conditions for Numa scenarios
    if enable_numa_pin:
        host_numa_node = utils_misc.NumaInfo()
        host_numa_node_list = host_numa_node.online_nodes
        for each_node in host_numa_node_list:
            free_mem = host_numa_node.read_from_node_meminfo(each_node,
                                                             'MemFree')
            if (int(free_mem) < int(vmxml.max_mem)):
                logging.debug("Host Numa node: %s doesnt have enough "
                              "memory", each_node)
                host_numa_node_list.remove(each_node)
        memory_mode = params.get("memory_mode", 'strict')
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        vcpu = vmxml.vcpu

        # To check if Host numa node available
        if (len(host_numa_node_list) == 0):
            test.cancel("No host numa node available to pin")

        # To check preferred memory mode not used for 2 numa nodes
        # if vcpu > 1, two guest Numa nodes are created in create_numa()
        if (int(vcpu) > 1) and (memory_mode == "preferred"):
            test.cancel("NUMA memory tuning in preferred mode only supports "
                        "single node")

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
        except Exception, info:
            test.cancel("HP not supported/configured: %s" % info)

    # To check mem hotplug should not exceed maxmem
    if mem_hotplug:
        # To check memory hotplug is supported by libvirt, memory hotplug
        # support QEMU/KVM driver was added in 1.2.14 version of libvirt
        if not libvirt_version.version_compare(1, 2, 14):
            test.cancel("Memory Hotplug is not supported")

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
        logging.debug("Hotplug mem = %d %s", mem_hotplug_size,
                      mem_size_unit)
        logging.debug("Hotplug count = %d", mem_hotplug_count)
        logging.debug("Current mem = %d", vm_current_mem)
        logging.debug("VM maxmem = %d", vm_max_mem_rt)
        if((vm_current_mem + vm_hotplug_mem) > vm_max_mem_rt):
            test.cancel("Cannot hotplug memory more than max dimm slots "
                        "supported")
        if mem_hotplug_count > vm_max_dimm_slots:
            test.cancel("Cannot hotplug memory more than %d times" %
                        vm_max_dimm_slots)

    # Get expected cache state for test
    attach_scsi_disk = "yes" == params.get("attach_scsi_disk", "no")
    disk_cache = params.get("virsh_migrate_disk_cache", "none")
    params["driver_cache"] = disk_cache
    unsafe_test = False
    if options.count("unsafe") and disk_cache != "none":
        unsafe_test = True

    nfs_client = None
    seLinuxBool = None
    skip_exception = False
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

        # Permit iptables to permit 49152-49216 ports to libvirt for
        # migration and if arch is ppc with power8 then switch off smt
        # will be taken care in remote machine for migration to succeed
        migrate_setup = libvirt.MigrationTest()
        migrate_setup.migrate_pre_setup(dest_uri, params)

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
                test.error("%s not having even 1 vcpu" % vm.name)
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

        # Perform iscsi hotplug/hotunplug before migration
        if iscsi_hotplug:
            vm_session = vm.wait_for_login()
            # passwordless ssh is not required between remote host and guest
            # if it is there and back migration as guest will be back to source
            if iscsi_hotplug_after_migrate or iscsi_hotunplug_after_migrate:
                if not migrate_there_and_back:
                    pwdless_ssh_remote_host_and_guest(params, vm)
            # create iscsi disk xml, hotplug it, checks libvirt return status
            # validate whether disk is usable from inside guest
            if iscsi_hotplug_before_migrate:
                iscsi_disk_xml = create_iscsi_disk_xml(disk_target, params,
                                                       port="3260")
                virsh_output = virsh.attach_device(vm_name, iscsi_disk_xml,
                                                   flagstr="--live",
                                                   debug=True)
                libvirt.check_exit_status(virsh_output)
                f_path, f_sum = create_file_with_hotplug_disk(disk_target, vm=vm)
                logging.debug("Iscsi Hotunplug before migration is "
                              "successful!!!")
            # hotunplug already created iscsi disk xml, checks libvirt return
            # status validate whether guest couldn't list the hotunplugged disk
            if iscsi_hotunplug_before_migrate:
                virsh_output = virsh.detach_device(vm_name, iscsi_disk_xml,
                                                   flagstr="--live",
                                                   debug=True)
                libvirt.check_exit_status(virsh_output)
                if disk_target in libvirt.get_parts_list(vm_session):
                    vm_session.close()
                    test.fail("Hotunplugged iscsi disk before migration "
                              "still exist inside guest")
                logging.debug("Iscsi Hotunplug before migration is "
                              "successful!!!")
            if vm_session:
                vm_session.close()

        # Perform cpu hotplug or hotunplug before migration
        if cpu_hotplug:
            guest_ip = vm.get_address()
            if hotplug_after_migrate or hotunplug_after_migrate:
                if not migrate_there_and_back:
                    pwdless_ssh_remote_host_and_guest(params, vm)
            if hotplug_before_migrate:
                logging.debug("Performing CPU Hotplug before migration")
                cpu_hotplug_hotunplug(vm, guest_ip, max_vcpus, "Hotplug")
            if cpu_hotunplug:
                if hotunplug_before_migrate:
                    logging.debug("Performing CPU Hot Unplug before migration")
                    cpu_hotplug_hotunplug(vm, guest_ip, current_vcpus, "Hotunplug")

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
                logging.debug("Old current memory %d", vm_current_mem)
                logging.debug("Hot plug mem %d", vm_hotplug_mem)
                logging.debug("New current memory %d", vm_new_current_mem)
                logging.debug("old mem + hotplug = %d", (vm_current_mem +
                                                         vm_hotplug_mem))
                if not (vm_new_current_mem == (vm_current_mem +
                                               vm_hotplug_mem)):
                    test.fail("Memory hotplug failed")
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
            test.error("%s did not respond after %d sec." % (vm.name,
                                                             ping_timeout))

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
                    test.error("Attaching nic to %s failed." % vm.name)
                ifaces = vm_xml.VMXML.get_net_dev(vm.name)
                new_nic_mac = vm.get_virsh_mac_address(
                    ifaces.index("tmp-vnet"))
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
                logging.debug("Xml file on source:\n%s", vm.get_xml())
                extra = ("%s --xml=%s" % (extra, vmxml.xml))
            elif extra.count("--dname"):
                vm_new_name = params.get("vm_new_name")
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
                if vm_new_name:
                    logging.debug("Preparing change VM XML with a new name")
                    vmxml.vm_name = vm_new_name
                extra = ("%s --xml=%s" % (extra, vmxml.xml))

        # Turn VM into certain state.
        logging.debug("Turning %s into certain state.", vm.name)
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
                test.fail(info)
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
                test.error("%s did not respond after %d sec." % (vm.name,
                                                                 ping_timeout))
            server_session.close()
            logging.debug("Migration from %s to %s success" %
                          (src_uri, dest_uri))
            if migrate_there_and_back:
                # pre migration setup for local machine
                migrate_setup.migrate_pre_setup(src_uri, params)
                logging.debug("Migrating back to source from %s to %s" %
                              (dest_uri, src_uri))
                params["connect_uri"] = dest_uri
                if not asynch_migration:
                    ret_migrate = do_migration(delay, vm, src_uri, options,
                                               extra)
                elif extra.count("--timeout-suspend"):
                    func = check_migration_timeout_suspend
                    try:
                        migration_test.do_migration(vms, dest_uri, src_uri,
                                                    'orderly', migrate_options,
                                                    thread_timeout=900,
                                                    ignore_status=True,
                                                    func=func,
                                                    func_params=params)
                    except Exception, info:
                        test.fail(info)
                    ret_migrate = migration_test.RET_MIGRATION
                elif postcopy_cmd != "":
                    try:
                        obj_migration.do_migration(vms, dest_uri, src_uri, "orderly",
                                                   options=migrate_options,
                                                   thread_timeout=postcopy_timeout,
                                                   ignore_status=False,
                                                   func=process.run,
                                                   func_params=cmd,
                                                   shell=True)
                    except Exception, info:
                        test.fail(info)
                    ret_migrate = obj_migration.RET_MIGRATION
                logging.info("To check VM network connectivity after "
                             "migrating back to source")
                s_ping, o_ping = utils_test.ping(vm_ip, count=ping_count,
                                                 timeout=ping_timeout)
                logging.info(o_ping)
                if s_ping != 0:
                    test.error("%s did not respond after %d sec." %
                               (vm.name, ping_timeout))
                # clean up of pre migration setup for local machine
                migrate_setup.migrate_pre_setup(src_uri, params,
                                                cleanup=True)

            # Perform iscsi hotplug/hotunplug after migration
            if iscsi_hotplug:
                # if iscsi disk is hotplugged in source host before migration
                # but not hotunplugged in source host, then validate whether
                # hotplugged disk is still available for guest from remote
                # host after migration and file created is available with
                # proper checksum
                if(iscsi_hotplug_before_migrate and not
                   iscsi_hotunplug_before_migrate):
                    check_disk_after_migration(disk_target, f_path, f_sum,
                                               guest_ip=vm_ip,
                                               params=params)
                # for there and back migration use source uri to connect
                if migrate_there_and_back:
                    uri = src_uri
                if iscsi_hotplug_after_migrate:
                    if not iscsi_disk_xml:
                        iscsi_xml_file = create_iscsi_disk_xml(disk_target,
                                                               params,
                                                               port="3260",
                                                               uri=uri)
                    virsh_output = virsh.attach_device(vm_name, iscsi_disk_xml,
                                                       flagstr="--live",
                                                       debug=True, uri=uri)
                    libvirt.check_exit_status(virsh_output)
                    # for there and back migration use vm object as guest will
                    # be available in source, else connect to remote host and
                    # then ssh to guest
                    if migrate_there_and_back:
                        f_path, f_sum = create_file_with_hotplug_disk(disk_target, vm=vm)
                    else:
                        f_path, f_sum = create_file_with_hotplug_disk(disk_target,
                                                                      params=params,
                                                                      guest_ip=vm_ip)
                    logging.debug("Iscsi Hotplug after migration is "
                                  "successful!!!")
                if iscsi_hotunplug_after_migrate:
                    virsh_output = virsh.detach_device(vm_name, iscsi_disk_xml,
                                                       flagstr="--live",
                                                       debug=True, uri=uri)
                    libvirt.check_exit_status(virsh_output)
                    if migrate_there_and_back:
                        vm_session = vm.wait_for_login()
                        if disk_target in libvirt.get_parts_list(vm_session):
                            vm_session.close()
                            test.fail("Hotunplugged iscsi disk after migration "
                                      "still exist inside guest")
                        vm_session.close()
                    else:
                        check_disk_after_migration(disk_target, f_path, f_sum,
                                                   guest_ip=vm_ip,
                                                   params=params,
                                                   hotunplug=True)
                    logging.debug("Iscsi Hotunplug after migration is "
                                  "successful!!!")

            # Perform CPU hotplug or CPU hotunplug after migration
            if cpu_hotplug:
                uri = dest_uri
                session = remote.wait_for_login('ssh', server_ip, '22',
                                                server_user, server_pwd,
                                                r"[\#\$]\s*$")
                if migrate_there_and_back:
                    uri = src_uri
                if hotplug_after_migrate:
                    logging.debug("Performing CPU Hotplug after migration")
                    cpu_hotplug_hotunplug(vm, vm_ip, max_vcpus, "Hotplug",
                                          uri=uri, params=params)
                if cpu_hotunplug:
                    if hotunplug_after_migrate:
                        logging.debug("Performing CPU Hot Unplug after migration")
                        cpu_hotplug_hotunplug(vm, vm_ip, current_vcpus,
                                              "Hotunplug", uri=uri,
                                              params=params)

        if graphics_server:
            logging.info("To check the process running '%s'.",
                         remote_viewer_executable)
            if process.pid_exists(int(remote_viewer_pid)) is False:
                test.fail("PID '%s' for process '%s' does not exist"
                          % (remote_viewer_pid, remote_viewer_executable))
            else:
                logging.info("PID '%s' for process '%s' still exists"
                             " as expected.", remote_viewer_pid,
                             remote_viewer_executable)
            logging.debug("Kill the PID '%s' running '%s'",
                          remote_viewer_pid, remote_viewer_executable)
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
        logging.info("Supposed state: %s", dest_state)
        logging.info("Actual state: %s", vm.state())

        # Check vm state on source.
        if extra.count("--timeout-suspend"):
            logging.debug("Checking '%s' state on source '%s'", vm.name,
                          src_uri)
            vm_state = virsh.domstate(vm.name, uri=src_uri).stdout.strip()
            if vm_state != "shut off":
                test.fail("Local vm state should be 'shut off'"
                          ", but found '%s'" % vm_state)

        # Recover VM state.
        logging.debug("Recovering %s state.", vm.name)
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
                         "%s should not exist on %s.", vm_name, src_uri)
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
            logging.info("Xml file on destination: %s", vm_dest_xml)
            if not re.search(new_nic_mac, vm_dest_xml):
                check_dest_xml = False

    except test.cancel, detail:
        skip_exception = True
    except Exception, detail:
        exception = True
        logging.error("%s: %s", detail.__class__, detail)

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

    # cleanup target, images, xml, iptables created during iscsi hotplug test
    if iscsi_hotplug:
        libvirt.setup_or_cleanup_iscsi(is_setup=False, is_login=False,
                                       chap_user=iscsi_username,
                                       chap_passwd=iscsi_password,
                                       restart_tgtd="yes")
        if os.path.isfile(iscsi_xml_file):
            logging.debug("Cleanup iscsi hotplug xml")
            os.remove(iscsi_xml_file)
        if iptable_rule:
            Iptables.setup_or_cleanup_iptables_rules(iptable_rule,
                                                     cleanup=True)
        if src_uuid:
            uri = None
            if iscsi_hotplug_after_migrate and not iscsi_hotplug_before_migrate:
                if not migrate_there_and_back:
                    uri = dest_uri
            virsh.secret_undefine(src_uuid, uri=uri)
        if dest_uuid:
            virsh.secret_undefine(dest_uuid, uri=dest_uri)

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
    # cleanup pre migration setup for remote machine
    migrate_setup.migrate_pre_setup(dest_uri, params, cleanup=True)
    if skip_exception:
        test.cancel(detail)
    if exception:
        test.error("Error occurred. \n%s: %s" % (detail.__class__, detail))

    # Check test result.
    if status_error == 'yes':
        if ret_migrate:
            test.fail("Migration finished with unexpected status.")
    else:
        if not ret_migrate:
            test.fail("Migration finished with unexpected status.")
        if not check_dest_state:
            test.fail("Wrong VM state on destination.")
        if not check_dest_persistent:
            test.fail("VM is not persistent on destination.")
        if not check_src_undefine:
            test.fail("VM is not undefined on source.")
        if not check_dest_dname:
            test.fail("Wrong VM name %s on destination." % dname)
        if not check_dest_xml:
            test.fail("Wrong xml configuration on destination.")
        if not check_unsafe_result:
            test.fail("Migration finished in unsafe mode.")
