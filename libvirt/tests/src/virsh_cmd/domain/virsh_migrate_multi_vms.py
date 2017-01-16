import logging
import threading
import time
import os

from avocado.core import exceptions

from virttest import data_dir
from virttest import libvirt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_test
from virttest import nfs
from virttest import ssh_key
from virttest import utils_net
from virttest.libvirt_xml import vm_xml


# To get result in thread, using global parameters
# Result of virsh migrate command
global ret_migration
# Result of virsh domjobabort
global ret_jobabort
# If downtime is tolerable
global ret_downtime_tolerable
# List of vms static ip address list
global vm_ip_check_list

# True means command executed successfully
ret_migration = True
ret_jobabort = True
ret_downtime_tolerable = True
flag_migration = True
vm_ip_check_list = []


def make_migration_options(method, optionstr="", timeout=60):
    """
    Analyse a string to options for migration.
    They are split by one space.

    :param method: migration method ie using p2p or p2p tunnelled or direct
    :param optionstr: a string contain all options and split by space
    :param timeout: timeout for migration.
    """
    options = ""
    migrate_exec = ""

    if method == "p2p":
        migrate_exec += " --p2p"
    elif method == "p2p_tunnelled":
        migrate_exec += " --p2p --tunnelled"
    elif method == "direct":
        migrate_exec += " --direct"
    else:
        # Default method or unknown method
        pass

    for option in optionstr.split():
        if option == "live":
            options += " --live"
        elif option == "persistent":
            options += " --persistent"
        elif option == "suspend":
            options += " --suspend"
        elif option == "change-protection":
            options += " --change-protection"
        elif option == "timeout":
            options += " --timeout %s" % timeout
        elif option == "unsafe":
            options += " --unsafe"
        elif option == "auto-converge":
            options += " --auto-converge"
        else:
            logging.debug("Do not support option '%s' yet." % option)
    return options + migrate_exec


def thread_func_ping(lrunner, rrunner, vm_ip, tolerable=5):
    """
    Check connectivity during migration: Ping vm every second, check whether
    the paused state is intolerable.
    """
    cmd = "ping -c 1 %s" % vm_ip
    time1 = None    # Flag the time local vm is down
    time2 = None    # Flag the time remote vm is up
    timeout = 360   # In case thread is not killed at the end of test
    global ret_downtime_tolerable
    while timeout:
        ls = lrunner.run(cmd, ignore_status=True).exit_status
        rs = rrunner.run(cmd, ignore_status=True).exit_status
        if ls and time1 is None:   # The first time local vm is not connective
            time1 = int(time.time())
        if not rs and time2 is None:  # The first time remote vm is connective
            time2 = int(time.time())
        if time1 is None or time2 is None:
            time.sleep(1)
            timeout -= 1
        else:
            if int(time2 - time1) > int(tolerable):
                logging.debug("The time local vm is down: %s", time1)
                logging.debug("The time remote vm is up: %s", time2)
                ret_downtime_tolerable = False
            break   # Got enough information, leaving thread anyway


def thread_func_jobabort(vm):
    global ret_jobabort
    if not vm.domjobabort():
        ret_jobabort = False


def multi_migration(vm, src_uri, dest_uri, options, migrate_type,
                    migrate_thread_timeout, jobabort=False,
                    lrunner=None, rrunner=None):
    """
    Migrate multiple vms simultaneously or not.

    :param vm: list of all vm instances
    :param src_uri: source ip address for migration
    :param dest_uri: destination ipaddress for migration
    :options: options to be passed in migration command
    :migrate_type: orderly or simultaneous migration type
    :migrate_thread_timeout: thread timeout for migrating vms
    :jobabort: If jobabort is True, run "virsh domjobabort vm_name"
               during migration.
    :param timeout: thread's timeout
    :lrunner: local session instance
    :rrunner: remote session instance
    """

    obj_migration = utils_test.libvirt.MigrationTest()
    if migrate_type.lower() == "simultaneous":
        logging.info("Migrate vms simultaneously.")
        try:
            obj_migration.do_migration(vms=vm, srcuri=src_uri,
                                       desturi=dest_uri,
                                       migration_type="simultaneous",
                                       options=options,
                                       thread_timeout=migrate_thread_timeout,
                                       ignore_status=False)
            if jobabort:
                # To ensure Migration has been started.
                time.sleep(5)
                logging.info("Aborting job during migration.")
                jobabort_threads = []
                func = thread_func_jobabort
                for each_vm in vm:
                    jobabort_thread = threading.Thread(target=func,
                                                       args=(each_vm,))
                    jobabort_threads.append(jobabort_thread)
                    jobabort_thread.start()
                for jobabort_thread in jobabort_threads:
                    jobabort_thread.join(migrate_thread_timeout)
            ret_migration = True

        except Exception, info:
            raise exceptions.TestFail(info)

    elif migrate_type.lower() == "orderly":
        logging.info("Migrate vms orderly.")
        try:
            obj_migration.do_migration(vms=vm, srcuri=src_uri,
                                       desturi=dest_uri,
                                       migration_type="orderly",
                                       options=options,
                                       thread_timeout=migrate_thread_timeout,
                                       ignore_status=False)
            for each_vm in vm:
                ping_thread = threading.Thread(target=thread_func_ping,
                                               args=(lrunner, rrunner,
                                                     each_vm.get_address()))
                ping_thread.start()

        except Exception, info:
            raise exceptions.TestFail(info)

    if obj_migration.RET_MIGRATION:
        ret_migration = True
    else:
        ret_migration = False


