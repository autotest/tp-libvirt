import logging
import os
import re
import signal
import time
import platform

from subprocess import PIPE
from subprocess import Popen

from avocado.core import exceptions
from avocado.core import data_dir
from avocado.utils import process
from avocado.utils import cpu as cpuutil

from virttest import cpu
from virttest import ssh_key
from virttest import data_dir
from virttest import nfs
from virttest import gluster
from virttest import remote
from virttest import libvirt_vm
from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_netperf
from virttest import utils_package
from virttest import utils_selinux
from virttest import utils_test
from virttest import virsh
from virttest import virt_vm
from virttest import migration

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import pool_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.smartcard import Smartcard
from virttest.libvirt_xml.devices.sound import Sound
from virttest.libvirt_xml.devices.watchdog import Watchdog
from virttest.utils_conn import SSHConnection
from virttest.utils_conn import TCPConnection
from virttest.utils_conn import TLSConnection
from virttest.utils_misc import SELinuxBoolean
from virttest.utils_net import IPv6Manager
from virttest.utils_net import block_specific_ip_by_time
from virttest.utils_net import check_listening_port_remote_by_service
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_config
from virttest import libvirt_version

MIGRATE_RET = False


def destroy_active_pool_on_remote(params):
    """
    This is to destroy active pool with same target path as pool_target
    on remote host
    :param params: a dict for parameters
    :return True if successful, otherwise False
    """
    ret = True

    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("migrate_dest_user", "root")
    remote_pwd = params.get("migrate_dest_pwd")

    virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                   'remote_pwd': remote_pwd, 'unprivileged_user': None,
                   'ssh_remote_auth': True}

    try:
        remote_session = virsh.VirshPersistent(**virsh_dargs)
        active_pools = remote_session.pool_list(option="--name")

        for pool in active_pools.stdout.strip().split("\n"):
            if pool is not '':
                pool_dumpxml = pool_xml.PoolXML.new_from_dumpxml(pool, remote_session)
                if(pool_dumpxml.target_path == params.get("pool_target")):
                    ret = remote_session.pool_destroy(pool)
    except Exception as e:
        logging.error("Exception when destroy active pool on target: %s", str(e))
        raise e
    finally:
        if remote_session:
            remote_session.close_session()

    return ret


def create_destroy_pool_on_remote(test, action, params):
    """
    This is to create or destroy a specified pool on remote host.
    :param action: "create" or "destory"
    :param params: a dict for parameters

    :return: True if successful, otherwise False
    :raise: TestFail: raised if command fails
    """
    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("migrate_dest_user", "root")
    remote_pwd = params.get("migrate_dest_pwd")

    virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                   'remote_pwd': remote_pwd, 'unprivileged_user': None,
                   'ssh_remote_auth': True}

    new_session = virsh.VirshPersistent(**virsh_dargs)

    pool_name = params.get("target_pool_name", "tmp_pool_1")
    timeout = params.get("timeout", 60)
    prompt = params.get("prompt", r"[\#\$]\s*$")

    if action == 'create':
        # Firstly check if the pool already exists
        all_pools = new_session.pool_list(option="--all")
        logging.debug("Pools on remote host:\n%s" % all_pools)
        if all_pools.stdout.find(pool_name) >= 0:
            logging.debug("The pool %s already exists and skip to create it."
                          % pool_name)
            new_session.close_session()
            return True

        pool_type = params.get("target_pool_type", "dir")
        pool_target = params.get("pool_target")
        cmd = "mkdir -p %s" % pool_target
        session = remote.wait_for_login("ssh", remote_ip, 22,
                                        remote_user, remote_pwd, prompt)
        status, output = session.cmd_status_output(cmd, timeout)
        session.close()
        if status:
            new_session.close_session()
            test.fail("Run command '%s' on remote host '%s'"
                      "failed: %s." % (cmd, remote_ip, output))
        ret = new_session.pool_create_as(pool_name, pool_type, pool_target)
    else:  # suppose it is to destroy
        ret = new_session.pool_destroy(pool_name)

    new_session.close_session()
    return ret


def check_output(test, output_msg, params):
    """
    Check if known messages exist in the given output messages.

    :param test: the test object
    :param output_msg: the given output messages
    :param params: the dictionary including necessary parameters

    :raise TestSkipError: raised if the known error is found together
                          with some conditions satisfied
    """
    err_msg = params.get("err_msg", None)
    status_error = "yes" == params.get("status_error", "no")
    if status_error and err_msg:
        if re.search(err_msg, output_msg):
            logging.debug("Expected error '%s' was found", err_msg)
            return
        else:
            test.fail("The expected error '%s' was not found in output '%s'" % (err_msg, output_msg))

    ERR_MSGDICT = {"Bug 1249587": "error: Operation not supported: " +
                   "pre-creation of storage targets for incremental " +
                   "storage migration is not supported",
                   "ERROR 1": "error: internal error: unable to " +
                   "execute QEMU command 'migrate': " +
                   "this feature or command is not currently supported",
                   "ERROR 2": "error: Cannot access storage file",
                   "ERROR 3": "Unable to read TLS confirmation: " +
                   "Input/output error",
                   "ERROR 4": "error: Unsafe migration: Migration " +
                   "without shared storage is unsafe"}

    # Check for special case firstly
    migrate_disks = "yes" == params.get("migrate_disks")
    if migrate_disks and status_error:
        logging.debug("To check for migrate-disks...")
        disk = params.get("attach_A_disk_source")
        last_msg = "(as uid:107, gid:107): No such file or directory"
        if not libvirt_version.version_compare(4, 5, 0):
            expect_msg = "%s '%s' %s" % (ERR_MSGDICT["ERROR 2"],
                                         disk,
                                         last_msg)
        else:
            expect_msg = ERR_MSGDICT["ERROR 4"]
        if output_msg.find(expect_msg) >= 0:
            logging.debug("The expected error '%s' was found", expect_msg)
            return
        else:
            test.fail("The actual output:\n%s\n"
                      "The expected error '%s' was not found"
                      % (output_msg, expect_msg))

    if params.get("target_vm_name"):
        if output_msg.find(ERR_MSGDICT['ERROR 3']) >= 0:
            logging.debug("The expected error is found: %s", ERR_MSGDICT['ERROR 3'])
            return
        else:
            test.fail("The actual output:\n%s\n"
                      "The expected error '%s' was not found"
                      % (output_msg, ERR_MSGDICT['ERROR 3']))

    for (key, value) in ERR_MSGDICT.items():
        if output_msg.find(value) >= 0:
            if key == "ERROR 1" and params.get("support_precreation") is True:
                logging.debug("The error is not expected: '%s'.", value)
            elif key == "ERROR 2":
                break
            else:
                logging.debug("The known error was found: %s --- %s",
                              key, value)
                test.cancel("Known error: %s --- %s in %s"
                            % (key, value, output_msg))


def migrate_vm(test, params):
    """
    Connect libvirt daemon

    :param test: the test object
    :param params: parameters used
    :raise: test.fail if migration does not get expected result
    """
    vm_name = params.get("vm_name_to_migrate")
    if vm_name is None:
        vm_name = params.get("main_vm", "")
    uri = params.get("desuri")
    options = params.get("virsh_options", "--live --verbose")
    extra = params.get("extra_args", "")
    su_user = params.get("su_user", "")
    auth_user = params.get("server_user")
    auth_pwd = params.get("server_pwd")
    virsh_patterns = params.get("patterns_virsh_cmd", r".*100\s%.*")
    status_error = params.get("status_error", "no")
    timeout = int(params.get("migration_timeout", 30))
    extra_opt = params.get("extra_opt", "")

    for option in options.split():
        if option.startswith("--"):
            check_virsh_command_and_option(test, "migrate", option)

    logging.info("Prepare migrate %s", vm_name)
    global MIGRATE_RET
    MIGRATE_RET, mig_output = libvirt.do_migration(vm_name, uri, extra,
                                                   auth_pwd, auth_user,
                                                   options,
                                                   virsh_patterns,
                                                   su_user, timeout,
                                                   extra_opt)

    if status_error == "no":
        if MIGRATE_RET:
            logging.info("Get an expected migration result:\n%s" % mig_output)
        else:
            check_output(test, mig_output, params)
            test.fail("Can't get an expected migration result:\n%s"
                      % mig_output)
    else:
        if not MIGRATE_RET:
            check_output(test, mig_output, params)
            logging.info("It's an expected error:\n%s" % mig_output)
        else:
            test.fail("Unexpected return result:\n%s" % mig_output)


def check_parameters(test, params):
    """
    Make sure all of parameters are assigned a valid value

    :param test: the test object
    :param params: parameters used
    :raise: test.cancel if not enough parameters are specified
    """
    client_ip = params.get("client_ip")
    server_ip = params.get("server_ip")
    ipv6_addr_src = params.get("ipv6_addr_src")
    ipv6_addr_des = params.get("ipv6_addr_des")
    client_cn = params.get("client_cn")
    server_cn = params.get("server_cn")
    client_ifname = params.get("client_ifname")
    server_ifname = params.get("server_ifname")

    args_list = [client_ip, server_ip, ipv6_addr_src,
                 ipv6_addr_des, client_cn, server_cn,
                 client_ifname, server_ifname]

    for arg in args_list:
        if arg and arg.count("ENTER.YOUR."):
            test.cancel("Please assign a value for %s!" % arg)


def config_libvirt(params):
    """
    Configure /etc/libvirt/libvirtd.conf
    """
    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd_conf = utils_config.LibvirtdConfig()

    for k, v in params.items():
        libvirtd_conf[k] = v

    logging.debug("the libvirtd config file content is:\n %s" % libvirtd_conf)
    libvirtd.restart()

    return libvirtd_conf


def add_disk_xml(test, device_type, source_file,
                 image_size, policy,
                 disk_type="file"):
    """
    Create a disk xml file for attaching to a guest.

    :param test: the test object
    :param device_type: CD-ROM or floppy
    :param source_file: disk's source file
    :param image_size: the size of disk
    :param policy: the policy for attaching disk
    :prams disk_type: disk type for attaching
    :raise: test.cancel if device type is not supported
    """
    if device_type != 'cdrom' and device_type != 'floppy':
        test.cancel("Only support 'cdrom' and 'floppy'"
                    " device type: %s" % device_type)

    dev_dict = {'cdrom': {'bus': 'scsi', 'dev': 'sdb'},
                'floppy': {'bus': 'fdc', 'dev': 'fda'}}
    if image_size:
        cmd = "qemu-img create %s %s" % (source_file, image_size)
        logging.info("Prepare to run %s", cmd)
        process.run(cmd, shell=True)
    disk_class = vm_xml.VMXML.get_device_class('disk')
    disk = disk_class(type_name=disk_type)
    disk.device = device_type
    if device_type == 'cdrom':
        disk.driver = dict(name='qemu')
    else:
        disk.driver = dict(name='qemu', cache='none')

    disk_attrs_dict = {}
    if disk_type == "file":
        disk_attrs_dict['file'] = source_file

    if disk_type == "block":
        disk_attrs_dict['dev'] = source_file

    if policy:
        disk_attrs_dict['startupPolicy'] = policy

    logging.debug("The disk attributes dictionary: %s", disk_attrs_dict)
    disk.source = disk.new_disk_source(attrs=disk_attrs_dict)
    disk.target = dev_dict.get(device_type)
    disk.xmltreefile.write()
    logging.debug("The disk XML: %s", disk.xmltreefile)

    return disk.xml


def prepare_gluster_disk(params):
    """
    Setup glusterfs and prepare disk image.
    """
    gluster_disk = "yes" == params.get("gluster_disk")
    disk_format = params.get("disk_format", "qcow2")
    vol_name = params.get("vol_name")
    disk_img = params.get("disk_img")
    default_pool = params.get("default_pool", "")
    pool_name = params.get("pool_name")
    data_path = data_dir.get_data_dir()
    brick_path = params.get("brick_path")
    # Get the image path and name from parameters
    image_name = params.get("image_name")
    image_format = params.get("image_format")
    image_source = os.path.join(data_path,
                                image_name + '.' + image_format)

    # Setup gluster.
    host_ip = gluster.setup_or_cleanup_gluster(True, **params)
    logging.debug("host ip: %s ", host_ip)
    image_info = utils_misc.get_image_info(image_source)
    if image_info["format"] == disk_format:
        disk_cmd = ("cp -f %s /mnt/%s" % (image_source, disk_img))
    else:
        # Convert the disk format
        disk_cmd = ("qemu-img convert -f %s -O %s %s /mnt/%s" %
                    (image_info["format"], disk_format, image_source, disk_img))

    # Mount the gluster disk and create the image.
    process.run("mount -t glusterfs %s:%s /mnt; %s; umount /mnt"
                % (host_ip, vol_name, disk_cmd), shell=True)

    return host_ip


def build_disk_xml(vm_name, disk_format, host_ip, disk_src_protocol,
                   vol_name_or_iscsi_target, disk_img=None, transport=None):
    """
    Try to rebuild disk xml
    """
    # Delete existed disks first.
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disks_dev = vmxml.get_devices(device_type="disk")
    for disk in disks_dev:
        vmxml.del_device(disk)

    disk_xml = Disk(type_name="network")
    driver_dict = {"name": "qemu",
                   "type": disk_format,
                   "cache": "none"}
    disk_xml.driver = driver_dict
    disk_xml.target = {"dev": "vda", "bus": "virtio"}
    if disk_src_protocol == "gluster":
        disk_xml.device = "disk"
        vol_name = vol_name_or_iscsi_target
        source_dict = {"protocol": disk_src_protocol,
                       "name": "%s/%s" % (vol_name, disk_img)}
        host_dict = {"name": host_ip, "port": "24007"}
        if transport:
            host_dict.update({"transport": transport})
        disk_xml.source = disk_xml.new_disk_source(
            **{"attrs": source_dict, "hosts": [host_dict]})
    if disk_src_protocol == "iscsi":
        iscsi_target = vol_name_or_iscsi_target[0]
        lun_num = vol_name_or_iscsi_target[1]
        source_dict = {'protocol': disk_src_protocol,
                       'name': iscsi_target + "/" + str(lun_num)}
        host_dict = {"name": host_ip, "port": "3260"}
        if transport:
            host_dict.update({"transport": transport})
        disk_xml.source = disk_xml.new_disk_source(
            **{"attrs": source_dict, "hosts": [host_dict]})

    # Add the new disk xml.
    vmxml.add_device(disk_xml)
    vmxml.sync()


