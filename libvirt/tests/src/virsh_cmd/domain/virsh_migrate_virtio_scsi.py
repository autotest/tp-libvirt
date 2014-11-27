import os
import logging
import threading
import time
from autotest.client.shared import error
from autotest.client.shared import utils
from autotest.client.shared import ssh_key
from virttest import utils_test
from virttest import virsh
from virttest import utils_misc
from virttest import libvirt_vm
from virttest import virt_vm
from virttest import data_dir
from virttest import remote
from virttest import aexpect
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test virsh migrate when disks are virtio-scsi.
    """

    def check_vm_state(vm, state):
        """
        Return True if vm is in the correct state.
        """
        try:
            actual_state = vm.state()
        except error.CmdError:
            return False
        if cmp(actual_state, state) == 0:
            return True
        else:
            return False

    def check_disks_in_vm(vm, vm_ip, disks_list=[], runner=None):
        """
        Check disks attached to vm.
        """
        fail_list = []
        while len(disks_list):
            disk = disks_list.pop()
            if runner:
                check_cmd = ("ssh %s \"dd if=/dev/urandom of=%s bs=1 "
                             "count=1024\"" % (vm_ip, disk))
                try:
                    logging.debug(runner.run(check_cmd))
                    continue
                except error.CmdError, detail:
                    logging.debug("Remote checking failed:%s", detail)
                    fail_list.append(disk)
            else:
                check_cmd = "dd if=/dev/urandom of=%s bs=1 count=1024"
                session = vm.wait_for_login()
                cs = session.cmd_status(check_cmd)
                if cs:
                    fail_list.append(disk)
                session.close()
        if len(fail_list):
            raise error.TestFail("Checking attached devices failed:%s"
                                 % fail_list)

    def get_disk_id(device):
        """
        Show disk by id.
        """
        output = utils.run("ls /dev/disk/by-id/").stdout
        for line in output.splitlines():
            disk_ids = line.split()
            for disk_id in disk_ids:
                disk = os.path.basename(utils.run("readlink %s"
                                                  % disk_id).stdout)
                if disk == os.path.basename(device):
                    return disk_id
        return None

    def cleanup_ssh_config(vm):
        session = vm.wait_for_login()
        session.cmd("rm -f ~/.ssh/authorized_keys")
        session.cmd("rm -f ~/.ssh/id_rsa*")
        session.close()

    def set_remote_vm_autologin(vm_ip, vm_pwd, runner):
        """
        Setup autologin between remote host and its vm.
        """
        runner.run("ssh-keygen -y -t rsa -q -N '' -f ~/.ssh/id_rsa")
        rsession = runner.session
        rsession.sendline("ssh-copy-id -i ~/.ssh/id_rsa.pub root@%s"
                          % vm_ip)
        while True:
            matched_strs = [r"[Aa]re you sure", r"[Pp]assword:\s*$",
                            r"lost connection", r"]#"]
            try:
                index, text = rsession.read_until_last_line_matches(
                    matched_strs, timeout=20,
                    internal_timeout=0.5)
            except (aexpect.ExpectTimeoutError,
                    aexpect.ExpectProcessTerminatedError), e:
                raise error.TestFail("Setup autologin to vm failed:%s" % e)
            logging.debug("%s:%s", index, text)
            if index == 0:
                logging.debug("Sending 'yes' for fingerprint...")
                rsession.sendline("yes")
                continue
            elif index == 1:
                logging.debug("Sending '%s' for vm logging...", vm_pwd)
                rsession.sendline(vm_pwd)
                continue
            elif index == 2:
                raise error.TestFail("Disconnected to vm on remote host.")
            elif index == 3:
                logging.debug("Logged in now...")
                break

    vm = env.get_vm(params.get("migrate_main_vm"))
    source_type = params.get("disk_source_type", "file")
    device_type = params.get("disk_device_type", "disk")
    disk_format = params.get("disk_format_type", "raw")
    if source_type == "file":
        params['added_disk_type'] = "file"
    else:
        params['added_disk_type'] = "block"
        block_device = params.get("disk_block_device", "/dev/EXAMPLE")
        if block_device.count("EXAMPLE"):
            # Prepare host parameters
            local_host = params.get("migrate_source_host", "LOCAL.EXAMPLE")
            remote_host = params.get("migrate_dest_host", "REMOTE.EXAMPLE")
            remote_user = params.get("migrate_dest_user", "root")
            remote_passwd = params.get("migrate_dest_pwd")
            if remote_host.count("EXAMPLE") or local_host.count("EXAMPLE"):
                raise error.TestNAError("Config remote or local host first.")
            rdm_params = {'remote_ip': remote_host,
                          'remote_user': remote_user,
                          'remote_pwd': remote_passwd}
            rdm = utils_test.RemoteDiskManager(rdm_params)
            # Try to build an iscsi device
            # For local, target is a device name
            target = utlv.setup_or_cleanup_iscsi(is_setup=True, is_login=True,
                                                 emulated_image="emulated_iscsi")
            logging.debug("Created target: %s", target)
            try:
                # Attach this iscsi device both local and remote
                remote_device = rdm.iscsi_login_setup(local_host, target)
            except Exception, detail:
                utlv.setup_or_cleanup_iscsi(is_setup=False)
                raise error.TestError("Attach iscsi device on remote failed:%s"
                                      % detail)

            # Use id to get same path on local and remote
            block_device = get_disk_id(target)
            if block_device is None:
                rdm.iscsi_login_setup(local_host, target, is_login=False)
                utlv.setup_or_cleanup_iscsi(is_setup=False)
                raise error.TestError("Set iscsi device couldn't find id?")

    srcuri = params.get("virsh_migrate_srcuri")
    dsturi = params.get("virsh_migrate_dsturi")
    remote_ip = params.get("remote_ip")
    username = params.get("remote_user", "root")
    host_pwd = params.get("remote_pwd")
    # Connection to remote, init here for cleanup
    runner = None
    # Identify easy config. mistakes early
    warning_text = ("Migration VM %s URI %s appears problematic "
                    "this may lead to migration problems. "
                    "Consider specifying vm.connect_uri using "
                    "fully-qualified network-based style.")

    if srcuri.count('///') or srcuri.count('EXAMPLE'):
        raise error.TestNAError(warning_text % ('source', srcuri))

    if dsturi.count('///') or dsturi.count('EXAMPLE'):
        raise error.TestNAError(warning_text % ('destination', dsturi))

    # Config auto-login to remote host for migration
    ssh_key.setup_ssh_key(remote_ip, username, host_pwd)

    sys_image = vm.get_first_disk_devices()
    sys_image_source = sys_image["source"]
    sys_image_info = utils_misc.get_image_info(sys_image_source)
    logging.debug("System image information:\n%s", sys_image_info)
    sys_image_fmt = sys_image_info["format"]
    created_img_path = os.path.join(os.path.dirname(sys_image_source),
                                    "vsmimages")

    migrate_in_advance = "yes" == params.get("migrate_in_advance", "no")

    status_error = "yes" == params.get("status_error", "no")
    if source_type == "file" and device_type == "lun":
        status_error = True

    try:
        # For safety and easily reasons, we'd better define a new vm
        new_vm_name = "%s_vsmtest" % vm.name
        mig = utlv.MigrationTest()
        if vm.is_alive():
            vm.destroy()
        utlv.define_new_vm(vm.name, new_vm_name)
        vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                           vm.address_cache)

        # Change the disk of the vm to shared disk
        # Detach exist devices
        devices = vm.get_blk_devices()
        for device in devices:
            s_detach = virsh.detach_disk(vm.name, device, "--config",
                                         debug=True)
            if not s_detach:
                raise error.TestError("Detach %s failed before test.", device)

        # Attach system image as vda
        # Then added scsi disks will be sda,sdb...
        attach_args = "--subdriver %s --config" % sys_image_fmt
        virsh.attach_disk(vm.name, sys_image_source, "vda",
                          attach_args, debug=True)

        vms = [vm]

        def start_check_vm(vm):
            try:
                vm.start()
            except virt_vm.VMStartError, detail:
                if status_error:
                    logging.debug("Expected failure:%s", detail)
                    return None, None
                else:
                    raise
            vm.wait_for_login()

            # Confirm VM can be accessed through network.
            # And this ip will be used on remote after migration
            vm_ip = vm.get_address()
            vm_pwd = params.get("password")
            s_ping, o_ping = utils_test.ping(vm_ip, count=2, timeout=60)
            logging.info(o_ping)
            if s_ping != 0:
                raise error.TestFail("%s did not respond after several "
                                     "seconds with attaching new devices."
                                     % vm.name)
            return vm_ip, vm_pwd

        options = "--live --unsafe"
        # Do migration before attaching new devices
        if migrate_in_advance:
            vm_ip, vm_pwd = start_check_vm(vm)
            cleanup_ssh_config(vm)
            mig_thread = threading.Thread(target=mig.thread_func_migration,
                                          args=(vm, dsturi, options))
            mig_thread.start()
            # Make sure migration is running
            time.sleep(2)

        # Attach other disks
        params['added_disk_target'] = "scsi"
        params['target_bus'] = "scsi"
        params['device_type'] = device_type
        params['type_name'] = source_type
        params['added_disk_format'] = disk_format
        if migrate_in_advance:
            params["attach_disk_config"] = "no"
            attach_disk_config = False
        else:
            params["attach_disk_config"] = "yes"
            attach_disk_config = True
        try:
            if source_type == "file":
                utlv.attach_disks(vm, "%s/image" % created_img_path,
                                  None, params)
            else:
                ret = utlv.attach_additional_device(vm.name, "sda", block_device,
                                                    params, config=attach_disk_config)
                if ret.exit_status:
                    raise error.TestFail(ret)
        except (error.TestFail, error.CmdError), detail:
            if status_error:
                logging.debug("Expected failure:%s", detail)
                return
            else:
                raise

        if migrate_in_advance:
            mig_thread.join(60)
            if mig_thread.isAlive():
                mig.RET_LOCK.acquire()
                mig.MIGRATION = False
                mig.RET_LOCK.release()
        else:
            vm_ip, vm_pwd = start_check_vm(vm)

        # Have got expected failures when starting vm, end the test
        if vm_ip is None and status_error:
            return

        # Start checking before migration and go on checking after migration
        disks = []
        for target in vm.get_disk_devices().keys():
            if target != "vda":
                disks.append("/dev/%s" % target)

        checked_count = int(params.get("checked_count", 0))
        disks_before = disks[:(checked_count / 2)]
        disks_after = disks[(checked_count / 2):checked_count]
        logging.debug("Disks to be checked:\nBefore migration:%s\n"
                      "After migration:%s", disks_before, disks_after)

        options = "--live --unsafe"
        if not migrate_in_advance:
            cleanup_ssh_config(vm)
            mig.do_migration(vms, None, dsturi, "orderly", options, 120)

        if mig.RET_MIGRATION:
            utils_test.check_dest_vm_network(vm, vm_ip, remote_ip,
                                             username, host_pwd)
            runner = remote.RemoteRunner(host=remote_ip, username=username,
                                         password=host_pwd)
            # After migration, config autologin to vm
            set_remote_vm_autologin(vm_ip, vm_pwd, runner)
            check_disks_in_vm(vm, vm_ip, disks_after, runner)

            if migrate_in_advance:
                raise error.TestFail("Migration before attaching successfully, "
                                     "but not expected.")

    finally:
        # Cleanup remote vm
        if srcuri != dsturi:
            mig.cleanup_dest_vm(vm, srcuri, dsturi)
        # Cleanup created vm anyway
        if vm.is_alive():
            vm.destroy(gracefully=False)
        virsh.undefine(new_vm_name)

        # Cleanup iscsi device for block if it is necessary
        if source_type == "block":
            if params.get("disk_block_device",
                          "/dev/EXAMPLE").count("EXAMPLE"):
                rdm.iscsi_login_setup(local_host, target, is_login=False)
                utlv.setup_or_cleanup_iscsi(is_setup=False,
                                            emulated_image="emulated_iscsi")

        if runner:
            runner.session.close()
        utils.run("rm -f %s/*vsmtest" % created_img_path)