def cleanup_dest(vm, src_uri, dest_uri):
    """
    Clean up the destination host environment
    when doing the uni-direction migration.

    :param src_uri: uri with source ip address for cleanup
    :param dest_uri: uri with destination ipaddress for cleanup
    """
    logging.info("Cleaning up VMs on %s" % dest_uri)
    try:
        if virsh.domain_exists(vm.name, uri=dest_uri):
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


def vepa_test(macvtap_vm):
    """
    vepa mode ping test, check guest can ping remote
    guest with macvtap configured

    :param macvtap_vm: VM object
    """
    global vm_ip_check_list
    session = macvtap_vm.wait_for_login()
    for each_macvtap_ip in vm_ip_check_list:
        ret, out = utils_test.ping(each_macvtap_ip, count=5, timeout=5,
                                   session=session)
        if ret:
            raise exceptions.TestFail("From %s failed to ping-%s: %s" %
                                      (macvtap_vm.name, each_macvtap_ip, out))


def get_static_ip_for_vm(params):
    """
    Generate static ipaddress

    :param params: Test params dict
    :return: static ipaddress of type string
    """
    global vm_ip_check_list
    vm_ip_static = params.get("vm_static_ip", "10.10.0.2")
    if vm_ip_static not in vm_ip_check_list:
        vm_ip_check_list.append(vm_ip_static)
    else:
        while vm_ip_static in vm_ip_check_list:
            ip_element = int(vm_ip_static.split('.')[-1])
            if (int(ip_element) + 1 >= 255) or (int(ip_element) <= 1):
                vm_ip_static = '.'.join(x.split('.')[0:3]) + ".%s" % str(2)
                continue
            vm_ip_static = ('.'.join(vm_ip_static.split('.')[0:3]) + ".%s"
                            % str(int(vm_ip_static.split('.')[-1]) + 1))
        vm_ip_check_list.append(vm_ip_static)
    return vm_ip_static


def get_persistent_file_content(mac, iface):
    """
    Forms udev rule with mac address and interface name

    :param mac: mac address of the interface
    :param iface: interface name
    :return: udev rule of type string
    """
    persistent_net_file_content = 'SUBSYSTEM=="net", '
    persistent_net_file_content += 'ACTION=="add", '
    persistent_net_file_content += 'DRIVERS=="?*", '
    persistent_net_file_content += 'ATTR{address}=="%s", ' % mac
    persistent_net_file_content += 'ATTR{dev_id}=="0x0", '
    persistent_net_file_content += 'ATTR{type}=="1", '
    persistent_net_file_content += 'KERNEL=="eth*", '
    persistent_net_file_content += 'NAME="%s"' % iface
    return persistent_net_file_content


def update_network_script(local_path, network_script):
    """
    Writes the network script to file in local path

    :param local_path: absolute path of local file
    :param network_script: config parameter of type list
    :return updated file path
    """
    try:
        with open(local_path, "a") as myfile:
            for each_line in network_script:
                myfile.write("\n%s" % each_line)
            myfile.write("\n")
        myfile.close()
        return local_path
    except (OSError, IOError, FileNotFoundError) as info:
        raise exceptions.TestError("Failed during n/w script update: %s"
                                   % info)