def get_cpu_xml_from_virsh_caps(test, runner=None):
    """
    Get CPU XML from virsh capabilities output

    :param test: test object
    :param runner: the runner object to execute commands
    :raise: test.fail if test fails
    """
    cmd = "virsh capabilities | awk '/<cpu>/,/<\\/cpu>/'"
    out = ""
    if not runner:
        out = process.run(cmd, shell=True).stdout_text
    else:
        out = runner(cmd)

    if not re.search('cpu', out):
        test.fail("Failed to get cpu XML: %s" % out)

    return out


def compute_cpu_baseline(test, cpu_xml, status_error="no"):
    """
    Compute CPU baseline

    :param test: test object
    :param cpu_xml: the cpu xml used for computing
    :param status_error: yes to not raise an exception on failure
    :raise: test.fail if test fails
    """
    result = virsh.cpu_baseline(cpu_xml, ignore_status=True, debug=True)
    status = result.exit_status
    output = result.stdout.strip()
    err = result.stderr.strip()
    if status_error == "no":
        if status:
            test.fail("Failed to compute baseline CPU: %s" % err)
        else:
            logging.info("Succeed to compute baseline CPU: %s", output)
    else:
        if status:
            logging.info("It's an expected %s", err)
        else:
            test.fail("Unexpected return result: %s" % output)

    return output


def get_same_processor(test, server_ip, server_user, server_pwd, verbose):
    """
    Return the same processor between local and remote host.
    Otherwise, raise a TestSkipError.

    :param test: test object
    :param server_ip: the address of remote host
    :param server_user: the user id to log on remote host
    :param server_pwd: the password for server_user
    :param verbose: the flag to control whether or not to log messages
    :raise: test.fail if test fails
    """
    local_processors = list(map(str, cpuutil.cpu_online_list()))
    cmd = "grep processor /proc/cpuinfo"
    status, output = run_remote_cmd(cmd, server_ip, server_user, server_pwd)
    if status:
        test.fail("Failed to run '%s' on the remote: %s" % (cmd, output))
    remote_processors = re.findall(r'processor\s+: (\d+)', output)
    if verbose:
        logging.debug("Local processors: %s", local_processors)
        logging.debug("Remote processors: %s", remote_processors)
    if '0' in local_processors and '0' in remote_processors:
        logging.info("The matched processor is '0'")
        return '0'
    matched = False
    local_processor = ''
    for local_processor in local_processors:
        for remote_processor in remote_processors:
            if local_processor == remote_processor:
                logging.info("The matched processor is %s", remote_processor)
                matched = True
                break
        else:
            continue
        break
    if not matched:
        test.cancel("There is no same processor "
                    "between local and remote host.")
    return local_processor


def custom_cpu(vm_name, cpu_model, cpu_vendor, cpu_model_fallback="allow",
               cpu_feature_dict={}, cpu_mode="custom", cpu_match="exact"):
    """
    Custom guest cpu match/model/features, etc .
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    cpu_xml = vm_xml.VMCPUXML()
    cpu_xml.mode = cpu_mode
    cpu_xml.match = cpu_match
    cpu_xml.model = cpu_model
    cpu_xml.vendor = cpu_vendor
    cpu_xml.fallback = cpu_model_fallback
    if cpu_feature_dict:
        for k, v in cpu_feature_dict.items():
            cpu_xml.add_feature(k, v)
    vmxml['cpu'] = cpu_xml
    vmxml.sync()


def delete_video_device(vm_name):
    """
    Remove video device
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug("The old VM XML:\n%s" % vmxml.xmltreefile)
    videos = vmxml.get_devices(device_type="video")
    for video in videos:
        vmxml.del_device(video)
    graphics = vmxml.get_devices(device_type="graphics")
    for graphic in graphics:
        vmxml.del_device(graphic)
    vmxml.sync()
    vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
    logging.debug("The VM XML after deleting video device: \n%s", vm_xml_cxt)


def update_sound_device(vm_name, sound_model):
    """
    Update sound device model
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug("The old VM XML:\n%s" % vmxml.xmltreefile)
    sounds = vmxml.get_devices(device_type="sound")
    for sound in sounds:
        vmxml.del_device(sound)
    new_sound = Sound()
    new_sound.model_type = sound_model
    vmxml.add_device(new_sound)
    logging.debug("The VM XML with new sound model:\n%s" % vmxml.xmltreefile)
    vmxml.sync()


def add_watchdog_device(vm_name, watchdog_model, watchdog_action="none"):
    """
    Update sound device model
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug("The old VM XML:\n%s" % vmxml.xmltreefile)
    watchdogs = vmxml.get_devices(device_type="watchdog")
    for watchdog in watchdogs:
        vmxml.del_device(watchdog)
    new_watchdog = Watchdog()
    new_watchdog.model_type = watchdog_model
    new_watchdog.action = watchdog_action
    vmxml.add_device(new_watchdog)
    logging.debug("The VM XML with new watchdog model:\n%s" % vmxml.xmltreefile)
    vmxml.sync()


def prepare_guest_watchdog(vm_name, vm, watchdog_model, watchdog_action="none",
                           mod_args="", watchdog_on=1, prepare_xml=True):
    """
    Prepare qemu guest agent on the VM.

    :param prepare_xml: Whether change VM's XML
    :param channel: Whether add agent channel in VM. Only valid if
                    prepare_xml is True
    :param start: Whether install and start the qemu-ga service
    """
    if prepare_xml:
        add_watchdog_device(vm_name, watchdog_model, watchdog_action)

    if not vm.is_alive():
        vm.start()

    session = vm.wait_for_login()

    def _has_watchdog_driver(watchdog_model):
        cmd = "lsmod | grep %s" % watchdog_model
        logging.debug("Run '%s' in VM", cmd)
        return session.cmd_status(cmd)

    def _load_watchdog_driver(watchdog_model, mod_args):
        if watchdog_model == "ib700":
            watchdog_model += "wdt"
        cmd = "modprobe %s %s" % (watchdog_model, mod_args)
        logging.debug("Run '%s' in VM", cmd)
        return session.cmd_status(cmd)

    def _remove_watchdog_driver(watchdog_model):
        if watchdog_model == "ib700":
            watchdog_model += "wdt"
        cmd = "modprobe -r %s" % watchdog_model
        logging.debug("Run '%s' in VM", cmd)
        return session.cmd_status(cmd)

    def _config_watchdog(default):
        cmd = "echo %s > /dev/watchdog" % default
        logging.debug("Run '%s' in VM", cmd)
        return session.cmd_status(cmd)

    try:
        if _has_watchdog_driver(watchdog_model):
            logging.info("Loading watchdog driver")
            _load_watchdog_driver(watchdog_model, mod_args)
            if _has_watchdog_driver(watchdog_model):
                raise virt_vm.VMError("Can't load watchdog driver in VM!")

        if mod_args != "":
            logging.info("Reconfigure %s with %s parameters", watchdog_model,
                         mod_args)
            _remove_watchdog_driver(watchdog_model)
            _load_watchdog_driver(watchdog_model, mod_args)
            if _has_watchdog_driver(watchdog_model):
                raise virt_vm.VMError("Can't load watchdog driver in VM!")

        logging.info("Turn watchdog on.")
        _config_watchdog(watchdog_on)
    finally:
        session.close()


def add_smartcard_device(vm_name, smartcard_type, smartcard_mode):
    """
    Update sound device model
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    logging.debug("The old VM XML:\n%s" % vmxml.xmltreefile)
    smartcards = vmxml.get_devices(device_type="smartcard")
    for smartcard in smartcards:
        vmxml.del_device(smartcard)
    new_smartcard = Smartcard()
    new_smartcard.smartcard_type = smartcard_type
    new_smartcard.smartcard_mode = smartcard_mode
    vmxml.add_device(new_smartcard)
    logging.debug("The VM XML with new sound model:\n%s" % vmxml.xmltreefile)
    vmxml.sync()


def update_disk_driver(vm_name, disk_name, disk_type, disk_cache, disk_shareable):
    """
    Update disk driver
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    devices = vmxml.devices
    disk_index = devices.index(devices.by_device_tag('disk')[0])
    disks = devices[disk_index]
    disk_driver = disks.get_driver()
    if disk_name:
        disk_driver["name"] = disk_name
    if disk_type:
        disk_driver["type"] = disk_type
    if disk_cache:
        disk_driver["cache"] = disk_cache
    if disk_shareable:
        disks.share = disk_shareable

    disks.set_driver(disk_driver)
    # SYNC VM XML change
    vmxml.devices = devices
    logging.debug("The VM XML with disk driver change:\n%s", vmxml.xmltreefile)
    vmxml.sync()


def update_interface_xml(vm_name, iface_address, iface_model=None,
                         iface_type=None):
    """
    Modify interface xml options
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    xml_devices = vmxml.devices
    iface_index = xml_devices.index(
        xml_devices.by_device_tag("interface")[0])
    iface = xml_devices[iface_index]

    if iface_model:
        iface.model = iface_model

    if iface_type:
        iface.type_name = iface_type

    if iface_address:
        addr_dict = {}
        if iface_address:
            for addr_option in iface_address.split(','):
                if addr_option != "":
                    d = addr_option.split('=')
                    addr_dict.update({d[0].strip(): d[1].strip()})
        if addr_dict:
            iface.address = iface.new_iface_address(
                **{"attrs": addr_dict})

    vmxml.devices = xml_devices
    vmxml.xmltreefile.write()
    vmxml.sync()


def check_virsh_command_and_option(test, command, option=None):
    """
    Check if virsh command exists

    :param test: test object
    :param command: the command to validate
    :param option: the option for the command
    :raise: test.cancel if commmand is not supported
    """
    msg = "This version of libvirt does not support "
    if not virsh.has_help_command(command):
        test.cancel(msg + "virsh command '%s'" % command)

    if option and not virsh.has_command_help_match(command, option):
        test.cancel(msg + "virsh command '%s' with option '%s'"
                    % (command, option))


def run_remote_cmd(command, server_ip, server_user, server_pwd,
                   ret_status_output=True, ret_session_status_output=False,
                   timeout=60, client="ssh", port="22", prompt=r"[\#\$]\s*$"):
    """
    Run command on remote host
    """
    logging.info("Execute '%s' on %s", command, server_ip)
    session = remote.wait_for_login(client, server_ip, port,
                                    server_user, server_pwd,
                                    prompt)
    status, output = session.cmd_status_output(command, timeout)

    if ret_status_output:
        session.close()
        return (status, output)

    if ret_session_status_output:
        return (session, status, output)


def setup_netsever_and_launch_netperf(test, params):
    """
    Setup netserver and run netperf client

    :param test: test object
    :param params: parameters used
    :raise: test.error if automake installation fails
    """
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    client_ip = params.get("client_ip")
    client_user = params.get("client_user")
    client_pwd = params.get("client_pwd")
    netperf_source = params.get("netperf_source")
    netperf_source = os.path.join(data_dir.get_root_dir(), netperf_source)
    client_md5sum = params.get("client_md5sum")
    client_path = params.get("client_path", "/var/tmp")
    server_md5sum = params.get("server_md5sum")
    server_path = params.get("server_path", "/var/tmp")
    compile_option_client = params.get("compile_option_client", "")
    compile_option_server = params.get("compile_option_server", "")
    # Run netperf with message size defined in range.
    netperf_test_duration = int(params.get("netperf_test_duration", 60))
    netperf_para_sess = params.get("netperf_para_sessions", "1")
    test_protocol = params.get("test_protocols", "TCP_STREAM")
    netperf_cmd_prefix = params.get("netperf_cmd_prefix", "")
    netperf_output_unit = params.get("netperf_output_unit", " ")
    netperf_package_sizes = params.get("netperf_package_sizes")
    test_option = params.get("test_option", "")
    direction = params.get("direction", "remote")
    remote_session = remote.remote_login("ssh", server_ip, "22", server_user,
                                         server_pwd, r'[$#%]')
    for loc in ['source', 'target']:
        session = None
        if loc == 'target':
            session = remote_session
        if not utils_package.package_install("automake", session):
            test.error("Failed to install automake on %s host." % loc)
    remote_session.close()
    n_client = utils_netperf.NetperfClient(client_ip,
                                           client_path,
                                           client_md5sum,
                                           netperf_source,
                                           client="ssh",
                                           port="22",
                                           username=client_user,
                                           password=client_pwd,
                                           compile_option=compile_option_client)

    logging.info("Start netserver on %s", server_ip)
    n_server = utils_netperf.NetperfServer(server_ip,
                                           server_path,
                                           server_md5sum,
                                           netperf_source,
                                           client="ssh",
                                           port="22",
                                           username=server_user,
                                           password=server_pwd,
                                           compile_option=compile_option_server)

    n_server.start()

    test_option += " -l %s" % netperf_test_duration
    start_time = time.time()
    stop_time = start_time + netperf_test_duration
    t_option = "%s -t %s" % (test_option, test_protocol)
    logging.info("Start netperf on %s", client_ip)
    n_client.bg_start(server_ip, t_option,
                      netperf_para_sess, netperf_cmd_prefix,
                      package_sizes=netperf_package_sizes)
    if utils_misc.wait_for(n_client.is_netperf_running, 10, 0, 1,
                           "Wait netperf test start"):
        logging.info("Start netperf on %s successfully.", client_ip)
        return (True, n_client, n_server)
    else:
        return (False, n_client, n_server)


def cleanup(objs_list):
    """
    Clean up test environment
    """
    # recovery test environment
    for obj in objs_list:
        obj.auto_recover = True
        obj.__del__()


def check_vm_disk_after_migration(test, vm, params):
    """
    Check the disk work well after migration on target host

    :param test: test object
    :param vm: the guest object to migrate
    :param params: parameters used
    :raise: test.fail if command execution fails
    """
    cmd = "fdisk -l|grep '^Disk /dev'|cut -d: -f1|cut -d' ' -f2"
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                           server_user, server_pwd,
                                           r"[\#\$]\s*$")
    vm_ip = vm.get_address(session=remote_session, timeout=480)
    remote_session.close()
    tmp_file = "/tmp/fdisk_test_file"
    mnt_dir = "/tmp/fdisk_test_dir"
    dd_cmd = "dd if=/dev/zero"
    dd_cmd = "%s of=%s/test_file bs=1024 count=512 && sync" % (dd_cmd, mnt_dir)
    params.update({'vm_ip': vm_ip, 'vm_pwd': params.get("password")})
    remote_vm_obj = remote.VMManager(params)
    remote_vm_obj.check_network()
    remote_vm_obj.setup_ssh_auth()
    cmdres = remote_vm_obj.run_command(cmd, ignore_status=True)
    if cmdres.exit_status:
        test.fail("Command '%s' result: %s\n" % (cmd, cmdres))
    disks = cmdres.stdout.strip().split("\n")
    logging.debug("Get disks in remote VM: %s", disks)
    for disk in disks:
        if disk == '' or disk == '/dev/vda' or disk.count('mapper') > 0:
            logging.debug("No need to check the disk '%s'", disk)
        else:
            cmd = "echo -e 'n\np\n\n\n\nw\n' > %s && " % tmp_file
            cmd = "%s fdisk %s < %s && mkfs.ext3 %s1 && " % (cmd, disk,
                                                             tmp_file, disk)
            cmd = "%s mkdir -p %s && mount %s1 %s && %s" % (cmd, mnt_dir,
                                                            disk, mnt_dir,
                                                            dd_cmd)
            # create partition and file system
            # mount disk and write file in it
            logging.debug("Execute command on remote VM: %s", cmd)
            cmdres = remote_vm_obj.run_command(cmd, ignore_status=True)
            if cmdres.exit_status:
                test.fail("Command '%s' result: %s\n" % (cmd, cmdres))


def check_migration_disk_port(params):
    """
    Handle the option '--disks-port'.
    As the migration thread begins to execute, this function is executed
    at same time almostly. It will wait for several seconds to make sure
    the storage migration start actually. Then it checks the port on remote
    in use is same as that specified by '--disks-port'.
    """
    disk_port = params.get("disk_port")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    client_ip = params.get("client_ip")
    test = params.get("test_object")
    # Here need to wait for several seconds before checking the port on
    # remote host because the storage migration needs some time (about 5s)
    # to start working actually. The whole period for the storage migration
    # maybe last several minutes (more than 3m). So '30' seconds can be a
    # choice to wait.
    time.sleep(30)
    cmd = "netstat -tunap|grep %s" % disk_port
    status, output = run_remote_cmd(cmd, server_ip, server_user, server_pwd)
    if status:
        test.fail("Failed to run '%s' on the remote: %s" % (cmd, output))
    pattern1 = r".*:::%s.*LISTEN.*qemu-kvm.*" % disk_port
    pattern2 = r".*%s:%s.*%s.*ESTABLISHED.*qemu-kvm.*" % (server_ip,
                                                          disk_port,
                                                          client_ip)
    logging.debug("Check the disk port specified is in use")
    if not re.search(pattern1, output) or not re.search(pattern2, output):
        test.fail("Can not find the expected patterns"
                  " '%s, %s' in output '%s'" % (pattern1, pattern2, output))


def update_disk_driver_with_iothread(vm_name, iothread):
    """ Update disk driver with iothread."""
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    # Delete cputune/iothreadids section, it may have conflicts
    # with domain iothreads.
    del vmxml.cputune
    del vmxml.iothreadids
    devices = vmxml.devices
    disk_index = devices.index(devices.by_device_tag('disk')[0])
    disks = devices[disk_index]
    disk_driver = disks.get_driver()
    disk_driver["iothread"] = iothread
    disks.set_driver(disk_driver)
    devices[disk_index] = disks
    vmxml.devices = devices
    vmxml.iothreads = int(iothread)
    # SYNC VM XML change.
    vmxml.sync()


def check_iothread_after_migration(test, vm_name, params, iothread):
    """
    Check iothread by qemu-monitor-command on remote host.

    :param test: test object
    :param vm_name: the guest name for migration
    :param params: parameters used
    :param iothread: the iothread value to check
    :raise: test.fail if checking fails
    """
    remote_virsh = virsh.VirshPersistent(**params)
    try:
        ret = remote_virsh.qemu_monitor_command(vm_name,
                                                '{"execute": "query-iothreads"}',
                                                "--pretty")
        libvirt.check_exit_status(ret)
        if ret.stdout.strip().count("thread-id") != int(iothread):
            test.fail("Failed to check domain iothreads")
    finally:
        remote_virsh.close_session()


def check_domjobinfo_on_complete(test, source_jobinfo, target_jobinfo):
    """
    Compare the domjobinfo outputs on source and target hosts.
    The domjobinfo currently includes below items:
     Job type
     Operation
     Time elapsed
     Time elapsed w/o network
     Data processed
     Data remaining
     Data total
     Memory processed
     Memory remaining
     Memory total
     Memory bandwidth
     Dirty rate
     Iteration
     Constant pages
     Normal pages
     Normal data
     Total downtime
     Downtime w/o network
     Setup time

    Most fields are required to be same between source and target hosts,
    except below:
        Operation
        Time elapsed
        Time elapsed w/o network

    :param test: avocado.core.test.Test object
    :param local_jobinfo: The domjobinfo output on source host
    :param remote_jobinfo: The domjobinfo output on target host
    :raise: test.fail if checking fails
    """
    source_info = read_domjobinfo(test, source_jobinfo)
    target_info = read_domjobinfo(test, target_jobinfo)

    for key, value in source_info.items():
        if key in ["Expected downtime"]:
            continue
        if key not in target_info:
            test.fail("The domjobinfo on target host "
                      "does not has the field: '%s'" % key)

        target_value = target_info[key]
        if key in ["Time elapsed",
                   "Time elapsed w/o network",
                   "Operation"]:
            continue
        else:
            if value != target_value:
                test.fail("The value '%s' for '%s' on source "
                          "host should be equal to the value "
                          "'%s' on target host"
                          % (value, key, target_value))


def read_domjobinfo(test, domjobinfo):
    """
    Read the domjobinfo into a dict

    :param test: avocado.core.test.Test object
    :param domjobinfo: The domjobinfo command output
    :raise: test.fail if checking fails
    :return: A dict contains the domjobinfo
    """
    jobinfo_dict = {}
    domjobinfo_list = domjobinfo.splitlines()
    for item in domjobinfo_list:
        item = item.strip()
        if not item or item.count("Job type:"):
            continue
        elif item.count("Time elapsed:"):
            time_elapse = re.findall(r'[0-9]+', item)
            if len(time_elapse) != 1:
                test.fail("Invalid item "
                          "for domjobinfo:%s" % item)
            jobinfo_dict["Time elapsed"] = time_elapse[0]
        elif item.count("Time elapsed w/o network:"):
            time_elapse_wo_net = re.findall(r'[0-9]+', item)
            if len(time_elapse_wo_net) != 1:
                test.fail("Invalid item "
                          "for domjobinfo:%s" % item)
            jobinfo_dict["Time elapsed w/o network"] = time_elapse_wo_net[0]
        else:
            pair = item.split(":")
            if len(pair) != 2:
                test.fail("Invalid item "
                          "for domjobinfo:%s" % item)
            jobinfo_dict[pair[0]] = pair[1]
    return jobinfo_dict


def get_virtual_size(test, disk_source):
    """
    Get the virtual size of image.

    :param disk_source: path to the image
    :return: virtual size, e.g. 10G
    :raise: test.error if checking fails
    """
    result = process.run("qemu-img info %s" % disk_source, ignore_status=False)
    size_pattern = r"virtual size: (\d+)\s?(.*) \("
    match = re.findall(size_pattern, result.stdout_text)
    if match and match[0]:
        size = match[0][0]
        unit = match[0][1][0]
        return size + unit
    else:
        test.error("Can not find valid virtual size "
                   "in %s" % result.stdout_text)


def redefine_vm_with_iscsi_target(host_ip, disk_format,
                                  iscsi_transport, target, vm_name):
    """
    Redefine vm to use iscsi target for vm image
    :param host_ip: IP of the iscsi host
    :param disk_format: image format, e.g. raw
    :param iscsi_transport: transport protocol for host
    :param target: iscsi target
    :param vm_name: vm name
    """
    build_disk_xml(vm_name, disk_format, host_ip, "iscsi",
                   target, transport=iscsi_transport)
    vmxml_iscsi = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    curr_vm_xml = process.run('cat %s' % vmxml_iscsi.xml, shell=True).stdout_text
    logging.debug("The current VM XML contents: \n%s", curr_vm_xml)


def update_host_ip(client_ip, ipv6_addr_src):
    """
    Use ipv6_addr_src as host_ip if given
    :param client_ip: Migration client ip (the host initiating the migration)
    :param ipv6_addr_src: The vm source source host ipv6 ip
    :return: selected host ip
    """
    return ipv6_addr_src if ipv6_addr_src else client_ip


def create_image_on_iscsi(test, vm, disk_source, disk_format, emulated_image):
    """
    Make vm image available to the iscsi target backstore
    :param test: test instance for reporting
    :param vm: vm for migration
    :param disk_source: vm image source path
    :param disk_format: image target format
    :param emulated_image: fileio backstore file target
    """
    tmpdir = data_dir.get_data_dir()
    emulated_path = os.path.join(tmpdir, emulated_image)
    if vm.is_alive():
        vm.destroy()
    cmd = "qemu-img convert %s -O %s %s" % (disk_source, disk_format, emulated_path)

    logging.debug("Running %s", cmd)
    result = process.run(cmd, shell=True, ignore_status=True)
    output = result.stdout_text
    logging.debug("Result: %s", output)
    if result.exit_status:
        test.error("Couldn't prepare vm image on iscsi, %s" % output)
    logging.debug("Stopped VM and make it available on iscsi block device %s", emulated_path)


def run(test, params, env):
    """
    Test remote access with TCP, TLS connection
    """

    def get_target_hugepage_num(params):
        """
        Get the number of hugepage on target host

        :param params: The parameters used
        :return: the number of hugepage to be allocated on target host
        """
        hugepage_file = params.get("kernel_hp_file", "/proc/sys/vm/nr_hugepages")
        with open(hugepage_file, 'r') as fp:
            hugepage_num = int(fp.readline().strip())
        more_less_hp = int(params.get("remote_target_hugepages", "0"))
        logging.debug("Number of huge pages on target host to be allocated:%d",
                      hugepage_num + more_less_hp)
        return (hugepage_num + more_less_hp)

    test_dict = dict(params)
    vm_name = test_dict.get("main_vm")
    vm = env.get_vm(vm_name)
    start_vm = test_dict.get("start_vm", "no")
    transport = test_dict.get("transport")
    plus = test_dict.get("conn_plus", "+")
    config_ipv6 = test_dict.get("config_ipv6", "no")
    listen_addr = test_dict.get("listen_addr", "0.0.0.0")
    uri_port = test_dict.get("uri_port", ":22")
    server_ip = test_dict.get("server_ip")
    server_user = test_dict.get("server_user")
    server_pwd = test_dict.get("server_pwd")
    client_ip = test_dict.get("client_ip")
    client_user = test_dict.get("client_user")
    client_pwd = test_dict.get("client_pwd")
    server_cn = test_dict.get("server_cn")
    client_cn = test_dict.get("client_cn")
    ipv6_addr_des = test_dict.get("ipv6_addr_des")
    portal_ip = test_dict.get("portal_ip", "127.0.0.1")
    restart_libvirtd = test_dict.get("restart_src_libvirtd", "no")
    driver = test_dict.get("test_driver", "qemu")
    uri_path = test_dict.get("uri_path", "/system")
    nfs_mount_dir = test_dict.get("nfs_mount_dir", "/var/lib/libvirt/images")
    nfs_mount_src = test_dict.get("nfs_mount_src", "/usr/share/avocado/data/avocado-vt/images")
    setup_ssh = test_dict.get("setup_ssh", "yes")
    setup_tcp = test_dict.get("setup_tcp", "yes")
    setup_tls = test_dict.get("setup_tls", "yes")
    ssh_recovery = test_dict.get("ssh_auto_recovery", "yes")
    tcp_recovery = test_dict.get("tcp_auto_recovery", "yes")
    tls_recovery = test_dict.get("tls_auto_recovery", "yes")
    source_type = test_dict.get("vm_disk_source_type", "file")
    target_vm_name = test_dict.get("target_vm_name")
    target_ip = test_dict.get("target_ip", "")
    adduser_cmd = test_dict.get("adduser_cmd")
    deluser_cmd = test_dict.get("deluser_cmd")
    host_uuid = test_dict.get("host_uuid")
    pause_vm = "yes" == test_dict.get("pause_vm", "no")
    reboot_vm = "yes" == test_dict.get("reboot_vm", "no")
    abort_job = "yes" == test_dict.get("abort_job", "no")
    ctrl_c = "yes" == test_dict.get("ctrl_c", "no")
    virsh_options = test_dict.get("virsh_options", "--verbose --live")
    remote_path = test_dict.get("remote_libvirtd_conf",
                                "/etc/libvirt/libvirtd.conf")
    log_file = test_dict.get("libvirt_log", "/var/log/libvirt/libvirtd.log")
    run_migr_back = "yes" == test_dict.get(
        "run_migrate_cmd_in_back", "no")
    run_migr_front = "yes" == test_dict.get(
        "run_migrate_cmd_in_front", "yes")
    stop_libvirtd_remotely = "yes" == test_dict.get(
        "stop_libvirtd_remotely", "no")
    restart_libvirtd_remotely = "yes" == test_dict.get(
        "restart_libvirtd_remotely", "no")
    cdrom_image_size = test_dict.get("cdrom_image_size")
    cdrom_device_type = test_dict.get("cdrom_device_type")
    floppy_image_size = test_dict.get("floppy_image_size")
    floppy_device_type = test_dict.get("floppy_device_type")
    policy = test_dict.get("startup_policy", "")
    local_image = test_dict.get("local_disk_image")
    target_disk_image = test_dict.get("target_disk_image")
    target_dev = test_dict.get("target_dev", "")
    update_disk_source = "yes" == test_dict.get("update_disk_source", "no")

    mb_enable = "yes" == test_dict.get("mb_enable", "no")
    config_remote_hugepages = "yes" == test_dict.get("config_remote_hugepages",
                                                     "no")
    enable_kvm_hugepages = "yes" == test_dict.get("enable_kvm_hugepages", "no")
    enable_remote_kvm_hugepages = "yes" == test_dict.get("enable_remote_kvm_hugepages", "no")
    remote_tgt_hugepages = get_target_hugepage_num(test_dict)
    remote_hugetlbfs_path = test_dict.get("remote_hugetlbfs_path")
    delay = int(params.get("delay_time", 10))

    stop_remote_guest = "yes" == test_dict.get("stop_remote_guest", "yes")
    memtune_options = test_dict.get("memtune_options")
    setup_nfs = "yes" == test_dict.get("setup_nfs", "yes")
    enable_virt_use_nfs = "yes" == test_dict.get("enable_virt_use_nfs", "yes")

    check_domain_state = "yes" == test_dict.get("check_domain_state", "no")
    expected_domain_state = test_dict.get("expected_domain_state")

    check_job_info = "yes" == test_dict.get("check_job_info", "yes")
    check_complete_job = test_dict.get("check_complete_job", "no")
    block_ip_addr = test_dict.get("block_ip_addr")
    block_time = test_dict.get("block_time")
    restart_vm = "yes" == test_dict.get("restart_vm", "no")
    diff_cpu_vendor = "yes" == test_dict.get("diff_cpu_vendor", "no")

    # Get iothread parameters.
    driver_iothread = test_dict.get("driver_iothread")

    nbd_port = test_dict.get("nbd_port")
    target_image_size = test_dict.get("target_image_size")
    target_image_format = test_dict.get("target_image_format")
    create_target_image = "yes" == test_dict.get("create_target_image", "no")
    create_disk_src_backing_file = test_dict.get(
        "create_local_disk_backfile_cmd")
    create_disk_tgt_backing_file = test_dict.get(
        "create_remote_disk_backfile_cmd")

    log_level = test_dict.get("log_level", "1")
    log_filters = test_dict.get("log_filters",
                                '"1:json 1:libvirt 1:qemu 1:monitor 3:remote 4:event"')

    libvirtd_conf_dict = {"log_level": log_level,
                          "log_filters": log_filters,
                          "log_outputs": '"%s:file:%s"' % (log_level, log_file)}
    remote_dargs = {'server_ip': server_ip, 'server_user': server_user,
                    'server_pwd': server_pwd,
                    'file_path': "/etc/libvirt/libvirt.conf"}

    remote_port = test_dict.get("open_remote_listening_port")

    vol_name = test_dict.get("vol_name")
    brick_path = test_dict.get("brick_path")
    disk_src_protocol = params.get("disk_source_protocol")
    gluster_transport = test_dict.get("gluster_transport")
    iscsi_transport = test_dict.get("iscsi_transport")
    config_libvirtd = test_dict.get("config_libvirtd", "no")

    cpu_set = "yes" == test_dict.get("cpu_set", "no")
    vcpu_num = test_dict.get("vcpu_num", "1")

    enable_stress_test = "yes" == test_dict.get("enable_stress_test", "no")
    stress_type = test_dict.get("stress_type")
    stress_args = test_dict.get("stress_args")

    no_swap = "yes" == test_dict.get("no_swap", "no")

    get_migr_cache = "yes" == test_dict.get("get_migrate_compcache", "no")
    set_migr_cache_size = test_dict.get("set_migrate_compcache_size")

    sound_model = test_dict.get("sound_model")
    source_file = test_dict.get("disk_source_file")

    # Process blkdeviotune parameters
    total_bytes_sec = test_dict.get("blkdevio_total_bytes_sec")
    read_bytes_sec = test_dict.get("blkdevio_read_bytes_sec")
    write_bytes_sec = test_dict.get("blkdevio_write_bytes_sec")
    total_iops_sec = test_dict.get("blkdevio_total_iops_sec")
    read_iops_sec = test_dict.get("blkdevio_read_iops_sec")
    write_iops_sec = test_dict.get("blkdevio_write_iops_sec")
    blkdevio_dev = test_dict.get("blkdevio_device")
    blkdevio_options = test_dict.get("blkdevio_options")

    # For --postcopy enable
    postcopy_options = test_dict.get("postcopy_options")
    if postcopy_options and not virsh_options.count(postcopy_options):
        virsh_options = "%s %s" % (virsh_options, postcopy_options)
        test_dict['virsh_options'] = virsh_options

    # For --migrate-disks test
    migrate_disks = "yes" == test_dict.get("migrate_disks", "no")

    # For bi-directional and tls reverse test
    src_uri = test_dict.get("migration_source_uri", "qemu:///system")

    # Pre-creation image parameters
    target_pool_name = test_dict.get("target_pool_name")
    target_pool_type = test_dict.get("target_pool_type", "dir")

    # disk_ports for storage migration used by nbd
    disk_port = test_dict.get("disk_port")

    tc_cmd = test_dict.get("tc_cmd")
    xml_path = test_dict.get("target_xml_path",
                             "/tmp/avocado_vt_remote_vm1.xml")

    # It's used to clean up SSH, TLS and TCP objs later
    objs_list = []

    # Default don't attach disk/cdrom to the guest.
    attach_disk = False

    # Make sure all of parameters are assigned a valid value
    check_parameters(test, test_dict)

    # Check for some skip situations
    os_ver_from = test_dict.get("os_ver_from")
    os_ver_to = test_dict.get("os_ver_to")
    os_ver_cmd = "cat /etc/redhat-release"

    if os_ver_from:
        curr_os_ver = process.run(os_ver_cmd, shell=True).stdout_text
        if os_ver_from not in curr_os_ver:
            test.cancel("The current OS is %s" % curr_os_ver)

    if os_ver_to:
        status, curr_os_ver = run_remote_cmd(os_ver_cmd, server_ip,
                                             server_user, server_pwd)
        if os_ver_to not in curr_os_ver:
            test.cancel("The current OS is %s" % curr_os_ver)

    speed = test_dict.get("set_migration_speed")
    if speed:
        cmd = "migrate-setspeed"
        if not virsh.has_help_command(cmd):
            test.cancel("This version of libvirt "
                        "does not support virsh "
                        "command %s" % cmd)

    # Set up SSH key
    ssh_key.setup_ssh_key(server_ip, server_user, server_pwd, 22)

    # Set up remote ssh key and remote /etc/hosts file for
    # bi-direction migration
    migrate_vm_back = "yes" == test_dict.get("migrate_vm_back", "no")
    remote_known_hosts_obj = None
    if migrate_vm_back:
        ssh_key.setup_remote_ssh_key(server_ip, server_user, server_pwd)
        remote_known_hosts_obj = ssh_key.setup_remote_known_hosts_file(client_ip,
                                                                       server_ip,
                                                                       server_user,
                                                                       server_pwd)

    if vm.is_alive() and start_vm == "no":
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Enable hugepages if necessary.
    if enable_kvm_hugepages:
        cmds = ["modprobe -r kvm",
                "modprobe kvm hpage=1"]
        for cmd in cmds:
            process.run(cmd)

    # Get current VM's memory
    current_mem = vmxml_backup.current_mem
    logging.debug("Current VM memory: %s", current_mem)

    # Disk XML file
    disk_xml = None

    # Add device type into a list
    dev_type_list = []

    if cdrom_device_type:
        dev_type_list.append(cdrom_device_type)

    if floppy_device_type:
        dev_type_list.append(floppy_device_type)

    # Add created image file into a list
    local_image_list = []
    remote_image_list = []

    # Defaut don't add new iptables rules
    add_iptables_rules = False

    # Converting time to second
    power = {'hours': 60 * 60, "minutes": 60, "seconds": 1}

    # Mounted hugepage filesystem
    HUGETLBFS_MOUNT = False

    # Get the first disk source path
    first_disk = vm.get_first_disk_devices()
    disk_source = first_disk['source']
    logging.debug("disk source: %s", disk_source)
    curr_vm_xml = process.run('cat %s' % vmxml_backup.xml, shell=True).stdout_text
    logging.debug("The current VM XML contents: \n%s", curr_vm_xml)
    orig_image_name = os.path.basename(disk_source)

    # define gluster_disk early in case needed in finally:
    gluster_disk = "yes" == test_dict.get("gluster_disk")

    # Set the pool target using the path of first disk
    test_dict["pool_target"] = os.path.dirname(disk_source)

    iscsi_setup = "yes" == test_dict.get("iscsi_setup", "no")
    disk_format = test_dict.get("disk_format", "qcow2")
    status_error = test_dict.get("status_error", "no")
    nfs_serv = None
    nfs_cli = None
    se_obj = None
    libvirtd_conf = None
    n_server_c = None
    n_client_c = None
    n_server_s = None
    n_client_s = None
    need_mkswap = False
    LOCAL_SELINUX_ENFORCING = True
    REMOTE_SELINUX_ENFORCING = True
    create_target_pool = False
    support_precreation = False
    pool_created = False
    remote_virsh_session = None
    remove_dict = {}
    remote_libvirt_file = None
    src_libvirt_file = None
    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': None,
                          'ssh_remote_auth': True}
    migrate_setup = migration.MigrationTest()
    dest_uri = libvirt_vm.complete_uri(server_ip)
    migrate_setup.cleanup_dest_vm(vm, vm.connect_uri, dest_uri)
    try:
        # Cold plug two pcie-root-port controllers for q35 vm in case
        # device hotplugging is needed during test
        machine_type = params.get("machine_type")
        if machine_type == 'q35':
            contr_dict = {
                    'controller_type': 'pci',
                    'controller_model': 'pcie-root-port'
                    }
            for i in range(0, 2):
                contr_xml = libvirt.create_controller_xml(contr_dict)
                libvirt.add_controller(vm.name, contr_xml)

        if iscsi_setup:
            fileio_name = "emulated-iscsi"
            img_vsize = get_virtual_size(test, disk_source)

            target = libvirt.setup_or_cleanup_iscsi(is_setup=True, is_login=False,
                                                    emulated_image=fileio_name,
                                                    portal_ip=portal_ip,
                                                    image_size=img_vsize)
            logging.debug("Created iscsi target: %s", target)
            host_ip = update_host_ip(client_ip, params.get("ipv6_addr_src"))
            redefine_vm_with_iscsi_target(host_ip, disk_format,
                                          iscsi_transport, target, vm_name)
            create_image_on_iscsi(test, vm, disk_source, disk_format, fileio_name)

        del_vm_video_dev = "yes" == test_dict.get("del_vm_video_dev", "no")
        if del_vm_video_dev:
            delete_video_device(vm_name)

        iface_address = test_dict.get("iface_address")
        if iface_address:
            update_interface_xml(vm_name, iface_address)

        if sound_model:
            logging.info("Prepare to update VM's sound XML")
            update_sound_device(vm_name, sound_model)

        watchdog_model = test_dict.get("watchdog_model")
        watchdog_action = test_dict.get("watchdog_action", "none")
        watchdog_module_args = test_dict.get("watchdog_module_args", "")
        if watchdog_model:
            prepare_guest_watchdog(vm_name, vm, watchdog_model, watchdog_action,
                                   watchdog_module_args)
            curr_vm_xml = process.run('cat %s' % vmxml_backup.xml, shell=True).stdout_text
            logging.debug("The current VM XML contents: \n%s", curr_vm_xml)

        smartcard_mode = test_dict.get("smartcard_mode")
        smartcard_type = test_dict.get("smartcard_type")
        if smartcard_mode and smartcard_type:
            add_smartcard_device(vm_name, smartcard_type, smartcard_mode)
            curr_vm_xml = process.run('cat %s' % vmxml_backup.xml, shell=True).stdout_text
            logging.debug("The current VM XML contents: \n%s", curr_vm_xml)

        pm_mem_enabled = test_dict.get("pm_mem_enabled", "no")
        pm_disk_enabled = test_dict.get("pm_disk_enabled", "no")
        suspend_target = test_dict.get("pm_suspend_target")
        if suspend_target:
            logging.info("Prepare to add VM's agent XML")
            vmxml_backup.set_pm_suspend(vm_name, pm_mem_enabled, pm_disk_enabled)

        config_vm_agent = "yes" == test_dict.get("config_vm_agent", "no")
        if config_vm_agent:
            vm.prepare_guest_agent()
            vm.setenforce(0)

        if nfs_mount_dir:
            cmd = "mkdir -p %s" % nfs_mount_dir
            logging.debug("Make sure %s exists both local and remote", nfs_mount_dir)
            output = process.run(cmd, shell=True).stdout_text
            if output:
                test.fail("Failed to run '%s' on the local : %s"
                          % (cmd, output))

            status, output = run_remote_cmd(cmd, server_ip, server_user, server_pwd)
            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))
            cmd = "mount | grep -E '.*%s.*%s.*'" % (client_ip + ':' + nfs_mount_src,
                                                    nfs_mount_dir)
            status, out = run_remote_cmd(cmd, server_ip, server_user, server_pwd)
            if not status:
                logging.warning("The '%s' is mounted unexpectedly. Umount it now."
                                % nfs_mount_dir)
                cmd = "umount -l %s" % nfs_mount_dir
                status, output = run_remote_cmd(cmd, server_ip, server_user, server_pwd)
                logging.debug("status:%d, output:%s", status, output)

        cpu_model = test_dict.get("cpu_model_name")
        cpu_vendor = test_dict.get("cpu_vendor")
        cpu_feature_dict = eval(test_dict.get("cpu_feature_dict", "{}"))
        cpu_mode = test_dict.get("cpu_mode", "custom")
        cpu_match = test_dict.get("cpu_match", "exact")
        cpu_model_fallback = test_dict.get("cpu_model_fallback", "allow")
        if cpu_model and cpu_vendor:
            custom_cpu(vm_name, cpu_model, cpu_vendor, cpu_model_fallback,
                       cpu_feature_dict, cpu_mode, cpu_match)

        # Update VM disk source to NFS sharing directory
        logging.debug("Migration mounting point: %s", nfs_mount_dir)
        new_disk_source = test_dict.get("new_disk_source")
        if (nfs_mount_dir and not migrate_disks and
                nfs_mount_dir != os.path.dirname(disk_source)):
            libvirt.update_vm_disk_source(vm_name, nfs_mount_dir, "", source_type)

        target_image_path = test_dict.get("target_image_path")
        target_image_name = test_dict.get("target_image_name", "")
        if new_disk_source and target_image_path:
            libvirt.update_vm_disk_source(vm_name, target_image_path,
                                          target_image_name, source_type)

        if update_disk_source and new_disk_source:
            image_info_dict = utils_misc.get_image_info(disk_source)
            if image_info_dict["format"] != disk_format:
                cmd = ("qemu-img convert -f %s -O %s %s %s"
                       % (image_info_dict["format"], disk_format, disk_source,
                          new_disk_source))
                process.run(cmd, ignore_status=False)
            libvirt.update_vm_disk_source(
               vm_name, os.path.dirname(new_disk_source),
               os.path.basename(new_disk_source), source_type)

        vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
        logging.debug("The VM XML with new disk source: \n%s", vm_xml_cxt)

        # Prepare to update VM first disk driver cache
        disk_name = test_dict.get("disk_driver_name")
        disk_type = test_dict.get("disk_driver_type")
        disk_cache = test_dict.get("disk_driver_cache", "none")
        disk_shareable = "yes" == test_dict.get("disk_shareable")
        if disk_name or disk_type or disk_cache or disk_shareable:
            update_disk_driver(vm_name, disk_name, disk_type, disk_cache,
                               disk_shareable)

        image_info_dict = utils_misc.get_image_info(disk_source)
        logging.debug("disk image info: %s", image_info_dict)
        target_image_source = test_dict.get("target_image_source", disk_source)
        cmd = test_dict.get("create_another_target_image_cmd")
        if cmd:
            status, output = run_remote_cmd(cmd, server_ip, server_user, server_pwd)
            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))

            remote_image_list.append(target_disk_image)

        # Process domain disk device parameters
        vol_name = test_dict.get("vol_name")
        default_pool = test_dict.get("default_pool", "")

        pool_name = test_dict.get("pool_name")
        if pool_name:
            test_dict['brick_path'] = os.path.join(test.virtdir, pool_name)

        if gluster_disk:
            # Setup glusterfs and disk xml.
            disk_img = "gluster.%s" % disk_format
            test_dict['disk_img'] = disk_img
            host_ip = prepare_gluster_disk(test_dict)
            build_disk_xml(vm_name, disk_format, host_ip, disk_src_protocol,
                           vol_name, disk_img, gluster_transport)

            vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
            logging.debug("The VM XML with gluster disk source: \n%s", vm_xml_cxt)

            # Check if gluster server is deployed locally
            if host_ip == client_ip:
                logging.debug("Enable port 24007 and 49152:49216")
                migrate_setup.migrate_pre_setup(src_uri, params, ports="24007")
                migrate_setup.migrate_pre_setup(src_uri, params)

        # generate remote IP
        if target_ip == "":
            if config_ipv6 == "yes" and ipv6_addr_des and not server_cn:
                target_ip = "[%s]" % ipv6_addr_des
            elif server_cn:
                target_ip = server_cn
            elif config_ipv6 != "yes" and ipv6_addr_des:
                target_ip = "[%s]" % ipv6_addr_des
            elif server_ip:
                target_ip = server_ip
            else:
                target_ip = target_ip

        # generate URI
        uri = "%s%s%s://%s%s%s" % (driver, plus, transport,
                                   target_ip, uri_port, uri_path)
        test_dict["desuri"] = uri

        logging.debug("The final test dict:\n<%s>", test_dict)

        if diff_cpu_vendor:
            local_vendor = cpu.get_cpu_vendor()
            logging.info("Local CPU vendor: %s", local_vendor)
            local_cpu_xml = get_cpu_xml_from_virsh_caps(test)
            logging.debug("Local CPU XML: \n%s", local_cpu_xml)

            cmd = "grep %s /proc/cpuinfo" % local_vendor
            session, status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                     server_pwd, ret_status_output=False,
                                                     ret_session_status_output=True)
            try:
                if not status:
                    test.cancel("The CPU model is the same between local "
                                "and remote host %s:%s"
                                % (local_vendor, output))
                if not session:
                    test.fail("The session is dead")

                runner = session.cmd_output
                remote_cpu_xml = get_cpu_xml_from_virsh_caps(test, runner)
                session.close()
                logging.debug("Remote CPU XML: \n%s", remote_cpu_xml)
                cpu_xml = os.path.join(data_dir.get_tmp_dir(), 'cpu.xml')
                fp = open(cpu_xml, "w+")
                fp.write(local_cpu_xml)
                fp.write("\n")
                fp.write(remote_cpu_xml)
                fp.close()
                cpu_xml_cxt = process.run("cat %s" % cpu_xml, shell=True).stdout_text
                logging.debug("The CPU XML contents: \n%s", cpu_xml_cxt)
                cmd = "sed -i '/<vendor>.*<\\/vendor>/d' %s" % cpu_xml
                process.system(cmd, shell=True)
                cpu_xml_cxt = process.run("cat %s" % cpu_xml, shell=True).stdout_text
                logging.debug("The current CPU XML contents: \n%s", cpu_xml_cxt)
                output = compute_cpu_baseline(test, cpu_xml, status_error)
                logging.debug("The baseline CPU XML: \n%s", output)
                output = output.replace("\n", "")
                vm_new_xml = os.path.join(data_dir.get_tmp_dir(), 'vm_new.xml')
                fp = open(vm_new_xml, "w+")
                fp.write(str(vmxml_backup))
                fp.close()
                vm_new_xml_cxt = process.run("cat %s" % vm_new_xml, shell=True).stdout_text
                logging.debug("The current VM XML contents: \n%s", vm_new_xml_cxt)
                cpuxml = output
                cmd = 'sed -i "/<\\/features>/ a\\%s" %s' % (cpuxml, vm_new_xml)
                logging.debug("The command: %s", cmd)
                process.system(cmd, shell=True)
                vm_new_xml_cxt = process.run("cat %s" % vm_new_xml, shell=True).stdout_text
                logging.debug("The new VM XML contents: \n%s", vm_new_xml_cxt)
                virsh.define(vm_new_xml)
                vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
                logging.debug("The current VM XML contents: \n%s", vm_xml_cxt)
            finally:
                logging.info("Recovery VM XML configration")
                vmxml_backup.sync()
                logging.debug("The current VM XML:\n%s", vmxml_backup.xmltreefile)

        if cpu_set:
            vcpu_cpuset = get_same_processor(test, server_ip, server_user, server_pwd,
                                             verbose=True)
            vcpu_args = ""
            if vcpu_cpuset:
                vcpu_args += "cpuset='%s'" % vcpu_cpuset
            edit_cmd = []
            update_cmd = r":%s/<vcpu.*>[0-9]*<\/vcpu>/<vcpu "
            update_cmd += vcpu_args + ">" + vcpu_num + r"<\/vcpu>"
            edit_cmd.append(update_cmd)
            libvirt.exec_virsh_edit(vm_name, edit_cmd)
            vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
            logging.debug("The current VM XML contents: \n%s", vm_xml_cxt)

        # setup IPv6
        if config_ipv6 == "yes":
            ipv6_obj = IPv6Manager(test_dict)
            objs_list.append(ipv6_obj)
            ipv6_obj.setup()

        # setup SSH
        if transport == "ssh" and setup_ssh == "yes":
            ssh_obj = SSHConnection(test_dict)
            if ssh_recovery == "yes":
                objs_list.append(ssh_obj)
            # setup test environment
            ssh_obj.conn_setup(timeout=60)

        # setup TLS
        if transport == "tls" and setup_tls == "yes":
            if target_vm_name is not None:
                # setup CA, server, client and server on local
                test_dict['server_setup_local'] = True
            tls_obj = TLSConnection(test_dict)
            if tls_recovery == "yes":
                objs_list.append(tls_obj)
            tls_obj.conn_setup()

        # setup TCP
        if transport == "tcp" and setup_tcp == "yes":
            tcp_obj = TCPConnection(test_dict)
            if tcp_recovery == "yes":
                objs_list.append(tcp_obj)
            # setup test environment
            tcp_obj.conn_setup()

        # check TCP/IP listening by service
        if restart_libvirtd != "no":
            service = 'libvirtd'
            if transport == "ssh":
                service = 'ssh'

            check_listening_port_remote_by_service(server_ip, server_user,
                                                   server_pwd, service,
                                                   '22', listen_addr)

        # add a user
        if adduser_cmd:
            process.system(adduser_cmd, ignore_status=True, shell=True)

        # update libvirtd config with new host_uuid
        if config_libvirtd == "yes":
            if host_uuid:
                libvirtd_conf_dict["host_uuid"] = host_uuid
            # Remove the old log_file if any both on local and remote host
            if os.path.exists(log_file):
                logging.debug("To delete local log file '%s'", log_file)
                os.remove(log_file)
            cmd = "rm -f %s" % log_file
            logging.debug("To delete remote log file '%s'", log_file)
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))

            server_params = {'server_ip': server_ip,
                             'server_user': server_user,
                             'server_pwd': server_pwd}
            libvirtd_conf = libvirt.customize_libvirt_config(libvirtd_conf_dict,
                                                             remote_host=True,
                                                             extra_params=server_params)

        # need to remotely stop libvirt service for negative testing
        if stop_libvirtd_remotely:
            libvirt.remotely_control_libvirtd(server_ip, server_user,
                                              server_pwd, "stop", status_error)

        if setup_nfs:
            logging.info("Setup NFS test environment...")
            nfs_serv = nfs.Nfs(test_dict)
            nfs_serv.setup()
            nfs_cli = nfs.NFSClient(test_dict)
            nfs_cli.setup()

        if enable_virt_use_nfs:
            logging.info("Enable virt NFS SELinux boolean")
            se_obj = SELinuxBoolean(test_dict)
            se_obj.setup()

        if mb_enable:
            logging.info("Add memoryBacking into VM XML")
            vm_xml.VMXML.set_memoryBacking_tag(vm_name)
            vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
            logging.debug("The current VM XML: \n%s", vm_xml_cxt)

        if config_remote_hugepages:
            cmds = ["mkdir -p %s" % remote_hugetlbfs_path,
                    "mount -t hugetlbfs none %s" % remote_hugetlbfs_path,
                    "sysctl vm.nr_hugepages=%s" % remote_tgt_hugepages]
            if enable_remote_kvm_hugepages:
                cmds.append("modprobe -r kvm")
                cmds.append("modprobe kvm hpage=1")
            for cmd in cmds:
                status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                server_pwd)
                if status:
                    test.fail("Failed to run '%s' on the remote: %s"
                              % (cmd, output))
            HUGETLBFS_MOUNT = True

        if create_disk_src_backing_file:
            cmd = create_disk_src_backing_file + orig_image_name
            out = process.run(cmd, ignore_status=True, shell=True).stdout_text
            if not out:
                test.fail("Failed to create backing file: %s" % cmd)
            logging.info(out)
            local_image_list.append(new_disk_source)

        if restart_libvirtd_remotely:
            cmd = "service libvirtd restart"
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))

        if remote_hugetlbfs_path:
            cmd = "ls %s" % remote_hugetlbfs_path
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))

        if memtune_options:
            virsh.memtune_set(vm_name, memtune_options)
            vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
            logging.debug("The VM XML with memory tune: \n%s", vm_xml_cxt)

        # Update disk driver with iothread attribute.
        if driver_iothread:
            update_disk_driver_with_iothread(vm_name, driver_iothread)

        check_image_size = "yes" == test_dict.get("check_image_size", "no")
        local_image_source = test_dict.get("local_image_source")
        tgt_size = 0
        if local_image_source and check_image_size:
            image_info_dict = utils_misc.get_image_info(local_image_source)
            logging.debug("Local disk image info: %s", image_info_dict)

            dsize = image_info_dict["dsize"]
            dd_image_count = int(test_dict.get("dd_image_count"))
            dd_image_bs = int(test_dict.get("dd_image_bs"))
            dd_image_size = dd_image_count * dd_image_bs
            tgt_size = dsize + dd_image_size
            logging.info("Expected disk image size: %s", tgt_size)

        if dev_type_list:
            for dev_type in dev_type_list:
                image_size = ""
                if not source_file:
                    source_file = "%s/virt_%s.img" % (nfs_mount_dir, dev_type)
                logging.debug("Disk source: %s", source_file)
                if cdrom_image_size and dev_type == 'cdrom':
                    image_size = cdrom_image_size
                if floppy_image_size and dev_type == 'floppy':
                    image_size = floppy_image_size
                if image_size:
                    local_image_list.append(source_file)
                    disk_xml = add_disk_xml(test, dev_type, source_file,
                                            image_size, policy)
                else:
                    cdrom_disk_type = test_dict.get("cdrom_disk_type")
                    disk_xml = add_disk_xml(test, dev_type, source_file,
                                            image_size, policy,
                                            cdrom_disk_type)

                logging.debug("Disk XML: %s", disk_xml)
                if disk_xml and os.path.isfile(disk_xml):
                    virsh_dargs = {'debug': True, 'ignore_status': True}
                    virsh.attach_device(domainarg=vm_name, filearg=disk_xml,
                                        flagstr="--config", **virsh_dargs)
                    process.run("rm -f %s" % disk_xml, ignore_status=True, shell=True)

                vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
                logging.debug("The VM XML with attached disk: \n%s", vm_xml_cxt)
                source_file = None

        if local_image and not os.path.exists(local_image):
            image_fmt = test_dict.get("local_image_format", "raw")
            disk_size = test_dict.get("local_disk_size", "10M")
            attach_args = test_dict.get("attach_disk_args")
            image_cmd = "qemu-img create -f %s %s %s" % (image_fmt,
                                                         local_image,
                                                         disk_size)
            logging.info("Create image for disk: %s", image_cmd)
            process.run(image_cmd, shell=True)
            local_image_list.append(local_image)

            setup_loop_cmd = test_dict.get("setup_loop_dev_cmd")
            mk_loop_fmt = test_dict.get("mk_loop_dev_format_cmd")
            if setup_loop_cmd and mk_loop_fmt:
                process.system_output(setup_loop_cmd, ignore_status=False, shell=True)
                process.system_output(mk_loop_fmt, ignore_status=False, shell=True)

                status, output = run_remote_cmd(setup_loop_cmd, server_ip,
                                                server_user, server_pwd)
                if status:
                    test.fail("Failed to run '%s' on remote: %s"
                              % (setup_loop_cmd, output))

            if attach_args:
                logging.info("Prepare to attach disk to guest")
                c_attach = virsh.attach_disk(vm_name, local_image, target_dev,
                                             attach_args, debug=True)
                if c_attach.exit_status != 0:
                    logging.error("Attach disk failed before test.")

                vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
                logging.debug("The VM XML with attached disk: \n%s", vm_xml_cxt)

                attach_disk = True

        start_filter_string = test_dict.get("start_filter_string")
        start_local_vm = True
        if target_vm_name is not None:
            start_local_vm = False
        # start local vm and prepare to migrate
        vm_session = None
        if start_local_vm is True and (not vm.is_alive() or vm.is_dead()):
            result = None
            try:
                vm.start()
            except virt_vm.VMStartError as e:
                logging.info("Recovery VM XML configration")
                vmxml_backup.sync()
                logging.debug("The current VM XML:\n%s", vmxml_backup.xmltreefile)
                if start_filter_string:
                    if re.search(start_filter_string, str(e)):
                        test.cancel("Failed to start VM: %s" % e)
                    else:
                        test.fail("Failed to start VM: %s" % e)
                else:
                    test.fail("Failed to start VM: %s" % e)

            if disk_src_protocol != "iscsi":
                vm_session = vm.wait_for_login()

        guest_cmd = test_dict.get("guest_cmd")
        if guest_cmd and vm_session:
            status, output = vm_session.cmd_status_output(guest_cmd)
            logging.debug("To run '%s' in VM: status=<%s>, output=<%s>",
                          guest_cmd, status, output)
            if status:
                test.fail("Failed to run '%s' : %s"
                          % (guest_cmd, output))
            logging.info(output)

        target_image_source = test_dict.get("target_image_source", disk_source)
        # Do not create target image when qemu supports drive-mirror
        # and nbd-server, but need create a specific pool.
        no_create_pool = test_dict.get("no_create_pool", "no")
        try:
            if ((utils_misc.is_qemu_capability_supported("drive-mirror") or
                 libvirt_version.version_compare(5, 3, 0)) and
                    utils_misc.is_qemu_capability_supported("nbd-server")):
                support_precreation = True
        except exceptions.TestError as e:
            logging.debug(e)

        test_dict["support_precreation"] = support_precreation
        if create_target_image:
            if support_precreation and no_create_pool == "no":
                create_target_pool = True
        if target_pool_name and create_target_pool:
            create_target_image = False
            pool_created = create_destroy_pool_on_remote(test, "create", test_dict)
            if not pool_created:
                test.error("Create pool on remote host '%s' "
                           "failed." % server_ip)
            remote_image_list.append(target_image_source)
        elif target_pool_name and not create_target_pool:
            pool_destroyed = destroy_active_pool_on_remote(test_dict)
            if not pool_destroyed:
                test.error("Destroy pool on remote host '%s' failed"
                           % server_ip)

        if create_target_image:
            if not target_image_size and image_info_dict:
                target_image_size = image_info_dict.get('vsize')
            if not target_image_format and image_info_dict:
                target_image_format = image_info_dict.get('format')
            if target_image_size and target_image_format:
                # Make sure the target image path exists
                cmd = "mkdir -p %s " % os.path.dirname(target_image_source)
                cmd += "&& qemu-img create -f %s %s %s" % (target_image_format,
                                                           target_image_source,
                                                           target_image_size)
                status, output = run_remote_cmd(cmd, server_ip,
                                                server_user, server_pwd)
                if status:
                    test.fail("Failed to run '%s' on remote: %s"
                              % (cmd, output))

                remote_image_list.append(target_image_source)
        # Below cases are to test option "--migrate-disks" with mix of storage and
        # nfs setup.
        # Case 1: --copy-storage-all without --migrate-disks will copy all images to
        #         remote host.
        # Check points for qemu-kvm >=2.10.0:
        # 1. Migration fails with error "Block node is read-only"
        #
        # Check points for qemu-kvm <2.10.0:
        # 1. Migration operation succeeds and guest on remote is running
        # 2. All Disks in guest on remote host can be r/w
        # 3. Libvirtd.log on remote host should include "nbd-server-add" message for
        #    all disks
        #
        # Case 2: --copy-storage-all with --migrate-disks <all non-shared-images> will
        #         copy images specified in --migrate-disks to remote host.
        # Check points:
        # 1. Same with Case 1
        # 2. Same with Case 1
        # 3. Libvirtd.log on remote host should include "nbd-server-add" message for
        #    the disks specified by --migrate-disks

        if migrate_disks:
            logging.debug("To handle --migrate_disks...")
            attach_A_disk_source = test_dict.get("attach_A_disk_source")
            attach_B_disk_source = test_dict.get("attach_B_disk_source")
            attach_A_disk_target = "vdb"
            attach_B_disk_target = "vdc"
            # create local images for disks to attach
            libvirt.create_local_disk("file", path=attach_A_disk_source, size="0.1",
                                      disk_format="qcow2")
            libvirt.create_local_disk("file", path=attach_B_disk_source, size="0.1",
                                      disk_format="qcow2")
            test_dict["driver_type"] = "qcow2"
            driver_cache = test_dict.get("disk_driver_cache", "none")
            test_dict["driver_cache"] = driver_cache
            libvirt.attach_additional_device(vm.name, attach_A_disk_target,
                                             attach_A_disk_source, test_dict,
                                             config=False)
            libvirt.attach_additional_device(vm.name, attach_B_disk_target,
                                             attach_B_disk_source, test_dict,
                                             config=False)

        if create_disk_tgt_backing_file:
            if not support_precreation:
                cmd = create_disk_src_backing_file + orig_image_name
                status, output = run_remote_cmd(cmd, server_ip,
                                                server_user, server_pwd)
                if status:
                    test.fail("Failed to run '%s' on remote: %s"
                              % (cmd, output))
            remote_image_list.append(new_disk_source)
        if pause_vm:
            if not vm.pause():
                test.fail("Guest state should be paused after started "
                          "because of init guest state")
        if reboot_vm:
            vm.reboot()

        if remote_port:
            cmd = "nc -l -p %s &" % remote_port
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))

        if get_migr_cache:
            check_virsh_command_and_option(test, "migrate-compcache")
            result = virsh.migrate_compcache(vm_name)
            logging.debug(result)

        if (total_bytes_sec or read_bytes_sec or write_bytes_sec or
                total_iops_sec or read_iops_sec or write_iops_sec) and blkdevio_dev:
            blkdevio_params = {'total_iops_sec': total_iops_sec,
                               'read_bytes_sec': read_bytes_sec,
                               'write_bytes_sec': write_bytes_sec,
                               'total_iops_sec': total_iops_sec,
                               'read_iops_sec': read_iops_sec,
                               'write_iops_sec': write_iops_sec}
            result = virsh.blkdeviotune(vm_name, blkdevio_dev,
                                        options=blkdevio_options,
                                        params=blkdevio_params,
                                        debug=True)
            libvirt.check_exit_status(result)

        if no_swap and vm_session:
            cmd = "swapon -s"
            logging.info("Execute command <%s> in the VM", cmd)
            status, output = vm_session.cmd_status_output(cmd, timeout=600)
            if status:
                test.fail("Failed to run %s in VM: %s" % (cmd, output))
            logging.debug(output)

            cmd = test_dict.get("memhog_install_pkg")
            logging.info("Execute command <%s> in the VM", cmd)
            status, output = vm_session.cmd_status_output(cmd, timeout=600)
            if status:
                test.fail("Failed to run %s in VM: %s" % (cmd, output))
            logging.debug(output)

            # memory size should be less than VM's physical memory
            mem_size = current_mem - 100
            cmd = "memhog -r20 %s" % mem_size
            logging.info("Execute command <%s> in the VM", cmd)
            status, output = vm_session.cmd_status_output(cmd, timeout=600)
            if status:
                test.fail("Failed to run %s in VM: %s" % (cmd, output))
            logging.debug(output)

        run_cmd_in_vm = test_dict.get("run_cmd_in_vm")
        if run_cmd_in_vm and vm_session:
            logging.info("Execute command <%s> in the VM", run_cmd_in_vm)
            status, output = vm_session.cmd_status_output(run_cmd_in_vm)
            if status:
                test.fail("Failed to run %s in VM: %s"
                          % (run_cmd_in_vm, output))
            logging.debug(output)

        if enable_stress_test and stress_args and stress_type:
            s_list = stress_type.split("_")

            if s_list and s_list[-1] == "vms":
                if not vm_session:
                    test.fail("The VM session is inactive")
                else:
                    cmd = "yum install patch -y"
                    logging.info("Run '%s' in VM", cmd)
                    status, output = vm_session.cmd_status_output(cmd,
                                                                  timeout=600)
                    if status:
                        test.fail("Failed to run %s in VM: %s"
                                  % (cmd, output))
                    logging.debug(output)

            elif s_list and s_list[-1] == "host":
                logging.info("Run '%s %s' in %s", s_list[0],
                             stress_args, s_list[-1])
                test_dict['stress_package'] = 'stress'
                err_msg = utils_test.load_stress(stress_type,
                                                 test_dict, [vm])
                if len(err_msg):
                    test.fail("Add stress for migration failed:%s"
                              % err_msg[0])
            else:
                test.fail("The stress type looks like "
                          "'stress_in_vms, iozone_in_vms, stress_on_host'")

        if set_migr_cache_size:
            check_virsh_command_and_option(test, "migrate-compcache")
            result = virsh.migrate_compcache(vm_name, size=set_migr_cache_size)
            logging.debug(result)

        netperf_version = test_dict.get("netperf_version")
        if netperf_version:
            # Install tar on client
            # Note: tar is used to untar netperf package later.
            if not utils_package.package_install(["tar"]):
                test.error("Failed to install tar on client")

            # Install tar on server
            # Note: tar is used to untar netperf package later.
            remote_session = remote.wait_for_login('ssh', server_ip, '22', server_user,
                                                   server_pwd, r"[\#\$]\s*$")
            if not utils_package.package_install(["tar"], remote_session):
                test.error("Failed to install tar on server")

            ret, n_client_c, n_server_c = setup_netsever_and_launch_netperf(
                test, test_dict)
            if not ret:
                test.error("Can not start netperf on %s" % client_ip)

            new_args_dict = dict(test_dict)
            new_args_dict["server_ip"] = client_ip
            new_args_dict["server_user"] = client_user
            new_args_dict["server_pwd"] = client_pwd
            new_args_dict["client_ip"] = server_ip
            new_args_dict["client_user"] = server_user
            new_args_dict["client_pwd"] = server_pwd
            new_args_dict["server_md5sum"] = test_dict.get("client_md5sum")
            new_args_dict["server_path"] = test_dict.get("client_path", "/var/tmp")
            new_args_dict["compile_option_server"] = test_dict.get("compile_option_client", "")
            new_args_dict["client_md5sum"] = test_dict.get("server_md5sum")
            new_args_dict["client_path"] = test_dict.get("server_path", "/var/tmp")
            new_args_dict["compile_option_client"] = test_dict.get("compile_option_server", "")

            ret, n_client_s, n_server_s = setup_netsever_and_launch_netperf(
                test, new_args_dict)
            if not ret:
                test.error("Can not start netperf on %s" % client_ip)

        speed = test_dict.get("set_migration_speed")
        if speed:
            cmd = "migrate-setspeed"
            if not virsh.has_help_command(cmd):
                test.cancel("This version of libvirt does not support "
                            "virsh command %s" % cmd)

            logging.debug("Set migration speed to %s", speed)
            virsh.migrate_setspeed(vm_name, speed)

        iface_num = int(test_dict.get("attach_iface_times", 0))
        if iface_num > 0:
            for i in range(int(iface_num)):
                logging.info("Try to attach interface loop %s" % i)
                options = test_dict.get("attach_iface_options", "")
                ret = virsh.attach_interface(vm_name, options,
                                             ignore_status=True)
                if ret.exit_status:
                    if ret.stderr.count("No more available PCI slots"):
                        break
                    elif status_error == 'yes':
                        continue
                    else:
                        logging.error("Command output %s" %
                                      ret.stdout.strip())
                        test.fail("Failed to attach-interface")
            vm_xml_cxt = process.run("virsh dumpxml %s" % vm_name, shell=True).stdout_text
            logging.debug("The VM XML with attached interface: \n%s",
                          vm_xml_cxt)

        set_src_pm_suspend_tgt = test_dict.get("set_src_pm_suspend_target")
        set_src_pm_wakeup = "yes" == test_dict.get("set_src_pm_wakeup", "no")
        state_delay = int(test_dict.get("state_delay", 10))
        if set_src_pm_suspend_tgt:
            tgts = set_src_pm_suspend_tgt.split(",")
            for tgt in tgts:
                tgt = tgt.strip()
                if tgt == "disk" or tgt == "hybrid":
                    if vm.is_dead():
                        vm.start()
                    need_mkswap = not vm.has_swap()
                    if need_mkswap:
                        logging.debug("Creating swap partition")
                        swap_path = test_dict.get("swap_path")
                        vm.create_swap_partition(swap_path)

                result = virsh.dompmsuspend(vm_name, tgt, ignore_status=True,
                                            debug=True)
                libvirt.check_exit_status(result)
                time.sleep(state_delay)
                if (tgt == "mem" or tgt == "hybrid") and set_src_pm_wakeup:
                    result = virsh.dompmwakeup(vm_name, ignore_status=True,
                                               debug=True)
                    libvirt.check_exit_status(result)
            logging.debug("Current VM state: <%s>", vm.state())
            if vm.state() == "in shutdown":
                vm.wait_for_shutdown()
            if vm.is_dead():
                vm.start()
                vm.wait_for_login()

        remove_dict = {"do_search": '{"%s": "ssh:/"}' % dest_uri}
        src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
            remove_dict)

        if run_migr_back:
            command = "virsh migrate %s %s %s" % (vm_name, virsh_options, uri)
            logging.debug("Start migrating: %s", command)
            p = Popen(command, shell=True, universal_newlines=True, stdout=PIPE, stderr=PIPE)

            # wait for live storage migration starting
            time.sleep(delay)

            if ctrl_c:
                if p.pid:
                    logging.info("Send SIGINT signal to cancel migration.")
                    if utils_misc.safe_kill(p.pid, signal.SIGKILL):
                        logging.info("Succeed to cancel migration:"
                                     " [%s].", p.pid)
                        time.sleep(delay)
                    else:
                        test.error("Fail to cancel migration: [%s]" % p.pid)
                else:
                    p.kill()
                    test.fail("Migration process is dead")

            if check_domain_state:
                domain_state = virsh.domstate(vm_name, debug=True).stdout.strip()
                if expected_domain_state != domain_state:
                    test.fail("The domain state is not expected: %s"
                              % domain_state)

            # Give enough time for starting job
            t = 0
            jobinfo = None
            jobtype = "None"
            options = ""
            check_time = int(test_dict.get("check_job_info_time", 10))
            if check_job_info:
                while t < check_time:
                    jobinfo = virsh.domjobinfo(vm_name, debug=True,
                                               ignore_status=True).stdout
                    logging.debug("Job info: %s", jobinfo)
                    for line in jobinfo.splitlines():
                        key = line.split(':')[0]
                        if key.count("type"):
                            jobtype = line.split(':')[-1].strip()
                    if "None" == jobtype:
                        t += 1
                        time.sleep(1)
                        continue
                    else:
                        break

                if check_complete_job == "yes":
                    stdout, stderr = p.communicate()
                    logging.info("status:[%d], stdout:[%s], stderr:[%s]",
                                 p.returncode, stdout, stderr)
                    if p.returncode:
                        test.fail("Failed to run migration: {}".format(stderr))
                    opts = "--completed"
                    args = vm_name + " " + opts
                    check_virsh_command_and_option(test, "domjobinfo", opts)
                    jobinfo = virsh.domjobinfo(args, debug=True,
                                               ignore_status=True).stdout
                    default_cache = params.get("default_cache")
                    if (default_cache and
                       "Compression cache: {}".format(default_cache) not in jobinfo):
                        test.fail("Failed to find "
                                  "default compression cache %s" % default_cache)
                    cmd = "virsh domjobinfo %s %s" % (vm_name, opts)
                    logging.debug("Get remote job info")
                    status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                    server_pwd)
                    if status:
                        test.fail("Failed to run '%s' on remote: %s"
                                  % (cmd, output))
                    else:
                        check_domjobinfo_on_complete(test, jobinfo, output)

            if block_ip_addr and block_time:
                block_specific_ip_by_time(block_ip_addr, block_time)
                add_iptables_rules = True

                stdout, stderr = p.communicate()
                logging.info("stdout:<%s> , stderr:<%s>", stdout, stderr)

            if abort_job and jobtype != "None":
                job_ret = virsh.domjobabort(vm_name, debug=True)
                if job_ret.exit_status:
                    test.error("Failed to abort active domain job.")
                else:
                    stderr = p.communicate()[1]
                    logging.debug(stderr)
                    err_str = ".*error.*migration.*job: canceled by client"
                    if not re.search(err_str, stderr):
                        test.fail("Can't find error: %s." % err_str)
                    else:
                        logging.info("Find error: %s.", err_str)

            max_down_time = test_dict.get("max_down_time")
            if max_down_time:
                max_down_time = str(int(float(max_down_time) * 1000))
                result = virsh.migrate_setmaxdowntime(vm_name, max_down_time)
                if result.exit_status:
                    logging.error("Set max migration downtime failed.")
                logging.debug(result)

            sleep_time = test_dict.get("sleep_time")
            kill_cmd = test_dict.get("kill_command")
            if sleep_time:
                logging.info("Sleep %s(s)", sleep_time)
                time.sleep(int(sleep_time))

            if kill_cmd:
                logging.info("Execute %s on the host", kill_cmd)
                process.system(kill_cmd, shell=True)

            wait_for_mgr_cmpl = test_dict.get("wait_for_migration_complete", "no")
            if wait_for_mgr_cmpl == "yes":
                stdout, stderr = p.communicate()
                logging.info("stdout:<%s> , stderr:<%s>", stdout, stderr)
                if p.returncode:
                    test.fail("Can't finish VM migration: {}".format(stderr))

            if p.poll():
                try:
                    p.kill()
                except OSError:
                    pass

        if (transport in ('tcp', 'tls') and uri_port) or disk_port:
            port = disk_port if disk_port else uri_port[1:]
            migrate_setup.migrate_pre_setup("//%s/" % server_ip, test_dict,
                                            cleanup=False, ports=port)

        # Case for --disk_ports option.
        # Start the storage migration on a thread
        # The storage migration needs 3-5s to start. After that, check the port
        # on remote host during the storage migration.
        # Check results.
        #
        # Check points:
        # The port should be like below
        # # netstat -tunap|grep 56789
        #tcp6       0 0 :::56789           :::*              LISTEN      21266/qemu-kvm
        #tcp6   23168 0 10.66.4.167:56789  10.66.5.225:41334 ESTABLISHED 21266/qemu-kvm

        if disk_port:
            # Run migration command on a seperate thread
            migration_test = migration.MigrationTest()
            vms = [vm]
            func_dict = {"disk_port": disk_port, "server_ip": server_ip,
                         "server_user": server_user, "server_pwd": server_pwd,
                         "client_ip": client_ip, "test_object": test}
            migration_test.do_migration(vms, None, uri, 'orderly',
                                        virsh_options,
                                        thread_timeout=900,
                                        ignore_status=True,
                                        func=check_migration_disk_port,
                                        func_params=func_dict)
            if migration_test.RET_MIGRATION:
                remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                                       server_user, server_pwd,
                                                       r"[\#\$]\s*$")
                utils_test.check_dest_vm_network(vm, vm.get_address(session=remote_session),
                                                 server_ip, server_user,
                                                 server_pwd,
                                                 shell_prompt=r"[\#\$]\s*$")
                remote_session.close()
            else:
                check_output(test, str(migration_test.ret), test_dict)
                test.fail("The migration with disks port failed")

        # For TLS reverse migration
        # This case do following steps:
        # 1. Setup CA, TLS server on remote host, TLS client on local host and
        # TLS server on local host for reverse connection. But TLS client on
        # remote host is not setup.
        # 2. Connect to remote host using TLS and migrate guest from remote to
        # local. Due to no TLS client setup on remote host, the migration will
        # fail.
        # 3. Check the libvirtd service is running on both hosts after migration
        # 4. Check the guest on remote host is still running and that guest does
        # not exist on local host.
        # 5. Destroy remote host
        if target_vm_name:
            # The guest on remote machine should already exist.
            # So check its status before migration back.
            guest_config = None

            try:
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
                logging.debug("Check if remote guest exists")
                if remote_virsh_session.domain_exists(target_vm_name) is False:
                    test.cancel("The guest '%s' on remote '%s' should be "
                                "installed before the test."
                                % (target_vm_name, server_ip))
                # Check the prepared guest state on remote host.
                # 'shut off' is expected.
                logging.debug("Check if remote guest is in shutoff")
                if remote_virsh_session.is_alive(target_vm_name):
                    test.error("The guest '%s' on remote "
                               "'%s' should not be alive."
                               % (target_vm_name, server_ip))

                # Replace the disk of the remote guest
                logging.debug("Replace guest image with nfs image")
                image_name = os.path.basename(disk_source)

                # Dumpxml of remote guest to tempory file for updating
                # its disk image
                logging.debug("Dumpxml of remote guest")
                cmd = "virsh dumpxml %s > %s" % (target_vm_name, xml_path)
                status, output = run_remote_cmd(cmd, server_ip,
                                                server_user, server_pwd)
                logging.debug("Remote guest original xml:\n%s\n", output)
                # Update the disk image to nfs shared storage on remote guest
                logging.debug("Create a remote file")
                guest_config = remote.RemoteFile(address=server_ip,
                                                 client='scp',
                                                 username=server_user,
                                                 password=server_pwd,
                                                 port='22',
                                                 remote_path=xml_path)
                logging.debug("Modify remote guest xml's disk path")
                pattern2repl = {r"<source file=.*":
                                "<source file='%s/%s'/>"
                                % (nfs_mount_dir, image_name)}
                guest_config.sub(pattern2repl)

                logging.debug("Modify remote guest xml's machine type")
                machine_type = "pc"
                arch = platform.machine()
                if arch.count("ppc64"):
                    machine_type = "pseries"

                pattern2repl = {r".*.machine=.*":
                                "<type arch='%s' machine='%s'>hvm</type>"
                                % (arch, machine_type)}
                guest_config.sub(pattern2repl)

                # undefine remote guest
                logging.debug("Undefine remote guest")
                remote_virsh_session.undefine(target_vm_name)

                # redefine remote guest using updated XML
                logging.debug("Redefine remote guest")
                result = remote_virsh_session.define(xml_path)
                logging.debug(result.stdout.strip())

                # start remote guest
                logging.debug("Start remote guest")
                remote_virsh_session.start(target_vm_name)

                # dumpxml remote guest
                logging.debug("Remote guest XML:\n%s\n",
                              remote_virsh_session.dumpxml(target_vm_name).stdout.strip())

                # Permit iptables to permit special port to libvirt for
                # migration on local machine
                migrate_setup.migrate_pre_setup(src_uri, params, ports=uri_port[1:])

            except (process.CmdError, remote.SCPError) as e:
                logging.debug(e)
            except Exception as details:
                logging.debug(details)
            finally:
                del guest_config
                remote_virsh_session.close_session()

            uri = "%s%s%s://%s:%s%s" % (driver, plus, transport,
                                        client_cn, uri_port[1:], uri_path)
            test_dict["desuri"] = uri
            test_dict["vm_name_to_migrate"] = target_vm_name

        # There is a migration different result from libvirt 4.3.0-1 when
        # migrating without shared storage and --copy-storage-all
        # Before: migration succeeds with image preallocation on target host
        # After: migration is forbidden
        err_msg = test_dict.get('err_msg', None)
        if (err_msg and
                "Migration without shared storage is unsafe" in err_msg and
                not libvirt_version.version_compare(4, 3, 1)):
            test_dict['status_error'] = 'no'
            status_error = "no"
            test_dict['err_msg'] = None

        if run_migr_front:
            migrate_vm(test, test_dict)

        if target_vm_name:
            remote_session = None
            try:
                logging.debug("Guest '%s' should not exist locally. "
                              "Check it...",
                              target_vm_name)
                if virsh.domain_exists(target_vm_name) is True:
                    test.fail("Guest '%s' should not exist locally"
                              % target_vm_name)

                logging.debug("Guest '%s' should be running remotely. "
                              "Check it...",
                              target_vm_name)
                remote_session = virsh.VirshPersistent(**remote_virsh_dargs)
                domstate = remote_session.domstate(target_vm_name)
                logging.debug("Guest '%s' on remote host is '%s'",
                              target_vm_name, domstate.stdout.strip())
                if domstate.stdout.strip() != "running":
                    test.fail("Guest '%s' on remote host is not running."
                              % target_vm_name)

            finally:
                if remote_session:
                    remote_session.close_session()

        set_tgt_pm_suspend_tgt = test_dict.get("set_tgt_pm_suspend_target")
        set_tgt_pm_wakeup = "yes" == test_dict.get("set_tgt_pm_wakeup", "no")
        state_delay = int(test_dict.get("state_delay", 10))
        if set_tgt_pm_suspend_tgt:
            tgts = set_tgt_pm_suspend_tgt.split(",")
            for tgt in tgts:
                cmd = "virsh dompmsuspend %s --target %s" % (vm_name, tgt)
                status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                server_pwd)
                if status:
                    test.fail("Failed to run '%s' on remote: %s"
                              % (cmd, output))
                time.sleep(state_delay)
                if tgt == "mem" and set_tgt_pm_wakeup:
                    cmd = "virsh dompmwakeup %s" % vm_name
                    status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                    server_pwd)
                    if status:
                        test.fail("Failed to run '%s' on the remote: %s"
                                  % (cmd, output))

        run_cmd_in_vm = test_dict.get("run_cmd_in_vm_after_migration")
        if run_cmd_in_vm:
            remote_session = remote.wait_for_login('ssh', server_ip, '22',
                                                   server_user, server_pwd,
                                                   r"[\#\$]\s*$")
            vm_ip = vm.get_address(session=remote_session, timeout=480)
            remote_session.close()
            vm_pwd = test_dict.get("password")
            logging.debug("The VM IP: <%s> password: <%s>", vm_ip, vm_pwd)
            logging.info("Execute command <%s> in the VM after migration",
                         run_cmd_in_vm)
            test_dict.update({'vm_ip': vm_ip, 'vm_pwd': vm_pwd})
            remote_vm_obj = remote.VMManager(test_dict)
            remote_vm_obj.check_network()

        cmd = test_dict.get("check_disk_size_cmd")
        if (virsh_options.find("copy-storage-all") >= 0 and
                test_dict.get("local_image_format") == "raw"):
            # Check the image size on target host after migration
            local_disk_image = test_dict.get("local_disk_image")
            remote_image_list.append(local_disk_image)
            remote_runner = remote.RemoteRunner(host=server_ip,
                                                username=server_user,
                                                password=server_pwd)
            cmdResult = remote_runner.run(cmd, ignore_status=True)
            if cmdResult.exit_status:
                test.error("Failed to run '%s' on remote: %s"
                           % (cmd, cmdResult))
            local_disk_size = test_dict.get("local_disk_size")
            remote_disk_size = cmdResult.stdout.strip()
            if (str(float(remote_disk_size[:-1]))+remote_disk_size[-1] !=
               str(float(local_disk_size[:-1]))+local_disk_size[-1]):
                test.fail("Image location: %s \n"
                          "The image sizes are not equal.\n"
                          "Remote size is %s\n"
                          "Local size is %s"
                          % (local_disk_image,
                             remote_disk_size,
                             local_disk_size))

        if cmd and check_image_size and not support_precreation:
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))
            logging.debug("Remote disk image info: %s", output)

        cmd = test_dict.get("target_qemu_filter")
        if cmd:
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            logging.debug("The filtered result:\n%s", output)
            if status:
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))
        if restart_libvirtd == "yes":
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()

        if restart_vm:
            vm.destroy()
            vm.start()
            vm.wait_for_login()

        if pause_vm:
            cmd = "virsh domstate %s" % vm_name
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status or not re.search("paused", output):
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))

        # Check iothread after migration.
        if driver_iothread:
            check_iothread_after_migration(test, vm_name, remote_virsh_dargs, driver_iothread)

        grep_str_local = test_dict.get("grep_str_from_local_libvirt_log")
        if grep_str_local == "migrate_set_downtime":
            if not libvirt_version.version_compare(6, 5, 0):
                grep_str_local = "migrate_set_downtime.*%s" % max_down_time
            else:
                grep_str_local = "migrate-set-parameters.*downtime-limit\":%s" % max_down_time

        if config_libvirtd == "yes" and grep_str_local:
            cmd = "grep -E '%s' %s" % (grep_str_local, log_file)
            logging.debug("Execute command %s: %s", cmd, process.run(cmd, shell=True).stdout_text)

        grep_str_remote = test_dict.get("grep_str_from_remote_libvirt_log")
        if grep_str_remote:
            cmd = "grep %s %s" % (grep_str_remote, log_file)
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            logging.debug("The command result: %s", output)
            if status:
                test.fail("Failed to run '%s' on remote: %s" % (cmd, output))
        # Check points for --migrate-disk cases.
        if migrate_disks and status_error == "no":
            # Check the libvirtd.log
            grep_from_remote = ".*(nbd-server-add.*drive-virtio-disk|block-export-add).*writable.*"
            cmd = "grep -E '%s' %s" % (grep_from_remote, log_file)
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.fail("Can not find expected log '%s' on remote host '%s'"
                          % (grep_from_remote, server_ip))
            if (re.search(r".*drive-virtio-disk0.*", output) is None or
                    re.search(r".*drive-virtio-disk1.*", output) is None):
                test.fail("The actual output:\n%s\n"
                          "Can not find 'disk0' or 'disk1' "
                          "in the log on remote host '%s'"
                          % (output, server_ip))
            if re.search(r".*drive-virtio-disk2.*", output) is None:
                if virsh_options.find("--migrate-disks") >= 0:
                    # This is expected as shared image should not be
                    # copied when "--migrate-disks --copy-storage-all"
                    logging.debug("The shared image is not copied when "
                                  " '--migrate-disks' option")
                else:
                    test.fail("The actual output:\n%s\n"
                              "Can not find expected log "
                              "'disk2' on remote host '%s'"
                              % (output, server_ip))
            else:
                if virsh_options.find("--migrate-disks") < 0:
                    # This is expected as shared image should be
                    # copied when "--copy-storage-all"
                    logging.debug("The shared image is copied when "
                                  "no '--migrate-disks' option")
                else:
                    test.fail("The actual output:\n%s\n"
                              "Find unexpected log "
                              "'disk2' on remote host '%s'"
                              % (output, server_ip))
            # Check the disks on VM can work correctly.
            check_vm_disk_after_migration(test, vm, test_dict)

        if migrate_vm_back:
            # Pre migration setup for local machine
            migrate_setup.migrate_pre_setup(src_uri, params)
            remove_dict = {"do_search": ('{"%s": "ssh:/"}' % src_uri)}
            remote_libvirt_file = libvirt_config\
                .remove_key_for_modular_daemon(remove_dict, remote_dargs)
            cmd = "virsh migrate %s %s %s" % (vm_name,
                                              virsh_options, src_uri)
            logging.debug("Start migrating: %s", cmd)
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd, timeout=300)
            logging.info(output)

            if status:
                destroy_cmd = "virsh destroy %s" % vm_name
                run_remote_cmd(destroy_cmd, server_ip,
                               server_user, server_pwd)
                test.fail("Failed to run '%s' on remote: %s"
                          % (cmd, output))

    finally:
        logging.info("Recovery test environment")

        logging.debug("Removing vm on remote if it exists.")
        virsh.remove_domain(vm.name, uri=uri)
        if src_libvirt_file:
            src_libvirt_file.restore()
        if remote_libvirt_file:
            del remote_libvirt_file
        # Clean up of pre migration setup for local machine
        if migrate_vm_back:
            migrate_setup.migrate_pre_setup(src_uri, params,
                                            cleanup=True)

        if need_mkswap:
            if not vm.is_alive() or vm.is_dead():
                vm.start()
                vm.wait_for_login()
                vm.cleanup_swap()

        if not LOCAL_SELINUX_ENFORCING:
            logging.info("Put SELinux in enforcing mode")
            utils_selinux.set_status("enforcing")

        if not REMOTE_SELINUX_ENFORCING:
            logging.info("Put remote SELinux in enforcing mode")
            cmd = "setenforce enforcing"
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.cancel("Failed to set SELinux "
                            "in enforcing mode, %s" % output)

        # Delete all rules in chain or all chains
        if add_iptables_rules:
            process.run("iptables -F", ignore_status=True, shell=True)

        # Disable ports 24007 and 49152:49216
        if gluster_disk and 'host_ip' in locals():
            if host_ip == client_ip:
                logging.debug("Disable 24007 and 49152:49216 in Firewall")
                migrate_setup.migrate_pre_setup(src_uri, params, cleanup=True,
                                                ports="24007")
                migrate_setup.migrate_pre_setup(src_uri, params, cleanup=True)

        # Restore libvirtd conf and restart libvirtd
        if libvirtd_conf:
            logging.debug("Recover the configurations")
            server_params = {'server_ip': server_ip,
                             'server_user': server_user,
                             'server_pwd': server_pwd}
            libvirt.customize_libvirt_config(None,
                                             remote_host=True,
                                             extra_params=server_params,
                                             is_recover=True,
                                             config_object=libvirtd_conf)

        if deluser_cmd:
            process.run(deluser_cmd, ignore_status=True, shell=True)

        if local_image_list:
            for img_file in local_image_list:
                if os.path.exists(img_file):
                    logging.debug("Remove local image file %s.", img_file)
                    os.remove(img_file)

        status_error = test_dict.get("status_error", "no")
        if migrate_disks is True:
            attach_A_disk_source = test_dict.get("attach_A_disk_source")
            attach_B_disk_source = test_dict.get("attach_B_disk_source")
            libvirt.delete_local_disk("file", attach_A_disk_source)
            libvirt.delete_local_disk("file", attach_B_disk_source)
            if status_error == "no":
                cmd = "rm -rf %s" % attach_A_disk_source
                status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                server_pwd)
                if status:
                    test.fail("Failed to run '%s' on the remote: %s"
                              % (cmd, output))
        # Recovery remotely libvirt service
        if stop_libvirtd_remotely:
            libvirt.remotely_control_libvirtd(server_ip, server_user,
                                              server_pwd, "start", status_error)

        #if status_error == "no" and MIGRATE_RET and stop_remote_guest and not migr_vm_back:
        if status_error == "no":
            cmd = "virsh domstate %s" % vm_name
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)

            if not status and output.strip() in ("running", "idle", "paused", "no state"):
                cmd = "virsh destroy %s" % vm_name
                status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                server_pwd)
                if status:
                    test.fail("Failed to run '%s' on remote: %s"
                              % (cmd, output))

            if not status and re.search("--persistent", virsh_options):
                cmd = "virsh undefine %s" % vm_name
                match_string = "Domain %s has been undefined" % vm_name
                status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                server_pwd)
                if status or not re.search(match_string, output):
                    test.fail("Failed to run '%s' on the remote: %s"
                              % (cmd, output))
            vm.connect_uri = "qemu:///system"

        libvirtd = utils_libvirtd.Libvirtd()
        if disk_src_protocol == "gluster":
            gluster.setup_or_cleanup_gluster(False, **test_dict)
            libvirtd.restart()

        if disk_src_protocol == "iscsi":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
            libvirtd.restart()

        logging.info("Recovery VM XML configration")
        vmxml_backup.sync()
        logging.debug("The current VM XML:\n%s", vmxml_backup.xmltreefile)

        if target_vm_name is not None:
            # destroy guest on target machine
            remote_virsh_session = None
            try:
                migrate_setup.migrate_pre_setup(src_uri, params,
                                                cleanup=True,
                                                ports=uri_port[1:])
                remote_virsh_session = virsh.VirshPersistent(**remote_virsh_dargs)
                logging.debug("Destroy remote guest")
                remote_virsh_session.destroy(target_vm_name)
                logging.debug("Recover remote guest xml")
                remote_virsh_session.define(xml_path)
            except (process.CmdError, remote.SCPError) as detail:
                test.error(detail)
            finally:
                remote_virsh_session.close_session()

        if se_obj:
            logging.info("Recover virt NFS SELinux boolean")
            # Keep .ssh/authorized_keys for NFS cleanup later
            se_obj.cleanup(True)

        if nfs_serv and nfs_cli:
            logging.info("Cleanup NFS test environment...")
            nfs_serv.unexportfs_in_clean = True
            nfs_cli.cleanup()
            nfs_serv.cleanup()

        if mb_enable:
            vm_xml.VMXML.del_memoryBacking_tag(vm_name)

        if remote_image_list:
            for img_file in remote_image_list:
                cmd = "rm -rf %s" % img_file
                status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                server_pwd)
                if status:
                    test.fail("Failed to run '%s' on remote: %s"
                              % (cmd, output))

        # vms will be shutdown, so no need to do this cleanup
        # And migrated vms may be not login if the network is local lan
        if stress_type == "stress_on_host":
            logging.info("Unload stress from host")
            utils_test.unload_stress(stress_type, params=test_dict, vms=[vm])

        if HUGETLBFS_MOUNT:
            cmds = ["umount -l %s" % remote_hugetlbfs_path,
                    "sysctl vm.nr_hugepages=0",
                    "service libvirtd restart"]
            for cmd in cmds:
                status, output = run_remote_cmd(cmd, server_ip, server_user,
                                                server_pwd)
                if status:
                    test.fail("Failed to run '%s' on remote: %s"
                              % (cmd, output))
        if pool_created:
            pool_destroyed = create_destroy_pool_on_remote(test, "destroy", test_dict)
            if not pool_destroyed:
                test.error("Destroy pool on remote '%s' failed."
                           % server_ip)

        if nfs_mount_dir:
            logging.info("To remove '%s' on remote host ...", nfs_mount_dir)
            cmd = "rm -rf %s" % nfs_mount_dir
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                test.fail("Failed to run '%s' on the remote: %s"
                          % (cmd, output))

        # Stop netserver service and clean up netperf package
        if n_server_c:
            n_server_c.stop()
            n_server_c.package.env_cleanup(True)
        if n_client_c:
            n_client_c.package.env_cleanup(True)

        if n_server_s:
            n_server_s.stop()
            n_server_s.package.env_cleanup(True)
        if n_client_s:
            n_client_s.package.env_cleanup(True)

        if objs_list and len(objs_list) > 0:
            logging.debug("Clean up the objects")
            cleanup(objs_list)
        if (transport in ('tcp', 'tls') and uri_port) or disk_port:
            port = disk_port if disk_port else uri_port[1:]
            migrate_setup.migrate_pre_setup("//%s/" % server_ip, test_dict,
                                            cleanup=True, ports=port)

        cmds = ["modprobe -r kvm",
                "modprobe kvm"]
        if enable_kvm_hugepages:
            for cmd in cmds:
                process.run(cmd)
        if enable_remote_kvm_hugepages:
            status, output = run_remote_cmd(cmd, server_ip, server_user,
                                            server_pwd)
            if status:
                logging.debug("Failed to reload kvm module. %s", output)