def macvtap_config(vm, vmxml, host_iface_name, params):
    """
    Performs network configurations for attaching macvtap interface to vm

    :param vm: VM object
    :param vmxml: guest xml
    :param host_iface_name: Host's base interface name
    :param params: Test params dict
    :return: base network script to backup & macvtap network script to cleanup
    """
    logging.debug("Performing macvtap configurations to %s", vm.name)
    cleanup_macvtap_file = ""
    cleanup_base_file = ""
    persistent_net_file = params.get("udev_rule_file",
                                     "EXAMPLE.YOUR.RULE.FILE.PATH")
    passwd = params.get("password", "EXAMPLE.VM.PASSWORD")
    vm_iface = params.get("guest_iface_name", "eth2")
    iface_mode = params.get("mode", "vepa")
    iface_model = params.get("model", "virtio")
    try:
        if vm.is_dead():
            vm.start()
        vm_session = vm.wait_for_login()
        # mac address of base interface
        mac = vmxml.get_devices(device_type="interface")[0].mac_address
        logging.debug("mac address of base interface - %s", mac)

        # attach macvtap interface
        interface_class = vm_xml.VMXML.get_device_class('interface')
        interface = interface_class(type_name="direct")
        interface.source = dict(dev=host_iface_name, mode=iface_mode)
        interface.model = iface_model
        interface.xmltreefile.write()
        virsh.attach_device(vm.name, interface.xml, flagstr="--config")
        os.remove(interface.xml)
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)

        # mac address of macvtap interface
        new_mac = vmxml_backup.get_devices(device_type="interface")[-1]
        new_mac = new_mac.mac_address
        logging.debug("mac address of macvtap interface - %s", new_mac)

        # network script for base interface
        network_script = utils_net.get_vm_network_script(vm, vm_iface, mac,
                                                         'dhcp',
                                                         '255.255.255.0')
        logging.debug("Network script for base interface - %s", network_script)

        static_ip = get_static_ip_for_vm(params)
        # Making interface name unique
        macvtap_iface = "%s%s" % (vm_iface, static_ip.split('.')[-1])
        # network script for macvtap interface
        macvtap_script = utils_net.get_vm_network_script(vm, macvtap_iface,
                                                         new_mac,
                                                         'static',
                                                         '255.255.255.0',
                                                         ip_addr=static_ip)
        logging.debug("Network script of macvtap interface - %s",
                      macvtap_script)

        # Network script file path for base and macvtap interfaces
        base_path = utils_net.get_vm_network_cfg_file(vm, vm_iface).strip()
        logging.debug("Base network path for %s = %s", vm.name, base_path)
        macvtap_path = utils_net.get_vm_network_cfg_file(vm,
                                                         macvtap_iface).strip()
        logging.debug("macvtap network path for %s = %s", vm.name,
                      macvtap_path)

        local_path = os.path.join(data_dir.get_tmp_dir(), "%s_base" % vm.name)
        if "ubuntu" in vm.get_distro().lower():
            cleanup_base_file = "%s_backup" % local_path
            remote.scp_from_remote(vm.get_address(), 22, 'root', passwd,
                                   base_path, cleanup_base_file)
        else:
            cleanup_base_file = str(base_path)
            cleanup_macvtap_file = str(macvtap_path)

        # change base interface network script file with user defined iface
        # name to ensure base network remains up after attaching macvtap
        # interface and reboot
        remote.scp_from_remote(vm.get_address(), 22, 'root', passwd,
                               base_path, local_path)
        local_path = update_network_script(local_path, network_script)
        remote.scp_to_remote(vm.get_address(), 22, 'root', passwd,
                             local_path, base_path)

        # change user defined macvtap interface
        local_path = os.path.join(data_dir.get_tmp_dir(),
                                  "%s_macvtap" % vm.name)
        remote.scp_from_remote(vm.get_address(), 22, 'root', passwd,
                               macvtap_path, local_path)
        local_path = update_network_script(local_path, macvtap_script)
        remote.scp_to_remote(vm.get_address(), 22, 'root', passwd, local_path,
                             macvtap_path)

        # Have udev rule to ensure user defined interface names are assigned
        # to respective interfaces when vm boots.
        # udev rule for base interface
        base_persistent = get_persistent_file_content(mac, vm_iface)
        # udev rule for macvtap interface
        mac_persistent = get_persistent_file_content(new_mac, macvtap_iface)
        vm_session.cmd("echo \'%s\n%s\' > %s " % (base_persistent,
                                                  mac_persistent,
                                                  persistent_net_file))
        cat_out = vm_session.cmd("cat %s" % persistent_net_file)
        logging.debug("persistent file output - %s", cat_out)
        return cleanup_base_file, cleanup_macvtap_file
    except Exception, info:
        raise exceptions.TestError("Failed to configure guest n/w: %s"
                                   % info)


def run(test, params, env):
    """
    Test migration of multi vms.
    """
    vm_names = params.get("migrate_vms").split()
    if len(vm_names) < 2:
        raise exceptions.TestSkipError("No multi vms provided.")

    # Prepare parameters
    method = params.get("virsh_migrate_method")
    jobabort = "yes" == params.get("virsh_migrate_jobabort", "no")
    options = params.get("virsh_migrate_options", "")
    status_error = "yes" == params.get("status_error", "no")
    remote_host = params.get("remote_host", "DEST_HOSTNAME.EXAMPLE.COM")
    local_host = params.get("local_host", "SOURCE_HOSTNAME.EXAMPLE.COM")
    host_user = params.get("host_user", "root")
    host_passwd = params.get("host_password", "PASSWORD")
    nfs_shared_disk = params.get("nfs_shared_disk", True)
    migration_type = params.get("virsh_migration_type", "simultaneous")
    migrate_timeout = int(params.get("virsh_migrate_thread_timeout", 900))
    migration_time = int(params.get("virsh_migrate_timeout", 60))
    login_timeout = int(params.get("virsh_vm_login_timeout", 300))
    macvtap = "yes" == params.get("virsh_migration_with_macvtap", "no")

    # Params for NFS and SSH setup
    params["server_ip"] = params.get("migrate_dest_host")
    params["server_user"] = "root"
    params["server_pwd"] = params.get("migrate_dest_pwd")
    params["client_ip"] = params.get("migrate_source_host")
    params["client_user"] = "root"
    params["client_pwd"] = params.get("migrate_source_pwd")
    params["nfs_client_ip"] = params.get("migrate_dest_host")
    params["nfs_server_ip"] = params.get("migrate_source_host")
    desturi = libvirt_vm.get_uri_with_transport(transport="ssh",
                                                dest_ip=remote_host)
    srcuri = libvirt_vm.get_uri_with_transport(transport="ssh",
                                               dest_ip=local_host)
    # Params for macvtap test
    if macvtap:
        # checks if interface name from params available in host, else returns
        # actual base interface
        iface_name = params.get("host_iface_name", None)
        iface_name = utils_net.get_macvtap_base_iface(base_interface=iface_name)
        persistent_net_file = params.get("udev_rule_file",
                                         "EXAMPLE.YOUR.RULE.FILE.PATH")
        passwd = params.get("password", "EXAMPLE.VM.PASSWORD")
        session = remote.remote_login("ssh", remote_host, "22", host_user,
                                      host_passwd, r"[\#\$]\s*$")
        # Get interface list of remote machine
        phy_iface, virt_iface = utils_net.get_sorted_net_if(session=session)
        if iface_name not in phy_iface:
            raise exceptions.TestSkipError("Migration can happen only if "
                                           "source and destination machines "
                                           "have same macvtap interface name")
        else:
            logging.debug("Interfaces are available on source and destination"
                          " - %s", iface_name)
    # Don't allow the defaults.
    if srcuri.count('///') or srcuri.count('EXAMPLE'):
        raise exceptions.TestSkipError("The srcuri '%s' is invalid" % srcuri)
    if desturi.count('///') or desturi.count('EXAMPLE'):
        raise exceptions.TestSkipError("The desturi '%s' is invalid" % desturi)

    # Config ssh autologin for remote host
    ssh_key.setup_ssh_key(remote_host, host_user, host_passwd, port=22)

    # Prepare local session and remote session
    localrunner = remote.RemoteRunner(host=remote_host, username=host_user,
                                      password=host_passwd)
    remoterunner = remote.RemoteRunner(host=remote_host, username=host_user,
                                       password=host_passwd)
    # Configure NFS in remote host
    if nfs_shared_disk:
        nfs_client = nfs.NFSClient(params)
        nfs_client.setup()

    # Prepare MigrationHelper instance
    vms = []
    vmxml = {}
    # variables used to cleanup macvtap config
    if macvtap:
        net = {}
        cleanup = {}
        macvtap_vm = env.get_vm(vm_names[0])
    try:
        for vm_name in vm_names:
            vm = env.get_vm(vm_name)
            vms.append(vm)
            # Backing up guest xml
            vmxml[vm_name] = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if macvtap:
                # make sure default interface doesn't gets affected
                net[vm_name], cleanup[vm_name] = macvtap_config(vm,
                                                                vmxml[vm_name],
                                                                iface_name,
                                                                params)
        option = make_migration_options(method, options, migration_time)

        # make sure cache=none
        if "unsafe" not in options:
            device_target = params.get("virsh_device_target", "sda")
            for vm in vms:
                if vm.is_alive():
                    vm.destroy()
            for each_vm in vm_names:
                logging.info("configure cache=none")
                # vmxml = vm_xml.VMXML.new_from_dumpxml(each_vm)
                device_source = str(vmxml[each_vm].get_disk_attr(each_vm,
                                                                 device_target,
                                                                 'source',
                                                                 'file'))
                ret_detach = virsh.detach_disk(each_vm, device_target,
                                               "--config")
                status = ret_detach.exit_status
                output = ret_detach.stdout.strip()
                logging.info("Status:%s", status)
                logging.info("Output:\n%s", output)
                if not ret_detach:
                    raise exceptions.TestError("Detach disks fails")

                subdriver = utils_test.get_image_info(device_source)['format']
                ret_attach = virsh.attach_disk(each_vm, device_source,
                                               device_target, "--driver qemu "
                                               "--config --cache none "
                                               "--subdriver %s" % subdriver)
                status = ret_attach.exit_status
                output = ret_attach.stdout.strip()
                logging.info("Status:%s", status)
                logging.info("Output:\n%s", output)
                if not ret_attach:
                    raise exceptions.TestError("Attach disks fails")
        for vm in vms:
            if vm.is_dead():
                vm.start()
                vm.wait_for_login(timeout=login_timeout)
        # Perform ping test with macvtap configured IPs before and after
        # migration
        if macvtap:
            # keep 1 vm in source to test the macvtap network connectivity
            # as we cannot reach macvtap IP of migrated vms in destination
            # from source host IP in vepa mode
            macvtap_vm = vms.pop(0)
            logging.debug("Macvtap ping test before migration")
            vepa_test(macvtap_vm)

        multi_migration(vms, srcuri, desturi, option, migration_type,
                        migrate_timeout, jobabort, lrunner=localrunner,
                        rrunner=remoterunner)
        if macvtap:
            logging.debug("Macvtap ping test after migration")
            vepa_test(macvtap_vm)
            # inserting back for cleanup
            vms.insert(0, macvtap_vm)
    except Exception, info:
        logging.error("Test failed: %s" % info)
        flag_migration = False
        # if migration fails
        if macvtap and (macvtap_vm not in vms):
            # inserting back for cleanup
            vms.insert(0, macvtap_vm)

    finally:
        # NFS cleanup
        if nfs_shared_disk:
            logging.info("NFS cleanup")
            nfs_client.cleanup(ssh_auto_recover=False)

        localrunner.session.close()
        remoterunner.session.close()

        # clean up vmxml
        for vm in vms:
            vmxml[vm.name].sync()
            if vm.is_alive():
                vm.destroy()
            vm.start()
            # macvtap cleanup
            if macvtap:
                session = vm.wait_for_login()
                try:
                    # copy back the base network script file
                    if "ubuntu" in vm.get_distro().lower():
                        vm_iface = params.get("guest_iface_name", "eth2")
                        vm_file = utils_net.get_vm_network_cfg_file(vm,
                                                                    vm_iface)
                        remote.scp_to_remote(vm.get_address(), 22, 'root',
                                             passwd, net[vm.name],
                                             vm_file.strip())
                    elif net[vm.name]:
                        session.cmd("rm -f %s" % net[vm.name])
                    # clean up udev rule
                    session.cmd("rm -f  %s" % (persistent_net_file))
                    # clean up macvtap network script file
                    if cleanup[vm.name]:
                        session.cmd("rm -f  %s" % (cleanup[vm.name]))
                # VM didn't configured with macvtap
                finally:
                    vm.destroy()
                    # clean vm in destination
                    cleanup_dest(vm, srcuri, desturi)
        # clean up temporary files created
        data_dir.clean_tmp_files()
        if not ret_migration or not flag_migration:
            if not status_error:
                raise exceptions.TestFail("Migration test failed")
        if not ret_jobabort:
            if not status_error:
                raise exceptions.TestFail("Abort migration failed")
        if not ret_downtime_tolerable:
            raise exceptions.TestFail("Downtime during migration is "
                                      "intolerable")
